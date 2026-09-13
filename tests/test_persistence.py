from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from pia_harness import (
    ActiveSession,
    CompletedTurn,
    ConversationContext,
    MemoryDocument,
    RollingSummary,
    StoreContractError,
    new_turn_id,
)
from pia_harness.persistence import (
    validate_active_session,
    validate_loaded_context,
    validate_loaded_memory,
)


class PersistenceValidationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)
        self.turn = CompletedTurn(
            "user",
            "session",
            new_turn_id(self.now),
            "question",
            "answer",
            self.now,
            int((self.now + timedelta(days=30)).timestamp()),
        )

    def test_session_memory_and_turn_owner_mismatch_fail_closed(self) -> None:
        with self.assertRaises(StoreContractError):
            validate_active_session(
                ActiveSession("other", "session", self.now), user_key="user"
            )
        with self.assertRaises(StoreContractError):
            validate_loaded_memory(
                MemoryDocument("other", "memory", self.turn.turn_id, self.now),
                user_key="user",
            )
        for turn in (
            replace(self.turn, user_key="other"),
            replace(self.turn, session_id="other"),
        ):
            with self.subTest(turn=turn), self.assertRaises(StoreContractError):
                validate_loaded_context(
                    ConversationContext(None, (turn,)),
                    user_key="user",
                    session_id="session",
                    now=self.now,
                )

    def test_context_rejects_bad_summary_order_and_expiry(self) -> None:
        summary = RollingSummary(
            "user",
            "session",
            "summary",
            self.turn.turn_id,
            1,
            "model",
            self.now,
        )
        cases = (
            ConversationContext(replace(summary, user_key="other"), ()),
            ConversationContext(None, (self.turn, self.turn)),
            ConversationContext(None, (replace(self.turn, expires_at=0),)),
            ConversationContext(summary, (self.turn,)),
        )
        for context in cases:
            with self.subTest(context=context):
                with self.assertRaises(StoreContractError):
                    validate_loaded_context(
                        context,
                        user_key="user",
                        session_id="session",
                        now=self.now,
                    )


if __name__ == "__main__":
    unittest.main()
