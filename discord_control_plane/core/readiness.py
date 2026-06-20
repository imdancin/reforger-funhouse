"""Readiness and state-retention decisions for the server lifecycle.

Pure functions that determine whether the server should transition to RUNNING
or retain its current state based on reachability signals.
"""

from __future__ import annotations

from discord_control_plane.core.models import ServerState


def is_ready(bootstrap_status_ready: bool, game_port_reachable: bool) -> bool:
    """Return True iff bootstrap status reports ready AND game port 2001 is reachable."""
    return bootstrap_status_ready and game_port_reachable


def should_retain_running(
    current_state: ServerState, server_reachable: bool, teardown_initiated: bool
) -> bool:
    """Return True if state should remain RUNNING despite unreachability.

    When state is RUNNING and the server becomes unreachable but no teardown
    has been initiated, the state is retained as RUNNING. Transient
    unreachability does NOT cause a state change away from RUNNING — only an
    explicit teardown changes it.
    """
    return (
        current_state == ServerState.RUNNING
        and not server_reachable
        and not teardown_initiated
    )
