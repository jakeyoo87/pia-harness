from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from .session import (
    MEMORY_MAX_CHARS,
    ActiveSession,
    CompletedTurn,
    ConversationContext,
    MemoryDocument,
    RollingSummary,
    as_utc,
    is_valid_turn_id,
    turn_id_matches_created_at,
)


class SessionStoreError(RuntimeError):
    pass


class TurnConflictError(SessionStoreError):
    pass


class TurnTooLargeError(SessionStoreError):
    pass


class StoreContractError(SessionStoreError):
    pass


class ConversationAbandoned(RuntimeError):
    """The consuming application no longer permits this conversation."""


class ConversationStore(Protocol):
    """Persistence contract required by the conversation runtime.

    Implementations own their database, physical schema, retention policy, and
    application lifecycle checks. Methods are synchronous because the runtime
    moves durable calls to worker threads.
    """

    def get_or_create_active_session(
        self, user_key: str, *, now: datetime | None = None
    ) -> ActiveSession: ...

    def append_completed_turn(
        self,
        *,
        user_key: str,
        session_id: str,
        turn_id: str,
        user_message: str,
        assistant_message: str,
        created_at: datetime,
    ) -> CompletedTurn: ...

    def load_context(
        self,
        *,
        user_key: str,
        session_id: str,
        now: datetime | None = None,
    ) -> ConversationContext: ...

    def get_memory(self, user_key: str) -> MemoryDocument | None: ...

    def replace_memory(
        self,
        memory: MemoryDocument,
        *,
        expected_last_reviewed_turn_id: str | None,
    ) -> bool: ...

    def load_unreviewed_turns(
        self,
        *,
        user_key: str,
        session_id: str,
        after_turn_id: str | None,
        now: datetime | None = None,
    ) -> tuple[CompletedTurn, ...]: ...

    def replace_summary(
        self,
        summary: RollingSummary,
        *,
        expected_through_turn_id: str | None,
    ) -> bool: ...

    def delete_turns_through(
        self, *, user_key: str, session_id: str, through_turn_id: str
    ) -> int: ...


def validate_loaded_turns(
    turns: Sequence[CompletedTurn],
    *,
    user_key: str,
    session_id: str,
    after_turn_id: str | None,
    now: datetime,
) -> tuple[CompletedTurn, ...]:
    """Fail closed when a store violates the ordering/boundary contract."""

    try:
        now_epoch = int(as_utc(now).timestamp())
    except (TypeError, ValueError) as error:
        raise StoreContractError("store validation time is invalid") from error
    validated = tuple(turns)
    previous = after_turn_id
    for turn in validated:
        if not isinstance(turn, CompletedTurn):
            raise StoreContractError("store returned a non-Turn value")
        if turn.user_key != user_key or turn.session_id != session_id:
            raise StoreContractError("store returned a Turn for another owner")
        try:
            valid_identity = is_valid_turn_id(
                turn.turn_id
            ) and turn_id_matches_created_at(turn.turn_id, turn.created_at)
        except (TypeError, ValueError):
            valid_identity = False
        if not valid_identity:
            raise StoreContractError("store returned a Turn with invalid identity")
        if previous is not None and turn.turn_id <= previous:
            raise StoreContractError("store returned Turns out of order or boundary")
        if turn.expires_at <= now_epoch:
            raise StoreContractError("store returned an expired Turn")
        previous = turn.turn_id
    return validated


def validate_active_session(session: ActiveSession, *, user_key: str) -> ActiveSession:
    if not isinstance(session, ActiveSession) or session.user_key != user_key:
        raise StoreContractError("store returned an active Session for another owner")
    if not isinstance(session.session_id, str) or not session.session_id:
        raise StoreContractError("store returned an invalid active Session")
    try:
        as_utc(session.created_at)
    except (TypeError, ValueError) as error:
        raise StoreContractError("store returned an invalid active Session") from error
    return session


def validate_loaded_memory(
    memory: MemoryDocument | None, *, user_key: str
) -> MemoryDocument | None:
    if memory is None:
        return None
    if not isinstance(memory, MemoryDocument) or memory.user_key != user_key:
        raise StoreContractError("store returned Memory for another owner")
    if (
        not isinstance(memory.memory_text, str)
        or len(memory.memory_text) > MEMORY_MAX_CHARS
        or not is_valid_turn_id(memory.last_reviewed_turn_id)
    ):
        raise StoreContractError("store returned invalid Memory")
    try:
        as_utc(memory.updated_at)
    except (TypeError, ValueError) as error:
        raise StoreContractError("store returned invalid Memory") from error
    return memory


def validate_loaded_context(
    context: ConversationContext,
    *,
    user_key: str,
    session_id: str,
    now: datetime,
) -> ConversationContext:
    if not isinstance(context, ConversationContext):
        raise StoreContractError("store returned invalid Conversation Context")
    summary = context.summary
    if summary is not None:
        if (
            not isinstance(summary, RollingSummary)
            or summary.user_key != user_key
            or summary.session_id != session_id
            or not isinstance(summary.summary_text, str)
            or not summary.summary_text
            or not is_valid_turn_id(summary.through_turn_id)
            or summary.summary_tokens <= 0
            or not isinstance(summary.model_id, str)
            or not summary.model_id
        ):
            raise StoreContractError("store returned invalid Summary")
        try:
            as_utc(summary.updated_at)
        except (TypeError, ValueError) as error:
            raise StoreContractError("store returned invalid Summary") from error
    boundary = None if summary is None else summary.through_turn_id
    turns = validate_loaded_turns(
        context.turns,
        user_key=user_key,
        session_id=session_id,
        after_turn_id=boundary,
        now=now,
    )
    return ConversationContext(summary, turns)
