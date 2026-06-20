"""LaunchOrchestrator Step Functions task Lambda handlers.

Each function in this module is a thin Lambda entry point for one state in the
LaunchOrchestrator state machine. The pure decision logic lives in `core/`;
these handlers orchestrate I/O (DynamoDB, SSM, Discord, GitHub) around those
decisions.

Step Functions invokes each handler with an event dict containing the launch
context (preset, interaction_token, application_id, channel_id, etc.).
"""

from __future__ import annotations

import logging
import os

from discord_control_plane.adapters.discord_messaging import (
    post_followup,
)
from discord_control_plane.adapters.state_store import (
    StateStore,
    TransitionStatus,
)
from discord_control_plane.core.models import ServerState

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Failed task Lambda
# ---------------------------------------------------------------------------


def handle_failed(event: dict, context=None) -> dict:
    """Handle a launch failure in the orchestrator.

    Posts a failure follow-up message including the failure reason to the
    originating Discord channel (via the interaction token), then reconciles
    the server state back to OFFLINE so a subsequent launch is possible.

    Expected event keys:
        - application_id: Discord application ID
        - interaction_token: Token for posting follow-ups
        - reason: Human-readable failure reason
        - table_name (optional): DynamoDB table override

    Returns a dict summarising the outcome for Step Functions.
    """
    application_id = event.get("application_id") or os.environ.get(
        "DISCORD_APPLICATION_ID", ""
    )
    interaction_token = event.get("interaction_token", "")
    reason = event.get("reason", "Unknown error")
    table_name = event.get("table_name", os.environ.get("STATE_TABLE", "arma-server-state"))

    # 1. Post failure follow-up to Discord
    message = f"❌ **Launch failed:** {reason}"
    followup_posted = False
    try:
        post_followup(
            application_id=application_id,
            interaction_token=interaction_token,
            content=message,
        )
        followup_posted = True
    except Exception as exc:
        logger.error("Failed to post failure follow-up to Discord: %s", exc)

    # 2. Reconcile state back to OFFLINE
    state_store = StateStore(table_name=table_name)
    current = state_store.get_state()

    reconciled = False
    if current.state in (ServerState.LAUNCHING, ServerState.RUNNING):
        result = state_store.try_transition(
            expected_state=current.state.value,
            new_state=ServerState.OFFLINE.value,
            attrs={"preset": "", "interaction_token": None, "channel_id": None},
            version=current.version,
        )
        reconciled = result.status == TransitionStatus.ACQUIRED
        if not reconciled:
            logger.warning(
                "State reconciliation to OFFLINE failed (conflict). "
                "Current state: %s, version: %d",
                result.record.state.value,
                result.record.version,
            )
    elif current.state == ServerState.OFFLINE:
        # Already OFFLINE, nothing to reconcile
        reconciled = True
    else:
        # TEARING_DOWN — don't interfere with an active teardown
        logger.info(
            "State is %s; skipping reconciliation to OFFLINE.",
            current.state.value,
        )

    return {
        "status": "failed",
        "reason": reason,
        "followup_posted": followup_posted,
        "reconciled": reconciled,
    }


# ---------------------------------------------------------------------------
# TimedOut task Lambda
# ---------------------------------------------------------------------------


def handle_timed_out(event: dict, context=None) -> dict:
    """Handle a launch timeout in the orchestrator.

    Posts a timeout follow-up message to the originating Discord channel,
    then reconciles the server state back to OFFLINE so a subsequent launch
    is possible.

    Expected event keys:
        - application_id: Discord application ID
        - interaction_token: Token for posting follow-ups
        - table_name (optional): DynamoDB table override

    Returns a dict summarising the outcome for Step Functions.
    """
    application_id = event.get("application_id") or os.environ.get(
        "DISCORD_APPLICATION_ID", ""
    )
    interaction_token = event.get("interaction_token", "")
    table_name = event.get("table_name", os.environ.get("STATE_TABLE", "arma-server-state"))

    # 1. Post timeout follow-up to Discord
    message = (
        "⏱️ **Launch timed out.** The server did not become ready within the "
        "configured timeout. You may try launching again."
    )
    followup_posted = False
    try:
        post_followup(
            application_id=application_id,
            interaction_token=interaction_token,
            content=message,
        )
        followup_posted = True
    except Exception as exc:
        logger.error("Failed to post timeout follow-up to Discord: %s", exc)

    # 2. Reconcile state back to OFFLINE
    state_store = StateStore(table_name=table_name)
    current = state_store.get_state()

    reconciled = False
    if current.state in (ServerState.LAUNCHING, ServerState.RUNNING):
        result = state_store.try_transition(
            expected_state=current.state.value,
            new_state=ServerState.OFFLINE.value,
            attrs={"preset": "", "interaction_token": None, "channel_id": None},
            version=current.version,
        )
        reconciled = result.status == TransitionStatus.ACQUIRED
        if not reconciled:
            logger.warning(
                "State reconciliation to OFFLINE failed (conflict). "
                "Current state: %s, version: %d",
                result.record.state.value,
                result.record.version,
            )
    elif current.state == ServerState.OFFLINE:
        # Already OFFLINE, nothing to reconcile
        reconciled = True
    else:
        # TEARING_DOWN — don't interfere with an active teardown
        logger.info(
            "State is %s; skipping reconciliation to OFFLINE.",
            current.state.value,
        )

    return {
        "status": "timed_out",
        "followup_posted": followup_posted,
        "reconciled": reconciled,
    }


# ---------------------------------------------------------------------------
# Lambda entry points (thin wrappers for AWS Lambda)
# ---------------------------------------------------------------------------


def lambda_handler_failed(event: dict, context=None) -> dict:
    """AWS Lambda entry point for the Failed task."""
    return handle_failed(event, context)


def lambda_handler_timed_out(event: dict, context=None) -> dict:
    """AWS Lambda entry point for the TimedOut task."""
    return handle_timed_out(event, context)
