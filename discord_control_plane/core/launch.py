"""Launch decision logic — pure state-transition function.

Evaluates the current server state to determine whether a new launch request
should acquire the server, be told the server is busy, or be told a teardown
is in progress.
"""

from __future__ import annotations

from discord_control_plane.core.models import (
    ConnectionDetails,
    LaunchDecision,
    LaunchDecisionType,
    ServerState,
    ServerStateRecord,
)


def decide_launch(current: ServerStateRecord, resolved_preset: str) -> LaunchDecision:
    """Pure transition decision: ACQUIRE / REPLY_BUSY / REPLY_TEARING_DOWN.

    Args:
        current: The current durable server state record.
        resolved_preset: The preset values file that was resolved for this request.

    Returns:
        A LaunchDecision indicating what action to take.
    """
    if current.state is ServerState.OFFLINE:
        return LaunchDecision(
            decision=LaunchDecisionType.ACQUIRE,
            current_state=ServerState.OFFLINE,
            preset=resolved_preset,
        )

    if current.state in (ServerState.LAUNCHING, ServerState.RUNNING):
        connection_details = (
            ConnectionDetails(public_ip=current.public_ip)
            if current.public_ip
            else None
        )
        return LaunchDecision(
            decision=LaunchDecisionType.REPLY_BUSY,
            current_state=current.state,
            connection_details=connection_details,
        )

    # TEARING_DOWN
    return LaunchDecision(
        decision=LaunchDecisionType.REPLY_TEARING_DOWN,
        current_state=ServerState.TEARING_DOWN,
    )
