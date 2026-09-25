from __future__ import annotations

from datetime import UTC, datetime, timedelta
from threading import RLock
from typing import Any
from uuid import uuid4

from .persistence import SessionConflictError, TurnConflictError, TurnTooLargeError
from .session import (
    MEMORY_MAX_CHARS,
    ActiveSession,
    CompletedTurn,
    ConversationContext,
    MemoryDocument,
    RollingSummary,
    as_utc,
    is_valid_turn_id,
    new_turn_id,
    turn_id_matches_created_at,
)


class InMemoryConversationStore:
    """Test-only reference implementation of the ConversationStore contract."""

    def __init__(
        self, *, retention_days: int = 30, max_turn_bytes: int = 256 * 1024
    ) -> None:
        if retention_days <= 0:
            raise ValueError("retention_days must be positive")
        if max_turn_bytes <= 0:
            raise ValueError("max_turn_bytes must be positive")
        self._retention_days = retention_days
        self._max_turn_bytes = max_turn_bytes
        self._sessions: dict[str, ActiveSession] = {}
        self._turns: dict[tuple[str, str, str], CompletedTurn] = {}
        self._summaries: dict[tuple[str, str], RollingSummary] = {}
        self._memories: dict[str, MemoryDocument] = {}
        self._lock = RLock()

    def get_or_create_active_session(
        self, user_key: str, *, now: datetime | None = None
    ) -> ActiveSession:
        user_key = _required("user_key", user_key)
        created_at = as_utc(now or datetime.now(UTC))
        with self._lock:
            current = self._sessions.get(user_key)
            if current is not None:
                return current
            session = ActiveSession(user_key, uuid4().hex, created_at)
            self._sessions[user_key] = session
            return session

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
        user_key = _required("user_key", user_key)
        session_id = _required("session_id", session_id)
        user_message = _required("user_message", user_message)
        assistant_message = _required("assistant_message", assistant_message)
        created_at = as_utc(created_at)
        if not is_valid_turn_id(turn_id) or not turn_id_matches_created_at(
            turn_id, created_at
        ):
            raise ValueError("turn_id must match created_at")
        if (
            len(user_message.encode()) + len(assistant_message.encode())
            > self._max_turn_bytes
        ):
            raise TurnTooLargeError("turn content exceeds the configured byte limit")
        turn = CompletedTurn(
            user_key,
            session_id,
            turn_id,
            user_message,
            assistant_message,
            created_at,
            int((created_at + timedelta(days=self._retention_days)).timestamp()),
        )
        key = (user_key, session_id, turn_id)
        with self._lock:
            current = self._turns.get(key)
            if current is None:
                self._turns[key] = turn
                return turn
            if current == turn:
                return current
            raise TurnConflictError("turn_id already exists with different content")

    def load_context(
        self,
        *,
        user_key: str,
        session_id: str,
        now: datetime | None = None,
    ) -> ConversationContext:
        user_key = _required("user_key", user_key)
        session_id = _required("session_id", session_id)
        now_epoch = int(as_utc(now or datetime.now(UTC)).timestamp())
        with self._lock:
            summary = self._summaries.get((user_key, session_id))
            turns = tuple(
                turn
                for turn in self._ordered_turns(user_key, session_id)
                if turn.expires_at > now_epoch
                and (summary is None or turn.turn_id > summary.through_turn_id)
            )
            return ConversationContext(summary, turns)

    def get_summary(self, *, user_key: str, session_id: str) -> RollingSummary | None:
        with self._lock:
            return self._summaries.get(
                (_required("user_key", user_key), _required("session_id", session_id))
            )

    def get_memory(self, user_key: str) -> MemoryDocument | None:
        with self._lock:
            return self._memories.get(_required("user_key", user_key))

    def replace_memory(
        self,
        memory: MemoryDocument,
        *,
        expected_last_reviewed_turn_id: str | None,
    ) -> bool:
        _required("user_key", memory.user_key)
        if (
            not isinstance(memory.memory_text, str)
            or len(memory.memory_text) > MEMORY_MAX_CHARS
        ):
            raise ValueError("memory_text is invalid")
        if not is_valid_turn_id(memory.last_reviewed_turn_id):
            raise ValueError("last_reviewed_turn_id must be a turn ID")
        with self._lock:
            current = self._memories.get(memory.user_key)
            current_boundary = (
                None if current is None else current.last_reviewed_turn_id
            )
            if current_boundary != expected_last_reviewed_turn_id:
                return False
            self._memories[memory.user_key] = memory
            return True

    def delete_memory(self, user_key: str) -> None:
        with self._lock:
            self._memories.pop(_required("user_key", user_key), None)

    def load_unreviewed_turns(
        self,
        *,
        user_key: str,
        session_id: str,
        after_turn_id: str | None,
        now: datetime | None = None,
    ) -> tuple[CompletedTurn, ...]:
        user_key = _required("user_key", user_key)
        session_id = _required("session_id", session_id)
        if after_turn_id is not None and not is_valid_turn_id(after_turn_id):
            raise ValueError("after_turn_id must be a turn ID")
        now_epoch = int(as_utc(now or datetime.now(UTC)).timestamp())
        with self._lock:
            return tuple(
                turn
                for turn in self._ordered_turns(user_key, session_id)
                if turn.expires_at > now_epoch
                and (after_turn_id is None or turn.turn_id > after_turn_id)
            )

    def replace_summary(
        self,
        summary: RollingSummary,
        *,
        expected_through_turn_id: str | None,
    ) -> bool:
        _required("user_key", summary.user_key)
        _required("session_id", summary.session_id)
        _required("summary_text", summary.summary_text)
        if not is_valid_turn_id(summary.through_turn_id):
            raise ValueError("through_turn_id must be a turn ID")
        if summary.summary_tokens <= 0:
            raise ValueError("summary_tokens must be positive")
        key = (summary.user_key, summary.session_id)
        with self._lock:
            current = self._summaries.get(key)
            current_boundary = None if current is None else current.through_turn_id
            if current_boundary != expected_through_turn_id:
                return False
            self._summaries[key] = summary
            return True

    def delete_turns_through(
        self, *, user_key: str, session_id: str, through_turn_id: str
    ) -> int:
        user_key = _required("user_key", user_key)
        session_id = _required("session_id", session_id)
        if not is_valid_turn_id(through_turn_id):
            raise ValueError("through_turn_id must be a turn ID")
        with self._lock:
            keys = [
                key
                for key in self._turns
                if key[0] == user_key
                and key[1] == session_id
                and key[2] <= through_turn_id
            ]
            for key in keys:
                del self._turns[key]
            return len(keys)

    def reset_active_session(
        self,
        *,
        user_key: str,
        expected_session_id: str,
        now: datetime | None = None,
    ) -> ActiveSession:
        user_key = _required("user_key", user_key)
        expected_session_id = _required("expected_session_id", expected_session_id)
        created_at = as_utc(now or datetime.now(UTC))
        with self._lock:
            current = self._sessions.get(user_key)
            if current is None or current.session_id != expected_session_id:
                raise SessionConflictError("active session changed before reset")
            replacement = ActiveSession(user_key, uuid4().hex, created_at)
            self._sessions[user_key] = replacement
            self._summaries.pop((user_key, expected_session_id), None)
            self._memories.pop(user_key, None)
            return replacement

    def delete_all_for_user(self, user_key: str) -> int:
        user_key = _required("user_key", user_key)
        with self._lock:
            keys = [key for key in self._turns if key[0] == user_key]
            count = len(keys)
            for key in keys:
                del self._turns[key]
            summaries = [key for key in self._summaries if key[0] == user_key]
            count += len(summaries)
            for key in summaries:
                del self._summaries[key]
            if self._sessions.pop(user_key, None) is not None:
                count += 1
            if self._memories.pop(user_key, None) is not None:
                count += 1
            return count

    def _ordered_turns(
        self, user_key: str, session_id: str
    ) -> tuple[CompletedTurn, ...]:
        return tuple(
            sorted(
                (
                    turn
                    for (owner, session, _), turn in self._turns.items()
                    if owner == user_key and session == session_id
                ),
                key=lambda turn: turn.turn_id,
            )
        )


class ConversationStoreContract:
    """Reusable unittest mixin for application ConversationStore adapters."""

    store: Any
    now: datetime

    def make_store(self) -> Any:
        raise NotImplementedError

    def setUp(self) -> None:
        self.store = self.make_store()
        self.now = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)

    def append(
        self,
        user_key: str,
        session_id: str,
        *,
        created_at: datetime | None = None,
        user_message: str = "question",
        assistant_message: str = "answer",
    ) -> CompletedTurn:
        created_at = created_at or self.now
        return self.store.append_completed_turn(
            user_key=user_key,
            session_id=session_id,
            turn_id=new_turn_id(created_at),
            user_message=user_message,
            assistant_message=assistant_message,
            created_at=created_at,
        )

    def test_contract_user_isolation_and_ordering(self) -> None:
        first = self.store.get_or_create_active_session("first", now=self.now)
        second = self.store.get_or_create_active_session("second", now=self.now)
        later = self.append(
            "first", first.session_id, created_at=self.now + timedelta(seconds=1)
        )
        earlier = self.append("first", first.session_id)
        self.assertEqual(
            [earlier.turn_id, later.turn_id],
            [
                turn.turn_id
                for turn in self.store.load_context(
                    user_key="first", session_id=first.session_id, now=self.now
                ).turns
            ],
        )
        self.assertEqual(
            (),
            self.store.load_context(
                user_key="second", session_id=second.session_id, now=self.now
            ).turns,
        )

    def test_contract_turn_replay_conflict_and_expiry(self) -> None:
        session = self.store.get_or_create_active_session("turns", now=self.now)
        turn = self.append("turns", session.session_id)
        replay = self.store.append_completed_turn(
            user_key=turn.user_key,
            session_id=turn.session_id,
            turn_id=turn.turn_id,
            user_message=turn.user_message,
            assistant_message=turn.assistant_message,
            created_at=turn.created_at,
        )
        self.assertEqual(turn, replay)
        with self.assertRaises(TurnConflictError):
            self.store.append_completed_turn(
                user_key=turn.user_key,
                session_id=turn.session_id,
                turn_id=turn.turn_id,
                user_message="different",
                assistant_message=turn.assistant_message,
                created_at=turn.created_at,
            )
        expiry = datetime.fromtimestamp(turn.expires_at, UTC)
        self.assertEqual(
            (),
            self.store.load_context(
                user_key="turns", session_id=session.session_id, now=expiry
            ).turns,
        )

    def test_contract_boundaries_cas_and_reset(self) -> None:
        session = self.store.get_or_create_active_session("boundaries", now=self.now)
        first = self.append("boundaries", session.session_id)
        second = self.append(
            "boundaries",
            session.session_id,
            created_at=self.now + timedelta(seconds=1),
        )
        memory = MemoryDocument("boundaries", "memory", first.turn_id, self.now)
        self.assertTrue(
            self.store.replace_memory(memory, expected_last_reviewed_turn_id=None)
        )
        self.assertFalse(
            self.store.replace_memory(
                MemoryDocument("boundaries", "loser", second.turn_id, self.now),
                expected_last_reviewed_turn_id=None,
            )
        )
        self.assertEqual(
            (second,),
            self.store.load_unreviewed_turns(
                user_key="boundaries",
                session_id=session.session_id,
                after_turn_id=first.turn_id,
                now=self.now,
            ),
        )
        summary = RollingSummary(
            "boundaries",
            session.session_id,
            "summary",
            first.turn_id,
            1,
            "model",
            self.now,
        )
        self.assertTrue(
            self.store.replace_summary(summary, expected_through_turn_id=None)
        )
        self.assertFalse(
            self.store.replace_summary(
                RollingSummary(
                    "boundaries",
                    session.session_id,
                    "loser",
                    second.turn_id,
                    1,
                    "model",
                    self.now,
                ),
                expected_through_turn_id=None,
            )
        )
        self.assertEqual(
            (second,),
            self.store.load_context(
                user_key="boundaries", session_id=session.session_id, now=self.now
            ).turns,
        )
        # Memory Review must still see Turns the Summary already covers.
        self.assertEqual(
            (first, second),
            self.store.load_unreviewed_turns(
                user_key="boundaries",
                session_id=session.session_id,
                after_turn_id=None,
                now=self.now,
            ),
        )
        replacement = self.store.reset_active_session(
            user_key="boundaries",
            expected_session_id=session.session_id,
            now=self.now + timedelta(minutes=1),
        )
        with self.assertRaises(SessionConflictError):
            self.store.reset_active_session(
                user_key="boundaries",
                expected_session_id=session.session_id,
                now=self.now,
            )
        self.assertEqual(
            replacement,
            self.store.get_or_create_active_session("boundaries", now=self.now),
        )
        self.assertIsNone(self.store.get_memory("boundaries"))
        self.assertEqual(
            (first, second),
            self.store.load_unreviewed_turns(
                user_key="boundaries",
                session_id=session.session_id,
                after_turn_id=None,
                now=self.now,
            ),
        )
        self.assertIsNone(
            self.store.get_summary(user_key="boundaries", session_id=session.session_id)
        )

    def test_contract_delete_turns_through_is_scoped_to_one_session(self) -> None:
        # Every out-of-scope Turn is older than the bound, so a wider delete removes it.
        old = self.store.get_or_create_active_session("owner", now=self.now)
        old_turn = self.append(
            "owner", old.session_id, created_at=self.now - timedelta(seconds=5)
        )
        current = self.store.reset_active_session(
            user_key="owner", expected_session_id=old.session_id, now=self.now
        )
        covered = self.append("owner", current.session_id)
        kept = self.append(
            "owner", current.session_id, created_at=self.now + timedelta(seconds=2)
        )
        neighbour = self.store.get_or_create_active_session("neighbour", now=self.now)
        neighbour_turn = self.append(
            "neighbour",
            neighbour.session_id,
            created_at=self.now - timedelta(seconds=10),
        )

        self.store.delete_turns_through(
            user_key="owner",
            session_id=current.session_id,
            through_turn_id=covered.turn_id,
        )

        self.assertEqual(
            (kept,),
            self.store.load_context(
                user_key="owner", session_id=current.session_id, now=self.now
            ).turns,
        )
        self.assertEqual(
            (old_turn,),
            self.store.load_context(
                user_key="owner", session_id=old.session_id, now=self.now
            ).turns,
        )
        self.assertEqual(
            (neighbour_turn,),
            self.store.load_context(
                user_key="neighbour", session_id=neighbour.session_id, now=self.now
            ).turns,
        )

    def test_contract_load_is_complete_beyond_one_mebibyte(self) -> None:
        session = self.store.get_or_create_active_session("complete", now=self.now)
        expected = []
        payload = "x" * 70_000
        for index in range(8):
            expected.append(
                self.append(
                    "complete",
                    session.session_id,
                    created_at=self.now + timedelta(seconds=index),
                    user_message=payload,
                    assistant_message=payload,
                )
            )
        self.assertGreater(
            sum(
                len(turn.user_message.encode()) + len(turn.assistant_message.encode())
                for turn in expected
            ),
            1024 * 1024,
        )
        self.assertEqual(
            tuple(expected),
            self.store.load_context(
                user_key="complete", session_id=session.session_id, now=self.now
            ).turns,
        )


def _required(name: str, value: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} is required")
    return value
