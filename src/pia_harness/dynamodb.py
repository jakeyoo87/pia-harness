from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from botocore.exceptions import ClientError

from .session import ActiveSession, CompletedTurn, as_utc, utc_text


_ACTIVE_SESSION_SK = "ACTIVE_SESSION"
_TURN_ID = re.compile(r"^[0-9]{8}T[0-9]{12}Z_[0-9a-f]{32}$")


class SessionStoreError(RuntimeError):
    pass


class SessionConflictError(SessionStoreError):
    pass


class TurnConflictError(SessionStoreError):
    pass


class TurnTooLargeError(SessionStoreError):
    pass


class DynamoDBConversationStore:
    def __init__(
        self,
        client: Any,
        table_name: str,
        *,
        retention_days: int = 14,
        max_turn_bytes: int = 256 * 1024,
    ) -> None:
        if not table_name:
            raise ValueError("table_name is required")
        if retention_days <= 0:
            raise ValueError("retention_days must be positive")
        if max_turn_bytes <= 0:
            raise ValueError("max_turn_bytes must be positive")
        self._client = client
        self._table_name = table_name
        self._retention_days = retention_days
        self._max_turn_bytes = max_turn_bytes

    def get_or_create_active_session(
        self, user_key: str, *, now: datetime | None = None
    ) -> ActiveSession:
        user_key = _user_key(user_key)
        existing = self._get_active_session(user_key)
        if existing is not None:
            return existing

        created_at = as_utc(now or datetime.now(UTC))
        session = ActiveSession(user_key, uuid4().hex, created_at)
        item = {
            "pk": {"S": _pk(user_key)},
            "sk": {"S": _ACTIVE_SESSION_SK},
            "entity": {"S": "active_session"},
            "session_id": {"S": session.session_id},
            "created_at": {"S": utc_text(session.created_at)},
        }
        try:
            self._client.put_item(
                TableName=self._table_name,
                Item=item,
                ConditionExpression="attribute_not_exists(pk) AND attribute_not_exists(sk)",
            )
            return session
        except ClientError as error:
            if not _is_conditional_failure(error):
                raise
            winner = self._get_active_session(user_key)
            if winner is None:
                raise SessionStoreError("active session create lost without a winner") from error
            return winner

    def append_completed_turn(
        self,
        *,
        user_key: str,
        session_id: str,
        turn_id: str,
        user_message: str,
        assistant_message: str,
        created_at: datetime,
    ) -> CompletedTurn:
        user_key = _user_key(user_key)
        session_id = _required("session_id", session_id)
        if not _TURN_ID.fullmatch(turn_id):
            raise ValueError("turn_id must be created by new_turn_id")
        user_message = _required("user_message", user_message)
        assistant_message = _required("assistant_message", assistant_message)
        content_bytes = len(user_message.encode("utf-8")) + len(
            assistant_message.encode("utf-8")
        )
        if content_bytes > self._max_turn_bytes:
            raise TurnTooLargeError("turn content exceeds the configured byte limit")

        created_at = as_utc(created_at)
        expected_prefix = created_at.strftime("%Y%m%dT%H%M%S%fZ_")
        if not turn_id.startswith(expected_prefix):
            raise ValueError("turn_id timestamp must match created_at")
        expires_at = int(
            (created_at + timedelta(days=self._retention_days)).timestamp()
        )
        turn = CompletedTurn(
            user_key,
            session_id,
            turn_id,
            user_message,
            assistant_message,
            created_at,
            expires_at,
        )
        item = _turn_item(turn)
        try:
            self._client.put_item(
                TableName=self._table_name,
                Item=item,
                ConditionExpression="attribute_not_exists(pk) AND attribute_not_exists(sk)",
            )
            return turn
        except ClientError as error:
            if not _is_conditional_failure(error):
                raise
            response = self._client.get_item(
                TableName=self._table_name,
                Key={"pk": item["pk"], "sk": item["sk"]},
                ConsistentRead=True,
            )
            if response.get("Item") == item:
                return turn
            raise TurnConflictError("turn_id already exists with different content") from error

    def list_turns(
        self,
        *,
        user_key: str,
        session_id: str,
        now: datetime | None = None,
    ) -> list[CompletedTurn]:
        user_key = _user_key(user_key)
        session_id = _required("session_id", session_id)
        now_epoch = int(as_utc(now or datetime.now(UTC)).timestamp())
        request: dict[str, Any] = {
            "TableName": self._table_name,
            "KeyConditionExpression": "pk = :pk AND begins_with(sk, :prefix)",
            "ExpressionAttributeValues": {
                ":pk": {"S": _pk(user_key)},
                ":prefix": {"S": _turn_prefix(session_id)},
            },
            "ConsistentRead": True,
        }
        items: list[dict[str, dict[str, str]]] = []
        while True:
            response = self._client.query(**request)
            items.extend(response.get("Items", []))
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                break
            request["ExclusiveStartKey"] = last_key

        return [
            _turn_from_item(item)
            for item in items
            if int(item["expires_at"]["N"]) > now_epoch
        ]

    def reset_active_session(
        self,
        *,
        user_key: str,
        expected_session_id: str,
        now: datetime | None = None,
    ) -> ActiveSession:
        user_key = _user_key(user_key)
        expected_session_id = _required("expected_session_id", expected_session_id)
        created_at = as_utc(now or datetime.now(UTC))
        session = ActiveSession(user_key, uuid4().hex, created_at)
        try:
            self._client.update_item(
                TableName=self._table_name,
                Key={
                    "pk": {"S": _pk(user_key)},
                    "sk": {"S": _ACTIVE_SESSION_SK},
                },
                UpdateExpression="SET session_id = :new_id, created_at = :created_at",
                ConditionExpression="session_id = :expected_id",
                ExpressionAttributeValues={
                    ":new_id": {"S": session.session_id},
                    ":created_at": {"S": utc_text(created_at)},
                    ":expected_id": {"S": expected_session_id},
                },
            )
            return session
        except ClientError as error:
            if _is_conditional_failure(error):
                raise SessionConflictError("active session changed before reset") from error
            raise

    def delete_all_for_user(self, user_key: str) -> int:
        user_key = _user_key(user_key)
        keys: list[dict[str, dict[str, str]]] = []
        request: dict[str, Any] = {
            "TableName": self._table_name,
            "KeyConditionExpression": "pk = :pk",
            "ExpressionAttributeValues": {":pk": {"S": _pk(user_key)}},
            "ProjectionExpression": "pk, sk",
            "ConsistentRead": True,
        }
        while True:
            response = self._client.query(**request)
            keys.extend(
                {"pk": item["pk"], "sk": item["sk"]}
                for item in response.get("Items", [])
            )
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                break
            request["ExclusiveStartKey"] = last_key

        for key in keys:
            self._client.delete_item(TableName=self._table_name, Key=key)
        return len(keys)

    def _get_active_session(self, user_key: str) -> ActiveSession | None:
        response = self._client.get_item(
            TableName=self._table_name,
            Key={"pk": {"S": _pk(user_key)}, "sk": {"S": _ACTIVE_SESSION_SK}},
            ConsistentRead=True,
        )
        item = response.get("Item")
        if item is None:
            return None
        return ActiveSession(
            user_key=user_key,
            session_id=item["session_id"]["S"],
            created_at=datetime.strptime(
                item["created_at"]["S"], "%Y-%m-%dT%H:%M:%S.%fZ"
            ).replace(tzinfo=UTC),
        )


def _pk(user_key: str) -> str:
    return f"USER#{user_key}"


def _turn_prefix(session_id: str) -> str:
    return f"TURN#{session_id}#"


def _turn_sk(session_id: str, turn_id: str) -> str:
    return f"{_turn_prefix(session_id)}{turn_id}"


def _turn_item(turn: CompletedTurn) -> dict[str, dict[str, str]]:
    return {
        "pk": {"S": _pk(turn.user_key)},
        "sk": {"S": _turn_sk(turn.session_id, turn.turn_id)},
        "entity": {"S": "turn"},
        "session_id": {"S": turn.session_id},
        "turn_id": {"S": turn.turn_id},
        "user_message": {"S": turn.user_message},
        "assistant_message": {"S": turn.assistant_message},
        "created_at": {"S": utc_text(turn.created_at)},
        "expires_at": {"N": str(turn.expires_at)},
    }


def _turn_from_item(item: dict[str, dict[str, str]]) -> CompletedTurn:
    return CompletedTurn(
        user_key=item["pk"]["S"].removeprefix("USER#"),
        session_id=item["session_id"]["S"],
        turn_id=item["turn_id"]["S"],
        user_message=item["user_message"]["S"],
        assistant_message=item["assistant_message"]["S"],
        created_at=datetime.strptime(
            item["created_at"]["S"], "%Y-%m-%dT%H:%M:%S.%fZ"
        ).replace(tzinfo=UTC),
        expires_at=int(item["expires_at"]["N"]),
    )


def _user_key(value: str) -> str:
    return _required("user_key", value)


def _required(name: str, value: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} is required")
    return value


def _is_conditional_failure(error: ClientError) -> bool:
    return error.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException"
