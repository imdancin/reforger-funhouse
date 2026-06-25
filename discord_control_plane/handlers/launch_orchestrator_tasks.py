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
    return event


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


def handle_check_ready(event: dict, context=None) -> dict:
    """Check if the server is ready (bootstrap-status + port 2001).

    Reads bootstrap-status from SSM and probes the game port on the
    public IP. Returns {"ready": True/False, ...event}.
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

    # Evaluate readiness
    bootstrap_ready = bootstrap_status.startswith("ready")
    port_reachable = _probe_port(public_ip, 2001) if public_ip else False
    server_ready = is_ready(bootstrap_ready, port_reachable)

    result = {**event, "ready": server_ready, "timed_out": False, "public_ip": public_ip}
    logger.info(
        "Readiness check: bootstrap=%s, port=%s, ready=%s",
        bootstrap_status,
        port_reachable,
        server_ready,
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
