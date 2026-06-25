"""Idle-accounting logic for the server lifecycle.

Pure functions that track how long the player count has been zero and decide
when to initiate teardown based on a configurable threshold.
"""

from __future__ import annotations

from dataclasses import dataclass

from discord_control_plane.core.models import IdleState

DEFAULT_IDLE_THRESHOLD_SECONDS: float = 30.0 * 60.0  # 30 minutes


@dataclass(frozen=True)
class IdleDecision:
    """Result of an idle-accounting update.

    Attributes:
        new_state: The updated IdleState after accounting.
        should_teardown: True if the idle duration has reached the threshold.
    """

    new_state: IdleState
    should_teardown: bool


def update_idle(
    state: IdleState,
    player_count: int,
    now: float,
    threshold_seconds: float = DEFAULT_IDLE_THRESHOLD_SECONDS,
) -> IdleDecision:
    """Pure idle accounting.

    - If player_count > 0: reset idle_since to None, no teardown.
    - If player_count == 0 and idle_since is None: set idle_since = now, no teardown.
    - If player_count == 0 and idle_since is set: check if (now - idle_since) >= threshold.
      If yes, flag teardown. If no, keep state, no teardown.
    """
    if player_count > 0:
        return IdleDecision(new_state=IdleState(idle_since=None), should_teardown=False)

    # player_count == 0
    if state.idle_since is None:
        return IdleDecision(new_state=IdleState(idle_since=now), should_teardown=False)

    # idle_since is already set — check against threshold
    elapsed = now - state.idle_since
    if elapsed >= threshold_seconds:
        return IdleDecision(new_state=state, should_teardown=True)

    return IdleDecision(new_state=state, should_teardown=False)
