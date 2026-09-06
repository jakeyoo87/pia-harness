from __future__ import annotations

import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import boto3

from pia_harness import (
    DynamoDBConversationStore,
    SessionConflictError,
    TurnConflictError,
    TurnTooLargeError,
    new_turn_id,
)


ENDPOINT = os.environ.get("PIA_HARNESS_DYNAMODB_ENDPOINT")


@unittest.skipUnless(ENDPOINT, "PIA_HARNESS_DYNAMODB_ENDPOINT is not set")
class DynamoDBConversationStoreTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.table_name = f"pia-harness-test-{uuid4().hex}"
        cls.client = boto3.client(
            "dynamodb",
            endpoint_url=ENDPOINT,
            region_name="ap-northeast-2",
            aws_access_key_id="test",
            aws_secret_access_key="test",
        )
        cls.client.create_table(
            TableName=cls.table_name,
            BillingMode="PAY_PER_REQUEST",
            AttributeDefinitions=[
                {"AttributeName": "pk", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
            ],
            KeySchema=[
                {"AttributeName": "pk", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.delete_table(TableName=cls.table_name)

    def setUp(self) -> None:
        self.store = DynamoDBConversationStore(self.client, self.table_name)
        self.now = datetime(2026, 9, 6, 9, 0, tzinfo=UTC)

    def append(
        self,
        user_key: str,
        session_id: str,
        *,
        created_at: datetime | None = None,
        user_message: str = "질문",
        assistant_message: str = "답변",
    ):
        created_at = created_at or self.now
        return self.store.append_completed_turn(
            user_key=user_key,
            session_id=session_id,
            turn_id=new_turn_id(created_at),
            user_message=user_message,
            assistant_message=assistant_message,
            created_at=created_at,
        )

    def test_users_are_isolated(self) -> None:
        first = self.store.get_or_create_active_session("member-a", now=self.now)
        second = self.store.get_or_create_active_session("member-b", now=self.now)
        self.append("member-a", first.session_id)

        self.assertEqual(
            1,
            len(
                self.store.load_context(
                    user_key="member-a", session_id=first.session_id, now=self.now
                ).turns
            ),
        )
        self.assertEqual(
            (),
            self.store.load_context(
                user_key="member-b", session_id=second.session_id, now=self.now
            ).turns,
        )

    def test_concurrent_get_or_create_returns_one_session(self) -> None:
        with ThreadPoolExecutor(max_workers=8) as executor:
            sessions = list(
                executor.map(
                    lambda _: self.store.get_or_create_active_session(
                        "member-concurrent", now=self.now
                    ),
                    range(8),
                )
            )

        self.assertEqual(1, len({session.session_id for session in sessions}))

    def test_turns_are_ordered_and_append_is_idempotent(self) -> None:
        session = self.store.get_or_create_active_session("member-order", now=self.now)
        later = self.append(
            "member-order", session.session_id, created_at=self.now + timedelta(seconds=1)
        )
        earlier = self.append("member-order", session.session_id, created_at=self.now)
        same_time = self.append("member-order", session.session_id, created_at=self.now)

        replay = self.store.append_completed_turn(
            user_key=earlier.user_key,
            session_id=earlier.session_id,
            turn_id=earlier.turn_id,
            user_message=earlier.user_message,
            assistant_message=earlier.assistant_message,
            created_at=earlier.created_at,
        )
        self.assertEqual(earlier, replay)
        self.assertEqual(
            sorted([earlier.turn_id, same_time.turn_id]) + [later.turn_id],
            [
                turn.turn_id
                for turn in self.store.load_context(
                    user_key="member-order", session_id=session.session_id, now=self.now
                ).turns
            ],
        )

        with self.assertRaises(TurnConflictError):
            self.store.append_completed_turn(
                user_key=earlier.user_key,
                session_id=earlier.session_id,
                turn_id=earlier.turn_id,
                user_message="다른 질문",
                assistant_message=earlier.assistant_message,
                created_at=earlier.created_at,
            )

    def test_expired_turns_are_filtered(self) -> None:
        session = self.store.get_or_create_active_session("member-expired", now=self.now)
        self.append(
            "member-expired",
            session.session_id,
            created_at=self.now - timedelta(days=15),
        )
        self.assertEqual(
            (),
            self.store.load_context(
                user_key="member-expired", session_id=session.session_id, now=self.now
            ).turns,
        )

    def test_reset_moves_only_the_active_pointer(self) -> None:
        current = self.store.get_or_create_active_session("member-reset", now=self.now)
        self.append("member-reset", current.session_id)
        replacement = self.store.reset_active_session(
            user_key="member-reset",
            expected_session_id=current.session_id,
            now=self.now + timedelta(minutes=1),
        )

        self.assertNotEqual(current.session_id, replacement.session_id)
        self.assertEqual(
            (),
            self.store.load_context(
                user_key="member-reset", session_id=replacement.session_id, now=self.now
            ).turns,
        )
        self.assertEqual(
            replacement,
            self.store.get_or_create_active_session("member-reset", now=self.now),
        )
        with self.assertRaises(SessionConflictError):
            self.store.reset_active_session(
                user_key="member-reset",
                expected_session_id=current.session_id,
                now=self.now,
            )

    def test_delete_all_for_user_is_scoped_and_repeatable(self) -> None:
        first = self.store.get_or_create_active_session("member-delete", now=self.now)
        other = self.store.get_or_create_active_session("member-keep", now=self.now)
        self.append("member-delete", first.session_id)
        self.append("member-keep", other.session_id)

        self.assertEqual(2, self.store.delete_all_for_user("member-delete"))
        self.assertEqual(0, self.store.delete_all_for_user("member-delete"))
        self.assertEqual(
            1,
            len(
                self.store.load_context(
                    user_key="member-keep", session_id=other.session_id, now=self.now
                ).turns
            ),
        )
        recreated = self.store.get_or_create_active_session("member-delete", now=self.now)
        self.assertNotEqual(first.session_id, recreated.session_id)

    def test_oversized_turn_is_rejected_before_write(self) -> None:
        store = DynamoDBConversationStore(
            self.client, self.table_name, max_turn_bytes=4
        )
        session = store.get_or_create_active_session("member-large", now=self.now)
        with self.assertRaises(TurnTooLargeError):
            store.append_completed_turn(
                user_key="member-large",
                session_id=session.session_id,
                turn_id=new_turn_id(self.now),
                user_message="123",
                assistant_message="456",
                created_at=self.now,
            )
        self.assertEqual(
            (),
            store.load_context(
                user_key="member-large", session_id=session.session_id, now=self.now
            ).turns,
        )
if __name__ == "__main__":
    unittest.main()
