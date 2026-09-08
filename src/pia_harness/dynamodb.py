from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from botocore.exceptions import ClientError

from .session import (
    ActiveSession,
    CompletedTurn,
    ConversationContext,
    MEMORY_MAX_CHARS,
    MemoryDocument,
    RollingSummary,
    as_utc,
    utc_text,
)


_ACTIVE_SESSION_SK = "ACTIVE_SESSION"
_MEMORY_SK = "MEMORY"
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
        retention_days: int = 30,
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

    def load_context(
        self,
        *,
        user_key: str,
        session_id: str,
        now: datetime | None = None,
    ) -> ConversationContext:
        user_key = _user_key(user_key)
        session_id = _required("session_id", session_id)
        now_epoch = int(as_utc(now or datetime.now(UTC)).timestamp())
        summary = self.get_summary(user_key=user_key, session_id=session_id)
        items = self._query_turn_items(user_key, session_id)
        turns = tuple(
            _turn_from_item(item)
            for item in items
            if int(item["expires_at"]["N"]) > now_epoch
            and (
                summary is None
                or item["turn_id"]["S"] > summary.through_turn_id
            )
        )
        return ConversationContext(summary=summary, turns=turns)

    def get_summary(
        self, *, user_key: str, session_id: str
    ) -> RollingSummary | None:
        user_key = _user_key(user_key)
        session_id = _required("session_id", session_id)
        response = self._client.get_item(
            TableName=self._table_name,
            Key={
                "pk": {"S": _pk(user_key)},
                "sk": {"S": _summary_sk(session_id)},
            },
            ConsistentRead=True,
        )
        item = response.get("Item")
        return None if item is None else _summary_from_item(item)

    def get_memory(self, user_key: str) -> MemoryDocument | None:
        user_key = _user_key(user_key)
        response = self._client.get_item(
            TableName=self._table_name,
            Key={"pk": {"S": _pk(user_key)}, "sk": {"S": _MEMORY_SK}},
            ConsistentRead=True,
        )
        item = response.get("Item")
        return None if item is None else _memory_from_item(item)

    def replace_memory(
        self,
        memory: MemoryDocument,
        *,
        expected_last_reviewed_turn_id: str | None,
    ) -> bool:
        _user_key(memory.user_key)
        if not isinstance(memory.memory_text, str):
            raise ValueError("memory_text must be a string")
        if len(memory.memory_text) > MEMORY_MAX_CHARS:
            raise ValueError("memory_text exceeds the character limit")
        if not _TURN_ID.fullmatch(memory.last_reviewed_turn_id):
            raise ValueError("last_reviewed_turn_id must be a turn ID")

        condition = "attribute_not_exists(pk) AND attribute_not_exists(sk)"
        values: dict[str, dict[str, str]] | None = None
        if expected_last_reviewed_turn_id is not None:
            if not _TURN_ID.fullmatch(expected_last_reviewed_turn_id):
                raise ValueError("expected_last_reviewed_turn_id must be a turn ID")
            condition = "last_reviewed_turn_id = :expected"
            values = {":expected": {"S": expected_last_reviewed_turn_id}}

        request: dict[str, Any] = {
            "TableName": self._table_name,
            "Item": _memory_item(memory),
            "ConditionExpression": condition,
        }
        if values is not None:
            request["ExpressionAttributeValues"] = values
        try:
            self._client.put_item(**request)
            return True
        except ClientError as error:
            if _is_conditional_failure(error):
                return False
            raise

    def delete_memory(self, user_key: str) -> None:
        user_key = _user_key(user_key)
        self._client.delete_item(
            TableName=self._table_name,
            Key={"pk": {"S": _pk(user_key)}, "sk": {"S": _MEMORY_SK}},
        )

    def load_unreviewed_turns(
        self,
        *,
        user_key: str,
        session_id: str,
        after_turn_id: str | None,
        now: datetime | None = None,
    ) -> tuple[CompletedTurn, ...]:
        user_key = _user_key(user_key)
        session_id = _required("session_id", session_id)
        if after_turn_id is not None and not _TURN_ID.fullmatch(after_turn_id):
            raise ValueError("after_turn_id must be a turn ID")
        now_epoch = int(as_utc(now or datetime.now(UTC)).timestamp())
        return tuple(
            _turn_from_item(item)
            for item in self._query_turn_items(user_key, session_id)
            if int(item["expires_at"]["N"]) > now_epoch
            and (after_turn_id is None or item["turn_id"]["S"] > after_turn_id)
        )

    def replace_summary(
        self,
        summary: RollingSummary,
        *,
        expected_through_turn_id: str | None,
    ) -> bool:
        _user_key(summary.user_key)
        _required("session_id", summary.session_id)
        _required("summary_text", summary.summary_text)
        _required("model_id", summary.model_id)
        if not _TURN_ID.fullmatch(summary.through_turn_id):
            raise ValueError("through_turn_id must be created by new_turn_id")
        if summary.summary_tokens <= 0:
            raise ValueError("summary_tokens must be positive")

        condition = "attribute_not_exists(pk) AND attribute_not_exists(sk)"
        values: dict[str, dict[str, str]] | None = None
        if expected_through_turn_id is not None:
            if not _TURN_ID.fullmatch(expected_through_turn_id):
                raise ValueError("expected_through_turn_id must be a turn ID")
            condition = "through_turn_id = :expected"
            values = {":expected": {"S": expected_through_turn_id}}

        request: dict[str, Any] = {
            "TableName": self._table_name,
            "Item": _summary_item(summary),
            "ConditionExpression": condition,
        }
        if values is not None:
            request["ExpressionAttributeValues"] = values
        try:
            self._client.put_item(**request)
            return True
        except ClientError as error:
            if _is_conditional_failure(error):
                return False
            raise

    def delete_turns_through(
        self, *, user_key: str, session_id: str, through_turn_id: str
    ) -> int:
        user_key = _user_key(user_key)
        session_id = _required("session_id", session_id)
        if not _TURN_ID.fullmatch(through_turn_id):
            raise ValueError("through_turn_id must be a turn ID")
        items = self._query_turn_items(user_key, session_id)
        keys = [
            {"pk": item["pk"], "sk": item["sk"]}
            for item in items
            if item["turn_id"]["S"] <= through_turn_id
        ]
        for key in keys:
            self._client.delete_item(TableName=self._table_name, Key=key)
        return len(keys)

    def _query_turn_items(
        self, user_key: str, session_id: str
    ) -> list[dict[str, dict[str, str]]]:
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
        return items

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
            self._client.delete_item(
                TableName=self._table_name,
                Key={
                    "pk": {"S": _pk(user_key)},
                    "sk": {"S": _summary_sk(expected_session_id)},
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


def _summary_sk(session_id: str) -> str:
    return f"SUMMARY#{session_id}"


def _turn_item(turn: CompletedTurn) -> dict[str, dict[str, str]]:
    return {
        "pk": {"S": _pk(turn.user_key)},
        "sk": {"S": _turn_sk(turn.session_id, turn.turn_id)},
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


def _summary_item(summary: RollingSummary) -> dict[str, dict[str, str]]:
    return {
        "pk": {"S": _pk(summary.user_key)},
        "sk": {"S": _summary_sk(summary.session_id)},
        "session_id": {"S": summary.session_id},
        "summary_text": {"S": summary.summary_text},
        "through_turn_id": {"S": summary.through_turn_id},
        "summary_tokens": {"N": str(summary.summary_tokens)},
        "model_id": {"S": summary.model_id},
        "updated_at": {"S": utc_text(summary.updated_at)},
    }


def _summary_from_item(item: dict[str, dict[str, str]]) -> RollingSummary:
    return RollingSummary(
        user_key=item["pk"]["S"].removeprefix("USER#"),
        session_id=item["session_id"]["S"],
        summary_text=item["summary_text"]["S"],
        through_turn_id=item["through_turn_id"]["S"],
        summary_tokens=int(item["summary_tokens"]["N"]),
        model_id=item["model_id"]["S"],
        updated_at=datetime.strptime(
            item["updated_at"]["S"], "%Y-%m-%dT%H:%M:%S.%fZ"
        ).replace(tzinfo=UTC),
    )


def _memory_item(memory: MemoryDocument) -> dict[str, dict[str, str]]:
    return {
        "pk": {"S": _pk(memory.user_key)},
        "sk": {"S": _MEMORY_SK},
        "memory_text": {"S": memory.memory_text},
        "last_reviewed_turn_id": {"S": memory.last_reviewed_turn_id},
        "updated_at": {"S": utc_text(memory.updated_at)},
    }


def _memory_from_item(item: dict[str, dict[str, str]]) -> MemoryDocument:
    return MemoryDocument(
        user_key=item["pk"]["S"].removeprefix("USER#"),
        memory_text=item["memory_text"]["S"],
        last_reviewed_turn_id=item["last_reviewed_turn_id"]["S"],
        updated_at=datetime.strptime(
            item["updated_at"]["S"], "%Y-%m-%dT%H:%M:%S.%fZ"
        ).replace(tzinfo=UTC),
    )


def _user_key(value: str) -> str:
    return _required("user_key", value)


def _required(name: str, value: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} is required")
    return value


def _is_conditional_failure(error: ClientError) -> bool:
    return error.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException"
