"""Idle monitor handler — long-running entry point.

Orchestrates the RCON sampling loop and Prometheus metrics server. Reads
configuration from environment variables, exposes an `arma_connected_players`
Prometheus gauge on an HTTP `/metrics` endpoint, and will (in later tasks)
run the continuous sampling loop with idle-accounting logic.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Callable, Tuple
from wsgiref.simple_server import make_server

import boto3
from prometheus_client import REGISTRY, Gauge
from prometheus_client.exposition import (
    ThreadingWSGIServer,
    _bake_output,
    _get_best_family,
    _SilentHandler,
)
from prometheus_client.registry import Collector

from discord_control_plane.adapters.rcon_sampler import RconError, sample_player_count
from discord_control_plane.core.idle import IdleDecision, update_idle
from discord_control_plane.core.models import IdleState

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration validation
# ---------------------------------------------------------------------------


def validate_sample_interval(value: int) -> int:
    """Clamp the sample interval to the valid range [10, 600].

    Values below 10 are clamped to 10; values above 600 are clamped to 600.
    """
    if value < 10:
        return 10
    if value > 600:
        return 600
    return value


def validate_metrics_port(value: int) -> int:
    """Validate the metrics port is within range [1024, 65535].

    Raises:
        ValueError: If the value is outside the allowed range.
    """
    if value < 1024 or value > 65535:
        raise ValueError(
            f"Metrics port must be between 1024 and 65535, got {value}"
        )
    return value


# ---------------------------------------------------------------------------
# Prometheus gauge
# ---------------------------------------------------------------------------

arma_connected_players: Gauge = Gauge(
    "arma_connected_players",
    "Number of players currently connected to the Arma Reforger server",
)


# ---------------------------------------------------------------------------
# Metrics HTTP server
# ---------------------------------------------------------------------------


def make_metrics_app(registry: Collector = REGISTRY) -> Callable:
    """Create a WSGI app that serves metrics only on ``/metrics``.

    - GET /metrics → 200 with Prometheus text exposition format
    - Any other path → 404
    - Non-GET method on /metrics → 405
    """

    def metrics_app(environ, start_response):
        path = environ.get("PATH_INFO", "/")
        method = environ["REQUEST_METHOD"]

        if path != "/metrics":
            status = "404 Not Found"
            headers = [("Content-Type", "text/plain; charset=utf-8")]
            output = b"Not Found\n"
            start_response(status, headers)
            return [output]

        if method != "GET":
            status = "405 Method Not Allowed"
            headers = [
                ("Content-Type", "text/plain; charset=utf-8"),
                ("Allow", "GET"),
            ]
            output = b"Method Not Allowed\n"
            start_response(status, headers)
            return [output]

        # Serve Prometheus metrics
        accept_header = environ.get("HTTP_ACCEPT")
        accept_encoding_header = environ.get("HTTP_ACCEPT_ENCODING")
        params = {}
        status, headers, output = _bake_output(
            registry, accept_header, accept_encoding_header, params, False
        )
        start_response(status, headers)
        return [output]

    return metrics_app


def start_metrics_server(
    port: int, registry: Collector = REGISTRY
) -> Tuple[ThreadingWSGIServer, threading.Thread]:
    """Start the Prometheus metrics HTTP server in a daemon thread.

    Returns the server instance and the daemon thread.
    """

    class MetricsServer(ThreadingWSGIServer):
        """Local copy to allow address_family mutation."""

    MetricsServer.address_family, addr = _get_best_family("0.0.0.0", port)
    app = make_metrics_app(registry)
    httpd = make_server(addr, port, app, MetricsServer, handler_class=_SilentHandler)
    t = threading.Thread(target=httpd.serve_forever)
    t.daemon = True
    t.start()
    return httpd, t


# ---------------------------------------------------------------------------
# Sample handling
# ---------------------------------------------------------------------------


def handle_sample(
    state: IdleState,
    gauge: Gauge,
    player_count: int,
    now: float,
    threshold: float,
    teardown_fn: str,
    aws_region: str,
) -> IdleState:
    """Process a successful RCON sample.

    Updates the Prometheus gauge, runs idle-accounting logic, and triggers
    the teardown Lambda if the idle threshold has been reached.

    Returns the new IdleState after accounting.
    """
    gauge.set(player_count)

    decision: IdleDecision = update_idle(state, player_count, now, threshold)

    logger.info(
        "Sample: players=%d, idle_since=%s, should_teardown=%s",
        player_count,
        decision.new_state.idle_since,
        decision.should_teardown,
    )

    if decision.should_teardown:
        try:
            client = boto3.client("lambda", region_name=aws_region)
            client.invoke(
                FunctionName=teardown_fn,
                InvocationType="Event",
            )
            logger.info("Teardown Lambda invoked: %s", teardown_fn)
        except Exception:
            logger.exception(
                "Failed to invoke teardown Lambda: %s", teardown_fn
            )

    return decision.new_state


def handle_error(state: IdleState, gauge: Gauge, error: RconError) -> IdleState:
    """Handle an RCON sampling error.

    Logs the error at warning level. Does NOT update the gauge (retains the
    last successful value). Returns the IdleState unchanged — no idle-accounting
    update is performed on error.
    """
    logger.warning("RCON sample failed: %s", error)
    return state


# ---------------------------------------------------------------------------
# Sampling loop
# ---------------------------------------------------------------------------


def run_sample_loop(
    gauge: Gauge,
    sample_interval: int,
    idle_threshold: float,
    rcon_host: str,
    rcon_port: int,
    rcon_password: str,
    teardown_fn: str,
    aws_region: str,
) -> None:
    """Run the continuous RCON sampling loop.

    Initializes idle state and loops forever: samples the player count via
    RCON, updates the gauge and idle-accounting on success, or logs and
    preserves state on error. Sleeps for ``sample_interval`` seconds between
    iterations (measured from completion of one sample to start of next).
    """
    state = IdleState(idle_since=None)

    while True:
        try:
            player_count = sample_player_count(
                host=rcon_host, port=rcon_port, password=rcon_password
            )
            state = handle_sample(
                state, gauge, player_count, time.time(),
                idle_threshold, teardown_fn, aws_region,
            )
        except RconError as e:
            state = handle_error(state, gauge, e)

        time.sleep(sample_interval)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Read configuration from environment and start the idle monitor."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    sample_interval = validate_sample_interval(
        int(os.environ.get("SAMPLE_INTERVAL_SECONDS", "60"))
    )
    metrics_port = validate_metrics_port(
        int(os.environ.get("METRICS_PORT", "8000"))
    )
    idle_threshold = int(os.environ.get("IDLE_THRESHOLD_SECONDS", "1800"))
    rcon_host = os.environ.get("RCON_HOST", "127.0.0.1")
    rcon_port = int(os.environ.get("RCON_PORT", "1999"))
    rcon_password = os.environ.get("RCON_PASSWORD", "")
    teardown_function_name = os.environ.get("TEARDOWN_FUNCTION_NAME", "")
    aws_region = os.environ.get("AWS_REGION", "")

    logger.info(
        "Idle monitor starting: sample_interval=%ds, metrics_port=%d, "
        "idle_threshold=%ds, rcon=%s:%d",
        sample_interval,
        metrics_port,
        idle_threshold,
        rcon_host,
        rcon_port,
    )

    # Start the metrics HTTP server in a daemon thread
    httpd, metrics_thread = start_metrics_server(metrics_port)
    logger.info("Metrics server started on port %d", metrics_port)

    # Start the continuous RCON sampling loop (blocks forever)
    run_sample_loop(
        gauge=arma_connected_players,
        sample_interval=sample_interval,
        idle_threshold=idle_threshold,
        rcon_host=rcon_host,
        rcon_port=rcon_port,
        rcon_password=rcon_password,
        teardown_fn=teardown_function_name,
        aws_region=aws_region,
    )


if __name__ == "__main__":
    main()
