"""Pure logic core — no I/O, no AWS dependencies."""

from discord_control_plane.core.models import (
    Allowlist,
    ConnectionDetails,
    DEFAULT_PRESET,
    IdleState,
    InteractionResponse,
    InteractionResponseType,
    LaunchDecision,
    LaunchDecisionType,
    LaunchRequest,
    PRESETS,
    Preset,
    PresetResolution,
    PresetResolutionStatus,
    ServerState,
    ServerStateRecord,
)
from discord_control_plane.core.readiness import is_ready, should_retain_running
from discord_control_plane.core.verification import verify_signature
from discord_control_plane.core.launch import decide_launch
from discord_control_plane.core.polling import (
    PollDecision,
    next_wait,
    should_continue,
)
from discord_control_plane.core.idle import (
    DEFAULT_IDLE_THRESHOLD_SECONDS,
    IdleDecision,
    update_idle,
)
from discord_control_plane.core.responses import (
    build_deferred_response,
    build_denial_response,
    build_error_response,
    build_pong_response,
)

__all__ = [
    "Allowlist",
    "ConnectionDetails",
    "DEFAULT_IDLE_THRESHOLD_SECONDS",
    "DEFAULT_PRESET",
    "IdleDecision",
    "IdleState",
    "InteractionResponse",
    "InteractionResponseType",
    "LaunchDecision",
    "LaunchDecisionType",
    "LaunchRequest",
    "PRESETS",
    "PollDecision",
    "Preset",
    "PresetResolution",
    "PresetResolutionStatus",
    "ServerState",
    "ServerStateRecord",
    "build_deferred_response",
    "build_denial_response",
    "build_error_response",
    "build_pong_response",
    "decide_launch",
    "is_ready",
    "next_wait",
    "should_continue",
    "should_retain_running",
    "update_idle",
    "verify_signature",
]
