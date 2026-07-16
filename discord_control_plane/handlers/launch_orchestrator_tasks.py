"""LaunchOrchestrator Step Functions task Lambda handlers.

Thin Lambda entry points for the orchestrator tasks:
- SetPreset: writes the active scenario to SSM
- DispatchApply: triggers GitHub Actions with instance_count=1
- CheckReady: probes bootstrap status + port 2001
- MarkRunning: sets state to RUNNING, posts connection details

Each handler receives the Step Functions event (carrying preset,
interaction_token, application_id, channel_id) and returns a result
dict for the next state.
"""

from __future__ import annotations

import logging
import os
import socket
import time

import boto3

from discord_control_plane.adapters.discord_messaging import post_followup
from discord_control_plane.adapters.github_dispatch import dispatch_apply
from discord_control_plane.adapters.scenario_store import set_active_scenario
from discord_control_plane.adapters.state_store import StateStore, TransitionStatus
from discord_control_plane.core.models import ServerState
from discord_control_plane.core.readiness import is_ready

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SetPreset
# ---------------------------------------------------------------------------


def handle_set_preset(event: dict, context=None) -> dict:
    """Write the resolved preset to /arma-reforger/active-scenario.

    Expected event keys:
        - preset: values file name (e.g. "values-freedomfighters.yaml")

    Returns the event dict (pass-through for next state).
    """
    preset = event.get("preset", "")
    set_active_scenario(preset)
    logger.info("Active scenario set to: %s", preset)
    return event


def lambda_handler_set_preset(event: dict, context=None) -> dict:
    """AWS Lambda entry point for SetPreset."""
    return handle_set_preset(event, context)


# ---------------------------------------------------------------------------
# DispatchApply
# ---------------------------------------------------------------------------


def handle_dispatch_apply(event: dict, context=None) -> dict:
    """Trigger GitHub Actions with instance_count=1.

    Expected event keys:
        - preset: active scenario values file

    Returns the event dict (pass-through for next state).
    """
    preset = event.get("preset", "")

    # Reset bootstrap-status to prevent stale "ready" from a prior cycle
    ssm = boto3.client("ssm")
    try:
        ssm.put_parameter(
            Name="/arma-reforger/bootstrap-status",
            Value="provisioning",
            Type="String",
            Overwrite=True,
        )
    except Exception as exc:
        logger.error("Failed to reset bootstrap-status: %s", exc)
        raise RuntimeError(f"Failed to reset bootstrap-status: {exc}") from exc
    logger.info("Reset bootstrap-status to provisioning")

    dispatch_apply(instance_count=1, active_scenario=preset)
    logger.info("GitHub dispatch sent: instance_count=1, scenario=%s", preset)

    # Note: the /launch handler now acknowledges the interaction immediately
    # with a "startup initiated" message, so there is no deferred "thinking"
    # spinner to clear here. MarkRunning posts the connection details as a
    # follow-up once the server is online.
    #
    # Stamp the dispatch time so CheckReady can track elapsed wait time and
    # set timed_out=True with a safety margin below the state machine's hard
    # TimeoutSeconds. Without this, a slow bootstrap gets silently killed by
    # the top-level timeout (which bypasses the Failed/TimedOut task states
    # and never posts a Discord message) instead of failing gracefully.
    return {**event, "dispatch_started_at": time.time()}


def lambda_handler_dispatch_apply(event: dict, context=None) -> dict:
    """AWS Lambda entry point for DispatchApply."""
    return handle_dispatch_apply(event, context)


# ---------------------------------------------------------------------------
# CheckReady
# ---------------------------------------------------------------------------


def _probe_port(host: str, port: int, timeout: float = 5.0) -> bool:
    """Return True if a TCP connection to host:port succeeds."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, socket.timeout):
        return False



# Safety margin (seconds) subtracted from LAUNCH_TIMEOUT_SECONDS so CheckReady
# reports timed_out=True — triggering the graceful Failed/TimedOut path that
# posts a Discord message — before the Step Functions state-machine-level
# TimeoutSeconds hard-kills the execution. A hard kill bypasses Catch blocks
# entirely, which previously left users with no message at all once bootstrap
# started taking longer (it now waits for the game server to genuinely be
# healthy rather than declaring victory right after `kubectl apply`).
_TIMEOUT_SAFETY_MARGIN_SECONDS = 60


def handle_check_ready(event: dict, context=None) -> dict:
    """Check if the server is ready (bootstrap-status + port 2001).

    Reads bootstrap-status from SSM and probes the game port on the
    public IP. Returns {"ready": True/False, "timed_out": True/False, ...event}.

    timed_out is derived from elapsed time since DispatchApply stamped
    dispatch_started_at on the event, compared against LAUNCH_TIMEOUT_SECONDS
    minus a safety margin. This lets the orchestrator take the graceful
    TimedOut branch (posts a Discord message, reconciles state to OFFLINE)
    instead of being silently killed by the state machine's hard timeout.
    """
    ssm = boto3.client("ssm")

    # Check bootstrap status
    try:
        response = ssm.get_parameter(Name="/arma-reforger/bootstrap-status")
        bootstrap_status = response["Parameter"]["Value"]
    except Exception:
        bootstrap_status = "unknown"

    # Get public IP from SSM or state store
    try:
        response = ssm.get_parameter(Name="/arma-reforger/public-address")
        public_ip = response["Parameter"]["Value"]
    except Exception:
        public_ip = None

    # Evaluate readiness — bootstrap-status is the primary signal.
    # Port 2001 is UDP (game traffic) and cannot be probed via TCP.
    # If bootstrap-status starts with "ready", the EC2 user_data completed
    # successfully, meaning K3s + game server pod are running.
    bootstrap_ready = bootstrap_status.startswith("ready")
    server_ready = bootstrap_ready

    # Compute elapsed time since dispatch to decide whether we've run out of
    # budget. dispatch_started_at may be absent (e.g. older in-flight
    # executions, or direct test invocations) — treat that as "just started"
    # rather than failing the check.
    dispatch_started_at = event.get("dispatch_started_at")
    launch_timeout = int(os.environ.get("LAUNCH_TIMEOUT_SECONDS", "900"))
    soft_deadline = max(launch_timeout - _TIMEOUT_SAFETY_MARGIN_SECONDS, 0)

    timed_out = False
    if not server_ready and dispatch_started_at is not None:
        elapsed = time.time() - dispatch_started_at
        timed_out = elapsed >= soft_deadline

    result = {
        **event,
        "ready": server_ready,
        "timed_out": timed_out,
        "public_ip": public_ip,
    }
    logger.info(
        "Readiness check: bootstrap=%s, ready=%s, timed_out=%s",
        bootstrap_status,
        server_ready,
        timed_out,
    )
    return result


def lambda_handler_check_ready(event: dict, context=None) -> dict:
    """AWS Lambda entry point for CheckReady."""
    return handle_check_ready(event, context)


# ---------------------------------------------------------------------------
# MarkRunning
# ---------------------------------------------------------------------------


def handle_mark_running(event: dict, context=None) -> dict:
    """Set state to RUNNING, persist public IP, post connection details.

    Expected event keys:
        - public_ip: server public IP address
        - interaction_token: for Discord follow-up
        - application_id: Discord application ID
        - preset: active preset
    """
    table_name = os.environ.get("STATE_TABLE_NAME", "arma-server-state")
    application_id = event.get("application_id", "")
    interaction_token = event.get("interaction_token", "")
    public_ip = event.get("public_ip", "")
    preset = event.get("preset", "")

    # Transition state to RUNNING
    state_store = StateStore(table_name=table_name)
    current = state_store.get_state()

    if current.state == ServerState.LAUNCHING:
        result = state_store.try_transition(
            expected_state=ServerState.LAUNCHING.value,
            new_state=ServerState.RUNNING.value,
            attrs={
                "preset": preset,
                "public_ip": public_ip,
            },
            version=current.version,
        )
        if result.status != TransitionStatus.ACQUIRED:
            logger.warning("Failed to transition to RUNNING (conflict)")

    # Post connection details to Discord
    if interaction_token and public_ip:
        try:
            post_followup(
                application_id=application_id,
                interaction_token=interaction_token,
                content=(
                    f"✅ **Server is ready!** Connect at `{public_ip}:2001`"
                ),
            )
        except Exception as exc:
            logger.error("Failed to post connection details: %s", exc)

    return {**event, "status": "running"}


def lambda_handler_mark_running(event: dict, context=None) -> dict:
    """AWS Lambda entry point for MarkRunning."""
    return handle_mark_running(event, context)
