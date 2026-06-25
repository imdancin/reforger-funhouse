"""GitHub Actions repository_dispatch adapter.

Triggers Terraform apply workflows by dispatching a repository_dispatch event
with instance_count and active_scenario in the client payload.
"""

from __future__ import annotations

import json
import urllib.request
from urllib.error import HTTPError, URLError

import boto3


class GitHubDispatchError(Exception):
    """Raised when the GitHub dispatch fails."""

    pass


def _get_github_token(secret_name: str, secrets_client=None) -> str:
    """Retrieve the GitHub fine-grained PAT from Secrets Manager.

    Raises GitHubDispatchError if the secret cannot be retrieved.
    """
    if secrets_client is None:
        secrets_client = boto3.client("secretsmanager")

    try:
        response = secrets_client.get_secret_value(SecretId=secret_name)
    except Exception as exc:
        raise GitHubDispatchError(
            f"Failed to retrieve GitHub token from Secrets Manager: {exc}"
        ) from exc

    return response["SecretString"]


def dispatch_apply(
    instance_count: int,
    active_scenario: str,
    repo: str = "imdancin/reforger-funhouse",
    secret_name: str = "/arma-reforger/github-dispatch-token",
    secrets_client=None,
) -> None:
    """Trigger a repository_dispatch event on the target repo.

    Fetches the GitHub PAT from Secrets Manager, then POSTs to
    https://api.github.com/repos/{repo}/dispatches with:
    {
        "event_type": "terraform-apply",
        "client_payload": {
            "instance_count": instance_count,
            "active_scenario": active_scenario
        }
    }

    Raises GitHubDispatchError on any failure.
    """
    token = _get_github_token(secret_name, secrets_client=secrets_client)

    url = f"https://api.github.com/repos/{repo}/dispatches"
    payload = {
        "event_type": "terraform-apply",
        "client_payload": {
            "instance_count": instance_count,
            "active_scenario": active_scenario,
        },
    }
    body = json.dumps(payload).encode("utf-8")

    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )

    try:
        with urllib.request.urlopen(request) as response:
            # GitHub returns 204 No Content on success
            if response.status not in (200, 204):
                raise GitHubDispatchError(
                    f"Unexpected response status {response.status} from GitHub"
                )
    except HTTPError as exc:
        raise GitHubDispatchError(
            f"GitHub dispatch failed with HTTP {exc.code}: {exc.reason}"
        ) from exc
    except URLError as exc:
        raise GitHubDispatchError(
            f"GitHub dispatch failed: {exc.reason}"
        ) from exc
