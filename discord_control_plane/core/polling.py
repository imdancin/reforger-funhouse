"""Launch-timeout polling decisions.

Pure functions that determine whether to continue polling for server readiness
or to declare a timeout, and compute the next wait interval with simple backoff.
"""

from __future__ import annotations

from enum import Enum

from discord_control_plane.core.models import ServerState


class PollDecision(str, Enum):
    """Outcome of a launch-timeout poll check."""

    CONTINUE = "CONTINUE"
    TIMEOUT = "TIMEOUT"


def should_continue(
    current_state: ServerState,
    elapsed_seconds: float,
    timeout_seconds: float,
) -> PollDecision:
    """Return TIMEOUT if not yet RUNNING and elapsed >= timeout, else CONTINUE.

    If the server has already reached RUNNING, we always return CONTINUE
    (the poll loop should stop naturally because readiness is satisfied).
    Otherwise, if the elapsed launch duration meets or exceeds the configured
    timeout, we declare TIMEOUT.
    """
    if current_state is ServerState.RUNNING:
        return PollDecision.CONTINUE

    if elapsed_seconds >= timeout_seconds:
        return PollDecision.TIMEOUT

    return PollDecision.CONTINUE


def next_wait(
    attempt: int,
    base_wait: float = 10.0,
    max_wait: float = 30.0,
) -> float:
    """Return the next wait duration (seconds) before the next readiness check.

    Uses simple linear backoff capped at *max_wait*:
        wait = min(base_wait * (attempt + 1), max_wait)

    Parameters
    ----------
    attempt:
        Zero-based attempt counter (0 for the first poll, 1 for the second, etc.)
    base_wait:
        Base interval in seconds (default 10).
    max_wait:
        Upper bound on the wait in seconds (default 30).
    """
    return min(base_wait * (attempt + 1), max_wait)
