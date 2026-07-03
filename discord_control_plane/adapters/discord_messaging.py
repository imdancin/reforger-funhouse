"""Discord messaging adapter for interaction follow-ups and webhook notifications.

This module handles two Discord messaging paths:

1. Interaction-token follow-ups (POST/PATCH) — used during the 15-minute window
   after a deferred acknowledgement to post launch progress and connection details.
2. Channel-webhook notifications (POST) — used for teardown notifications that
   occur after the interaction token has expired.

Uses urllib.request for HTTP calls (no external HTTP library dependency).
"""

from __future__ import annotations

import json
import urllib.request
import urllib.error

import boto3

DISCORD_API_BASE = "https://discord.com/api/v10"


class DiscordMessagingError(Exception):
    """Raised when a Discord messaging operation fails."""

    pass


def post_followup(
    application_id: str,
    interaction_token: str,
    content: str,
) -> None:
    """Post a follow-up message using the interaction token.

    POST to /webhooks/{application_id}/{interaction_token}

    Used to send additional messages after the initial deferred ack, within
    the ~15 minute token validity window.

    Args:
        application_id: The Discord application (bot) ID.
        interaction_token: The interaction token from the original webhook event.
        content: The message text to post.

    Raises:
        DiscordMessagingError: If the request fails or Discord returns an error.
    """
    url = f"{DISCORD_API_BASE}/webhooks/{application_id}/{interaction_token}"
    payload = json.dumps({"content": content}).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req) as resp:
            # 2xx is success; Discord returns 200 or 204 for follow-ups
            if resp.status >= 300:
                raise DiscordMessagingError(
                    f"Discord follow-up POST failed: HTTP {resp.status}"
                )
    except urllib.error.HTTPError as exc:
        raise DiscordMessagingError(
            f"Discord follow-up POST failed: HTTP {exc.code} - {exc.reason}"
        ) from exc
    except urllib.error.URLError as exc:
        raise DiscordMessagingError(
            f"Discord follow-up POST failed: {exc.reason}"
        ) from exc


def edit_original(
    application_id: str,
    interaction_token: str,
    content: str,
) -> None:
    """Edit the original interaction response.

    PATCH to /webhooks/{application_id}/{interaction_token}/messages/@original

    Used to update the deferred ack placeholder with real content (e.g.
    connection details once the server is RUNNING).

    Args:
        application_id: The Discord application (bot) ID.
        interaction_token: The interaction token from the original webhook event.
        content: The updated message text.

    Raises:
        DiscordMessagingError: If the request fails or Discord returns an error.
    """
    url = (
        f"{DISCORD_API_BASE}/webhooks/{application_id}/{interaction_token}"
        f"/messages/@original"
    )
    payload = json.dumps({"content": content}).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="PATCH",
    )

    try:
        with urllib.request.urlopen(req) as resp:
            if resp.status >= 300:
                raise DiscordMessagingError(
                    f"Discord edit-original PATCH failed: HTTP {resp.status}"
                )
    except urllib.error.HTTPError as exc:
        raise DiscordMessagingError(
            f"Discord edit-original PATCH failed: HTTP {exc.code} - {exc.reason}"
        ) from exc
    except urllib.error.URLError as exc:
        raise DiscordMessagingError(
            f"Discord edit-original PATCH failed: {exc.reason}"
        ) from exc


def post_webhook_notification(
    content: str,
    secret_name: str = "/arma-reforger/discord-channel-webhook-url",
    secrets_client=None,
) -> None:
    """Post a message via a Discord channel webhook.

    Used for notifications that occur after the interaction token has expired
    (e.g. teardown notifications). Fetches the webhook URL from AWS Secrets
    Manager, then POSTs to it.

    Args:
        content: The message text to post.
        secret_name: The Secrets Manager secret name containing the webhook URL.
            Defaults to "/arma-reforger/discord-channel-webhook-url" — this must
            match the secret defined in Terraform (control-plane.tf) and the ARN
            scoped in the Lambda IAM policy, otherwise GetSecretValue is denied
            and teardown notifications fail silently.
        secrets_client: Optional pre-configured boto3 Secrets Manager client.
            If None, a default client is created.

    Raises:
        DiscordMessagingError: If the secret cannot be retrieved or the
            webhook POST fails.
    """
    # Retrieve webhook URL from Secrets Manager
    if secrets_client is None:
        secrets_client = boto3.client("secretsmanager")

    try:
        response = secrets_client.get_secret_value(SecretId=secret_name)
        webhook_url = response["SecretString"]
    except Exception as exc:
        raise DiscordMessagingError(
            f"Failed to retrieve webhook URL from Secrets Manager "
            f"(secret: {secret_name}): {exc}"
        ) from exc

    if not webhook_url:
        raise DiscordMessagingError(
            f"Webhook URL secret '{secret_name}' is empty"
        )

    # POST the notification to the channel webhook
    payload = json.dumps({"content": content}).encode("utf-8")

    req = urllib.request.Request(
        webhook_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req) as resp:
            if resp.status >= 300:
                raise DiscordMessagingError(
                    f"Discord webhook POST failed: HTTP {resp.status}"
                )
    except urllib.error.HTTPError as exc:
        raise DiscordMessagingError(
            f"Discord webhook POST failed: HTTP {exc.code} - {exc.reason}"
        ) from exc
    except urllib.error.URLError as exc:
        raise DiscordMessagingError(
            f"Discord webhook POST failed: {exc.reason}"
        ) from exc
