"""Standalone RCON metrics exporter — long-running entry point.

Continuously samples the Arma Reforger server's connected player count via
BattlEye RCON and exposes it as the `arma_connected_players` Prometheus gauge
on an HTTP `/metrics` endpoint. This is intentionally minimal: no idle
accounting, no teardown logic — just the metric, for the standard monitoring
stack (Prometheus/Grafana) to scrape alongside node-exporter and
kube-state-metrics.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Callable, Tuple
from wsgiref.simple_server import make_server

from prometheus_client import REGISTRY, Gauge
from prometheus_client.exposition import (
    ThreadingWSGIServer,
    _bake_output,
    _get_best_family,
    _SilentHandler,
)
from prometheus_client.registry import Collector

from discord_control_plane.adapters.rcon_sampler import RconError, sample_player_count

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

    - GET /metrics -> 200 with Prometheus text exposition format
    - Any other path -> 404
    - Non-GET method on /metrics -> 405
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
# Sampling loop
# ---------------------------------------------------------------------------


def run_sample_loop(
    gauge: Gauge,
    sample_interval: int,
    rcon_host: str,
    rcon_port: int,
    rcon_password: str,
) -> None:
    """Run the continuous RCON sampling loop.

    Samples the player count via RCON and updates the gauge on success. On
    error, logs and retains the gauge's last successfully observed value.
    Sleeps for ``sample_interval`` seconds between iterations.
    """
    consecutive_errors = 0

    while True:
        try:
            player_count = sample_player_count(
                host=rcon_host, port=rcon_port, password=rcon_password
            )
            if consecutive_errors:
                logger.info(
                    "RCON sampling recovered after %d consecutive error(s); "
                    "players=%d",
                    consecutive_errors,
                    player_count,
                )
                consecutive_errors = 0
            gauge.set(player_count)
            logger.debug("Sample: players=%d", player_count)
        except RconError as e:
            consecutive_errors += 1
            if consecutive_errors == 1 or consecutive_errors % 5 == 0:
                logger.warning(
                    "RCON sampling failing: %d consecutive error(s). "
                    "Gauge retains last known value: %s",
                    consecutive_errors,
                    e,
                )

        time.sleep(sample_interval)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Read configuration from environment and start the metrics exporter."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    sample_interval = validate_sample_interval(
        int(os.environ.get("SAMPLE_INTERVAL_SECONDS", "60"))
    )
    metrics_port = validate_metrics_port(
        int(os.environ.get("METRICS_PORT", "9877"))
    )
    rcon_host = os.environ.get("RCON_HOST", "127.0.0.1")
    rcon_port = int(os.environ.get("RCON_PORT", "1999"))
    rcon_password = os.environ.get("RCON_PASSWORD", "")

    logger.info(
        "Metrics exporter starting: sample_interval=%ds, metrics_port=%d, "
        "rcon=%s:%d",
        sample_interval,
        metrics_port,
        rcon_host,
        rcon_port,
    )

    start_metrics_server(metrics_port)
    logger.info("Metrics server started on port %d", metrics_port)

    run_sample_loop(
        gauge=arma_connected_players,
        sample_interval=sample_interval,
        rcon_host=rcon_host,
        rcon_port=rcon_port,
        rcon_password=rcon_password,
    )


if __name__ == "__main__":
    main()
