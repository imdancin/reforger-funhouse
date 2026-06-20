"""DynamoDB-backed server state store with optimistic concurrency.

The table `arma-server-state` holds a single item (pk="SERVER") that represents
the lifecycle state of the Arma Reforger server. All writes are guarded by a
conditional expression requiring both the expected state AND the expected version
to match, ensuring exactly-once transitions even under concurrent callers.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import boto3
from botocore.exceptions import ClientError

from discord_control_plane.core.models import ServerState, ServerStateRecord


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


class TransitionStatus(str, Enum):
    """Outcome of a conditional state transition attempt."""

    ACQUIRED = "ACQUIRED"
    CONFLICT = "CONFLICT"


@dataclass(frozen=True)
class TransitionResult:
    """Result of a try_transition call.

    On ACQUIRED, `record` is the new state that was persisted.
    On CONFLICT, `record` is the current state that blocked the write.
    """

    status: TransitionStatus
    record: ServerStateRecord


# ---------------------------------------------------------------------------
# Store implementation
# ---------------------------------------------------------------------------

_PK = "SERVER"


class StateStore:
    """DynamoDB-backed server state store with optimistic concurrency."""

    def __init__(self, table_name: str = "arma-server-state", dynamodb_client=None):
        self._table_name = table_name
        self._client = dynamodb_client or boto3.client("dynamodb")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_state(self) -> ServerStateRecord:
        """Read the current server state from DynamoDB.

        If no item exists yet, returns an initial OFFLINE record at version 0.
        """
        response = self._client.get_item(
            TableName=self._table_name,
            Key={"pk": {"S": _PK}},
            ConsistentRead=True,
        )

        item = response.get("Item")
        if item is None:
            return ServerStateRecord(
                state=ServerState.OFFLINE,
                preset="",
                version=0,
            )

        return self._item_to_record(item)

    def try_transition(
        self,
        expected_state: str,
        new_state: str,
        attrs: dict,
        version: int,
    ) -> TransitionResult:
        """Conditional update guarded by (state == expected AND version == version).

        On success the version is incremented and the new record is returned with
        status ACQUIRED. On failure (condition not met), the current record is read
        back and returned with status CONFLICT. The in-memory change is discarded so
        the prior persisted state remains authoritative.
        """
        new_version = version + 1

        # Build the attribute values to persist
        expression_attr_names = {
            "#st": "state",
            "#v": "version",
        }
        expression_attr_values = {
            ":expected_state": {"S": expected_state},
            ":expected_version": {"N": str(version)},
            ":new_state": {"S": new_state},
            ":new_version": {"N": str(new_version)},
        }

        # Build SET clause for the update expression
        set_parts = ["#st = :new_state", "#v = :new_version"]

        # Add optional attributes
        attr_index = 0
        for key, value in attrs.items():
            if value is None:
                continue
            attr_index += 1
            name_placeholder = f"#a{attr_index}"
            value_placeholder = f":a{attr_index}"
            expression_attr_names[name_placeholder] = key
            expression_attr_values[value_placeholder] = {"S": str(value)}
            set_parts.append(f"{name_placeholder} = {value_placeholder}")

        update_expression = "SET " + ", ".join(set_parts)
        condition_expression = "#st = :expected_state AND #v = :expected_version"

        try:
            self._client.update_item(
                TableName=self._table_name,
                Key={"pk": {"S": _PK}},
                UpdateExpression=update_expression,
                ConditionExpression=condition_expression,
                ExpressionAttributeNames=expression_attr_names,
                ExpressionAttributeValues=expression_attr_values,
                ReturnValues="ALL_NEW",
            )

            # Build the record that was just written
            new_record = ServerStateRecord(
                state=ServerState(new_state),
                preset=attrs.get("preset", ""),
                version=new_version,
                public_ip=attrs.get("public_ip"),
                interaction_token=attrs.get("interaction_token"),
                channel_id=attrs.get("channel_id"),
                launch_started_at=attrs.get("launch_started_at"),
                updated_at=attrs.get("updated_at"),
            )
            return TransitionResult(status=TransitionStatus.ACQUIRED, record=new_record)

        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                # Condition failed — read the current record to return as conflict
                current_record = self.get_state()
                return TransitionResult(
                    status=TransitionStatus.CONFLICT, record=current_record
                )
            # Re-raise unexpected errors
            raise

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _item_to_record(item: dict) -> ServerStateRecord:
        """Convert a DynamoDB item dict to a ServerStateRecord."""
        return ServerStateRecord(
            state=ServerState(item["state"]["S"]),
            preset=item.get("preset", {}).get("S", ""),
            version=int(item["version"]["N"]),
            public_ip=item.get("public_ip", {}).get("S"),
            interaction_token=item.get("interaction_token", {}).get("S"),
            channel_id=item.get("channel_id", {}).get("S"),
            launch_started_at=item.get("launch_started_at", {}).get("S"),
            updated_at=item.get("updated_at", {}).get("S"),
        )
