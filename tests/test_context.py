from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from pia_harness import (
    DEFAULT_MAX_RESPONSE_TOKENS,
    AssembledPromptContext,
    CompletedTurn,
    ContextBudgetExceeded,
    ConversationContext,
    MemoryDocument,
    ModelTokenBudget,
    PromptContextAssembler,
    PromptContextKind,
    PromptContextValidationError,
    PromptTrust,
    ToolObservation,
    RollingSummary,
    new_turn_id,
)


class PromptContextAssemblerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)
        self.user_key = "member-a"
        self.session_id = "session-a"
        self.first = self.turn(1, "첫 질문", "첫 답변")
        self.second = self.turn(2, "둘째 질문", "둘째 답변")
        self.summary = RollingSummary(
            user_key=self.user_key,
            session_id=self.session_id,
            summary_text="이전 대화 요약",
            through_turn_id=new_turn_id(self.now),
            summary_tokens=5,
            model_id="test-model",
            updated_at=self.now,
        )
        self.memory = MemoryDocument(
            user_key=self.user_key,
            memory_text="장기투자를 선호함",
            last_reviewed_turn_id=self.first.turn_id,
            updated_at=self.now,
        )

    def turn(
        self, seconds: int, user_message: str, assistant_message: str
    ) -> CompletedTurn:
        created_at = self.now + timedelta(seconds=seconds)
        return CompletedTurn(
            user_key=self.user_key,
            session_id=self.session_id,
            turn_id=new_turn_id(created_at),
            user_message=user_message,
            assistant_message=assistant_message,
            created_at=created_at,
            expires_at=int((created_at + timedelta(days=30)).timestamp()),
        )

    def assemble(self, counter, **overrides) -> AssembledPromptContext:
        values = {
            "user_key": self.user_key,
            "session_id": self.session_id,
            "system_prompt": "PIA system policy",
            "memory": self.memory,
            "conversation": ConversationContext(
                summary=self.summary,
                turns=(self.first, self.second),
            ),
            "current_user_message": "현재 질문",
            "token_budget": ModelTokenBudget(10_000, 1_000),
        }
        values.update(overrides)
        return PromptContextAssembler(counter).assemble(**values)

    def test_current_tool_request_and_result_follow_user_input(self) -> None:
        result = self.assemble(
            lambda parts: sum(len(part.content) for part in parts),
            tool_observations=(
                ToolObservation("search", '{"query":"news"}', "cited answer"),
            ),
        )
        self.assertEqual(
            (
                PromptContextKind.CURRENT_USER,
                PromptContextKind.TOOL_REQUEST,
                PromptContextKind.TOOL_RESULT,
            ),
            tuple(part.kind for part in result.parts[-3:]),
        )
        self.assertTrue(
            all(part.trust is PromptTrust.UNTRUSTED_DATA for part in result.parts[-3:])
        )
        self.assertEqual(self.user_key, result.user_key)

    def test_full_context_has_exact_order_content_and_trust(self) -> None:
        counted = []

        def count(parts):
            counted.append(parts)
            return 123

        result = self.assemble(count)
        self.assertEqual(
            (
                PromptContextKind.SYSTEM,
                PromptContextKind.MEMORY,
                PromptContextKind.SUMMARY,
                PromptContextKind.USER_TURN,
                PromptContextKind.ASSISTANT_TURN,
                PromptContextKind.USER_TURN,
                PromptContextKind.ASSISTANT_TURN,
                PromptContextKind.CURRENT_USER,
            ),
            tuple(part.kind for part in result.parts),
        )
        self.assertEqual(
            (
                "PIA system policy",
                "장기투자를 선호함",
                "이전 대화 요약",
                "첫 질문",
                "첫 답변",
                "둘째 질문",
                "둘째 답변",
                "현재 질문",
            ),
            tuple(part.content for part in result.parts),
        )
        self.assertEqual(PromptTrust.TRUSTED_INSTRUCTION, result.parts[0].trust)
        self.assertTrue(
            all(part.trust is PromptTrust.UNTRUSTED_DATA for part in result.parts[1:])
        )
        self.assertEqual((result.parts,), tuple(counted))
        self.assertEqual(123, result.estimated_input_tokens)
        self.assertEqual(9_000, result.input_budget)
        self.assertFalse(hasattr(result, "context_limit"))
        self.assertFalse(hasattr(result, "reserved_response_tokens"))

    def test_optional_empty_context_is_omitted(self) -> None:
        empty_memory = MemoryDocument(
            self.user_key,
            "",
            self.first.turn_id,
            self.now,
        )
        result = self.assemble(
            lambda parts: 2,
            memory=empty_memory,
            conversation=ConversationContext(summary=None, turns=()),
        )
        self.assertEqual(
            (PromptContextKind.SYSTEM, PromptContextKind.CURRENT_USER),
            tuple(part.kind for part in result.parts),
        )

    def test_identity_mismatches_are_rejected_before_counting(self) -> None:
        calls = []

        def count(parts):
            calls.append(parts)
            return 1

        wrong_memory = MemoryDocument(
            "another-user",
            self.memory.memory_text,
            self.memory.last_reviewed_turn_id,
            self.now,
        )
        wrong_summary_user = RollingSummary(
            "another-user",
            self.session_id,
            "summary",
            self.summary.through_turn_id,
            1,
            "model",
            self.now,
        )
        wrong_summary_session = RollingSummary(
            self.user_key,
            "another-session",
            "summary",
            self.summary.through_turn_id,
            1,
            "model",
            self.now,
        )
        wrong_turn_user = CompletedTurn(
            "another-user",
            self.session_id,
            self.first.turn_id,
            "question",
            "answer",
            self.first.created_at,
            self.first.expires_at,
        )
        wrong_turn_session = CompletedTurn(
            self.user_key,
            "another-session",
            self.first.turn_id,
            "question",
            "answer",
            self.first.created_at,
            self.first.expires_at,
        )
        cases = (
            {"memory": wrong_memory},
            {"conversation": ConversationContext(wrong_summary_user, (self.first,))},
            {"conversation": ConversationContext(wrong_summary_session, (self.first,))},
            {"conversation": ConversationContext(None, (wrong_turn_user,))},
            {"conversation": ConversationContext(None, (wrong_turn_session,))},
        )
        for overrides in cases:
            with self.subTest(overrides=overrides):
                with self.assertRaises(PromptContextValidationError):
                    self.assemble(count, **overrides)
        self.assertEqual([], calls)

    def test_turn_order_and_summary_overlap_are_rejected(self) -> None:
        with self.assertRaisesRegex(
            PromptContextValidationError, "strictly increasing"
        ):
            self.assemble(
                lambda parts: 1,
                conversation=ConversationContext(
                    summary=None,
                    turns=(self.second, self.first),
                ),
            )

        overlapping_summary = RollingSummary(
            self.user_key,
            self.session_id,
            "summary",
            self.second.turn_id,
            1,
            "model",
            self.now,
        )
        with self.assertRaisesRegex(PromptContextValidationError, "overlaps"):
            self.assemble(
                lambda parts: 1,
                conversation=ConversationContext(
                    summary=overlapping_summary,
                    turns=(self.first, self.second),
                ),
            )

    def test_exact_budget_succeeds_and_overflow_contains_counts_only(self) -> None:
        exact = self.assemble(
            lambda parts: 90,
            token_budget=ModelTokenBudget(100, 10),
        )
        self.assertEqual(90, exact.estimated_input_tokens)
        self.assertEqual(90, exact.input_budget)

        with self.assertRaises(ContextBudgetExceeded) as raised:
            self.assemble(
                lambda parts: 91,
                token_budget=ModelTokenBudget(100, 10),
            )
        error = raised.exception
        self.assertEqual(91, error.required_input_tokens)
        self.assertEqual(90, error.input_budget)
        self.assertEqual(ModelTokenBudget(100, 10), error.token_budget)
        self.assertNotIn("PIA system policy", str(error))
        self.assertNotIn("현재 질문", str(error))

    def test_limits_required_text_and_counter_results_are_validated(self) -> None:
        invalid_calls = (
            {"system_prompt": ""},
            {"current_user_message": "  "},
            {"token_budget": None},
        )
        for overrides in invalid_calls:
            with self.subTest(overrides=overrides):
                with self.assertRaises(PromptContextValidationError):
                    self.assemble(lambda parts: 1, **overrides)

        for result in (-1, 1.5, True):
            with self.subTest(counter_result=result):
                with self.assertRaisesRegex(
                    PromptContextValidationError, "non-negative integer"
                ):
                    self.assemble(lambda parts, value=result: value)

        with self.assertRaisesRegex(ValueError, "must be callable"):
            PromptContextAssembler(None)

        for values in ((0, 1), (100, 0), (100, 100), (True, 1), (100, True)):
            with self.subTest(token_budget=values):
                with self.assertRaises(ValueError):
                    ModelTokenBudget(*values)

    def test_counter_failure_propagates_and_budget_default_is_shared(self) -> None:
        def fail(parts):
            raise RuntimeError("counter unavailable")

        with self.assertRaisesRegex(RuntimeError, "counter unavailable"):
            self.assemble(fail)

        token_budget = ModelTokenBudget(5_000)
        self.assertEqual(DEFAULT_MAX_RESPONSE_TOKENS, token_budget.response_tokens)
        default_reserve = PromptContextAssembler(lambda parts: 1).assemble(
            user_key=self.user_key,
            session_id=self.session_id,
            system_prompt="PIA system policy",
            memory=None,
            conversation=ConversationContext(summary=None, turns=()),
            current_user_message="현재 질문",
            token_budget=token_budget,
        )
        self.assertEqual(
            5_000 - DEFAULT_MAX_RESPONSE_TOKENS, default_reserve.input_budget
        )
        self.assertEqual(904, default_reserve.input_budget)


if __name__ == "__main__":
    unittest.main()
