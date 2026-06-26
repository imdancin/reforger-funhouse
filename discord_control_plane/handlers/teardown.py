"""Teardown_Handler Lambda entry point.

Drives the server lifecycle from RUNNING -> TEARING_DOWN -> OFFLINE by dispatching
a repository_dispatch with instance_count=0 to destroy the EC2 instance, then
finalizing state and posting a notification via the Discord channel webhook.

On post-destroy failure: the instance is NOT recreated, the current state is retained,
and a failure message (with reason) is posted to the Discord channel.

This handler is invoked by the Idle_Monitor CronJob via the AWS SDK when the idle
threshold is reached.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from discord_control_plane.adapters.state_store import (
    StateStore,
    TransitionStatus,
)
from discord_control_plane.adapters.github_dispatch import (
    dispatch_apply,
    GitHubDispatchError,
)
from discord_control_plane.adapters.discord_messaging import (
    post_webhook_notification,
    DiscordMessagingError,
)
from discord_control_plane.core.models import ServerState

logger = logging.getLogger(__name__)


class TeardownError(Exception):
    """Raised when the teardown handler encounters an unrecoverable error."""

    pass


def handle_teardown(
    source: str = "idle_monitor",
    state_store: StateStore | None = None,
    secrets_client=None,
) -> dict:
    """Execute server teardown.

    Steps:
        1. Transition state RUNNING -> TEARING_DOWN.
        2. Dispatch instance_count=0 via GitHub Actions to destroy the EC2 instance.
        3. On successful dispatch, transition state TEARING_DOWN -> OFFLINE.
        4. Post a teardown notification to the Discord channel via webhook.

    On post-destroy failure (after dispatch succeeds):
        - Do NOT recreate the instance (it's already being destroyed).
        - Retain the current state (TEARING_DOWN).
        - Post a failure message with the reason.

    Args:
        source: Identifier of what triggered the teardown (e.g. "idle_monitor").
        state_store: Optional StateStore instance (created with defaults if None).
        secrets_client: Optional boto3 Secrets Manager client for adapter calls.

    Returns:
        dict with "status" key: "success", "conflict", or "failure".
    """
    if state_store is None:
        state_store = StateStore()

    # Step 1: Transition RUNNING/LAUNCHING -> TEARING_DOWN
    current_record = state_store.get_state()

    teardown_eligible = {ServerState.RUNNING, ServerState.LAUNCHING}
    if current_record.state not in teardown_eligible:
        logger.info(
            "Teardown skipped: server state is %s (expected RUNNING or LAUNCHING)",
            current_record.state.value,
        )
        return {
            "status": "conflict",
            "reason": f"Server state is {current_record.state.value}, not RUNNING or LAUNCHING",
        }

    now_iso = datetime.now(timezone.utc).isoformat()
    transition_result = state_store.try_transition(
        expected_state=current_record.state.value,
        new_state=ServerState.TEARING_DOWN.value,
        attrs={
            "preset": current_record.preset,
            "updated_at": now_iso,
        },
        version=current_record.version,
    )

    if transition_result.status != TransitionStatus.ACQUIRED:
        logger.warning(
            "Teardown state transition %s -> TEARING_DOWN failed (conflict)",
            current_record.state.value,
        )
        return {
            "status": "conflict",
            "reason": "State transition conflict; another operation may be in progress",
        }

    tearing_down_record = transition_result.record

    # Step 2: Dispatch instance_count=0
    try:
        dispatch_apply(
            instance_count=0,
            active_scenario=current_record.preset,
            secrets_client=secrets_client,
        )
    except GitHubDispatchError as exc:
        # Dispatch failed — the instance is still running.
        # Retain TEARING_DOWN state and post failure notification.
        failure_reason = f"GitHub dispatch failed: {exc}"
        logger.error("Teardown dispatch failed: %s", failure_reason)
        _post_failure_notification(failure_reason)
        return {
            "status": "failure",
            "reason": failure_reason,
        }

    # Step 3: Transition TEARING_DOWN -> OFFLINE
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        offline_result = state_store.try_transition(
            expected_state=ServerState.TEARING_DOWN.value,
            new_state=ServerState.OFFLINE.value,
            attrs={
                "preset": current_record.preset,
                "public_ip": None,
                "interaction_token": None,
                "updated_at": now_iso,
            },
            version=tearing_down_record.version,
        )

        if offline_result.status != TransitionStatus.ACQUIRED:
            # Post-destroy failure: do not recreate, retain state, post failure
            failure_reason = (
                "State transition TEARING_DOWN -> OFFLINE failed after dispatch "
                "(conflict). Instance destroyed but state not updated."
            )
            logger.error(failure_reason)
            _post_failure_notification(failure_reason)
            return {
                "status": "failure",
                "reason": failure_reason,
            }
    except Exception as exc:
        # Post-destroy failure: retain current state, do not recreate
        failure_reason = (
            f"Failed to set OFFLINE state after teardown dispatch: {exc}"
        )
        logger.error(failure_reason)
        _post_failure_notification(failure_reason)
        return {
            "status": "failure",
            "reason": failure_reason,
        }

    # Step 4: Post teardown notification via channel webhook
    try:
        post_webhook_notification(
            content=(
                "🔌 **Server torn down** — The Arma Reforger server has been "
                f"shut down (triggered by: {source}). Use `/launch` to start it again."
            ),
            secrets_client=secrets_client,
        )
    except DiscordMessagingError as exc:
        # Notification failure is non-critical — state is already OFFLINE.
        # Log but do not fail the overall teardown.
        logger.warning("Failed to post teardown notification: %s", exc)

    logger.info("Teardown completed successfully (source: %s)", source)
    return {"status": "success"}


def _post_failure_notification(reason: str) -> None:
    """Post a teardown failure notification via the channel webhook.

    Best-effort: logs but does not raise on webhook failure.
    """
    try:
        post_webhook_notification(
            content=(
                "⚠️ **Teardown failure** — An error occurred during server teardown. "
                "The instance will NOT be recreated. Manual intervention may be required.\n"
                f"**Reason:** {reason}"
            ),
        )
    except DiscordMessagingError as exc:
        logger.error(
            "Failed to post teardown failure notification: %s", exc
        )


# ---------------------------------------------------------------------------
# Lambda entry point
# ---------------------------------------------------------------------------


def lambda_handler(event: dict, context) -> dict:
    """AWS Lambda handler for the Teardown_Handler.

    Invoked by the Idle_Monitor CronJob (or manually) via the AWS SDK
    lambda:InvokeFunction.

    Args:
        event: Lambda invocation event. May contain:
            - "source": string identifying the caller (default: "idle_monitor")
        context: Lambda context object (unused).

    Returns:
        dict with teardown result status.
    """
    source = event.get("source", "idle_monitor")
    return handle_teardown(source=source)
