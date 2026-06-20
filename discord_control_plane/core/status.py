"""Status reply builder for verified status queries."""

from __future__ import annotations

from dataclasses import dataclass

from discord_control_plane.core.models import (
    ConnectionDetails,
    ServerState,
    ServerStateRecord,
)


@dataclass(frozen=True)
class StatusReply:
    """Reply payload for a status query."""

    state: ServerState
    connection_details: ConnectionDetails | None = None


def build_status_reply(record: ServerStateRecord) -> StatusReply:
    """Build a status reply from the current server state record.

    Always includes the current state.
    Includes ConnectionDetails (public IP + port 2001) iff state is RUNNING and public_ip is set.
    """
    connection_details: ConnectionDetails | None = None

    if record.state == ServerState.RUNNING and record.public_ip is not None:
        connection_details = ConnectionDetails(
            public_ip=record.public_ip, game_port=2001
        )

    return StatusReply(state=record.state, connection_details=connection_details)
