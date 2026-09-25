from __future__ import annotations

import asyncio
import unittest
from datetime import UTC, datetime, timedelta

from pia_harness import (
    MESSAGE_SEPARATOR,
    ActiveSession,
    ConversationAbandoned,
    ConversationContext,
    ConversationOrchestrator,
    ConversationProgress,
    GeneratedAnswer,
    MemoryAction,
    NextActionDecision,
    MemoryDocument,
    MemoryReviewResult,
    MemoryReviewStatus,
    ModelTokenBudget,
    OrchestratorStatus,
    PromptContextAssembler,
    ToolCall,
    ToolResult,
    ReadToolDefinition,
    ReadToolResult,
    new_turn_id,
)


class FakeStore:
    def __init__(self) -> None:
        self.sessions: dict[str, ActiveSession] = {}
        self.turns = []
        self.memories = {}
        self.fail_append = False
        self.fail_replace = False
        self.abandon_on: str | None = None

    def _check(self, operation: str) -> None:
        if self.abandon_on == operation:
            self.abandon_on = None
            raise ConversationAbandoned(operation)

    def get_or_create_active_session(self, user_key, *, now=None):
        self._check("session")
        session = self.sessions.get(user_key)
        if session is None:
            session = ActiveSession(user_key, f"session-{user_key}-0", now)
            self.sessions[user_key] = session
        return session

    def get_memory(self, user_key):
        self._check("get_memory")
        return self.memories.get(user_key)

    def replace_memory(self, memory, *, expected_last_reviewed_turn_id):
        self._check("replace_memory")
        if self.fail_replace:
            return False
        current = self.memories.get(memory.user_key)
        current_boundary = None if current is None else current.last_reviewed_turn_id
        if current_boundary != expected_last_reviewed_turn_id:
            return False
        self.memories[memory.user_key] = memory
        return True

    def load_context(self, *, user_key, session_id, now=None):
        self._check("load_context")
        return ConversationContext(
            summary=None,
            turns=tuple(
                turn
                for turn in self.turns
                if turn.user_key == user_key and turn.session_id == session_id
            ),
        )

    def load_unreviewed_turns(self, *, user_key, session_id, after_turn_id, now=None):
        return tuple(
            turn
            for turn in self.turns
            if turn.user_key == user_key
            and turn.session_id == session_id
            and (after_turn_id is None or turn.turn_id > after_turn_id)
        )

    def append_completed_turn(self, **values):
        self._check("append")
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


class FakeMemoryReviewer:
    def __init__(self) -> None:
        self.explicit_inputs = []
        self.calls = []
        self.fail = False
        self.force_changes = ()
        self.abandon_on: str | None = None

    def _check(self, operation: str) -> None:
        if self.abandon_on == operation:
            self.abandon_on = None
            raise ConversationAbandoned(operation)

    def review_if_due(self, **values):
        self.calls.append("revisit")
        self._check("revisit")
        if self.fail:
            raise RuntimeError("memory failed")
        return None

    def force_review(self, **values):
        self.calls.append("force")
        self._check("force")
        if self.fail:
            raise RuntimeError("memory failed")
        if self.force_changes:
            return MemoryReviewResult(
                MemoryReviewStatus.REPLACED,
                MemoryDocument(
                    "user",
                    "memory",
                    new_turn_id(values["now"] - timedelta(seconds=1)),
                    values["now"],
                ),
                self.force_changes,
            )
        return None

    def review_explicit_input(self, **values):
        self.calls.append("explicit")
        self._check("explicit")
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
        self.abandon = False

    def should_compact(self, **values):
        self.calls.append(("check", values))
        return self.due

    def compact(self, **values):
        self.calls.append(("compact", values))
        if self.abandon:
            self.abandon = False
            raise ConversationAbandoned("compact")
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
        generate_with_progress=None,
        progress=None,
        execute_tool=None,
        read_tools=(),
        choose_next=None,
        build_tool_call=None,
        read_routing_timeout_seconds=120.0,
        memory_action=MemoryAction.NONE,
    ):
        counter = counter or (lambda parts: sum(len(part.content) for part in parts))

        async def default_deliver(user_key, text):
            self.delivered.append((user_key, text))

        async def default_choose_next(context, tools):
            return NextActionDecision("answer", memory_action)

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
            generate_answer_with_progress=generate_with_progress,
            progress=progress,
            execute_tool=execute_tool,
            read_tools=read_tools,
            choose_next=choose_next or default_choose_next,
            build_tool_call=build_tool_call,
            read_routing_timeout_seconds=read_routing_timeout_seconds,
        )

    async def test_explicit_memory_failure_notice_is_required_and_validated(
        self,
    ) -> None:
        async def generate(context):
            return GeneratedAnswer("answer", "model", 10)

        with self.assertRaises(ValueError):
            self.orchestrator(generate, failure_notice=None)
        with self.assertRaises(ValueError):
            self.orchestrator(generate, failure_notice="   ")
        with self.assertRaises(ValueError):
            self.orchestrator(generate, generate_with_progress=generate)
        with self.assertRaises(ValueError):
            self.orchestrator(generate, progress=generate)

    async def test_progress_wraps_delivery_and_is_best_effort(self) -> None:
        ordered: list[tuple] = []

        async def generate(context):
            raise AssertionError("legacy generator must not run")

        async def generate_with_progress(context, report):
            await report(ConversationProgress.WEB_SEARCH_STARTED)
            return GeneratedAnswer("answer", "model", 10)

        async def progress(user_key, progress_id, event):
            ordered.append(("progress", user_key, progress_id, event))

        async def deliver(user_key, text):
            ordered.append(("deliver", user_key, text))

        result = await self.orchestrator(
            generate,
            deliver=deliver,
            generate_with_progress=generate_with_progress,
            progress=progress,
        ).submit(user_key="user", message="question", accepted_at=self.now)

        self.assertEqual(OrchestratorStatus.DELIVERED, result.status)
        self.assertEqual("progress", ordered[0][0])
        self.assertEqual("user", ordered[0][1])
        self.assertIsInstance(ordered[0][2], str)
        self.assertEqual(ConversationProgress.WEB_SEARCH_STARTED, ordered[0][3])
        self.assertEqual(("deliver", "user", "answer"), ordered[1])
        self.assertEqual(
            ("progress", "user", ordered[0][2], ConversationProgress.COMPLETE),
            ordered[2],
        )

        async def failing_progress(user_key, progress_id, event):
            raise RuntimeError("progress unavailable")

        result = await self.orchestrator(
            generate,
            generate_with_progress=generate_with_progress,
            progress=failing_progress,
        ).submit(user_key="other", message="question", accepted_at=self.now)
        self.assertEqual(OrchestratorStatus.DELIVERED, result.status)

    async def test_superseded_progress_is_completed_with_its_own_turn_id(self) -> None:
        first_started = asyncio.Event()
        events: list[tuple[str, ConversationProgress]] = []

        async def generate(context):
            raise AssertionError("legacy generator must not run")

        async def generate_with_progress(context, report):
            await report(ConversationProgress.WEB_SEARCH_STARTED)
            if context.parts[-1].content == "A":
                first_started.set()
                await asyncio.Event().wait()
            return GeneratedAnswer("answer", "model", 10)

        async def progress(user_key, progress_id, event):
            events.append((progress_id, event))

        orchestrator = self.orchestrator(
            generate,
            generate_with_progress=generate_with_progress,
            progress=progress,
        )
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
        progress_ids = {progress_id for progress_id, _ in events}
        self.assertEqual(2, len(progress_ids))
        for progress_id in progress_ids:
            self.assertIn(
                (progress_id, ConversationProgress.WEB_SEARCH_STARTED), events
            )
            self.assertIn((progress_id, ConversationProgress.COMPLETE), events)

    async def test_new_message_interrupts_and_only_combined_answer_commits(
        self,
    ) -> None:
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
            return GeneratedAnswer("answer", "model", 10)

        orchestrator = self.orchestrator(generate, memory_action=MemoryAction.UPDATE)
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

    async def test_generated_forget_allows_clear_without_duplicate_revisit(
        self,
    ) -> None:
        async def generate(context):
            return GeneratedAnswer("answer", "model", 10)

        result = await self.orchestrator(
            generate, memory_action=MemoryAction.FORGET
        ).submit(user_key="user", message="forget this", accepted_at=self.now)

        self.assertEqual(OrchestratorStatus.DELIVERED, result.status)
        self.assertEqual(["explicit"], self.memory.calls)
        self.assertTrue(self.memory.explicit_inputs[0][1])

    async def test_failure_notice_survives_automatic_change_summaries(self) -> None:
        self.memory.fail = False
        self.compactor.due = True
        self.memory.force_changes = ("첫째", "둘째", "셋째")

        async def generate(context):
            return GeneratedAnswer("answer", "model", 10)

        class FailingExplicit(FakeMemoryReviewer):
            def review_explicit_input(self, **values):
                self.calls.append("explicit")
                raise RuntimeError("memory failed")

        self.memory = FailingExplicit()
        self.memory.force_changes = ("첫째", "둘째", "셋째")
        result = await self.orchestrator(
            generate,
            failure_notice="기억에 반영하지 못했어요.",
            memory_action=MemoryAction.UPDATE,
        ).submit(user_key="user", message="remember", accepted_at=self.now)

        self.assertEqual(OrchestratorStatus.DELIVERED, result.status)
        self.assertTrue(result.memory_failed)
        # The explicit failure must not be pushed out of the three-item budget by
        # summaries an automatic pre-Compaction Review added afterwards.
        self.assertIn("기억에 반영하지 못했어요.", result.final_text)

    async def test_explicit_memory_failure_appends_caller_notice(self) -> None:
        self.memory.fail = True

        async def generate(context):
            return GeneratedAnswer("answer", "model", 10)

        result = await self.orchestrator(
            generate,
            failure_notice="기억에 반영하지 못했어요.",
            memory_action=MemoryAction.UPDATE,
        ).submit(user_key="user", message="remember", accepted_at=self.now)

        self.assertEqual(OrchestratorStatus.DELIVERED, result.status)
        self.assertTrue(result.memory_failed)
        self.assertEqual("answer\n\n- 기억에 반영하지 못했어요.", result.final_text)

    async def test_stale_explicit_review_reports_failure_like_an_exception(
        self,
    ) -> None:
        # A lost compare-and-set writes nothing, so it must reach the caller and the
        # user exactly as a raised reviewer failure does.
        class StaleExplicit(FakeMemoryReviewer):
            def review_explicit_input(self, **values):
                self.calls.append("explicit")
                return MemoryReviewResult(MemoryReviewStatus.STALE, None)

        self.memory = StaleExplicit()

        async def generate(context):
            return GeneratedAnswer("answer", "model", 10)

        result = await self.orchestrator(
            generate,
            failure_notice="기억에 반영하지 못했어요.",
            memory_action=MemoryAction.UPDATE,
        ).submit(user_key="user", message="remember", accepted_at=self.now)

        self.assertEqual(OrchestratorStatus.DELIVERED, result.status)
        self.assertTrue(result.memory_failed)
        self.assertEqual("answer\n\n- 기억에 반영하지 못했어요.", result.final_text)
        self.assertEqual(["explicit"], self.memory.calls)

    async def test_invalid_jev_memory_action_fails_before_commit(self) -> None:
        async def generate(context):
            raise AssertionError("invalid Jev result must stop before generation")

        async def choose_next(context, tools):
            return NextActionDecision("answer", "UPDATE")

        result = await self.orchestrator(generate, choose_next=choose_next).submit(
            user_key="user", message="remember", accepted_at=self.now
        )

        self.assertEqual(OrchestratorStatus.GENERATION_FAILED, result.status)
        self.assertEqual([], self.memory.calls)
        self.assertEqual([], self.store.turns)

    async def test_invalid_generated_web_search_usage_fails_before_commit(self) -> None:
        for index, value in enumerate((True, -1, "1")):
            with self.subTest(value=value):

                async def generate(context, value=value):
                    return GeneratedAnswer(
                        "answer", "model", 10, web_search_requests=value
                    )

                result = await self.orchestrator(generate).submit(
                    user_key=f"user-{index}",
                    message="question",
                    accepted_at=self.now,
                )

                self.assertEqual(OrchestratorStatus.GENERATION_FAILED, result.status)
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

    async def test_delivery_and_persistence_failures_have_simple_boundaries(
        self,
    ) -> None:
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

    async def test_store_abandonment_clears_batch_and_allows_future_use(self) -> None:
        seen = []

        async def generate(context):
            seen.append(context.parts[-1].content)
            return GeneratedAnswer("answer", "model", 10)

        orchestrator = self.orchestrator(generate)
        self.store.abandon_on = "session"
        abandoned = await orchestrator.submit(
            user_key="user", message="A", accepted_at=self.now
        )
        self.assertEqual(OrchestratorStatus.ABANDONED, abandoned.status)
        self.assertEqual([], seen)
        self.assertEqual([], self.delivered)

        recovered = await orchestrator.submit(
            user_key="user", message="B", accepted_at=self.now
        )
        self.assertEqual(OrchestratorStatus.DELIVERED, recovered.status)
        self.assertEqual(["B"], seen)

    async def test_memory_and_compaction_abandonment_do_not_deliver(self) -> None:
        async def explicit(context):
            return GeneratedAnswer("answer", "model", 10)

        orchestrator = self.orchestrator(explicit, memory_action=MemoryAction.UPDATE)
        self.memory.abandon_on = "explicit"
        result = await orchestrator.submit(
            user_key="user", message="remember", accepted_at=self.now
        )
        self.assertEqual(OrchestratorStatus.ABANDONED, result.status)
        self.assertEqual([], self.delivered)
        self.assertEqual([], self.store.turns)

        async def ordinary(context):
            return GeneratedAnswer("answer", "model", 90)

        self.compactor.due = True
        self.compactor.abandon = True
        result = await self.orchestrator(ordinary).submit(
            user_key="other", message="compact", accepted_at=self.now
        )
        self.assertEqual(OrchestratorStatus.ABANDONED, result.status)
        self.assertEqual([], self.delivered)

    async def test_due_and_overflow_abandonment_stop_before_delivery(self) -> None:
        async def generate(context):
            return GeneratedAnswer("answer", "model", 10)

        self.memory.abandon_on = "revisit"
        due = await self.orchestrator(generate).submit(
            user_key="due", message="question", accepted_at=self.now
        )
        self.assertEqual(OrchestratorStatus.ABANDONED, due.status)

        self.memory = FakeMemoryReviewer()
        self.memory.abandon_on = "force"
        forced = await self.orchestrator(generate, counter=lambda parts: 901).submit(
            user_key="overflow-review", message="question", accepted_at=self.now
        )
        self.assertEqual(OrchestratorStatus.ABANDONED, forced.status)
        self.assertFalse(any(call[0] == "compact" for call in self.compactor.calls))

        self.memory = FakeMemoryReviewer()
        self.compactor = FakeCompactor()
        self.compactor.abandon = True
        compacted = await self.orchestrator(generate, counter=lambda parts: 901).submit(
            user_key="overflow-compact", message="question", accepted_at=self.now
        )
        self.assertEqual(OrchestratorStatus.ABANDONED, compacted.status)
        self.assertEqual([], self.delivered)

    async def test_delivery_and_post_delivery_append_abandonment_clear_batch(
        self,
    ) -> None:
        seen = []
        delivery_calls = 0

        async def generate(context):
            seen.append(context.parts[-1].content)
            return GeneratedAnswer("answer", "model", 10)

        async def abandon_once(user_key, text):
            nonlocal delivery_calls
            delivery_calls += 1
            if delivery_calls == 1:
                raise ConversationAbandoned("inactive")
            self.delivered.append((user_key, text))

        orchestrator = self.orchestrator(generate, deliver=abandon_once)
        first = await orchestrator.submit(
            user_key="user", message="A", accepted_at=self.now
        )
        self.assertEqual(OrchestratorStatus.ABANDONED, first.status)
        second = await orchestrator.submit(
            user_key="user", message="B", accepted_at=self.now
        )
        self.assertEqual(OrchestratorStatus.DELIVERED, second.status)
        self.assertEqual(["A", "B"], seen)

        self.store.abandon_on = "append"
        after_delivery = await orchestrator.submit(
            user_key="user", message="C", accepted_at=self.now
        )
        self.assertEqual(OrchestratorStatus.ABANDONED, after_delivery.status)
        final = await orchestrator.submit(
            user_key="user", message="D", accepted_at=self.now
        )
        self.assertEqual(OrchestratorStatus.DELIVERED, final.status)
        self.assertEqual("D", seen[-1])

    async def test_tool_runs_after_generation_and_persists_only_host_text(self) -> None:
        calls = []

        async def generate(context):
            return GeneratedAnswer(
                "model draft is not delivered",
                "model",
                10,
                tool_call=ToolCall("quote", '{"symbol":"005930"}'),
            )

        async def execute_tool(user_key, call, inputs):
            calls.append((user_key, call, inputs))
            return ToolResult("current price: 70000", "[tool request]", "[tool answer]")

        orchestrator = self.orchestrator(generate, execute_tool=execute_tool)
        result = await orchestrator.submit(
            user_key="user", message="price for 005930", accepted_at=self.now
        )

        self.assertEqual(OrchestratorStatus.DELIVERED, result.status)
        self.assertEqual([("user", "current price: 70000")], self.delivered)
        self.assertEqual(1, len(calls))
        self.assertEqual("quote", calls[0][1].name)
        self.assertEqual("price for 005930", calls[0][2][0].message)
        self.assertEqual("[tool request]", self.store.turns[0].user_message)
        self.assertEqual("[tool answer]", self.store.turns[0].assistant_message)

    async def test_tool_failure_clears_pending_without_delivery(self) -> None:
        calls = 0

        async def generate(context):
            if context.parts[-1].content == "price":
                return GeneratedAnswer(
                    "unused", "model", 10, tool_call=ToolCall("quote", "{}")
                )
            return GeneratedAnswer("ordinary", "model", 10)

        async def execute_tool(user_key, call, inputs):
            nonlocal calls
            calls += 1
            raise RuntimeError("synthetic tool failure")

        orchestrator = self.orchestrator(generate, execute_tool=execute_tool)
        failed = await orchestrator.submit(
            user_key="user", message="price", accepted_at=self.now
        )
        self.assertEqual(OrchestratorStatus.TOOL_FAILED, failed.status)
        self.assertEqual([], self.delivered)
        self.assertEqual([], self.store.turns)

        following = await orchestrator.submit(
            user_key="user",
            message="hello",
            accepted_at=self.now + timedelta(seconds=1),
        )
        self.assertEqual(OrchestratorStatus.DELIVERED, following.status)
        self.assertEqual(1, calls)
        self.assertEqual("hello", self.store.turns[0].user_message)

    async def test_tool_requires_executor_and_excludes_search(self) -> None:
        async def generate(context):
            return GeneratedAnswer(
                "unused", "model", 10, tool_call=ToolCall("quote", "{}")
            )

        no_executor = self.orchestrator(generate)
        missing = await no_executor.submit(
            user_key="user", message="price", accepted_at=self.now
        )
        self.assertEqual(OrchestratorStatus.GENERATION_FAILED, missing.status)

        async def invalid_generate(context):
            return GeneratedAnswer(
                "unused",
                "model",
                10,
                web_search_requests=1,
                tool_call=ToolCall("quote", "{}"),
            )

        async def execute_tool(user_key, call, inputs):
            raise AssertionError("invalid answer must not execute")

        invalid = self.orchestrator(invalid_generate, execute_tool=execute_tool)
        rejected = await invalid.submit(
            user_key="other", message="price", accepted_at=self.now
        )
        self.assertEqual(OrchestratorStatus.GENERATION_FAILED, rejected.status)

    async def test_superseded_model_tool_call_never_executes(self) -> None:
        started = asyncio.Event()
        release = asyncio.Event()
        generation_tasks = []
        calls = []

        async def generate(context):
            generation_tasks.append(asyncio.current_task())
            if context.parts[-1].content == "first":
                started.set()
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    await release.wait()
                return GeneratedAnswer(
                    "late", "model", 10, tool_call=ToolCall("quote", "{}")
                )
            release.set()
            return GeneratedAnswer("current", "model", 10)

        async def execute_tool(user_key, call, inputs):
            calls.append(call)
            return ToolResult("result", "[request]", "[result]")

        orchestrator = self.orchestrator(generate, execute_tool=execute_tool)
        first = asyncio.create_task(
            orchestrator.submit(user_key="user", message="first", accepted_at=self.now)
        )
        await started.wait()
        second = asyncio.create_task(
            orchestrator.submit(user_key="user", message="second", accepted_at=self.now)
        )
        first_result, second_result = await asyncio.gather(first, second)
        await asyncio.gather(*generation_tasks, return_exceptions=True)

        self.assertEqual(OrchestratorStatus.SUPERSEDED, first_result.status)
        self.assertEqual(OrchestratorStatus.DELIVERED, second_result.status)
        self.assertEqual([], calls)
        self.assertEqual([("user", "current")], self.delivered)

    async def test_repeated_external_cancel_does_not_abandon_owned_tool_commit(
        self,
    ) -> None:
        tool_started = asyncio.Event()
        release_tool = asyncio.Event()

        async def generate(context):
            return GeneratedAnswer(
                "unused", "model", 10, tool_call=ToolCall("quote", "{}")
            )

        async def execute_tool(user_key, call, inputs):
            tool_started.set()
            await release_tool.wait()
            return ToolResult("quote returned", "[request]", "[result]")

        orchestrator = self.orchestrator(generate, execute_tool=execute_tool)
        submission = asyncio.create_task(
            orchestrator.submit(user_key="user", message="quote", accepted_at=self.now)
        )
        await tool_started.wait()
        active_task = orchestrator._states["user"].active_task
        active_task.cancel()
        await asyncio.sleep(0)
        active_task.cancel()
        await asyncio.sleep(0)
        self.assertFalse(active_task.done())
        release_tool.set()
        result = await submission

        self.assertEqual(OrchestratorStatus.DELIVERED, result.status)
        self.assertEqual([("user", "quote returned")], self.delivered)
        self.assertEqual("[result]", self.store.turns[0].assistant_message)

    async def test_jev_search_result_returns_to_router_and_persists(self) -> None:
        decisions = []
        queries = []
        request = "시장 뉴스 찾아주고 배당주 선호를 기억해줘"

        async def generate(context):
            raise AssertionError("a verified Search answer needs no second LLM")

        async def choose_next(context, tools):
            decisions.append(tuple(part.kind.value for part in context.parts))
            if len(decisions) == 1:
                return NextActionDecision("search")
            return NextActionDecision("answer", MemoryAction.UPDATE)

        async def build_call(context, tool):
            return ToolCall(tool.name, '{"query":"market news"}')

        async def execute_search(user_key, call, inputs):
            queries.append((user_key, call, inputs))
            return ReadToolResult(
                "News with source https://example.com",
                "News [source](https://example.com)",
            )

        tool = ReadToolDefinition(
            "search",
            "Find sourced public information",
            {"type": "object"},
            execute_search,
        )
        result = await self.orchestrator(
            generate,
            read_tools=(tool,),
            choose_next=choose_next,
            build_tool_call=build_call,
        ).submit(user_key="member", message=request, accepted_at=self.now)

        self.assertEqual(OrchestratorStatus.DELIVERED, result.status)
        self.assertEqual(
            [
                (
                    "member",
                    f"News [source](https://example.com)\n\n- remembered:{request}",
                )
            ],
            self.delivered,
        )
        self.assertEqual(1, len(queries))
        self.assertEqual("TOOL_RESULT", decisions[1][-1])
        self.assertIn(
            "Tool request (data): search", self.store.turns[0].assistant_message
        )
        self.assertIn("News with source", self.store.turns[0].assistant_message)
        self.assertEqual(request, self.memory.explicit_inputs[0][0].user_message)

    async def test_compaction_happens_before_answer_generation(self) -> None:
        self.compactor.due = True

        async def generate(context):
            self.assertEqual(
                1, len([call for call in self.compactor.calls if call[0] == "compact"])
            )
            return GeneratedAnswer("answer", "model", 10)

        result = await self.orchestrator(generate).submit(
            user_key="user", message="question", accepted_at=self.now
        )
        self.assertEqual(OrchestratorStatus.DELIVERED, result.status)
        self.assertEqual(
            1, len([call for call in self.compactor.calls if call[0] == "compact"])
        )

    async def test_superseded_read_tool_cannot_commit(self) -> None:
        tool_started = asyncio.Event()

        async def generate(context):
            return GeneratedAnswer("current answer", "model", 10)

        async def choose_next(context, tools):
            current = next(
                part.content
                for part in context.parts
                if part.kind.value == "CURRENT_USER"
            )
            return NextActionDecision("answer" if "new" in current else "search")

        async def build_call(context, tool):
            return ToolCall(tool.name, "{}")

        async def execute_search(user_key, call, inputs):
            tool_started.set()
            await asyncio.Event().wait()
            return ReadToolResult("stale result")

        tool = ReadToolDefinition(
            "search", "Search", {"type": "object"}, execute_search
        )
        orchestrator = self.orchestrator(
            generate,
            read_tools=(tool,),
            choose_next=choose_next,
            build_tool_call=build_call,
        )
        first = asyncio.create_task(
            orchestrator.submit(user_key="user", message="old", accepted_at=self.now)
        )
        await tool_started.wait()
        second = asyncio.create_task(
            orchestrator.submit(user_key="user", message="new", accepted_at=self.now)
        )
        first_result, second_result = await asyncio.gather(first, second)
        self.assertEqual(OrchestratorStatus.SUPERSEDED, first_result.status)
        self.assertEqual(OrchestratorStatus.DELIVERED, second_result.status)
        self.assertEqual([("user", "current answer")], self.delivered)
        self.assertEqual(1, len(self.store.turns))

    async def test_read_routing_deadline_stops_repeated_choices(self) -> None:
        choices = []

        async def generate(context):
            raise AssertionError("routing did not select answer")

        async def choose_next(context, tools):
            choices.append("search")
            return NextActionDecision("search")

        async def build_call(context, tool):
            return ToolCall(tool.name, "{}")

        async def execute_search(user_key, call, inputs):
            await asyncio.sleep(0)
            return ReadToolResult("short observation")

        tool = ReadToolDefinition(
            "search", "Search", {"type": "object"}, execute_search
        )
        orchestrator = self.orchestrator(
            generate,
            read_tools=(tool,),
            choose_next=choose_next,
            build_tool_call=build_call,
            read_routing_timeout_seconds=0.01,
        )
        result = await orchestrator.submit(
            user_key="user", message="question", accepted_at=self.now
        )
        self.assertEqual(OrchestratorStatus.GENERATION_FAILED, result.status)
        self.assertGreater(len(choices), 1)
        self.assertEqual([], self.delivered)
        self.assertEqual([], self.store.turns)
        self.assertNotIn("user", orchestrator._states)

    async def test_no_tool_timeout_keeps_the_existing_pending_behavior(self) -> None:
        async def generate(context):
            raise TimeoutError("model unavailable")

        orchestrator = self.orchestrator(generate)
        result = await orchestrator.submit(
            user_key="user", message="question", accepted_at=self.now
        )
        self.assertEqual(OrchestratorStatus.GENERATION_FAILED, result.status)
        self.assertEqual(1, len(orchestrator._states["user"].pending))


if __name__ == "__main__":
    unittest.main()
