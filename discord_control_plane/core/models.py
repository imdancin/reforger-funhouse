"""Core data models for the Discord control plane.

All models are frozen dataclasses (immutable value objects) to keep the pure-logic
core free of mutation side effects.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


# ---------------------------------------------------------------------------
# Server lifecycle state
# ---------------------------------------------------------------------------


class ServerState(str, Enum):
    """Lifecycle state of the Arma Reforger server."""

    OFFLINE = "OFFLINE"
    LAUNCHING = "LAUNCHING"
    RUNNING = "RUNNING"
    TEARING_DOWN = "TEARING_DOWN"


# ---------------------------------------------------------------------------
# Persistent state record (DynamoDB shape)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ServerStateRecord:
    """Durable state stored in DynamoDB for the server lifecycle."""

    state: ServerState
    preset: str
    version: int
    public_ip: str | None = None
    interaction_token: str | None = None
    channel_id: str | None = None
    launch_started_at: str | None = None
    updated_at: str | None = None


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Allowlist:
    """Set of Discord user IDs and role IDs authorized to launch the server."""

    user_ids: frozenset[str]
    role_ids: frozenset[str]


# ---------------------------------------------------------------------------
# Presets
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Preset:
    """A named server configuration mapping to a Helm values file."""

    key: str
    values_file: str
    display_name: str


PRESETS: dict[str, Preset] = {
    "freedomfighters": Preset(
        "freedomfighters", "values-freedomfighters.yaml", "Freedom Fighters"
    ),
    "proceduralcombat": Preset(
        "proceduralcombat", "values-proceduralcombat.yaml", "Procedural Combat"
    ),
}

DEFAULT_PRESET: str = "values-freedomfighters.yaml"


# ---------------------------------------------------------------------------
# Connection details
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConnectionDetails:
    """Information a player needs to join the server."""

    public_ip: str
    game_port: int = 2001


# ---------------------------------------------------------------------------
# Launch request / decision
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LaunchRequest:
    """Incoming launch request from a Discord interaction."""

    user_id: str
    role_ids: list[str]
    requested_preset: str | None
    interaction_token: str
    channel_id: str


class LaunchDecisionType(str, Enum):
    """Possible outcomes of a launch decision."""

    ACQUIRE = "ACQUIRE"
    REPLY_BUSY = "REPLY_BUSY"
    REPLY_TEARING_DOWN = "REPLY_TEARING_DOWN"


@dataclass(frozen=True)
class LaunchDecision:
    """Result of evaluating a launch request against the current server state."""

    decision: LaunchDecisionType
    current_state: ServerState
    connection_details: ConnectionDetails | None = None
    preset: str | None = None  # recorded preset on ACQUIRE


# ---------------------------------------------------------------------------
# Preset resolution
# ---------------------------------------------------------------------------


class PresetResolutionStatus(str, Enum):
    """Outcome of resolving a requested preset."""

    OK = "OK"
    ERROR = "ERROR"


@dataclass(frozen=True)
class PresetResolution:
    """Result of resolving a preset from a user request."""

    status: PresetResolutionStatus
    preset: Preset | None = None  # set when OK
    values_file: str | None = None  # set when OK
    error_message: str | None = None  # set when ERROR
    available_presets: list[str] | None = None  # set when ERROR


# ---------------------------------------------------------------------------
# Discord interaction response
# ---------------------------------------------------------------------------


class InteractionResponseType(int, Enum):
    """Discord interaction callback types."""

    PONG = 1
    CHANNEL_MESSAGE = 4
    DEFERRED_CHANNEL_MESSAGE = 5
    DEFERRED_UPDATE = 6


@dataclass(frozen=True)
class InteractionResponse:
    """Structured Discord interaction response."""

    type: InteractionResponseType
    content: str | None = None
    ephemeral: bool = False
