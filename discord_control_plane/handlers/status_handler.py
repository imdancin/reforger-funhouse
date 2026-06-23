"""Status command handler.

Handles the /status Discord slash command by reading current server state
from DynamoDB and returning a formatted status message.

This is a lightweight read-only operation — no authorization required
since server status is informational for all guild members.
"""

from __future__ import annotations

import json
from typing import Any

from discord_control_plane.core.models import (
    InteractionResponseType,
    ServerState,
)
from discord_control_plane.core.responses import build_error_response
from discord_control_plane.core.status import build_status_reply


# ---------------------------------------------------------------------------
# Status emoji mapping
# ---------------------------------------------------------------------------

_STATE_EMOJI: dict[ServerState, str] = {
    ServerState.OFFLINE: "⚫",
    ServerState.LAUNCHING: "🟡",
    ServerState.RUNNING: "🟢",
    ServerState.TEARING_DOWN: "🔴",
}

_STATE_DESCRIPTION: dict[ServerState, str] = {
    ServerState.OFFLINE: "Server is offline. Use `/launch` to start it.",
    ServerState.LAUNCHING: "Server is starting up. Please wait...",
    ServerState.RUNNING: "Server is online and accepting connections.",
    ServerState.TEARING_DOWN: "Server is shutting down. Please wait...",
}


def _serialize_response(response) -> dict[str, Any]:
    """Serialize an InteractionResponse to a Discord-compatible dict."""
    result: dict[str, Any] = {"type": response.type.value}
    if response.content is not None:
        data: dict[str, Any] = {"content": response.content}
        if response.ephemeral:
            data["flags"] = 64  # Discord ephemeral flag
        result["data"] = data
    return result


def handle_status(*, state_store) -> dict[str, Any]:
    """Process a /status command interaction.

    Reads the current server state from DynamoDB and returns a formatted
    status message with connection details when available.

    Args:
        state_store: Object with get_state() method.

    Returns:
        HTTP response dict with statusCode and body.
    """
    try:
        record = state_store.get_state()
    except Exception:
        error_resp = build_error_response(
            "Unable to retrieve server status. Please try again later."
        )
        return {"statusCode": 200, "body": json.dumps(_serialize_response(error_resp))}

    status_reply = build_status_reply(record)
    emoji = _STATE_EMOJI.get(status_reply.state, "❓")
    description = _STATE_DESCRIPTION.get(status_reply.state, "Unknown state.")

    # Build the status message
    lines = [
        f"{emoji} **Server Status: {status_reply.state.value}**",
        "",
        description,
    ]

    # Add connection details if running
    if status_reply.connection_details:
        ip = status_reply.connection_details.public_ip
        port = status_reply.connection_details.game_port
        lines.append("")
        lines.append(f"🔗 **Connect:** `{ip}:{port}`")

    # Add preset info if available
    if record.preset:
        lines.append(f"🎮 **Preset:** {record.preset}")

    content = "\n".join(lines)

    from discord_control_plane.core.models import InteractionResponse

    response = InteractionResponse(
        type=InteractionResponseType.CHANNEL_MESSAGE,
        content=content,
    )
    return {"statusCode": 200, "body": json.dumps(_serialize_response(response))}
