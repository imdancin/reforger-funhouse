"""Response builders for Discord interaction callbacks.

Each function returns an InteractionResponse value object suitable for
serializing into a Discord interaction callback payload.
"""

from __future__ import annotations

from discord_control_plane.core.models import InteractionResponse, InteractionResponseType


def build_pong_response() -> InteractionResponse:
    """Build a PONG response (type 1) to answer a Discord PING interaction."""
    return InteractionResponse(type=InteractionResponseType.PONG)


def build_deferred_response() -> InteractionResponse:
    """Build a deferred channel message response (type 5).

    Used to acknowledge the command within 3 seconds while background work
    (e.g. launching infrastructure) continues asynchronously.
    """
    return InteractionResponse(type=InteractionResponseType.DEFERRED_CHANNEL_MESSAGE)


def build_launch_started_response(estimate: str = "8–12 minutes") -> InteractionResponse:
    """Build the immediate /launch acknowledgement (type 4).

    Returned synchronously within Discord's 3-second window. The connection
    details are posted later as a follow-up by the orchestrator's MarkRunning
    step once the server is RUNNING.

    Args:
        estimate: Human-readable estimate of how long the server takes to come
            online. Defaults to the typical provisioning window.
    """
    return InteractionResponse(
        type=InteractionResponseType.CHANNEL_MESSAGE,
        content=(
            "🚀 **Server startup initiated!** The server is provisioning now and "
            f"should be online in about {estimate}. I'll post the connection "
            "details here once it's ready."
        ),
    )


def build_denial_response(
    reason: str = "You are not authorized to use this command.",
) -> InteractionResponse:
    """Build an ephemeral denial response for unauthorized users."""
    return InteractionResponse(
        type=InteractionResponseType.CHANNEL_MESSAGE,
        content=reason,
        ephemeral=True,
    )


def build_error_response(message: str) -> InteractionResponse:
    """Build an error response (e.g. unknown preset with available presets listed)."""
    return InteractionResponse(
        type=InteractionResponseType.CHANNEL_MESSAGE,
        content=message,
        ephemeral=True,
    )
