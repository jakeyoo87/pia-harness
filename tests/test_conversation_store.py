from __future__ import annotations

import unittest

from pia_harness import TurnTooLargeError, new_turn_id
from pia_harness.testing import (
    ConversationStoreContract,
    InMemoryConversationStore,
)


class InMemoryConversationStoreContractTest(
    ConversationStoreContract, unittest.TestCase
):
    def make_store(self) -> InMemoryConversationStore:
        return InMemoryConversationStore()

    def test_delete_all_for_user_is_scoped_and_repeatable(self) -> None:
        first = self.store.get_or_create_active_session("delete", now=self.now)
        other = self.store.get_or_create_active_session("keep", now=self.now)
        self.append("delete", first.session_id)
        self.append("keep", other.session_id)

        self.assertEqual(2, self.store.delete_all_for_user("delete"))
        self.assertEqual(0, self.store.delete_all_for_user("delete"))
        self.assertEqual(
            1,
            len(
                self.store.load_context(
                    user_key="keep", session_id=other.session_id, now=self.now
                ).turns
            ),
        )

    def test_oversized_turn_is_rejected_before_write(self) -> None:
        store = InMemoryConversationStore(max_turn_bytes=4)
        session = store.get_or_create_active_session("large", now=self.now)
        with self.assertRaises(TurnTooLargeError):
            store.append_completed_turn(
                user_key="large",
                session_id=session.session_id,
                turn_id=new_turn_id(self.now),
                user_message="123",
                assistant_message="456",
                created_at=self.now,
            )
        self.assertEqual(
            (),
            store.load_context(
                user_key="large", session_id=session.session_id, now=self.now
            ).turns,
        )


if __name__ == "__main__":
    unittest.main()
