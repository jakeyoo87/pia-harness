from __future__ import annotations

import os
import unittest
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import boto3

from pia_harness import (
    CompactionPolicy,
    ContextUsage,
    DynamoDBConversationStore,
    RollingSummary,
    SummaryOutput,
    SummaryValidationError,
    TokenCompactor,
    new_turn_id,
)


ENDPOINT = os.environ.get("PIA_HARNESS_DYNAMODB_ENDPOINT")


@unittest.skipUnless(ENDPOINT, "PIA_HARNESS_DYNAMODB_ENDPOINT is not set")
class TokenCompactionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.table_name = f"pia-harness-compaction-test-{uuid4().hex}"
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
        self.now = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)
        self.policy = CompactionPolicy(
            trigger_ratio=0.90,
            max_response_tokens=10,
            protected_tail_ratio=0.20,
        )

    def append_turns(
        self, user_key: str, session_id: str, count: int, *, size: int = 6
    ):
        turns = []
        for index in range(count):
            created_at = self.now + timedelta(seconds=index)
            turns.append(
                self.store.append_completed_turn(
                    user_key=user_key,
                    session_id=session_id,
                    turn_id=new_turn_id(created_at),
                    user_message="u" * size,
                    assistant_message="a" * size,
                    created_at=created_at,
                )
            )
        return turns

    def compact(self, user_key, session_id, summarize, *, context_limit=100):
        return TokenCompactor(
            self.store,
            summarize,
            estimate_tokens=len,
            policy=self.policy,
        ).compact_after_response(
            user_key=user_key,
            session_id=session_id,
            context_limit=context_limit,
            model_id="nemotron",
            estimated_context_tokens=0,
            usage=ContextUsage("nemotron", 90),
            now=self.now + timedelta(minutes=1),
        )

    def test_policy_uses_usable_budget_and_matching_provider_usage(self) -> None:
        policy = CompactionPolicy()
        self.assertEqual(232243, policy.trigger_tokens(262144))
        self.assertTrue(
            policy.should_compact(
                context_limit=262144,
                model_id="nemotron",
                estimated_context_tokens=1,
                usage=ContextUsage("nemotron", 232243),
            )
        )
        self.assertFalse(
            policy.should_compact(
                context_limit=262144,
                model_id="nemotron",
                estimated_context_tokens=1,
                usage=ContextUsage("another-model", 999999),
            )
        )

    def test_success_replaces_old_turns_with_summary_and_keeps_tail(self) -> None:
        user_key = "compact-success"
        session = self.store.get_or_create_active_session(user_key, now=self.now)
        turns = self.append_turns(user_key, session.session_id, 4)
        requests = []

        def summarize(request):
            requests.append(request)
            return SummaryOutput("short", "nemotron", 5)

        summary = self.compact(user_key, session.session_id, summarize)
        self.assertIsNotNone(summary)
        self.assertEqual(tuple(turns[:3]), requests[0].turns)
        self.assertEqual(turns[2].turn_id, summary.through_turn_id)

        context = self.store.load_context(
            user_key=user_key, session_id=session.session_id, now=self.now
        )
        self.assertEqual(summary, context.summary)
        self.assertEqual((turns[-1],), context.turns)
        items = self.client.query(
            TableName=self.table_name,
            KeyConditionExpression="pk = :pk",
            ExpressionAttributeValues={":pk": {"S": f"USER#{user_key}"}},
        )["Items"]
        self.assertEqual(3, len(items))

    def test_newest_turn_is_kept_even_when_over_tail_budget(self) -> None:
        user_key = "compact-large-tail"
        session = self.store.get_or_create_active_session(user_key, now=self.now)
        turns = self.append_turns(user_key, session.session_id, 2, size=30)
        requests = []

        def summarize(request):
            requests.append(request)
            return SummaryOutput("s", "nemotron", 1)

        self.compact(user_key, session.session_id, summarize)
        self.assertEqual((turns[0],), requests[0].turns)
        context = self.store.load_context(
            user_key=user_key, session_id=session.session_id, now=self.now
        )
        self.assertEqual((turns[-1],), context.turns)

    def test_invalid_summary_preserves_raw_turns(self) -> None:
        user_key = "compact-invalid"
        session = self.store.get_or_create_active_session(user_key, now=self.now)
        turns = self.append_turns(user_key, session.session_id, 3)

        with self.assertRaises(SummaryValidationError):
            self.compact(
                user_key,
                session.session_id,
                lambda request: SummaryOutput("", "nemotron", 1),
            )

        context = self.store.load_context(
            user_key=user_key, session_id=session.session_id, now=self.now
        )
        self.assertIsNone(context.summary)
        self.assertEqual(tuple(turns), context.turns)

    def test_summary_cas_boundary_and_reset(self) -> None:
        user_key = "compact-cas"
        session = self.store.get_or_create_active_session(user_key, now=self.now)
        turns = self.append_turns(user_key, session.session_id, 2)
        first = RollingSummary(
            user_key=user_key,
            session_id=session.session_id,
            summary_text="first",
            through_turn_id=turns[0].turn_id,
            summary_tokens=1,
            model_id="nemotron",
            updated_at=self.now,
        )
        stale = RollingSummary(
            user_key=user_key,
            session_id=session.session_id,
            summary_text="stale",
            through_turn_id=turns[1].turn_id,
            summary_tokens=1,
            model_id="nemotron",
            updated_at=self.now,
        )
        self.assertTrue(
            self.store.replace_summary(first, expected_through_turn_id=None)
        )
        self.assertFalse(
            self.store.replace_summary(stale, expected_through_turn_id=None)
        )
        context = self.store.load_context(
            user_key=user_key, session_id=session.session_id, now=self.now
        )
        self.assertEqual((turns[1],), context.turns)
        self.assertTrue(
            self.store.replace_summary(
                stale, expected_through_turn_id=first.through_turn_id
            )
        )
        self.assertEqual(
            stale,
            self.store.get_summary(user_key=user_key, session_id=session.session_id),
        )

        replacement = self.store.reset_active_session(
            user_key=user_key,
            expected_session_id=session.session_id,
            now=self.now + timedelta(minutes=2),
        )
        self.assertIsNone(
            self.store.get_summary(user_key=user_key, session_id=session.session_id)
        )
        self.assertIsNone(
            self.store.load_context(
                user_key=user_key,
                session_id=replacement.session_id,
                now=self.now,
            ).summary
        )


if __name__ == "__main__":
    unittest.main()
