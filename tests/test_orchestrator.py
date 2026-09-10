from __future__ import annotations

import asyncio
import unittest
from datetime import UTC, datetime, timedelta

from pia_harness import (
    MESSAGE_SEPARATOR,
    ActiveSession,
    ConversationContext,
    ConversationOrchestrator,
    GeneratedAnswer,
    MemoryAction,
    MemoryDocument,
    ModelTokenBudget,
    MemoryReviewResult,
    MemoryReviewStatus,
    OrchestratorStatus,
    PromptContextAssembler,
)


class FakeStore:
    def __init__(self) -> None:
        self.sessions: dict[str, ActiveSession] = {}
        self.turns = []
        self.memories = {}
        self.fail_append = False
        self.fail_replace = False
        self.reset_count = 0

    def get_or_create_active_session(self, user_key, *, now=None):
        session = self.sessions.get(user_key)
        if session is None:
            session = ActiveSession(user_key, f"session-{user_key}-0", now)
            self.sessions[user_key] = session
        return session

    def get_memory(self, user_key):
        return self.memories.get(user_key)

    def replace_memory(self, memory, *, expected_last_reviewed_turn_id):
        if self.fail_replace:
            return False
        current = self.memories.get(memory.user_key)
        current_boundary = None if current is None else current.last_reviewed_turn_id
        if current_boundary != expected_last_reviewed_turn_id:
            return False
        self.memories[memory.user_key] = memory
        return True

    def load_context(self, *, user_key, session_id, now=None):
        return ConversationContext(
            summary=None,
            turns=tuple(
                turn
                for turn in self.turns
                if turn.user_key == user_key and turn.session_id == session_id
            ),
        )

    def load_unreviewed_turns(
        self, *, user_key, session_id, after_turn_id, now=None
    ):
        return tuple(
            turn
            for turn in self.turns
            if turn.user_key == user_key
            and turn.session_id == session_id
            and (after_turn_id is None or turn.turn_id > after_turn_id)
        )

    def append_completed_turn(self, **values):
        if self.fail_append:
            self.fail_append = False
            raise RuntimeError("append failed")
        from pia_harness import CompletedTurn

        turn = CompletedTurn(
            expires_at=int(values["created_at"].timestamp()) + 30 * 86400,
            **values,
        )
        self.turns.append(turn)
        return turn

    def reset_active_session(self, *, user_key, expected_session_id, now=None):
        self.reset_count += 1
        replacement = ActiveSession(
            user_key,
            f"session-{user_key}-{self.reset_count}",
            now,
        )
        self.sessions[user_key] = replacement
        return replacement


class FakeMemoryReviewer:
    def __init__(self) -> None:
        self.explicit_inputs = []
        self.calls = []
        self.fail = False
        self.force_changes = ()

    def review_if_due(self, **values):
        self.calls.append("revisit")
        if self.fail:
            raise RuntimeError("memory failed")
        return None

    def force_review(self, **values):
        self.calls.append("force")
        if self.fail:
            raise RuntimeError("memory failed")
        if self.force_changes:
            return MemoryReviewResult(
                MemoryReviewStatus.REPLACED,
                MemoryDocument("user", "memory", "0000000000000-old", values["now"]),
                self.force_changes,
            )
        return None

    def review_explicit_input(self, **values):
        self.calls.append("explicit")
        current = values["current_input"]
        self.explicit_inputs.append((current, values["allow_clear"]))
        if self.fail:
            raise RuntimeError("memory failed")
        return MemoryReviewResult(
            MemoryReviewStatus.REPLACED,
            MemoryDocument(
                current.user_key,
                "memory",
                current.turn_id,
                values["now"],
            ),
            (f"remembered:{current.user_message}",),
        )


class FakeCompactor:
    def __init__(self) -> None:
        self.due = False
        self.progress = True
        self.calls = []

    def should_compact(self, **values):
        self.calls.append(("check", values))
        return self.due

    def compact_after_response(self, **values):
        self.calls.append(("compact", values))
        return object() if self.progress else None


class ConversationOrchestratorTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)
        self.store = FakeStore()
        self.memory = FakeMemoryReviewer()
        self.compactor = FakeCompactor()
        self.delivered = []
        self.token_budget = ModelTokenBudget(1_000, 100)

    def orchestrator(
        self,
        generate,
        *,
        counter=None,
        deliver=None,
        failure_notice="memory update failed",
    ):
        counter = counter or (lambda parts: sum(len(part.content) for part in parts))

        async def default_deliver(user_key, text):
            self.delivered.append((user_key, text))

        return ConversationOrchestrator(
            store=self.store,
            assembler=PromptContextAssembler(counter),
            memory_reviewer=self.memory,
            compactor=self.compactor,
            generate_answer=generate,
            deliver=deliver or default_deliver,
            system_prompt="system",
            token_budget=self.token_budget,
            model_id="model",
            explicit_memory_failure_notice=failure_notice,
        )

    async def test_explicit_memory_failure_notice_is_required_and_validated(self) -> None:
        async def generate(context):
            return GeneratedAnswer("answer", "model", 10)

        with self.assertRaises(ValueError):
            self.orchestrator(generate, failure_notice=None)
        with self.assertRaises(ValueError):
            self.orchestrator(generate, failure_notice="   ")

    async def test_new_message_interrupts_and_only_combined_answer_commits(self) -> None:
        first_started = asyncio.Event()
        cancelled = asyncio.Event()
        seen = []

        async def generate(context):
            current = context.parts[-1].content
            seen.append(current)
            if current == "A":
                first_started.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    cancelled.set()
                    raise
            return GeneratedAnswer(f"answer:{current}", "model", 10)

        orchestrator = self.orchestrator(generate)
        first = asyncio.create_task(
            orchestrator.submit(user_key="user", message="A", accepted_at=self.now)
        )
        await first_started.wait()
        second = asyncio.create_task(
            orchestrator.submit(user_key="user", message="B", accepted_at=self.now)
        )
        first_result, second_result = await asyncio.gather(first, second)

        self.assertEqual(OrchestratorStatus.SUPERSEDED, first_result.status)
        self.assertEqual(OrchestratorStatus.DELIVERED, second_result.status)
        self.assertTrue(cancelled.is_set())
        combined = f"A{MESSAGE_SEPARATOR}B"
        self.assertEqual(["A", combined], seen)
        self.assertEqual(combined, self.store.turns[0].user_message)
        self.assertEqual(1, len(self.delivered))

    async def test_late_cancelled_result_cannot_deliver(self) -> None:
        first_started = asyncio.Event()
        allow_late = asyncio.Event()
        generation_tasks = []

        async def generate(context):
            generation_tasks.append(asyncio.current_task())
            current = context.parts[-1].content
            if current == "A":
                first_started.set()
                try:
                    await allow_late.wait()
                except asyncio.CancelledError:
                    await allow_late.wait()
                return GeneratedAnswer("late", "model", 10)
            allow_late.set()
            return GeneratedAnswer("current", "model", 10)

        orchestrator = self.orchestrator(generate)
        first = asyncio.create_task(
            orchestrator.submit(user_key="user", message="A", accepted_at=self.now)
        )
        await first_started.wait()
        second = asyncio.create_task(
            orchestrator.submit(user_key="user", message="B", accepted_at=self.now)
        )
        first_result, second_result = await asyncio.gather(first, second)
        # Join the superseded generation itself rather than yielding once, so the
        # provider that ignored cancellation runs all the way to its commit
        # attempt and generation ownership is what stops it.
        await asyncio.gather(*generation_tasks, return_exceptions=True)

        self.assertEqual(OrchestratorStatus.SUPERSEDED, first_result.status)
        self.assertEqual(OrchestratorStatus.DELIVERED, second_result.status)
        self.assertEqual([("user", "current")], self.delivered)
        self.assertEqual(1, len(self.store.turns))
        self.assertEqual("current", self.store.turns[0].assistant_message)

    async def test_message_during_delivery_waits_for_a_new_generation(self) -> None:
        delivering = asyncio.Event()
        release_delivery = asyncio.Event()
        generated = []

        async def generate(context):
            current = context.parts[-1].content
            generated.append(current)
            return GeneratedAnswer(f"answer:{current}", "model", 10)

        async def deliver(user_key, text):
            self.delivered.append((user_key, text))
            if len(self.delivered) == 1:
                delivering.set()
                await release_delivery.wait()

        orchestrator = self.orchestrator(generate, deliver=deliver)
        first = asyncio.create_task(
            orchestrator.submit(user_key="user", message="A", accepted_at=self.now)
        )
        await delivering.wait()
        second = asyncio.create_task(
            orchestrator.submit(user_key="user", message="B", accepted_at=self.now)
        )
        await asyncio.sleep(0)
        self.assertFalse(second.done())
        release_delivery.set()
        first_result, second_result = await asyncio.gather(first, second)

        self.assertEqual(OrchestratorStatus.DELIVERED, first_result.status)
        self.assertEqual(OrchestratorStatus.DELIVERED, second_result.status)
        self.assertEqual(["A", "B"], generated)
        self.assertEqual(["A", "B"], [turn.user_message for turn in self.store.turns])

    async def test_newest_explicit_input_reviews_the_combined_text(self) -> None:
        first_started = asyncio.Event()

        async def generate(context):
            current = context.parts[-1].content
            if current == "A":
                first_started.set()
                await asyncio.Event().wait()
            return GeneratedAnswer(
                "answer", "model", 10, memory_action=MemoryAction.UPDATE
            )

        orchestrator = self.orchestrator(generate)
        first = asyncio.create_task(
            orchestrator.submit(user_key="user", message="A", accepted_at=self.now)
        )
        await first_started.wait()
        second = asyncio.create_task(
            orchestrator.submit(
                user_key="user",
                message="B",
                accepted_at=self.now,
            )
        )
        first_result, second_result = await asyncio.gather(first, second)

        combined = f"A{MESSAGE_SEPARATOR}B"
        self.assertEqual(OrchestratorStatus.SUPERSEDED, first_result.status)
        self.assertEqual(OrchestratorStatus.DELIVERED, second_result.status)
        self.assertEqual(combined, self.memory.explicit_inputs[0][0].user_message)
        self.assertFalse(self.memory.explicit_inputs[0][1])
        self.assertEqual(["explicit"], self.memory.calls)
        self.assertEqual(combined, self.store.turns[0].user_message)
        self.assertIn(f"- remembered:{combined}", second_result.final_text)

    async def test_generated_forget_allows_clear_without_duplicate_revisit(self) -> None:
        async def generate(context):
            return GeneratedAnswer(
                "answer", "model", 10, memory_action=MemoryAction.FORGET
            )

        result = await self.orchestrator(generate).submit(
            user_key="user", message="forget this", accepted_at=self.now
        )

        self.assertEqual(OrchestratorStatus.DELIVERED, result.status)
        self.assertEqual(["explicit"], self.memory.calls)
        self.assertTrue(self.memory.explicit_inputs[0][1])

    async def test_delete_all_requires_preceding_delivered_request(self) -> None:
        self.store.memories["user"] = MemoryDocument(
            "user", "remembered", "0000000000000-old", self.now
        )

        async def generate(context):
            confirmed = context.parts[-1].content == "yes"
            return GeneratedAnswer(
                "confirm" if not confirmed else "cleared",
                "model",
                10,
                memory_action=MemoryAction.DELETE_ALL,
                delete_all_confirmed=confirmed,
            )

        orchestrator = self.orchestrator(generate)
        requested = await orchestrator.submit(
            user_key="user", message="delete everything", accepted_at=self.now
        )
        self.assertEqual(OrchestratorStatus.DELIVERED, requested.status)
        self.assertEqual("remembered", self.store.memories["user"].memory_text)
        self.assertTrue(
            orchestrator._states["user"].delete_all_confirmation_pending
        )

        confirmed = await orchestrator.submit(
            user_key="user",
            message="yes",
            accepted_at=self.now + timedelta(seconds=1),
        )
        self.assertEqual(OrchestratorStatus.DELIVERED, confirmed.status)
        cleared = self.store.memories["user"]
        self.assertEqual("", cleared.memory_text)
        self.assertEqual(confirmed.turn_id, cleared.last_reviewed_turn_id)
        self.assertEqual(
            (),
            self.store.load_unreviewed_turns(
                user_key="user",
                session_id="session-user-0",
                after_turn_id=cleared.last_reviewed_turn_id,
            ),
        )
        self.assertNotIn("user", orchestrator._states)

    async def test_delete_all_confirmation_without_pending_marker_is_not_honored(self) -> None:
        self.store.memories["user"] = MemoryDocument(
            "user", "remembered", "0000000000000-old", self.now
        )

        async def generate(context):
            return GeneratedAnswer(
                "cleared",
                "model",
                10,
                memory_action=MemoryAction.DELETE_ALL,
                delete_all_confirmed=True,
            )

        orchestrator = self.orchestrator(
            generate, failure_notice="삭제를 확인하지 못했어요."
        )
        result = await orchestrator.submit(
            user_key="user", message="yes", accepted_at=self.now
        )

        self.assertEqual(OrchestratorStatus.DELIVERED, result.status)
        self.assertEqual("remembered", self.store.memories["user"].memory_text)
        self.assertTrue(
            orchestrator._states["user"].delete_all_confirmation_pending
        )
        # The answer said "cleared", so a silent no-op would leave the user
        # believing Memory was deleted.
        self.assertTrue(result.memory_failed)
        self.assertEqual("cleared\n\n- 삭제를 확인하지 못했어요.", result.final_text)

    async def test_failure_notice_survives_automatic_change_summaries(self) -> None:
        self.memory.fail = False
        self.compactor.due = True
        self.memory.force_changes = ("첫째", "둘째", "셋째")

        async def generate(context):
            return GeneratedAnswer(
                "answer", "model", 10, memory_action=MemoryAction.UPDATE
            )

        class FailingExplicit(FakeMemoryReviewer):
            def review_explicit_input(self, **values):
                self.calls.append("explicit")
                raise RuntimeError("memory failed")

        self.memory = FailingExplicit()
        self.memory.force_changes = ("첫째", "둘째", "셋째")
        result = await self.orchestrator(
            generate, failure_notice="기억에 반영하지 못했어요."
        ).submit(user_key="user", message="remember", accepted_at=self.now)

        self.assertEqual(OrchestratorStatus.DELIVERED, result.status)
        self.assertTrue(result.memory_failed)
        # The explicit failure must not be pushed out of the three-item budget by
        # summaries an automatic pre-Compaction Review added afterwards.
        self.assertIn("기억에 반영하지 못했어요.", result.final_text)

    async def test_explicit_memory_failure_appends_caller_notice(self) -> None:
        self.memory.fail = True

        async def generate(context):
            return GeneratedAnswer(
                "answer", "model", 10, memory_action=MemoryAction.UPDATE
            )

        result = await self.orchestrator(
            generate, failure_notice="기억에 반영하지 못했어요."
        ).submit(user_key="user", message="remember", accepted_at=self.now)

        self.assertEqual(OrchestratorStatus.DELIVERED, result.status)
        self.assertTrue(result.memory_failed)
        self.assertEqual(
            "answer\n\n- 기억에 반영하지 못했어요.", result.final_text
        )

    async def test_failure_notice_is_never_dropped_for_a_full_change_budget(self) -> None:
        # A rejected confirmation runs the ordinary revisit Review, which can fill
        # the three-item budget on its own and would otherwise hide the failure of
        # the deletion the user just asked for.
        class BusyRevisit(FakeMemoryReviewer):
            def review_if_due(self, **values):
                self.calls.append("revisit")
                return MemoryReviewResult(
                    MemoryReviewStatus.REPLACED,
                    MemoryDocument("user", "memory", "0000000000000-old", values["request_at"]),
                    ("첫째", "둘째", "셋째"),
                )

        self.memory = BusyRevisit()
        self.store.memories["user"] = MemoryDocument(
            "user", "remembered", "0000000000000-old", self.now
        )

        async def generate(context):
            return GeneratedAnswer(
                "cleared",
                "model",
                10,
                memory_action=MemoryAction.DELETE_ALL,
                delete_all_confirmed=True,
            )

        result = await self.orchestrator(
            generate, failure_notice="삭제를 확인하지 못했어요."
        ).submit(user_key="user", message="yes", accepted_at=self.now)

        self.assertEqual(OrchestratorStatus.DELIVERED, result.status)
        self.assertTrue(result.memory_failed)
        self.assertEqual("remembered", self.store.memories["user"].memory_text)
        self.assertIn("삭제를 확인하지 못했어요.", result.final_text)
        self.assertEqual(3, result.final_text.count("\n- "))

    async def test_stale_explicit_review_reports_failure_like_an_exception(self) -> None:
        # A lost compare-and-set writes nothing, so it must reach the caller and the
        # user exactly as a raised reviewer failure does.
        class StaleExplicit(FakeMemoryReviewer):
            def review_explicit_input(self, **values):
                self.calls.append("explicit")
                return MemoryReviewResult(MemoryReviewStatus.STALE, None)

        self.memory = StaleExplicit()

        async def generate(context):
            return GeneratedAnswer(
                "answer", "model", 10, memory_action=MemoryAction.UPDATE
            )

        result = await self.orchestrator(
            generate, failure_notice="기억에 반영하지 못했어요."
        ).submit(user_key="user", message="remember", accepted_at=self.now)

        self.assertEqual(OrchestratorStatus.DELIVERED, result.status)
        self.assertTrue(result.memory_failed)
        self.assertEqual(
            "answer\n\n- 기억에 반영하지 못했어요.", result.final_text
        )
        self.assertEqual(["explicit"], self.memory.calls)

    async def test_invalid_generated_memory_action_fails_before_commit(self) -> None:
        async def generate(context):
            return GeneratedAnswer("answer", "model", 10, memory_action="UPDATE")

        result = await self.orchestrator(generate).submit(
            user_key="user", message="remember", accepted_at=self.now
        )

        self.assertEqual(OrchestratorStatus.GENERATION_FAILED, result.status)
        self.assertEqual([], self.memory.calls)
        self.assertEqual([], self.store.turns)

    async def test_overflow_compacts_once_and_stops_without_progress(self) -> None:
        generated = []

        async def generate(context):
            generated.append(context)
            return GeneratedAnswer("answer", "model", 10)

        def counter(parts):
            return 901 if not self.compactor.calls else 1

        orchestrator = self.orchestrator(generate, counter=counter)
        result = await orchestrator.submit(
            user_key="user", message="question", accepted_at=self.now
        )
        compact_calls = [call for call in self.compactor.calls if call[0] == "compact"]
        self.assertEqual(OrchestratorStatus.DELIVERED, result.status)
        self.assertEqual(1, len(compact_calls))
        self.assertEqual(901, compact_calls[0][1]["estimated_context_tokens"])
        self.assertIsNone(compact_calls[0][1]["usage"])
        self.assertIs(self.token_budget, compact_calls[0][1]["token_budget"])
        self.assertEqual(1, len(generated))

        self.compactor = FakeCompactor()
        self.compactor.progress = False
        orchestrator = self.orchestrator(generate, counter=lambda parts: 901)
        result = await orchestrator.submit(
            user_key="other", message="question", accepted_at=self.now
        )
        self.assertEqual(OrchestratorStatus.CONTEXT_OVERFLOW, result.status)
        self.assertEqual(1, len([c for c in self.compactor.calls if c[0] == "compact"]))

    async def test_overflow_does_not_strand_later_messages(self) -> None:
        # The overflowing batch is deterministically unassemblable, so keeping it
        # pending would make every later message for that user fail the same way.
        async def generate(assembled):
            return GeneratedAnswer("answer", "model", 10)

        orchestrator = self.orchestrator(generate)
        self.compactor.progress = False
        overflowed = await orchestrator.submit(
            user_key="member",
            message="x" * 2_000,
            accepted_at=self.now,
        )
        self.assertEqual(OrchestratorStatus.CONTEXT_OVERFLOW, overflowed.status)

        followed = await orchestrator.submit(
            user_key="member",
            message="짧은 질문",
            accepted_at=self.now + timedelta(seconds=1),
        )
        self.assertEqual(OrchestratorStatus.DELIVERED, followed.status)
        self.assertEqual(1, len(self.store.turns))
        self.assertEqual("짧은 질문", self.store.turns[0].user_message)

    async def test_delivery_and_persistence_failures_have_simple_boundaries(self) -> None:
        attempts = []

        async def generate(context):
            attempts.append(context.parts[-1].content)
            return GeneratedAnswer("answer", "model", 10)

        delivery_calls = 0

        async def fail_once(user_key, text):
            nonlocal delivery_calls
            delivery_calls += 1
            if delivery_calls == 1:
                raise RuntimeError("delivery failed")
            self.delivered.append((user_key, text))

        orchestrator = self.orchestrator(generate, deliver=fail_once)
        failed = await orchestrator.submit(
            user_key="user", message="A", accepted_at=self.now
        )
        self.assertEqual(OrchestratorStatus.DELIVERY_FAILED, failed.status)
        self.assertEqual([], self.store.turns)

        recovered = await orchestrator.submit(
            user_key="user", message="B", accepted_at=self.now
        )
        self.assertEqual(OrchestratorStatus.DELIVERED, recovered.status)
        self.assertEqual(f"A{MESSAGE_SEPARATOR}B", attempts[-1])

        self.store.fail_append = True
        persistence_failed = await orchestrator.submit(
            user_key="user", message="C", accepted_at=self.now
        )
        self.assertEqual(
            OrchestratorStatus.PERSISTENCE_FAILED, persistence_failed.status
        )
        after_failure = await orchestrator.submit(
            user_key="user", message="D", accepted_at=self.now
        )
        self.assertEqual(OrchestratorStatus.DELIVERED, after_failure.status)
        self.assertEqual("D", attempts[-1])

    async def test_reset_supersedes_generation_and_preserves_future_use(self) -> None:
        started = asyncio.Event()

        async def generate(context):
            if context.parts[-1].content == "A":
                started.set()
                await asyncio.Event().wait()
            return GeneratedAnswer("answer", "model", 10)

        orchestrator = self.orchestrator(generate)
        pending = asyncio.create_task(
            orchestrator.submit(user_key="user", message="A", accepted_at=self.now)
        )
        await started.wait()
        reset_result = await orchestrator.reset(user_key="user", now=self.now)
        self.assertEqual(OrchestratorStatus.SUPERSEDED, (await pending).status)
        self.assertEqual("session-user-1", reset_result.session.session_id)

        result = await orchestrator.submit(
            user_key="user", message="B", accepted_at=self.now
        )
        self.assertEqual(OrchestratorStatus.DELIVERED, result.status)
        self.assertEqual("session-user-1", self.store.turns[-1].session_id)

    async def test_reset_waits_for_commit_and_supersedes_queued_input(self) -> None:
        delivering = asyncio.Event()
        release_delivery = asyncio.Event()

        async def generate(context):
            return GeneratedAnswer("answer", "model", 10)

        async def deliver(user_key, text):
            delivering.set()
            await release_delivery.wait()

        orchestrator = self.orchestrator(generate, deliver=deliver)
        first = asyncio.create_task(
            orchestrator.submit(user_key="user", message="A", accepted_at=self.now)
        )
        await delivering.wait()
        resetting = asyncio.create_task(orchestrator.reset(user_key="user", now=self.now))
        await asyncio.sleep(0.01)
        queued = asyncio.create_task(
            orchestrator.submit(user_key="user", message="B", accepted_at=self.now)
        )
        self.assertEqual(OrchestratorStatus.SUPERSEDED, (await queued).status)
        self.assertFalse(resetting.done())

        release_delivery.set()
        self.assertEqual(OrchestratorStatus.DELIVERED, (await first).status)
        reset_result = await resetting
        self.assertEqual("session-user-1", reset_result.session.session_id)
        self.assertEqual(["A"], [turn.user_message for turn in self.store.turns])

    async def test_different_users_generate_concurrently(self) -> None:
        both_started = asyncio.Event()
        started = set()

        async def generate(context):
            started.add(context.parts[-1].content)
            if len(started) == 2:
                both_started.set()
            await both_started.wait()
            return GeneratedAnswer("answer", "model", 10)

        orchestrator = self.orchestrator(generate)
        first = asyncio.create_task(
            orchestrator.submit(user_key="first", message="A", accepted_at=self.now)
        )
        second = asyncio.create_task(
            orchestrator.submit(user_key="second", message="B", accepted_at=self.now)
        )
        results = await asyncio.gather(first, second)
        self.assertEqual(
            [OrchestratorStatus.DELIVERED, OrchestratorStatus.DELIVERED],
            [result.status for result in results],
        )


if __name__ == "__main__":
    unittest.main()
