"""Authorization logic for the Discord control plane."""

from discord_control_plane.core.models import Allowlist


def is_authorized(user_id: str, role_ids: list[str], allowlist: Allowlist) -> bool:
    """True iff user_id or any role_id is present in the allowlist."""
    if user_id in allowlist.user_ids:
        return True
    if any(role_id in allowlist.role_ids for role_id in role_ids):
        return True
    return False
