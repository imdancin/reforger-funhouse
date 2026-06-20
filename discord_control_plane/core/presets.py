"""Preset resolution for launch requests.

Maps a user-supplied preset name to a known Preset definition, falling
back to the default when no preset is specified and returning a
descriptive error when the requested preset is unknown.
"""

from __future__ import annotations

from discord_control_plane.core.models import (
    DEFAULT_PRESET,
    PRESETS,
    Preset,
    PresetResolution,
    PresetResolutionStatus,
)


def resolve_preset(
    requested: str | None, presets: dict[str, Preset] = PRESETS
) -> PresetResolution:
    """Map a request to a Preset. Missing -> default. Unknown -> error listing valid presets.

    Parameters
    ----------
    requested:
        The preset key supplied by the user, or None / empty string if omitted.
    presets:
        The available preset definitions (defaults to the module-level PRESETS dict).

    Returns
    -------
    PresetResolution
        OK with the resolved preset and values file, or ERROR with a message
        listing the available presets.
    """
    if not requested:
        # No preset specified — use the default (freedomfighters)
        default_preset = presets["freedomfighters"]
        return PresetResolution(
            status=PresetResolutionStatus.OK,
            preset=default_preset,
            values_file=DEFAULT_PRESET,
        )

    if requested in presets:
        preset = presets[requested]
        return PresetResolution(
            status=PresetResolutionStatus.OK,
            preset=preset,
            values_file=preset.values_file,
        )

    # Unknown preset — return error listing available presets
    available = sorted(presets.keys())
    return PresetResolution(
        status=PresetResolutionStatus.ERROR,
        error_message=f"Unknown preset '{requested}'. Available presets: {', '.join(available)}",
        available_presets=available,
    )
