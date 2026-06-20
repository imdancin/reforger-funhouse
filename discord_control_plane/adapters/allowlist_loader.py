"""Load the Discord allowlist from AWS SSM Parameter Store.

The allowlist is stored as a JSON document at /arma-reforger/discord-allowlist
and defines which Discord user IDs and role IDs are authorized to launch the server.

Expected JSON format:
    {"user_ids": ["111111111111111111"], "role_ids": ["222222222222222222"]}

On any failure (missing parameter, access denied, invalid JSON, invalid structure),
this module raises AllowlistConfigurationError. It never falls back to an empty or
permissive allowlist.
"""

from __future__ import annotations

import json

import boto3
from botocore.exceptions import ClientError

from discord_control_plane.core.models import Allowlist


class AllowlistConfigurationError(Exception):
    """Raised when the allowlist cannot be loaded from SSM."""

    pass


_DEFAULT_PARAMETER_NAME = "/arma-reforger/discord-allowlist"


def load_allowlist(
    ssm_client=None,
    parameter_name: str = _DEFAULT_PARAMETER_NAME,
) -> Allowlist:
    """Load and parse the allowlist from SSM Parameter Store.

    Args:
        ssm_client: An optional boto3 SSM client. If not provided, a new client
            is created using the default session.
        parameter_name: The SSM parameter path. Defaults to
            /arma-reforger/discord-allowlist.

    Returns:
        An Allowlist instance with user_ids and role_ids as frozensets.

    Raises:
        AllowlistConfigurationError: If the parameter is missing, access is denied,
            the value is not valid JSON, or the JSON structure is invalid.
            Never falls back to an empty/permissive allowlist.
    """
    if ssm_client is None:
        ssm_client = boto3.client("ssm")

    # Fetch the parameter from SSM
    try:
        response = ssm_client.get_parameter(
            Name=parameter_name,
            WithDecryption=True,
        )
    except ClientError as e:
        error_code = e.response["Error"]["Code"]
        if error_code == "ParameterNotFound":
            raise AllowlistConfigurationError(
                f"Allowlist SSM parameter not found: {parameter_name}"
            ) from e
        elif error_code in ("AccessDeniedException", "AccessDenied"):
            raise AllowlistConfigurationError(
                f"Access denied reading allowlist parameter: {parameter_name}"
            ) from e
        else:
            raise AllowlistConfigurationError(
                f"Failed to read allowlist parameter {parameter_name}: {error_code}"
            ) from e
    except Exception as e:
        raise AllowlistConfigurationError(
            f"Unexpected error loading allowlist from {parameter_name}: {e}"
        ) from e

    # Parse the JSON value
    raw_value = response["Parameter"]["Value"]
    try:
        data = json.loads(raw_value)
    except (json.JSONDecodeError, TypeError) as e:
        raise AllowlistConfigurationError(
            f"Allowlist parameter is not valid JSON: {e}"
        ) from e

    # Validate structure
    if not isinstance(data, dict):
        raise AllowlistConfigurationError(
            f"Allowlist must be a JSON object, got {type(data).__name__}"
        )

    user_ids = data.get("user_ids")
    role_ids = data.get("role_ids")

    if user_ids is None:
        raise AllowlistConfigurationError(
            "Allowlist JSON missing required field: user_ids"
        )
    if role_ids is None:
        raise AllowlistConfigurationError(
            "Allowlist JSON missing required field: role_ids"
        )

    if not isinstance(user_ids, list) or not all(
        isinstance(uid, str) for uid in user_ids
    ):
        raise AllowlistConfigurationError(
            "Allowlist 'user_ids' must be a list of strings"
        )
    if not isinstance(role_ids, list) or not all(
        isinstance(rid, str) for rid in role_ids
    ):
        raise AllowlistConfigurationError(
            "Allowlist 'role_ids' must be a list of strings"
        )

    return Allowlist(
        user_ids=frozenset(user_ids),
        role_ids=frozenset(role_ids),
    )
