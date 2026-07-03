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
import os
from typing import Any

import boto3

from discord_control_plane.adapters.allowlist_loader import load_allowlist
from discord_control_plane.adapters import discord_messaging
from discord_control_plane.adapters.state_store import StateStore
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
    build_denial_response,
    build_error_response,
    build_launch_started_response,
    build_pong_response,
)
from discord_control_plane.core.verification import verify_signature
from discord_control_plane.handlers.status_handler import handle_status
from discord_control_plane.handlers.stop_handler import handle_stop


_JSON_HEADERS = {"Content-Type": "application/json"}

# ---------------------------------------------------------------------------
# AWS clients created at module INIT time.
#
# With SnapStart, module-level initialization runs once and is captured in the
# execution-environment snapshot. Creating the boto3 clients here (rather than
# per-invocation) means botocore's service models are already loaded in the
# snapshot, so restored invocations skip that CPU-heavy work. This is the
# difference that keeps the /launch acknowledgement inside Discord's 3-second
# deadline on a cold restore.
#
# region_name is provided explicitly so the module can be imported locally
# (and under test) without AWS_REGION set. Client creation does not require
# credentials — those are resolved lazily at call time.
# ---------------------------------------------------------------------------

_AWS_REGION = os.environ.get("AWS_REGION", "us-west-2")


def _init_client(service_name: str):
    """Create a boto3 client at INIT for SnapStart priming.

    On Lambda the execution role provides credentials during INIT, so this
    succeeds and the built client (with its loaded service model) is captured
    in the SnapStart snapshot. In local/test environments where credentials
    can't be resolved, this returns None and callers fall back to lazy
    creation via _client().
    """
    try:
        return boto3.client(service_name, region_name=_AWS_REGION)
    except Exception:
        return None


def _client(service_name: str, primed):
    """Return the INIT-primed client, or lazily create one if priming failed."""
    return primed if primed is not None else boto3.client(service_name, region_name=_AWS_REGION)


_SSM_CLIENT = _init_client("ssm")
_DDB_CLIENT = _init_client("dynamodb")
_SFN_CLIENT = _init_client("stepfunctions")


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
        return {"statusCode": 200, "headers": _JSON_HEADERS, "body": json.dumps(_serialize_response(pong))}

    # 3. Load allowlist
    try:
        allowlist = allowlist_loader()
    except Exception:
        error_resp = build_error_response(
            "Configuration error: unable to load authorization data."
        )
        return {"statusCode": 200, "headers": _JSON_HEADERS, "body": json.dumps(_serialize_response(error_resp))}

    # 4. Authorize
    member = interaction.get("member", {})
    user_id = member.get("user", {}).get("id", "")
    role_ids = member.get("roles", [])

    if not is_authorized(user_id, role_ids, allowlist):
        denial = build_denial_response()
        return {"statusCode": 200, "headers": _JSON_HEADERS, "body": json.dumps(_serialize_response(denial))}

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
        return {"statusCode": 200, "headers": _JSON_HEADERS, "body": json.dumps(_serialize_response(error_resp))}

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
        return {"statusCode": 200, "headers": _JSON_HEADERS, "body": json.dumps(_serialize_response(error_resp))}

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
        return {"statusCode": 200, "headers": _JSON_HEADERS, "body": json.dumps(_serialize_response(error_resp))}

    # 7. Start the LaunchOrchestrator Step Functions execution.
    #    The orchestrator provisions the server and posts the connection
    #    details as a follow-up once it is RUNNING.
    try:
        step_functions_starter(
            input_data={
                "preset": resolution.values_file,
                "interaction_token": interaction_token,
                "channel_id": channel_id,
            }
        )
    except Exception as e:
        print(f"[ERROR] Failed to start Step Functions: {e}")
        # Roll the lock back to OFFLINE so the failed launch can be retried
        # rather than leaving the server stuck in LAUNCHING.
        try:
            state_store.try_transition(
                expected_state=ServerState.LAUNCHING.value,
                new_state=ServerState.OFFLINE.value,
                attrs={"preset": "", "interaction_token": None, "channel_id": None},
                version=transition_result.record.version,
            )
        except Exception as rollback_err:
            print(f"[ERROR] Failed to roll back LAUNCHING lock: {rollback_err}")
        error_resp = build_error_response(
            "Failed to start the launch. Please try again in a moment."
        )
        return {"statusCode": 200, "headers": _JSON_HEADERS, "body": json.dumps(_serialize_response(error_resp))}

    # 8. Acknowledge immediately (type 4) with an informative message, well
    #    inside Discord's 3-second deadline.
    started = build_launch_started_response()
    return {"statusCode": 200, "headers": _JSON_HEADERS, "body": json.dumps(_serialize_response(started))}


# ---------------------------------------------------------------------------
# Lambda entry point
# ---------------------------------------------------------------------------


def lambda_handler(event: dict, context) -> dict[str, Any]:
    """AWS Lambda handler for the Launch_Handler.

    All heavy imports (boto3, etc.) happen at module level so they occur
    during Lambda INIT — which is NOT counted against Discord's 3-second
    interaction response deadline.

    Args:
        event: API Gateway v2.0 payload.
        context: Lambda context (unused).

    Returns:
        API Gateway-compatible response dict.
    """
    import base64
    import os

    public_key_hex = os.environ["DISCORD_PUBLIC_KEY"]

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

    # Verify signature
    if not signature or not timestamp:
        return {"statusCode": 401, "body": "Invalid signature"}

    if not verify_signature(public_key_hex, signature, timestamp, body):
        return {"statusCode": 401, "body": "Invalid signature"}

    try:
        interaction = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {"statusCode": 401, "body": "Invalid body"}

    # PING → PONG
    if interaction.get("type") == 1:
        pong = build_pong_response()
        return {"statusCode": 200, "headers": _JSON_HEADERS, "body": json.dumps(_serialize_response(pong))}

    # --- Command routing ---
    table_name = os.environ.get("STATE_TABLE_NAME", "arma-server-state")
    orchestrator_arn = os.environ.get("ORCHESTRATOR_ARN", "")
    application_id = os.environ.get("DISCORD_APPLICATION_ID", "")

    # Resolve the INIT-primed clients so restored invocations skip botocore
    # service-model loading (the main cost that pushed /launch past 3s).
    ssm_client = _client("ssm", _SSM_CLIENT)
    ddb_client = _client("dynamodb", _DDB_CLIENT)
    sfn_client = _client("stepfunctions", _SFN_CLIENT)

    state_store = StateStore(table_name=table_name, dynamodb_client=ddb_client)
    command_name = interaction.get("data", {}).get("name", "")

    # /status — read-only, no auth needed
    if command_name == "status":
        return handle_status(state_store=state_store)

    # /stop — authorized teardown
    if command_name == "stop":
        allowlist = load_allowlist(ssm_client=ssm_client)
        return handle_stop(
            interaction=interaction,
            allowlist=allowlist,
            state_store=state_store,
        )

    # /launch — full auth + orchestration flow
    class _MessengerAdapter:
        def post_followup(self, app_id, token, content):
            discord_messaging.post_followup(app_id, token, content)

    discord_messenger = _MessengerAdapter()

    def step_functions_starter(input_data: dict):
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
        allowlist_loader=lambda: load_allowlist(ssm_client=ssm_client),
        state_store=state_store,
        step_functions_starter=step_functions_starter,
        discord_messenger=discord_messenger,
        application_id=application_id,
    )
