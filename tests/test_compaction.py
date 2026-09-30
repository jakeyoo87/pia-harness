from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from pia_harness import (
    ToolObservation,
    CompactionPolicy,
    ContextUsage,
    ConversationContext,
    ModelTokenBudget,
    RollingSummary,
    StoreContractError,
    SummaryOutput,
    SummaryValidationError,
    TokenCompactor,
    new_turn_id,
)
from pia_harness.testing import InMemoryConversationStore


class TokenCompactionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryConversationStore()
        self.now = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)
        self.policy = CompactionPolicy(trigger_tokens=81, tail_chars=10)
        self.token_budget = ModelTokenBudget(100, 10)

    def append_turns(
        self,
        user_key: str,
        session_id: str,
        count: int,
        *,
        size: int = 6,
        start_seconds: int = 0,
    ):
        turns = []
        for index in range(count):
            created_at = self.now + timedelta(seconds=start_seconds + index)
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

    def compact(self, user_key, session_id, summarize, *, token_budget=None):
        return TokenCompactor(
            self.store,
            summarize,
            estimate_tokens=len,
            policy=self.policy,
        ).compact(
            user_key=user_key,
            session_id=session_id,
            token_budget=token_budget or self.token_budget,
            model_id="nemotron",
            estimated_context_tokens=0,
            usage=ContextUsage("nemotron", 90),
            now=self.now + timedelta(minutes=1),
        )

    def test_policy_triggers_at_its_size_on_matching_provider_usage(self) -> None:
        policy = CompactionPolicy()
        self.assertEqual(
            (256_000, 100_000, 10_000),
            (policy.trigger_tokens, policy.tail_chars, policy.summary_chars),
        )
        # The budget is the model's own window, far above the trigger.
        token_budget = ModelTokenBudget(1_050_000)

        def compacts(usage):
            return policy.should_compact(
                token_budget=token_budget,
                model_id="luna",
                estimated_context_tokens=1,
                usage=usage,
            )

        self.assertTrue(compacts(ContextUsage("luna", 256_000)))
        self.assertFalse(compacts(ContextUsage("luna", 255_999)))
        self.assertFalse(compacts(ContextUsage("another-model", 999_999)))

    def test_policy_sizes_are_checked(self) -> None:
        for values in (
            {"trigger_tokens": 0},
            {"tail_chars": True},
            {"summary_chars": -1},
        ):
            with self.assertRaises(ValueError):
                CompactionPolicy(**values)
        # A trigger the model's input budget never reaches is a setup error.
        with self.assertRaisesRegex(ValueError, "below the input budget"):
            CompactionPolicy().should_compact(
                token_budget=ModelTokenBudget(200_000),
                model_id="luna",
                estimated_context_tokens=1,
            )

    def test_reported_summary_over_the_output_limit_is_rejected(self) -> None:
        user_key = "compact-output-limit"
        session = self.store.get_or_create_active_session(user_key, now=self.now)
        turns = self.append_turns(user_key, session.session_id, 4)

        with self.assertRaisesRegex(
            SummaryValidationError, "exceeds the output token limit"
        ):
            self.compact(
                user_key,
                session.session_id,
                lambda request: SummaryOutput(
                    "short", "nemotron", request.max_output_tokens + 1
                ),
            )
        context = self.store.load_context(
            user_key=user_key, session_id=session.session_id, now=self.now
        )
        self.assertIsNone(context.summary)
        self.assertEqual(tuple(turns), context.turns)

    def test_a_summary_over_its_character_limit_is_rejected(self) -> None:
        user_key = "compact-long-summary"
        session = self.store.get_or_create_active_session(user_key, now=self.now)
        turns = self.append_turns(user_key, session.session_id, 4, size=40)
        requests = []

        def summarize(request):
            requests.append(request)
            return SummaryOutput("가" * (request.max_characters + 1), "nemotron")

        with self.assertRaisesRegex(SummaryValidationError, "character limit"):
            self.compact(user_key, session.session_id, summarize)
        # The writer is told the limit, and the output cap leaves room for it.
        self.assertEqual(self.policy.summary_chars, requests[0].max_characters)
        self.assertEqual(2 * self.policy.summary_chars, requests[0].max_output_tokens)
        context = self.store.load_context(
            user_key=user_key, session_id=session.session_id, now=self.now
        )
        self.assertEqual(tuple(turns), context.turns)

    def test_success_replaces_old_turns_with_summary_and_keeps_tail(self) -> None:
        user_key = "compact-success"
        session = self.store.get_or_create_active_session(user_key, now=self.now)
        turns = self.append_turns(user_key, session.session_id, 4)
        requests = []

        def summarize(request):
            requests.append(request)
            return SummaryOutput(f"summary-{len(requests)}", "nemotron", 5)

        summary = self.compact(user_key, session.session_id, summarize)
        self.assertIsNotNone(summary)
        self.assertEqual(tuple(turns[:3]), requests[0].turns)
        self.assertEqual(turns[2].turn_id, summary.through_turn_id)

        context = self.store.load_context(
            user_key=user_key, session_id=session.session_id, now=self.now
        )
        self.assertEqual(summary, context.summary)
        self.assertEqual((turns[-1],), context.turns)

        more_turns = self.append_turns(
            user_key, session.session_id, 3, start_seconds=10
        )
        replacement = self.compact(user_key, session.session_id, summarize)
        self.assertIsNotNone(replacement)
        self.assertEqual(summary.summary_text, requests[1].previous_summary)
        self.assertEqual((turns[-1], *more_turns[:2]), requests[1].turns)
        self.assertEqual(more_turns[1].turn_id, replacement.through_turn_id)
        context = self.store.load_context(
            user_key=user_key, session_id=session.session_id, now=self.now
        )
        self.assertEqual(replacement, context.summary)
        self.assertEqual((more_turns[-1],), context.turns)
        self.assertEqual(
            (more_turns[-1],),
            self.store.load_unreviewed_turns(
                user_key=user_key,
                session_id=session.session_id,
                after_turn_id=None,
                now=self.now,
            ),
        )

    def test_tool_records_count_when_keeping_recent_turns(self) -> None:
        user_key = "compact-tools"
        session = self.store.get_or_create_active_session(user_key, now=self.now)
        turns = []
        for index in range(3):
            created_at = self.now + timedelta(seconds=index)
            turns.append(
                self.store.append_completed_turn(
                    user_key=user_key,
                    session_id=session.session_id,
                    turn_id=new_turn_id(created_at),
                    user_message="uu",
                    assistant_message="aa",
                    created_at=created_at,
                    tool_observations=(
                        ToolObservation("web_search", "{}", "r" * 50, "c1", 0),
                    ),
                )
            )
        requests = []

        def summarize(request):
            requests.append(request)
            return SummaryOutput("s", "nemotron", 1)

        # Requests and answers alone would all fit the tail and leave nothing
        # to compact; with their tool records only the newest Turn stays.
        summary = self.compact(user_key, session.session_id, summarize)
        self.assertIsNotNone(summary)
        self.assertEqual(tuple(turns[:2]), requests[0].turns)
        context = self.store.load_context(
            user_key=user_key, session_id=session.session_id, now=self.now
        )
        self.assertEqual((turns[-1],), context.turns)

    def test_newest_turn_is_kept_even_when_over_tail_budget(self) -> None:
        user_key = "compact-large-tail"
        session = self.store.get_or_create_active_session(user_key, now=self.now)
        turns = self.append_turns(user_key, session.session_id, 2, size=3000)
        requests = []

        def summarize(request):
            requests.append(request)
            return SummaryOutput("가" * 1500, "nemotron")

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

    def test_summary_cas_boundary(self) -> None:
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

    def test_misordered_store_turns_fail_before_summary_or_delete(self) -> None:
        class MisorderedStore(InMemoryConversationStore):
            def __init__(self):
                super().__init__()
                self.delete_calls = 0

            def load_context(self, **values):
                context = super().load_context(**values)
                return ConversationContext(
                    context.summary, tuple(reversed(context.turns))
                )

            def delete_turns_through(self, **values):
                self.delete_calls += 1
                return super().delete_turns_through(**values)

        store = MisorderedStore()
        session = store.get_or_create_active_session("misordered", now=self.now)
        for offset in range(3):
            created_at = self.now + timedelta(seconds=offset)
            store.append_completed_turn(
                user_key="misordered",
                session_id=session.session_id,
                turn_id=new_turn_id(created_at),
                user_message="question",
                assistant_message="answer",
                created_at=created_at,
            )
        summaries = []
        compactor = TokenCompactor(
            store,
            lambda request: summaries.append(request),
            estimate_tokens=len,
            policy=self.policy,
        )
        with self.assertRaises(StoreContractError):
            compactor.compact(
                user_key="misordered",
                session_id=session.session_id,
                token_budget=self.token_budget,
                model_id="model",
                estimated_context_tokens=90,
                now=self.now + timedelta(minutes=1),
            )
        self.assertEqual([], summaries)
        self.assertEqual(0, store.delete_calls)


if __name__ == "__main__":
    unittest.main()
