"""Stop command handler.

Handles the /stop Discord slash command by triggering server teardown.
Requires the same authorization as /launch (allowlist check).
"""

from __future__ import annotations

import json
from typing import Any

from discord_control_plane.adapters.state_store import StateStore
from discord_control_plane.core.authorization import is_authorized
from discord_control_plane.core.models import (
    Allowlist,
    InteractionResponse,
    InteractionResponseType,
)
from discord_control_plane.core.responses import (
    build_denial_response,
    build_error_response,
)
from discord_control_plane.handlers.teardown import handle_teardown


def _serialize_response(response) -> dict[str, Any]:
    """Serialize an InteractionResponse to a Discord-compatible dict."""
    result: dict[str, Any] = {"type": response.type.value}
    if response.content is not None:
        data: dict[str, Any] = {"content": response.content}
        if response.ephemeral:
            data["flags"] = 64  # Discord ephemeral flag
        result["data"] = data
    return result


_JSON_HEADERS = {"Content-Type": "application/json"}


def handle_stop(
    *,
    interaction: dict,
    allowlist: Allowlist,
    state_store: StateStore,
) -> dict[str, Any]:
    """Process a /stop command interaction.

    Checks authorization, then triggers server teardown. Returns an
    immediate channel message with the result.

    Args:
        interaction: The parsed Discord interaction payload.
        allowlist: Authorized user/role IDs.
        state_store: StateStore instance for state transitions.

    Returns:
        HTTP response dict with statusCode and body.
    """
    # Authorization check
    member = interaction.get("member", {})
    user_id = member.get("user", {}).get("id", "")
    role_ids = frozenset(member.get("roles", []))

    if not is_authorized(user_id=user_id, role_ids=role_ids, allowlist=allowlist):
        denial = build_denial_response()
        return {
            "statusCode": 200,
            "headers": _JSON_HEADERS,
            "body": json.dumps(_serialize_response(denial)),
        }

    # Execute teardown
    result = handle_teardown(source="discord_stop", state_store=state_store)

    status = result.get("status", "failure")
    if status == "success":
        content = "🛑 **Server stop initiated.** The instance is being torn down."
    elif status == "conflict":
        reason = result.get("reason", "Server is not in a stoppable state.")
        content = f"⚠️ **Cannot stop:** {reason}"
    else:
        reason = result.get("reason", "Unknown error.")
        content = f"❌ **Stop failed:** {reason}"

    response = InteractionResponse(
        type=InteractionResponseType.CHANNEL_MESSAGE,
        content=content,
    )
    return {
        "statusCode": 200,
        "headers": _JSON_HEADERS,
        "body": json.dumps(_serialize_response(response)),
    }
