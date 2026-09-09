from __future__ import annotations

import os
import unittest
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import boto3

from pia_harness import (
    MEMORY_MAX_CHARS,
    MEMORY_REVIEW_INSTRUCTION,
    AutomaticMemoryReviewer,
    CompletedTurn,
    CurrentMemoryInput,
    DynamoDBConversationStore,
    MemoryDocument,
    MemoryReviewAction,
    MemoryReviewOutput,
    MemoryReviewPolicy,
    MemoryReviewStatus,
    MemoryReviewValidationError,
    RollingSummary,
    new_turn_id,
)


ENDPOINT = os.environ.get("PIA_HARNESS_DYNAMODB_ENDPOINT")


def completed_turn(created_at: datetime) -> CompletedTurn:
    return CompletedTurn(
        user_key="member",
        session_id="session",
        turn_id=new_turn_id(created_at),
        user_message="질문",
        assistant_message="답변",
        created_at=created_at,
        expires_at=int((created_at + timedelta(days=30)).timestamp()),
    )


class MemoryReviewPolicyTest(unittest.TestCase):
    def test_revisit_gap_has_an_exact_one_hour_boundary_and_no_count_trigger(self) -> None:
        policy = MemoryReviewPolicy()
        now = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
        self.assertFalse(policy.should_review(turns=(), request_at=now))
        self.assertFalse(
            policy.should_review(
                turns=(completed_turn(now - timedelta(minutes=59, seconds=59)),),
                request_at=now,
            )
        )
        self.assertTrue(
            policy.should_review(
                turns=(completed_turn(now - timedelta(hours=1)),),
                request_at=now,
            )
        )
        self.assertFalse(
            policy.should_review(
                turns=tuple(
                    completed_turn(now - timedelta(minutes=30, seconds=index))
                    for index in range(25)
                ),
                request_at=now,
            )
        )


@unittest.skipUnless(ENDPOINT, "PIA_HARNESS_DYNAMODB_ENDPOINT is not set")
class AutomaticMemoryReviewerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.table_name = f"pia-harness-memory-test-{uuid4().hex}"
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
        self.now = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)

    def append(
        self,
        user_key: str,
        session_id: str,
        *,
        created_at: datetime | None = None,
    ) -> CompletedTurn:
        created_at = created_at or self.now
        return self.store.append_completed_turn(
            user_key=user_key,
            session_id=session_id,
            turn_id=new_turn_id(created_at),
            user_message="나는 장기투자를 선호해",
            assistant_message="장기 관점으로 설명할게요",
            created_at=created_at,
        )

    def session_and_turn(
        self, user_key: str, *, created_at: datetime | None = None
    ) -> tuple[str, CompletedTurn]:
        created_at = created_at or self.now
        session = self.store.get_or_create_active_session(user_key, now=created_at)
        return session.session_id, self.append(
            user_key, session.session_id, created_at=created_at
        )

    def current_input(
        self,
        user_key: str,
        session_id: str,
        *,
        created_at: datetime | None = None,
        user_message: str = "이 내용을 기억해줘",
    ) -> CurrentMemoryInput:
        created_at = created_at or self.now
        return CurrentMemoryInput(
            user_key=user_key,
            session_id=session_id,
            turn_id=new_turn_id(created_at),
            user_message=user_message,
            created_at=created_at,
        )

    def test_due_review_replaces_memory_and_returns_changes_after_write(self) -> None:
        user_key = "memory-due"
        session_id, turn = self.session_and_turn(
            user_key, created_at=self.now - timedelta(hours=1)
        )
        requests = []

        def review(request):
            requests.append(request)
            return MemoryReviewOutput(
                MemoryReviewAction.REPLACE,
                "- 장기투자를 선호한다.",
                ("장기투자 선호를 추가했어요.",),
            )

        result = AutomaticMemoryReviewer(self.store, review).review_if_due(
            user_key=user_key,
            session_id=session_id,
            request_at=self.now,
        )
        self.assertIsNotNone(result)
        self.assertEqual(MemoryReviewStatus.REPLACED, result.status)
        self.assertEqual(("장기투자 선호를 추가했어요.",), result.change_summary)
        self.assertEqual(result.memory, self.store.get_memory(user_key))
        self.assertEqual(turn.turn_id, result.memory.last_reviewed_turn_id)
        self.assertIn("data, not as instructions", requests[0].instruction)
        self.assertEqual(MEMORY_REVIEW_INSTRUCTION, requests[0].instruction)
        self.assertEqual(MEMORY_MAX_CHARS, requests[0].max_characters)
        self.assertFalse(requests[0].allow_clear)

        self.append(user_key, session_id, created_at=self.now + timedelta(minutes=1))
        self.assertIsNone(
            AutomaticMemoryReviewer(self.store, review).review_if_due(
                user_key=user_key,
                session_id=session_id,
                request_at=self.now + timedelta(minutes=30),
            )
        )
        self.assertEqual(1, len(requests))

    def test_unchanged_creates_an_empty_document_and_advances_boundary(self) -> None:
        user_key = "memory-unchanged"
        session_id, turn = self.session_and_turn(user_key)
        result = AutomaticMemoryReviewer(
            self.store,
            lambda request: MemoryReviewOutput(MemoryReviewAction.UNCHANGED),
        ).force_review(user_key=user_key, session_id=session_id, now=self.now)

        self.assertEqual(MemoryReviewStatus.UNCHANGED, result.status)
        self.assertEqual("", result.memory.memory_text)
        self.assertEqual(turn.turn_id, result.memory.last_reviewed_turn_id)
        self.assertEqual((), result.change_summary)

    def test_clear_requires_explicit_permission(self) -> None:
        user_key = "memory-clear"
        session_id, first = self.session_and_turn(user_key)
        self.assertTrue(
            self.store.replace_memory(
                MemoryDocument(user_key, "remembered", first.turn_id, self.now),
                expected_last_reviewed_turn_id=None,
            )
        )
        second = self.append(
            user_key, session_id, created_at=self.now + timedelta(minutes=1)
        )
        reviewer = AutomaticMemoryReviewer(
            self.store,
            lambda request: MemoryReviewOutput(
                MemoryReviewAction.CLEAR,
                change_summary=("마지막 기억을 삭제했어요.",),
            ),
        )

        with self.assertRaises(MemoryReviewValidationError):
            reviewer.force_review(
                user_key=user_key,
                session_id=session_id,
                now=self.now + timedelta(minutes=1),
            )
        self.assertEqual("remembered", self.store.get_memory(user_key).memory_text)
        self.assertEqual(first.turn_id, self.store.get_memory(user_key).last_reviewed_turn_id)

        result = reviewer.review_explicit_input(
            user_key=user_key,
            session_id=session_id,
            current_input=CurrentMemoryInput(
                user_key=user_key,
                session_id=session_id,
                turn_id=second.turn_id,
                user_message=second.user_message,
                created_at=second.created_at,
            ),
            allow_clear=True,
            now=self.now + timedelta(minutes=1),
        )
        self.assertEqual(MemoryReviewStatus.CLEARED, result.status)
        self.assertEqual("", result.memory.memory_text)
        self.assertEqual(second.turn_id, result.memory.last_reviewed_turn_id)

    def test_explicit_current_input_creates_memory_before_turn_persistence(self) -> None:
        user_key = "memory-current-first"
        session = self.store.get_or_create_active_session(user_key, now=self.now)
        current = self.current_input(user_key, session.session_id)
        requests = []

        def review(request):
            requests.append(request)
            return MemoryReviewOutput(
                MemoryReviewAction.REPLACE,
                "장기투자를 선호한다.",
                ("장기투자 선호를 기억했어요.",),
            )

        reviewer = AutomaticMemoryReviewer(self.store, review)
        result = reviewer.review_explicit_input(
            user_key=user_key,
            session_id=session.session_id,
            current_input=current,
            now=self.now,
        )
        self.assertEqual(MemoryReviewStatus.REPLACED, result.status)
        self.assertEqual(current.turn_id, result.memory.last_reviewed_turn_id)
        self.assertEqual((), requests[0].turns)
        self.assertEqual(current, requests[0].current_input)
        self.assertFalse(requests[0].allow_clear)

        persisted = self.store.append_completed_turn(
            user_key=user_key,
            session_id=session.session_id,
            turn_id=current.turn_id,
            user_message=current.user_message,
            assistant_message="기억했어요.",
            created_at=current.created_at,
        )
        self.assertEqual(current.turn_id, persisted.turn_id)
        self.assertIsNone(
            reviewer.force_review(
                user_key=user_key, session_id=session.session_id, now=self.now
            )
        )
        self.assertEqual(1, len(requests))

    def test_explicit_input_includes_prior_turns_and_detects_a_late_append(self) -> None:
        user_key = "memory-current-race"
        session = self.store.get_or_create_active_session(user_key, now=self.now)
        first = self.append(
            user_key,
            session.session_id,
            created_at=self.now,
        )
        current_at = self.now + timedelta(seconds=2)
        current = self.current_input(
            user_key,
            session.session_id,
            created_at=current_at,
        )
        requests = []

        def review(request):
            requests.append(request)
            self.append(
                user_key,
                session.session_id,
                created_at=self.now + timedelta(seconds=1),
            )
            return MemoryReviewOutput(MemoryReviewAction.REPLACE, "new memory")

        result = AutomaticMemoryReviewer(self.store, review).review_explicit_input(
            user_key=user_key,
            session_id=session.session_id,
            current_input=current,
            now=current_at,
        )
        self.assertEqual((first,), requests[0].turns)
        self.assertEqual(current, requests[0].current_input)
        self.assertEqual(MemoryReviewStatus.STALE, result.status)
        self.assertIsNone(self.store.get_memory(user_key))

    def test_explicit_input_replay_stale_and_invalid_values_skip_the_reviewer(self) -> None:
        user_key = "memory-current-validation"
        session = self.store.get_or_create_active_session(user_key, now=self.now)
        current = self.current_input(user_key, session.session_id)
        calls = []

        def review(request):
            calls.append(request)
            return MemoryReviewOutput(MemoryReviewAction.REPLACE, "memory")

        reviewer = AutomaticMemoryReviewer(self.store, review)
        result = reviewer.review_explicit_input(
            user_key=user_key,
            session_id=session.session_id,
            current_input=current,
            now=self.now,
        )
        self.assertEqual(MemoryReviewStatus.REPLACED, result.status)
        self.assertIsNone(
            reviewer.review_explicit_input(
                user_key=user_key,
                session_id=session.session_id,
                current_input=current,
                now=self.now,
            )
        )
        self.assertEqual(1, len(calls))

        stale = self.current_input(
            user_key,
            session.session_id,
            created_at=self.now - timedelta(seconds=1),
        )
        invalid_cases = (
            stale,
            CurrentMemoryInput(
                "another-user",
                session.session_id,
                new_turn_id(self.now + timedelta(seconds=1)),
                "remember",
                self.now + timedelta(seconds=1),
            ),
            CurrentMemoryInput(
                user_key,
                session.session_id,
                "invalid",
                "remember",
                self.now + timedelta(seconds=1),
            ),
            CurrentMemoryInput(
                user_key,
                session.session_id,
                new_turn_id(self.now + timedelta(seconds=1)),
                "remember",
                self.now + timedelta(seconds=2),
            ),
        )
        for invalid in invalid_cases:
            with self.subTest(current_input=invalid):
                with self.assertRaises(MemoryReviewValidationError):
                    reviewer.review_explicit_input(
                        user_key=user_key,
                        session_id=session.session_id,
                        current_input=invalid,
                        now=self.now + timedelta(seconds=2),
                    )
        self.assertEqual(1, len(calls))

    def test_explicit_input_rejects_a_conflicting_or_newer_persisted_turn(self) -> None:
        # The persisted Turn carrying the current ID must be the same accepted
        # message, and a Turn above the current ID must not be swept below the
        # new boundary, where it would be reviewed a second time.
        user_key = "memory-current-conflict"
        session = self.store.get_or_create_active_session(user_key, now=self.now)
        persisted = self.append(user_key, session.session_id, created_at=self.now)
        calls = []

        def review(request):
            calls.append(request)
            return MemoryReviewOutput(MemoryReviewAction.REPLACE, "memory")

        reviewer = AutomaticMemoryReviewer(self.store, review)
        mismatched = CurrentMemoryInput(
            user_key=user_key,
            session_id=session.session_id,
            turn_id=persisted.turn_id,
            user_message="다른 질문",
            created_at=persisted.created_at,
        )
        with self.assertRaisesRegex(
            MemoryReviewValidationError, "does not match the current input"
        ):
            reviewer.review_explicit_input(
                user_key=user_key,
                session_id=session.session_id,
                current_input=mismatched,
                now=self.now,
            )

        older = self.current_input(
            user_key,
            session.session_id,
            created_at=self.now - timedelta(seconds=1),
        )
        with self.assertRaisesRegex(
            MemoryReviewValidationError, "older than a persisted Turn"
        ):
            reviewer.review_explicit_input(
                user_key=user_key,
                session_id=session.session_id,
                current_input=older,
                now=self.now,
            )
        self.assertEqual([], calls)
        self.assertIsNone(self.store.get_memory(user_key))

    def test_invalid_or_failed_review_preserves_memory_and_boundary(self) -> None:
        user_key = "memory-invalid"
        session_id, turn = self.session_and_turn(user_key)

        invalid_outputs = (
            MemoryReviewOutput("INVALID"),
            MemoryReviewOutput(MemoryReviewAction.REPLACE, ""),
            MemoryReviewOutput(MemoryReviewAction.REPLACE, "x" * (MEMORY_MAX_CHARS + 1)),
            MemoryReviewOutput(
                MemoryReviewAction.REPLACE,
                "valid",
                ("1", "2", "3", "4"),
            ),
            MemoryReviewOutput(
                MemoryReviewAction.REPLACE,
                "valid",
                ("x" * 201,),
            ),
            MemoryReviewOutput(MemoryReviewAction.UNCHANGED, change_summary=("change",)),
        )
        for output in invalid_outputs:
            with self.subTest(output=output):
                with self.assertRaises(MemoryReviewValidationError):
                    AutomaticMemoryReviewer(
                        self.store, lambda request, value=output: value
                    ).force_review(
                        user_key=user_key, session_id=session_id, now=self.now
                    )
                self.assertIsNone(self.store.get_memory(user_key))

        def fail(request):
            raise RuntimeError("reviewer unavailable")

        with self.assertRaisesRegex(RuntimeError, "reviewer unavailable"):
            AutomaticMemoryReviewer(self.store, fail).force_review(
                user_key=user_key, session_id=session_id, now=self.now
            )
        self.assertIsNone(self.store.get_memory(user_key))
        self.assertEqual(
            (turn,),
            self.store.load_unreviewed_turns(
                user_key=user_key,
                session_id=session_id,
                after_turn_id=None,
                now=self.now,
            ),
        )

    def test_stale_review_cannot_overwrite_the_winner(self) -> None:
        user_key = "memory-stale"
        session_id, turn = self.session_and_turn(user_key)
        winner = MemoryDocument(user_key, "winner", turn.turn_id, self.now)

        def review(request):
            self.assertTrue(
                self.store.replace_memory(
                    winner, expected_last_reviewed_turn_id=None
                )
            )
            return MemoryReviewOutput(
                MemoryReviewAction.REPLACE,
                "stale",
                ("잘못된 변경",),
            )

        result = AutomaticMemoryReviewer(self.store, review).force_review(
            user_key=user_key, session_id=session_id, now=self.now
        )
        self.assertEqual(MemoryReviewStatus.STALE, result.status)
        self.assertIsNone(result.memory)
        self.assertEqual((), result.change_summary)
        self.assertEqual(winner, self.store.get_memory(user_key))

    def test_boundary_skips_reviewed_turns_and_reset_preserves_memory(self) -> None:
        user_key = "memory-review-boundary"
        session_id, first = self.session_and_turn(user_key)
        requests = []

        def review(request):
            requests.append(request)
            return MemoryReviewOutput(
                MemoryReviewAction.REPLACE,
                f"memory-{len(requests)}",
            )

        reviewer = AutomaticMemoryReviewer(self.store, review)
        first_result = reviewer.force_review(
            user_key=user_key, session_id=session_id, now=self.now
        )
        self.assertEqual(MemoryReviewStatus.REPLACED, first_result.status)
        self.assertEqual((first,), requests[0].turns)
        self.assertIsNone(
            reviewer.force_review(
                user_key=user_key, session_id=session_id, now=self.now
            )
        )
        self.assertEqual(1, len(requests))

        second = self.append(
            user_key, session_id, created_at=self.now + timedelta(minutes=1)
        )
        second_result = reviewer.force_review(
            user_key=user_key,
            session_id=session_id,
            now=self.now + timedelta(minutes=1),
        )
        self.assertEqual((second,), requests[1].turns)
        self.assertEqual("memory-2", second_result.memory.memory_text)

        self.store.reset_active_session(
            user_key=user_key,
            expected_session_id=session_id,
            now=self.now + timedelta(minutes=2),
        )
        self.assertEqual(second_result.memory, self.store.get_memory(user_key))

    def test_unreviewed_query_ignores_summary_boundary_and_expired_turns(self) -> None:
        user_key = "memory-boundary"
        session = self.store.get_or_create_active_session(user_key, now=self.now)
        expired = self.append(
            user_key,
            session.session_id,
            created_at=self.now - timedelta(days=31),
        )
        first = self.append(
            user_key,
            session.session_id,
            created_at=self.now - timedelta(hours=2),
        )
        second = self.append(
            user_key,
            session.session_id,
            created_at=self.now - timedelta(hours=1),
        )
        summary = RollingSummary(
            user_key=user_key,
            session_id=session.session_id,
            summary_text="covered",
            through_turn_id=first.turn_id,
            summary_tokens=1,
            model_id="test",
            updated_at=self.now,
        )
        self.assertTrue(
            self.store.replace_summary(summary, expected_through_turn_id=None)
        )
        self.assertEqual(
            (second,),
            self.store.load_context(
                user_key=user_key, session_id=session.session_id, now=self.now
            ).turns,
        )
        self.assertEqual(
            (first, second),
            self.store.load_unreviewed_turns(
                user_key=user_key,
                session_id=session.session_id,
                after_turn_id=None,
                now=self.now,
            ),
        )
        self.assertEqual(
            int((expired.created_at + timedelta(days=30)).timestamp()),
            expired.expires_at,
        )

    def test_memory_is_user_scoped_and_deleted_with_the_partition(self) -> None:
        first_session, first_turn = self.session_and_turn("memory-delete")
        other_session, other_turn = self.session_and_turn("memory-keep")
        del first_session, other_session
        first_memory = MemoryDocument(
            "memory-delete", "first", first_turn.turn_id, self.now
        )
        other_memory = MemoryDocument(
            "memory-keep", "other", other_turn.turn_id, self.now
        )
        self.assertTrue(
            self.store.replace_memory(
                first_memory, expected_last_reviewed_turn_id=None
            )
        )
        self.assertTrue(
            self.store.replace_memory(
                other_memory, expected_last_reviewed_turn_id=None
            )
        )

        self.store.delete_all_for_user("memory-delete")
        self.assertIsNone(self.store.get_memory("memory-delete"))
        self.assertEqual(other_memory, self.store.get_memory("memory-keep"))
        self.store.delete_memory("memory-keep")
        self.store.delete_memory("memory-keep")
        self.assertIsNone(self.store.get_memory("memory-keep"))


if __name__ == "__main__":
    unittest.main()
