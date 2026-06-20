"""Launch_Handler Lambda entry point.

Orchestrates the Discord interaction flow:
1. Verify Ed25519 signature → 401 on failure
2. PING → PONG
3. Load allowlist from SSM → refuse on failure
4. Authorize → ephemeral denial on failure
5. Resolve preset → error on unknown
6. Conditional OFFLINE → LAUNCHING transition (records preset, token, channel)
   → on conflict reply busy/tearing-down/connection details
7. Post "launch started" message to originating channel
8. StartExecution on LaunchOrchestrator
9. Return deferred ack (type 5) within 3 seconds
"""

from __future__ import annotations

import json
from typing import Any

from discord_control_plane.core.authorization import is_authorized
from discord_control_plane.core.launch import decide_launch
from discord_control_plane.core.models import (
    Allowlist,
    InteractionResponseType,
    LaunchDecisionType,
    PresetResolutionStatus,
    ServerState,
    ServerStateRecord,
)
from discord_control_plane.core.presets import resolve_preset
from discord_control_plane.core.responses import (
    build_deferred_response,
    build_denial_response,
    build_error_response,
    build_pong_response,
)
from discord_control_plane.core.verification import verify_signature


def _serialize_response(response) -> dict[str, Any]:
    """Serialize an InteractionResponse to a Discord-compatible dict."""
    result: dict[str, Any] = {"type": response.type.value}
    if response.content is not None:
        data: dict[str, Any] = {"content": response.content}
        if response.ephemeral:
            data["flags"] = 64  # Discord ephemeral flag
        result["data"] = data
    return result


def handle_interaction(
    *,
    body: bytes,
    signature: str | None,
    timestamp: str | None,
    public_key_hex: str,
    allowlist_loader,
    state_store,
    step_functions_starter,
    discord_messenger,
    application_id: str,
) -> dict[str, Any]:
    """Process a Discord interaction webhook.

    Args:
        body: Raw request body bytes.
        signature: X-Signature-Ed25519 header value (or None if missing).
        timestamp: X-Signature-Timestamp header value (or None if missing).
        public_key_hex: The Discord application public key hex string.
        allowlist_loader: Callable that returns an Allowlist (raises on failure).
        state_store: Object with get_state() and try_transition() methods.
        step_functions_starter: Callable(state_machine_arn, input_dict) to start execution.
        discord_messenger: Object with post_followup(app_id, token, content) method.
        application_id: Discord application ID for follow-ups.

    Returns:
        HTTP response dict with statusCode and body.
    """
    # 1. Verify signature
    if not signature or not timestamp:
        return {"statusCode": 401, "body": "Invalid signature"}

    if not verify_signature(public_key_hex, signature, timestamp, body):
        return {"statusCode": 401, "body": "Invalid signature"}

    # Parse the interaction body
    try:
        interaction = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {"statusCode": 401, "body": "Invalid body"}

    # 2. PING → PONG
    interaction_type = interaction.get("type")
    if interaction_type == 1:  # PING
        pong = build_pong_response()
        return {"statusCode": 200, "body": json.dumps(_serialize_response(pong))}

    # 3. Load allowlist
    try:
        allowlist = allowlist_loader()
    except Exception:
        error_resp = build_error_response(
            "Configuration error: unable to load authorization data."
        )
        return {"statusCode": 200, "body": json.dumps(_serialize_response(error_resp))}

    # 4. Authorize
    member = interaction.get("member", {})
    user_id = member.get("user", {}).get("id", "")
    role_ids = member.get("roles", [])

    if not is_authorized(user_id, role_ids, allowlist):
        denial = build_denial_response()
        return {"statusCode": 200, "body": json.dumps(_serialize_response(denial))}

    # 5. Resolve preset
    options = interaction.get("data", {}).get("options", [])
    requested_preset = None
    for opt in options:
        if opt.get("name") == "preset":
            requested_preset = opt.get("value")
            break

    resolution = resolve_preset(requested_preset)
    if resolution.status == PresetResolutionStatus.ERROR:
        error_resp = build_error_response(resolution.error_message or "Unknown preset")
        return {"statusCode": 200, "body": json.dumps(_serialize_response(error_resp))}

    # 6. Conditional state transition OFFLINE → LAUNCHING
    current_record = state_store.get_state()
    decision = decide_launch(current_record, resolution.values_file)

    if decision.decision != LaunchDecisionType.ACQUIRE:
        # Reply with current state info
        if decision.decision == LaunchDecisionType.REPLY_BUSY:
            details = ""
            if decision.connection_details:
                details = (
                    f" Connect at {decision.connection_details.public_ip}"
                    f":{decision.connection_details.game_port}"
                )
            content = f"Server is currently {decision.current_state.value}.{details}"
        else:
            content = "A teardown is currently in progress. Please try again shortly."

        error_resp = build_error_response(content)
        return {"statusCode": 200, "body": json.dumps(_serialize_response(error_resp))}

    # Attempt the conditional write
    interaction_token = interaction.get("token", "")
    channel_id = interaction.get("channel_id", "")

    transition_result = state_store.try_transition(
        expected_state=ServerState.OFFLINE.value,
        new_state=ServerState.LAUNCHING.value,
        attrs={
            "preset": resolution.values_file,
            "interaction_token": interaction_token,
            "channel_id": channel_id,
        },
        version=current_record.version,
    )

    if transition_result.status.value == "CONFLICT":
        content = f"Server is currently {transition_result.record.state.value}."
        error_resp = build_error_response(content)
        return {"statusCode": 200, "body": json.dumps(_serialize_response(error_resp))}

    # 7. Post "launch started" message to the originating channel
    discord_messenger.post_followup(
        application_id,
        interaction_token,
        f"🚀 Launch started! Deploying with preset: {resolution.preset.display_name}",
    )

    # 8. Start the LaunchOrchestrator Step Functions execution
    try:
        step_functions_starter(
            input_data={
                "preset": resolution.values_file,
                "interaction_token": interaction_token,
                "channel_id": channel_id,
            }
        )
    except Exception:
        # Non-fatal for the ack — the user will get a follow-up about failure
        pass

    # 9. Return deferred ack (type 5) within 3 seconds
    deferred = build_deferred_response()
    return {"statusCode": 200, "body": json.dumps(_serialize_response(deferred))}


# ---------------------------------------------------------------------------
# Lambda entry point
# ---------------------------------------------------------------------------


def lambda_handler(event: dict, context) -> dict[str, Any]:
    """AWS Lambda handler for the Launch_Handler.

    Wires up dependencies from environment variables and invokes the
    core handle_interaction logic.

    Args:
        event: API Gateway v2.0 payload.
        context: Lambda context (unused).

    Returns:
        API Gateway-compatible response dict.
    """
    import base64
    import os

    from discord_control_plane.adapters.allowlist_loader import load_allowlist
    from discord_control_plane.adapters.discord_messaging import DiscordMessenger
    from discord_control_plane.adapters.state_store import StateStore

    public_key_hex = os.environ["DISCORD_PUBLIC_KEY"]
    table_name = os.environ.get("STATE_TABLE_NAME", "arma-server-state")
    orchestrator_arn = os.environ.get("ORCHESTRATOR_ARN", "")
    application_id = os.environ.get("DISCORD_APPLICATION_ID", "")

    # Extract signature and timestamp from headers
    headers = event.get("headers", {})
    signature = headers.get("x-signature-ed25519")
    timestamp = headers.get("x-signature-timestamp")

    # Body may be base64-encoded by API Gateway
    body_str = event.get("body", "")
    is_base64 = event.get("isBase64Encoded", False)
    if is_base64:
        body = base64.b64decode(body_str)
    else:
        body = body_str.encode("utf-8") if isinstance(body_str, str) else body_str

    # Build dependencies
    state_store = StateStore(table_name=table_name)
    discord_messenger = DiscordMessenger()

    def step_functions_starter(input_data: dict):
        import boto3
        sfn_client = boto3.client("stepfunctions")
        sfn_client.start_execution(
            stateMachineArn=orchestrator_arn,
            input=json.dumps({
                **input_data,
                "application_id": application_id,
            }),
        )

    return handle_interaction(
        body=body,
        signature=signature,
        timestamp=timestamp,
        public_key_hex=public_key_hex,
        allowlist_loader=load_allowlist,
        state_store=state_store,
        step_functions_starter=step_functions_starter,
        discord_messenger=discord_messenger,
        application_id=application_id,
    )
