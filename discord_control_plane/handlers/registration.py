"""Slash-command registration payload builder.

Produces the JSON-serializable payloads required by the Discord
`POST /applications/{app_id}/commands` endpoint to register
slash commands (/launch, /status).

This module is a pure payload builder — it does not perform HTTP calls.
"""

from __future__ import annotations

from discord_control_plane.core.models import PRESETS, Preset


# Discord Application Command Option Types
# https://discord.com/developers/docs/interactions/application-commands#application-command-object-application-command-option-type
_OPTION_TYPE_STRING = 3


def build_launch_command_payload(
    presets: dict[str, Preset] = PRESETS,
) -> dict:
    """Build the Discord slash-command registration payload for /launch.

    The payload includes a single string option named ``preset`` whose
    choices are derived from the provided preset definitions.

    Parameters
    ----------
    presets:
        Preset definitions to expose as choices. Defaults to the
        module-level ``PRESETS`` dict.

    Returns
    -------
    dict
        A JSON-serializable dictionary conforming to Discord's
        Create Global Application Command request body.
    """
    choices = [
        {"name": preset.display_name, "value": preset.key}
        for preset in presets.values()
    ]

    return {
        "name": "launch",
        "description": "Launch the Arma Reforger server with a chosen preset",
        "type": 1,  # CHAT_INPUT
        "options": [
            {
                "name": "preset",
                "description": "Server preset to launch",
                "type": _OPTION_TYPE_STRING,
                "required": False,
                "choices": choices,
            }
        ],
    }


def build_status_command_payload() -> dict:
    """Build the Discord slash-command registration payload for /status.

    The /status command has no options — it simply reports the current
    server lifecycle state and connection details when available.

    Returns
    -------
    dict
        A JSON-serializable dictionary conforming to Discord's
        Create Global Application Command request body.
    """
    return {
        "name": "status",
        "description": "Check the current Arma Reforger server status",
        "type": 1,  # CHAT_INPUT
        "options": [],
    }


def build_stop_command_payload() -> dict:
    """Build the Discord slash-command registration payload for /stop.

    The /stop command has no options — it triggers a manual server teardown.
    Requires the same authorization as /launch.

    Returns
    -------
    dict
        A JSON-serializable dictionary conforming to Discord's
        Create Global Application Command request body.
    """
    return {
        "name": "stop",
        "description": "Stop the Arma Reforger server and tear down the instance",
        "type": 1,  # CHAT_INPUT
        "options": [],
    }
