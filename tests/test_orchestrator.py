from __future__ import annotations

import asyncio
import itertools
import json
import unittest
from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import patch

import pia_harness.orchestrator as orchestrator_module
from pia_harness import (
    CONFIRMATION_EXPIRED_NOTICE,
    MESSAGE_SEPARATOR,
    ActiveSession,
    ConversationAbandoned,
    ConversationContext,
    ConversationOrchestrator,
    ConversationStep,
    ExecutionToolDefinition,
    GeneratedAnswer,
    MemoryAction,
    MemoryDocument,
    MemoryReviewResult,
    MemoryReviewStatus,
    ModelReply,
    ModelTokenBudget,
    OrchestratorStatus,
    PreparationResult,
    PreparedAction,
    PromptContextAssembler,
    ReadToolDefinition,
    ReadToolResult,
    ToolCall,
    ToolLink,
    TurnTooLargeError,
)
from pia_harness.orchestrator import RESEARCH_LIMIT_NOTICE

GOAL = "기사 핵심 내용"
EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {"urls": {"type": "array"}, "goal": {"type": "string"}},
    "required": ["urls", "goal"],
}


def _message(context):
    """The current user message without the received-time line."""
    content = next(
        part.content for part in context.parts if part.kind.value == "CURRENT_USER"
    )
    return content.split("\n", 1)[1] if content.startswith("[Received ") else content


def _results(context):
    """This Turn's tool results; earlier Turns' come before the current request."""
    kinds = [part.kind.value for part in context.parts]
    start = kinds.index("CURRENT_USER")
    return [
        part.content
        for part in context.parts[start:]
        if part.kind.value == "TOOL_RESULT"
    ]


def _notes(context):
    return [part.content for part in context.parts if part.kind.value == "HARNESS_NOTE"]


class FakeStore:
    def __init__(self) -> None:
        self.sessions: dict[str, ActiveSession] = {}
        self.turns = []
        self.memories = {}
        self.fail_append = False
        self.fail_replace = False
        # Refuse Turns that carry tool records, as a store at its size limit does.
        self.refuse_tool_records = False
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
        if self.refuse_tool_records and values.get("tool_observations"):
            raise TurnTooLargeError("turn too large")
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
        self.abandon_on: str | None = None

    def _check(self, operation: str) -> None:
        if self.abandon_on == operation:
            self.abandon_on = None
            raise ConversationAbandoned(operation)

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
        count=None,
        deliver=None,
        failure_notice="memory update failed",
        progress=None,
        read_tools=(),
        execution_tools=(),
        research_timeout_seconds=120.0,
        memory_action=MemoryAction.NONE,
    ):
        """generate returns a GeneratedAnswer (the model answers at once) or a
        ModelReply. memory_action makes the model call the memory tool first."""
        counter = counter or (lambda parts: sum(len(part.content) for part in parts))

        async def default_deliver(user_key, text):
            self.delivered.append((user_key, text))

        async def reply(context):
            if memory_action is not MemoryAction.NONE and not _results(context):
                arguments = json.dumps({"action": memory_action.value.lower()})
                return ModelReply(tool_calls=(ToolCall("memory", arguments, "m1"),))
            value = await generate(context)
            return value if isinstance(value, ModelReply) else ModelReply(answer=value)

        return ConversationOrchestrator(
            store=self.store,
            assembler=PromptContextAssembler(
                count or (lambda parts, tools: counter(parts))
            ),
            memory_reviewer=self.memory,
            compactor=self.compactor,
            generate_reply=reply,
            deliver=deliver or default_deliver,
            system_prompt="system",
            token_budget=self.token_budget,
            model_id="model",
            explicit_memory_failure_notice=failure_notice,
            progress=progress,
            read_tools=read_tools,
            execution_tools=execution_tools,
            research_timeout_seconds=research_timeout_seconds,
        )

    @staticmethod
    def script(*steps, contexts=None):
        """A model that follows steps: a list of (tool, arguments) calls for one
        response, answer text, or a function of the context returning either."""
        remaining = iter(steps)
        ids = itertools.count(1)

        async def generate(context):
            if contexts is not None:
                contexts.append(context)
            step = next(remaining)
            if callable(step):
                step = step(context)
            if isinstance(step, str):
                return GeneratedAnswer(step, "model", 10)
            if isinstance(step, ModelReply):
                return step
            return ModelReply(
                tool_calls=tuple(
                    ToolCall(
                        name, json.dumps(arguments, ensure_ascii=False), f"c{next(ids)}"
                    )
                    for name, arguments in step
                )
            )

        return generate

    @staticmethod
    def search(results=None, queries=None, execute=None):
        results = list(results or ())

        async def default_execute(user_key, call, inputs):
            if queries is not None:
                queries.append(json.loads(call.arguments_json)["query"])
            return results.pop(0) if results else ReadToolResult("searched")

        return ReadToolDefinition(
            "search",
            "Search news",
            execute or default_execute,
            arguments_schema={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        )

    @staticmethod
    def extractor(read=None, execute=None):
        async def default_execute(user_key, call, inputs):
            arguments = json.loads(call.arguments_json)
            if read is not None:
                read.append(arguments["urls"])
            return ReadToolResult(
                "\n".join(f"- {url}: read. 본문 {url}" for url in arguments["urls"])
            )

        return ReadToolDefinition(
            "web_extract",
            "Read pages",
            execute or default_execute,
            arguments_schema=EXTRACT_SCHEMA,
            url_argument="urls",
        )

    def order_tool(self, *, executed=None, execute=None, action=True):
        executed = executed if executed is not None else []

        async def prepare(user_key, call):
            if not action:
                return PreparationResult("Order not prepared. Missing: quantity.")
            name = json.loads(call.arguments_json).get("name", "Samsung")
            return PreparationResult(
                "Order prepared and waiting for confirmation.",
                PreparedAction(
                    f"{name} 10 shares limit buy",
                    f"Buy 10 {name} shares at 285,500 KRW?",
                    call.arguments_json,
                ),
            )

        async def default_execute(user_key, prepared):
            executed.append(prepared.arguments_json)
            return f"Order accepted: {json.loads(prepared.arguments_json)['name']}."

        return ExecutionToolDefinition(
            "order",
            "Prepare an order",
            {"type": "object"},
            prepare,
            execute or default_execute,
        )

    @staticmethod
    def confirm_waiting(context):
        """confirm every action the harness lists as waiting."""
        note = _notes(context)[0]
        ids = [line[2:].split(":", 1)[0] for line in note.splitlines()[1:]]
        return [("confirm", {"action_ids": ids})]

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
            self.orchestrator(generate, progress="not callable")

    async def test_new_message_interrupts_and_only_combined_answer_commits(
        self,
    ) -> None:
        first_started = asyncio.Event()
        cancelled = asyncio.Event()
        seen = []

        async def generate(context):
            current = _message(context)
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
            current = _message(context)
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
            current = _message(context)
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
            current = _message(context)
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
            attempts.append(_message(context))
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
            started.add(_message(context))
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
            seen.append(_message(context))
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

    async def test_compaction_abandonment_stops_before_delivery(self) -> None:
        async def generate(context):
            return GeneratedAnswer("answer", "model", 10)

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
            seen.append(_message(context))
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

    async def test_repeated_external_cancel_does_not_abandon_owned_commit(
        self,
    ) -> None:
        delivery_started = asyncio.Event()
        release_delivery = asyncio.Event()

        async def generate(context):
            return GeneratedAnswer("answer", "model", 10)

        async def deliver(user_key, text):
            delivery_started.set()
            await release_delivery.wait()
            self.delivered.append((user_key, text))

        orchestrator = self.orchestrator(generate, deliver=deliver)
        submission = asyncio.create_task(
            orchestrator.submit(
                user_key="user", message="question", accepted_at=self.now
            )
        )
        await delivery_started.wait()
        active_task = orchestrator._states["user"].active_task
        active_task.cancel()
        await asyncio.sleep(0)
        active_task.cancel()
        await asyncio.sleep(0)
        self.assertFalse(active_task.done())
        release_delivery.set()
        result = await submission

        self.assertEqual(OrchestratorStatus.DELIVERED, result.status)
        self.assertEqual([("user", "answer")], self.delivered)
        self.assertEqual(1, len(self.store.turns))

    async def test_code_and_spacing_are_left_as_written(self) -> None:
        delivered = []
        given = "https://fund.example/kodex"
        code = f"```python\nurl = '{given}'\nif  items[1]:\n    pass\n```"
        text = f"값은 `rows[2]` 기준  {given} 입니다 [7].\n\n{code}"

        async def generate(context):
            return GeneratedAnswer(text, "model", 10)

        async def deliver(user_key, text):
            delivered.append(text)

        await self.orchestrator(generate, deliver=deliver).submit(
            user_key="user", message=f"이거 봐줘 {given}", accepted_at=self.now
        )

        # Only prose links and stand-alone numbers change; code, indexes and
        # the answer's own spacing stay.
        self.assertEqual(
            [f"값은 `rows[2]` 기준  [1] 입니다.\n\n{code}\n\n출처\n[1] {given}"],
            delivered,
        )

    async def test_an_answer_of_only_removed_links_fails(self) -> None:
        async def generate(context):
            return GeneratedAnswer("<https://made.up/x> [1]", "model", 10)

        result = await self.orchestrator(generate).submit(
            user_key="user", message="질문", accepted_at=self.now
        )

        # Restoring the original would bring back what was removed.
        self.assertEqual(OrchestratorStatus.GENERATION_FAILED, result.status)

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
        # Still due after the answer, so it runs once more then.
        self.assertEqual(
            2, len([call for call in self.compactor.calls if call[0] == "compact"])
        )

    async def test_no_tool_timeout_keeps_the_existing_pending_behavior(self) -> None:
        async def generate(context):
            raise TimeoutError("model unavailable")

        orchestrator = self.orchestrator(generate)
        result = await orchestrator.submit(
            user_key="user", message="question", accepted_at=self.now
        )
        self.assertEqual(OrchestratorStatus.GENERATION_FAILED, result.status)
        self.assertEqual(1, len(orchestrator._states["user"].pending))

    async def test_the_prompt_carries_the_received_time_but_not_the_turn(self) -> None:
        contexts = []

        async def generate(context):
            contexts.append(context)
            return GeneratedAnswer("answer", "model", 10)

        await self.orchestrator(generate).submit(
            user_key="user", message="오늘 뉴스", accepted_at=self.now
        )

        current = next(
            part.content
            for part in contexts[0].parts
            if part.kind.value == "CURRENT_USER"
        )
        received = self.now.astimezone(timezone(timedelta(hours=9)))
        self.assertEqual(
            f"[Received {received:%Y-%m-%d %H:%M} KST. This is the current time, "
            "not the as-of date of any source.]\n오늘 뉴스",
            current,
        )
        self.assertEqual("오늘 뉴스", self.store.turns[0].user_message)

    # --- Progress -----------------------------------------------------------

    async def test_progress_reports_each_tool_call_then_none_after_delivery(
        self,
    ) -> None:
        ordered: list[tuple] = []
        url = "https://n.news.naver.com/a"

        async def progress(user_key, progress_id, step):
            ordered.append(("progress", user_key, progress_id, step))

        async def deliver(user_key, text):
            ordered.append(("deliver", user_key, text))

        result = await self.orchestrator(
            self.script(
                [("search", {"query": "삼성전자 주가"})],
                [("web_extract", {"urls": [url], "goal": GOAL})],
                "answer",
            ),
            deliver=deliver,
            progress=progress,
            read_tools=(
                self.search([ReadToolResult("News", (ToolLink("기사", url),))]),
                self.extractor(),
            ),
        ).submit(user_key="user", message="question", accepted_at=self.now)

        self.assertEqual(OrchestratorStatus.DELIVERED, result.status)
        progress_id = ordered[0][2]
        self.assertEqual(
            [
                (
                    "progress",
                    "user",
                    progress_id,
                    ConversationStep("search", '{"query": "삼성전자 주가"}'),
                ),
                (
                    "progress",
                    "user",
                    progress_id,
                    ConversationStep(
                        "web_extract",
                        json.dumps({"urls": [url], "goal": GOAL}, ensure_ascii=False),
                    ),
                ),
                ("deliver", "user", "answer"),
                ("progress", "user", progress_id, None),
            ],
            ordered,
        )

    async def test_failing_progress_callback_does_not_stop_the_answer(self) -> None:
        async def failing_progress(user_key, progress_id, step):
            raise RuntimeError("progress unavailable")

        result = await self.orchestrator(
            self.script([("search", {"query": "q"})], "answer"),
            progress=failing_progress,
            read_tools=(self.search(),),
        ).submit(user_key="user", message="question", accepted_at=self.now)
        self.assertEqual(OrchestratorStatus.DELIVERED, result.status)

    async def test_stuck_progress_callback_does_not_hold_the_turn(self) -> None:
        async def stuck_progress(user_key, progress_id, step):
            await asyncio.Event().wait()

        orchestrator = self.orchestrator(
            self.script([("search", {"query": "q"})], "answer"),
            progress=stuck_progress,
            read_tools=(self.search(),),
        )
        with patch.object(orchestrator_module, "PROGRESS_TIMEOUT_SECONDS", 0.01):
            result = await asyncio.wait_for(
                orchestrator.submit(
                    user_key="user", message="question", accepted_at=self.now
                ),
                timeout=2,
            )
            await asyncio.sleep(0.05)
        still_running = [
            task
            for task in asyncio.all_tasks()
            if "_run_generation" in repr(task.get_coro())
        ]

        self.assertEqual(OrchestratorStatus.DELIVERED, result.status)
        self.assertEqual([("user", "answer")], self.delivered)
        self.assertEqual([], still_running)

    async def test_model_failure_ends_progress_only_after_a_step(self) -> None:
        steps: list[ConversationStep | None] = []

        def fail_on(call_number):
            calls = 0

            async def generate(context):
                nonlocal calls
                calls += 1
                if calls == call_number:
                    raise RuntimeError("model unavailable")
                return ModelReply(
                    tool_calls=(ToolCall("search", '{"query":"q"}', "c1"),)
                )

            return generate

        async def progress(user_key, progress_id, step):
            steps.append(step)

        # The first model call fails: nothing was reported, so no end either.
        result = await self.orchestrator(
            fail_on(1), progress=progress, read_tools=(self.search(),)
        ).submit(user_key="user", message="question", accepted_at=self.now)
        self.assertEqual(OrchestratorStatus.GENERATION_FAILED, result.status)
        self.assertEqual([], steps)

        # It fails after one tool call: that step, then the end.
        result = await self.orchestrator(
            fail_on(2), progress=progress, read_tools=(self.search(),)
        ).submit(user_key="other", message="question", accepted_at=self.now)
        self.assertEqual(OrchestratorStatus.GENERATION_FAILED, result.status)
        self.assertEqual([ConversationStep("search", '{"query":"q"}'), None], steps)

    async def test_superseded_progress_is_ended_with_its_own_turn_id(self) -> None:
        first_started = asyncio.Event()
        events: list[tuple[str, ConversationStep | None]] = []

        async def generate(context):
            if _results(context):
                return GeneratedAnswer("answer", "model", 10)
            return ModelReply(tool_calls=(ToolCall("search", '{"query":"q"}', "c1"),))

        async def execute(user_key, call, inputs):
            if inputs[-1].message == "A":
                first_started.set()
                await asyncio.Event().wait()
            return ReadToolResult("searched")

        async def progress(user_key, progress_id, step):
            events.append((progress_id, step))

        orchestrator = self.orchestrator(
            generate, progress=progress, read_tools=(self.search(execute=execute),)
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
            self.assertIn((progress_id, None), events)

    # --- Memory tool ----------------------------------------------------------

    async def test_memory_tool_is_applied_after_the_answer(self) -> None:
        contexts = []
        result = await self.orchestrator(
            self.script([("memory", {"action": "update"})], "answer", contexts=contexts)
        ).submit(user_key="user", message="배당주 선호, 기억해줘", accepted_at=self.now)

        self.assertEqual(OrchestratorStatus.DELIVERED, result.status)
        self.assertEqual(["explicit"], self.memory.calls)
        self.assertFalse(self.memory.explicit_inputs[0][1])
        self.assertIn("Memory is changed from this request", _results(contexts[1])[0])
        # Both calls saw the memory tool.
        self.assertIn("memory", [tool.name for tool in contexts[0].tools])

    async def test_invalid_memory_action_changes_nothing(self) -> None:
        # A wrong value or a wrong JSON type is a bad call, reported to the model.
        for action in ("save", []):
            with self.subTest(action=action):
                contexts = []
                self.memory = FakeMemoryReviewer()
                result = await self.orchestrator(
                    self.script(
                        [("memory", {"action": action})], "answer", contexts=contexts
                    )
                ).submit(user_key="user", message="remember", accepted_at=self.now)

                self.assertEqual(OrchestratorStatus.DELIVERED, result.status)
                self.assertEqual([], self.memory.calls)
                self.assertIn('"update" or "forget"', _results(contexts[1])[0])

    # --- Reading ----------------------------------------------------------------

    async def test_search_links_are_listed_and_only_conversation_links_read(
        self,
    ) -> None:
        contexts = []
        read = []
        request = "삼성전자 왜 떨어져?"
        results = [
            ReadToolResult(
                "News search: 삼성전자 주가",
                (
                    ToolLink(
                        "외국인 순매도", "https://n.news.naver.com/a", "2026-09-24"
                    ),
                    ToolLink(
                        "실적 전망 하향", "https://n.news.naver.com/b", None, "요약"
                    ),
                ),
            )
        ]

        result = await self.orchestrator(
            self.script(
                [("search", {"query": "삼성전자 주가"})],
                [
                    (
                        "web_extract",
                        {
                            "urls": [
                                "https://n.news.naver.com/b",
                                "https://invented.example/x",
                            ],
                            "goal": GOAL,
                        },
                    )
                ],
                "answer from read bodies",
                contexts=contexts,
            ),
            read_tools=(self.search(results), self.extractor(read)),
        ).submit(user_key="member", message=request, accepted_at=self.now)

        self.assertEqual(OrchestratorStatus.DELIVERED, result.status)
        self.assertEqual([["https://n.news.naver.com/b"]], read)
        search_text, extract_text = _results(contexts[-1])
        self.assertIn(
            "[2026-09-24] 외국인 순매도 <https://n.news.naver.com/a>", search_text
        )
        self.assertIn("실적 전망 하향 — 요약 <https://n.news.naver.com/b>", search_text)
        self.assertIn("본문 https://n.news.naver.com/b", extract_text)
        self.assertIn(
            "- 1 requested link(s) did not appear in this conversation; not read.",
            extract_text,
        )
        # A rejected link is not echoed, or it would become readable next time.
        self.assertNotIn("invented.example", extract_text)
        # Only the request and final answer are stored; tool results stay in this Turn.
        self.assertNotIn("본문", self.store.turns[0].assistant_message)

    async def test_calls_and_results_are_paired_in_order(self) -> None:
        contexts = []

        async def execute(user_key, call, inputs):
            query = json.loads(call.arguments_json)["query"]
            # The first call finishes last; results still follow the call order.
            await asyncio.sleep(0.02 if query == "first" else 0)
            return ReadToolResult(f"result {query}")

        await self.orchestrator(
            self.script(
                [("search", {"query": "first"}), ("search", {"query": "second"})],
                "answer",
                contexts=contexts,
            ),
            read_tools=(self.search(execute=execute),),
        ).submit(user_key="user", message="question", accepted_at=self.now)

        tool_parts = [
            (part.kind.value, part.call_id, part.content)
            for part in contexts[1].parts
            if part.kind.value in ("TOOL_REQUEST", "TOOL_RESULT")
        ]
        self.assertEqual(
            [
                ("TOOL_REQUEST", "c1", '{"query": "first"}'),
                ("TOOL_REQUEST", "c2", '{"query": "second"}'),
                ("TOOL_RESULT", "c1", "result first"),
                ("TOOL_RESULT", "c2", "result second"),
            ],
            tool_parts,
        )

    async def test_repeated_search_lists_only_new_candidates(self) -> None:
        link = ToolLink("같은 기사", "https://n.news.naver.com/a")
        results = [
            ReadToolResult("first", (link,)),
            ReadToolResult(
                "second", (link, ToolLink("새 기사", "https://n.news.naver.com/b"))
            ),
            ReadToolResult("third", (link,)),
        ]
        contexts = []

        await self.orchestrator(
            self.script(
                [("search", {"query": "1"})],
                [("search", {"query": "2"})],
                [("search", {"query": "3"})],
                "answer",
                contexts=contexts,
            ),
            read_tools=(self.search(results),),
        ).submit(user_key="user", message="news", accepted_at=self.now)

        seen = _results(contexts[-1])
        self.assertIn("같은 기사 <https://n.news.naver.com/a>", seen[0])
        self.assertNotIn("같은 기사", seen[1])
        self.assertIn("새 기사 <https://n.news.naver.com/b>", seen[1])
        self.assertIn("No new candidates", seen[2])

    async def test_links_in_an_earlier_answer_can_be_read_next_turn(self) -> None:
        # Articles summarized in one Turn, "read them" in the next.
        read = []
        found = ReadToolResult(
            "News",
            (
                ToolLink("기사 1", "https://n.news.naver.com/a?sid=101"),
                ToolLink("기사 2", "https://n.news.naver.com/b"),
            ),
        )
        links = ["https://n.news.naver.com/a?sid=101", "https://n.news.naver.com/b"]
        orchestrator = self.orchestrator(
            self.script(
                [("search", {"query": "전력기기"})],
                f"요약입니다. <{links[0]}> <{links[1]}>",
                [("web_extract", {"urls": links, "goal": GOAL})],
                "본문 요약",
            ),
            read_tools=(self.search([found]), self.extractor(read)),
        )
        await orchestrator.submit(
            user_key="user", message="전력기기 이슈는?", accepted_at=self.now
        )
        result = await orchestrator.submit(
            user_key="user",
            message="기사들 본문 읽고 요약해줘",
            accepted_at=self.now + timedelta(minutes=1),
        )

        self.assertEqual(OrchestratorStatus.DELIVERED, result.status)
        self.assertEqual([links], read)

    async def test_memory_links_are_not_readable(self) -> None:
        read = []
        contexts = []
        self.store.memories["user"] = MemoryDocument(
            "user", "관심 링크 https://memory.example/a", None, self.now
        )

        await self.orchestrator(
            self.script(
                [
                    (
                        "web_extract",
                        {"urls": ["https://memory.example/a"], "goal": GOAL},
                    )
                ],
                "answer",
                contexts=contexts,
            ),
            read_tools=(self.extractor(read),),
        ).submit(user_key="user", message="그 링크 읽어줘", accepted_at=self.now)

        self.assertEqual([], read)
        self.assertIn("Not run: no new link to read.", _results(contexts[1])[0])

    async def test_a_link_is_read_once_a_turn(self) -> None:
        read = []
        contexts = []
        a, b = "https://example.com/a", "https://example.com/b"

        await self.orchestrator(
            self.script(
                # Two calls in one response ask for the same link and goal
                # (keys in another order): read once.
                [
                    ("web_extract", {"urls": [a], "goal": GOAL}),
                    ("web_extract", {"goal": GOAL, "urls": [a]}),
                ],
                [("web_extract", {"urls": [a, b], "goal": GOAL})],
                # Another goal may read the same page again.
                [("web_extract", {"urls": [a], "goal": "기준일"})],
                "answer",
                contexts=contexts,
            ),
            read_tools=(self.extractor(read),),
        ).submit(user_key="user", message=f"{a} {b} 비교해줘", accepted_at=self.now)

        self.assertEqual([[a], [b], [a]], read)
        first, second, third, fourth = _results(contexts[-1])
        self.assertIn("본문 https://example.com/a", first)
        self.assertIn("Not run: no new link to read.", second)
        self.assertIn(
            "- 1 requested link(s) were already read in this Turn; not read again.",
            third,
        )
        self.assertIn("본문 https://example.com/a", fourth)

    async def test_bad_calls_come_back_as_results(self) -> None:
        contexts = []
        orchestrator = self.orchestrator(
            self.script(
                [("search", {})],
                lambda context: ModelReply(
                    tool_calls=(ToolCall("search", "not json", "x1"),)
                ),
                [("nothing", {})],
                "answer",
                contexts=contexts,
            ),
            read_tools=(self.search(execute=self.never_run),),
        )
        result = await orchestrator.submit(
            user_key="user", message="뉴스", accepted_at=self.now
        )

        self.assertEqual(OrchestratorStatus.DELIVERED, result.status)
        self.assertEqual(
            [
                "Not run: missing query. Call again with them.",
                "Not run: the arguments were not a JSON object. Call again.",
                "Not run: there is no tool named nothing.",
            ],
            _results(contexts[-1]),
        )

    @staticmethod
    async def never_run(user_key, call, inputs):
        raise AssertionError("a bad call is not run")

    async def test_a_broken_read_tool_ends_the_turn(self) -> None:
        async def execute(user_key, call, inputs):
            raise AttributeError("bug")

        result = await self.orchestrator(
            self.script([("search", {"query": "q"})], "answer"),
            read_tools=(self.search(execute=execute),),
        ).submit(user_key="user", message="뉴스", accepted_at=self.now)

        # A code error is not an expected failure; the model would keep calling it.
        self.assertEqual(OrchestratorStatus.TOOL_FAILED, result.status)
        self.assertEqual([], self.delivered)

    async def test_superseded_read_tool_cannot_commit(self) -> None:
        tool_started = asyncio.Event()

        async def generate(context):
            if "new" in _message(context) or _results(context):
                return GeneratedAnswer("current answer", "model", 10)
            return ModelReply(tool_calls=(ToolCall("search", '{"query":"q"}', "c1"),))

        async def execute(user_key, call, inputs):
            tool_started.set()
            await asyncio.Event().wait()
            return ReadToolResult("stale result")

        orchestrator = self.orchestrator(
            generate, read_tools=(self.search(execute=execute),)
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

    # --- Limits ------------------------------------------------------------------

    async def test_research_deadline_answers_without_tools(self) -> None:
        contexts = []

        async def execute(user_key, call, inputs):
            await asyncio.sleep(1)
            return ReadToolResult("too late")

        result = await self.orchestrator(
            self.script(
                [("search", {"query": "q"})], "partial answer", contexts=contexts
            ),
            read_tools=(self.search(execute=execute),),
            research_timeout_seconds=0.05,
        ).submit(user_key="user", message="question", accepted_at=self.now)

        self.assertEqual(OrchestratorStatus.DELIVERED, result.status)
        final = contexts[-1]
        self.assertEqual((), final.tools)
        self.assertEqual(["Not finished: the research time ran out."], _results(final))
        self.assertEqual([RESEARCH_LIMIT_NOTICE], _notes(final))
        self.assertEqual([("user", "partial answer")], self.delivered)

    async def test_read_limit_is_checked_across_one_response(self) -> None:
        queries = []
        contexts = []

        with patch.object(orchestrator_module, "MAX_READ_TOOL_CALLS", 2):
            result = await self.orchestrator(
                self.script(
                    [("search", {"query": str(n)}) for n in range(3)],
                    "partial answer",
                    contexts=contexts,
                ),
                read_tools=(self.search(queries=queries),),
            ).submit(user_key="user", message="조사해줘", accepted_at=self.now)

        self.assertEqual(OrchestratorStatus.DELIVERED, result.status)
        self.assertEqual(["0", "1"], queries)
        final = contexts[-1]
        self.assertEqual("Not run: the research limit was reached.", _results(final)[2])
        self.assertEqual((), final.tools)
        self.assertEqual([RESEARCH_LIMIT_NOTICE], _notes(final))

    async def test_a_request_too_large_with_tools_is_answered_without_them(
        self,
    ) -> None:
        contexts = []
        self.compactor.progress = False

        def counter(parts, tools):
            return 901 if tools else 10

        orchestrator = self.orchestrator(
            self.script("answer", contexts=contexts), count=counter
        )
        result = await orchestrator.submit(
            user_key="user", message="question", accepted_at=self.now
        )

        self.assertEqual(OrchestratorStatus.DELIVERED, result.status)
        self.assertEqual((), contexts[0].tools)
        self.assertEqual([RESEARCH_LIMIT_NOTICE], _notes(contexts[0]))

    # --- Tool definitions -------------------------------------------------------

    async def test_tool_names_and_url_argument_are_checked(self) -> None:
        async def generate(context):
            return GeneratedAnswer("answer", "model", 10)

        tool = self.order_tool()
        for name in ("confirm", "memory", "bad name"):
            reserved = ExecutionToolDefinition(
                name, "x", {"type": "object"}, tool.prepare, tool.execute
            )
            with self.assertRaises(ValueError):
                self.orchestrator(generate, execution_tools=(reserved,))
        wrong_argument = ReadToolDefinition(
            "reader",
            "x",
            self.never_run,
            arguments_schema=EXTRACT_SCHEMA,
            url_argument="links",
        )
        with self.assertRaises(ValueError):
            self.orchestrator(generate, read_tools=(wrong_argument,))

    # --- Execution and confirmation --------------------------------------------

    async def test_an_order_waits_for_confirmation_in_the_next_turn(self) -> None:
        executed: list[str] = []
        contexts = []
        orchestrator = self.orchestrator(
            self.script(
                [("order", {"name": "Samsung"})],
                "주문을 준비했어요.",
                self.confirm_waiting,
                "주문했어요.",
                "다른 답",
                contexts=contexts,
            ),
            execution_tools=(self.order_tool(executed=executed),),
        )
        first = await orchestrator.submit(
            user_key="user", message="buy Samsung", accepted_at=self.now
        )
        self.assertEqual(OrchestratorStatus.DELIVERED, first.status)
        self.assertEqual([], executed)
        self.assertNotIn("confirm", [tool.name for tool in contexts[0].tools])
        # After a draft the model answers without tools.
        self.assertEqual((), contexts[1].tools)
        # The question is last and stored with the Turn.
        self.assertTrue(
            first.final_text.endswith("\n\nBuy 10 Samsung shares at 285,500 KRW?")
        )
        self.assertEqual(first.final_text, self.store.turns[-1].assistant_message)

        second = await orchestrator.submit(
            user_key="user", message="응", accepted_at=self.now + timedelta(minutes=1)
        )
        self.assertEqual(OrchestratorStatus.DELIVERED, second.status)
        self.assertIn("confirm", [tool.name for tool in contexts[2].tools])
        self.assertIn("Samsung 10 shares limit buy", _notes(contexts[2])[0])
        self.assertEqual(['{"name": "Samsung"}'], executed)
        self.assertEqual("주문했어요.", second.final_text)
        self.assertEqual("Order accepted: Samsung.", _results(contexts[3])[0])

        await orchestrator.submit(
            user_key="user", message="응", accepted_at=self.now + timedelta(minutes=2)
        )
        self.assertNotIn("confirm", [tool.name for tool in contexts[4].tools])
        self.assertEqual(1, len(executed))

    async def test_several_orders_are_confirmed_one_by_one(self) -> None:
        executed: list[str] = []

        def confirm_first(context):
            note = _notes(context)[0]
            first_id = note.splitlines()[1][2:].split(":", 1)[0]
            return [("confirm", {"action_ids": [first_id]})]

        orchestrator = self.orchestrator(
            self.script(
                [("order", {"name": "Samsung"}), ("order", {"name": "Hynix"})],
                "두 건을 준비했어요.",
                confirm_first,
                "삼성전자만 주문했어요.",
            ),
            execution_tools=(self.order_tool(executed=executed),),
        )
        first = await orchestrator.submit(
            user_key="user", message="삼성 하닉 10주씩", accepted_at=self.now
        )
        self.assertTrue(
            first.final_text.endswith(
                "Buy 10 Samsung shares at 285,500 KRW?\n\n"
                "Buy 10 Hynix shares at 285,500 KRW?"
            )
        )
        await orchestrator.submit(
            user_key="user",
            message="삼성만 해줘",
            accepted_at=self.now + timedelta(minutes=1),
        )
        self.assertEqual(['{"name": "Samsung"}'], executed)

    async def test_at_most_five_orders_are_prepared_at_once(self) -> None:
        contexts = []
        orchestrator = self.orchestrator(
            self.script(
                [("order", {"name": f"stock{n}"}) for n in range(6)],
                "answer",
                contexts=contexts,
            ),
            execution_tools=(self.order_tool(),),
        )
        await orchestrator.submit(user_key="user", message="buy", accepted_at=self.now)

        self.assertEqual(5, len(orchestrator._pending_actions["user"]))
        self.assertEqual("Not prepared: at most 5 at a time.", _results(contexts[1])[5])

    async def test_confirm_after_other_tool_results_executes_nothing(self) -> None:
        executed: list[str] = []
        contexts = []

        orchestrator = self.orchestrator(
            self.script(
                [("order", {"name": "Samsung"})],
                "준비했어요.",
                # A page read first could have told the model to confirm.
                [("search", {"query": "q"})],
                self.confirm_waiting,
                "확정하지 못했어요.",
                contexts=contexts,
            ),
            read_tools=(self.search(),),
            execution_tools=(self.order_tool(executed=executed),),
        )
        await orchestrator.submit(user_key="user", message="buy", accepted_at=self.now)
        result = await orchestrator.submit(
            user_key="user",
            message="뉴스 보고 판단해",
            accepted_at=self.now + timedelta(minutes=1),
        )

        self.assertEqual(OrchestratorStatus.DELIVERED, result.status)
        self.assertEqual([], executed)
        self.assertIn(
            "confirm works only as the first tool call", _results(contexts[-1])[1]
        )

    async def test_confirm_runs_alone_in_its_response(self) -> None:
        executed: list[str] = []
        contexts = []

        def confirm_and_search(context):
            return [("search", {"query": "q"}), *self.confirm_waiting(context)]

        orchestrator = self.orchestrator(
            self.script(
                [("order", {"name": "Samsung"})],
                "준비했어요.",
                confirm_and_search,
                "주문했어요.",
                contexts=contexts,
            ),
            read_tools=(self.search(execute=self.never_run),),
            execution_tools=(self.order_tool(executed=executed),),
        )
        await orchestrator.submit(user_key="user", message="buy", accepted_at=self.now)
        await orchestrator.submit(
            user_key="user", message="응", accepted_at=self.now + timedelta(minutes=1)
        )

        self.assertEqual(['{"name": "Samsung"}'], executed)
        search_result, confirm_result = _results(contexts[-1])
        self.assertIn("Not run: confirming comes first.", search_result)
        self.assertEqual("Order accepted: Samsung.", confirm_result)

    async def test_confirm_with_unknown_ids_executes_nothing(self) -> None:
        executed: list[str] = []
        contexts = []
        orchestrator = self.orchestrator(
            self.script(
                [("order", {"name": "Samsung"})],
                "준비했어요.",
                [("confirm", {"action_ids": ["a999"]})],
                "확정하지 못했어요.",
                contexts=contexts,
            ),
            execution_tools=(self.order_tool(executed=executed),),
        )
        await orchestrator.submit(user_key="user", message="buy", accepted_at=self.now)
        await orchestrator.submit(
            user_key="user", message="응", accepted_at=self.now + timedelta(minutes=1)
        )

        self.assertEqual([], executed)
        self.assertIn("unknown action IDs", _results(contexts[-1])[0])

    async def test_waiting_action_is_dropped_after_an_unrelated_turn(self) -> None:
        orchestrator = self.orchestrator(
            self.script([("order", {"name": "Samsung"})], "준비했어요.", "다른 답"),
            execution_tools=(self.order_tool(),),
        )
        await orchestrator.submit(user_key="user", message="buy", accepted_at=self.now)
        await orchestrator.submit(
            user_key="user",
            message="what?",
            accepted_at=self.now + timedelta(minutes=1),
        )
        self.assertEqual({}, orchestrator._pending_actions)

    async def test_expired_confirmation_is_dropped_with_a_notice(self) -> None:
        executed: list[str] = []
        contexts = []
        orchestrator = self.orchestrator(
            self.script(
                [("order", {"name": "Samsung"})],
                "준비했어요.",
                "늦었어요.",
                contexts=contexts,
            ),
            execution_tools=(self.order_tool(executed=executed),),
        )
        await orchestrator.submit(user_key="user", message="buy", accepted_at=self.now)
        late = await orchestrator.submit(
            user_key="user", message="yes", accepted_at=self.now + timedelta(minutes=6)
        )
        self.assertNotIn("confirm", [tool.name for tool in contexts[-1].tools])
        self.assertIn(CONFIRMATION_EXPIRED_NOTICE, late.final_text)
        self.assertEqual([], executed)

    async def test_missing_arguments_prepare_nothing(self) -> None:
        orchestrator = self.orchestrator(
            self.script([("order", {"name": "Samsung"})], "How many shares?"),
            execution_tools=(self.order_tool(action=False),),
        )
        result = await orchestrator.submit(
            user_key="user", message="buy Samsung", accepted_at=self.now
        )
        self.assertEqual("How many shares?", result.final_text)
        self.assertEqual({}, orchestrator._pending_actions)

    async def test_new_message_after_confirm_waits_for_the_execution(self) -> None:
        executing = asyncio.Event()
        release = asyncio.Event()

        async def execute(user_key, prepared):
            executing.set()
            await release.wait()
            return "Order accepted."

        orchestrator = self.orchestrator(
            self.script(
                [("order", {"name": "Samsung"})],
                "준비했어요.",
                self.confirm_waiting,
                "주문했어요.",
                "뉴스 답",
            ),
            execution_tools=(self.order_tool(execute=execute),),
        )
        await orchestrator.submit(user_key="user", message="buy", accepted_at=self.now)
        later = self.now + timedelta(minutes=1)
        confirm = asyncio.create_task(
            orchestrator.submit(user_key="user", message="yes", accepted_at=later)
        )
        await executing.wait()
        other = asyncio.create_task(
            orchestrator.submit(user_key="user", message="and news?", accepted_at=later)
        )
        await asyncio.sleep(0)
        self.assertFalse(confirm.done())
        release.set()
        confirm_result, other_result = await asyncio.gather(confirm, other)

        self.assertEqual(OrchestratorStatus.DELIVERED, confirm_result.status)
        self.assertEqual(OrchestratorStatus.DELIVERED, other_result.status)
        self.assertEqual(
            ["buy", "yes", "and news?"],
            [turn.user_message for turn in self.store.turns],
        )

    async def test_new_message_while_reporting_confirm_runs_nothing(self) -> None:
        executed: list[str] = []
        reporting = asyncio.Event()

        async def progress(user_key, progress_id, step):
            # The confirm step is only the model's call; the claim comes after it.
            if step is not None and step.next_action == "confirm":
                reporting.set()
                await asyncio.Event().wait()

        def confirm_once(context):
            if "wait" in _message(context):
                return "기다릴게요."
            return self.confirm_waiting(context)

        orchestrator = self.orchestrator(
            self.script(
                [("order", {"name": "Samsung"})],
                "준비했어요.",
                confirm_once,
                confirm_once,
            ),
            progress=progress,
            execution_tools=(self.order_tool(executed=executed),),
        )
        await orchestrator.submit(user_key="user", message="buy", accepted_at=self.now)
        later = self.now + timedelta(minutes=1)
        confirm = asyncio.create_task(
            orchestrator.submit(user_key="user", message="yes", accepted_at=later)
        )
        await reporting.wait()
        other = asyncio.create_task(
            orchestrator.submit(user_key="user", message="wait", accepted_at=later)
        )
        confirm_result, other_result = await asyncio.gather(confirm, other)

        self.assertEqual(OrchestratorStatus.SUPERSEDED, confirm_result.status)
        self.assertEqual(OrchestratorStatus.DELIVERED, other_result.status)
        self.assertEqual([], executed)

    async def test_answer_failure_after_execution_sends_the_fixed_result(self) -> None:
        def fail(context):
            raise RuntimeError("model unavailable")

        orchestrator = self.orchestrator(
            self.script(
                [("order", {"name": "Samsung"})],
                "준비했어요.",
                self.confirm_waiting,
                fail,
            ),
            execution_tools=(self.order_tool(),),
        )
        await orchestrator.submit(user_key="user", message="buy", accepted_at=self.now)
        result = await orchestrator.submit(
            user_key="user", message="yes", accepted_at=self.now + timedelta(minutes=1)
        )
        self.assertEqual(OrchestratorStatus.DELIVERED, result.status)
        self.assertEqual("Order accepted: Samsung.", result.final_text)

    async def test_external_cancel_after_confirm_still_reports_the_order(
        self,
    ) -> None:
        executing = asyncio.Event()
        release = asyncio.Event()

        async def execute(user_key, prepared):
            executing.set()
            await release.wait()
            return "Order accepted."

        orchestrator = self.orchestrator(
            self.script(
                [("order", {"name": "Samsung"})],
                "준비했어요.",
                self.confirm_waiting,
                "주문했어요.",
            ),
            execution_tools=(self.order_tool(execute=execute),),
        )
        await orchestrator.submit(user_key="user", message="buy", accepted_at=self.now)
        confirm = asyncio.create_task(
            orchestrator.submit(
                user_key="user",
                message="yes",
                accepted_at=self.now + timedelta(minutes=1),
            )
        )
        await executing.wait()
        active = orchestrator._states["user"].active_task
        assert active is not None
        active.cancel()
        await asyncio.sleep(0)
        active.cancel()
        release.set()
        result = await asyncio.wait_for(confirm, timeout=2)

        self.assertEqual(OrchestratorStatus.DELIVERED, result.status)
        self.assertEqual(
            ["buy", "yes"], [turn.user_message for turn in self.store.turns]
        )
        self.assertNotIn("user", orchestrator._states)

    # --- Tool records across Turns ------------------------------------------

    async def test_tool_records_are_stored_and_replayed_in_later_turns(self) -> None:
        contexts = []
        link = "https://etf.example/091160"
        found = ReadToolResult("News", (ToolLink("KODEX 반도체", link),))
        orchestrator = self.orchestrator(
            self.script(
                [("search", {"query": "KODEX"})],
                "상위 종목을 찾았어요.",
                # A link found by the earlier search is still readable.
                [("web_extract", {"urls": [link], "goal": "비중"})],
                "비중이에요.",
                contexts=contexts,
            ),
            read_tools=(self.search([found]), self.extractor()),
        )
        await orchestrator.submit(
            user_key="user", message="KODEX", accepted_at=self.now
        )
        await orchestrator.submit(
            user_key="user",
            message="3위 비중은?",
            accepted_at=self.now + timedelta(minutes=1),
        )

        stored = self.store.turns[0].tool_observations
        self.assertEqual(["search"], [item.name for item in stored])
        self.assertIn("KODEX 반도체", stored[0].result_text)
        # The earlier Turn replays as it happened, before the current request.
        self.assertEqual(
            [
                "SYSTEM",
                "USER_TURN",
                "TOOL_REQUEST",
                "TOOL_RESULT",
                "ASSISTANT_TURN",
                "CURRENT_USER",
            ],
            [part.kind.value for part in contexts[2].parts],
        )
        self.assertEqual(
            ["web_extract"], [i.name for i in self.store.turns[1].tool_observations]
        )

    async def test_call_ids_may_repeat_across_turns(self) -> None:
        contexts = []

        async def generate(context):
            contexts.append(context)
            if _results(context):
                return GeneratedAnswer("answer", "model", 10)
            return ModelReply(tool_calls=(ToolCall("search", '{"query":"q"}', "same"),))

        orchestrator = self.orchestrator(generate, read_tools=(self.search(),))
        for minutes in range(2):
            result = await orchestrator.submit(
                user_key="user",
                message="news",
                accepted_at=self.now + timedelta(minutes=minutes),
            )
            self.assertEqual(OrchestratorStatus.DELIVERED, result.status)
        # Each Turn closes its own calls; the repeated ID stays within its Turn.
        ids = [
            (part.kind.value, part.call_id)
            for part in contexts[-1].parts
            if part.call_id
        ]
        self.assertEqual(
            [
                ("TOOL_REQUEST", "same"),
                ("TOOL_RESULT", "same"),
                ("TOOL_REQUEST", "same"),
                ("TOOL_RESULT", "same"),
            ],
            ids,
        )

    async def test_a_turn_too_large_with_tools_keeps_request_and_answer(self) -> None:
        self.store.refuse_tool_records = True
        result = await self.orchestrator(
            self.script([("search", {"query": "q"})], "answer"),
            read_tools=(self.search(),),
        ).submit(user_key="user", message="news", accepted_at=self.now)

        self.assertEqual(OrchestratorStatus.DELIVERED, result.status)
        self.assertEqual((), self.store.turns[0].tool_observations)
        self.assertEqual("answer", self.store.turns[0].assistant_message)

    async def test_compaction_runs_after_the_answer_when_due(self) -> None:
        async def generate(context):
            # Not due while answering; due once the answer made it large.
            self.compactor.due = True
            return GeneratedAnswer("answer", "model", 10)

        result = await self.orchestrator(generate).submit(
            user_key="user", message="question", accepted_at=self.now
        )

        self.assertEqual(OrchestratorStatus.DELIVERED, result.status)
        self.assertFalse(result.compaction_failed)
        self.assertEqual(
            1, len([call for call in self.compactor.calls if call[0] == "compact"])
        )
        self.assertEqual(1, len(self.store.turns))

    async def test_a_failed_compaction_after_the_answer_keeps_the_turn(self) -> None:
        class FailingCompactor(FakeCompactor):
            def compact(self, **values):
                raise RuntimeError("summary unavailable")

        self.compactor = FailingCompactor()

        async def generate(context):
            self.compactor.due = True
            return GeneratedAnswer("answer", "model", 10)

        orchestrator = self.orchestrator(generate)
        result = await orchestrator.submit(
            user_key="user", message="question", accepted_at=self.now
        )

        # Delivered and stored: only the Compaction failed, nothing runs again.
        self.assertEqual(OrchestratorStatus.DELIVERED, result.status)
        self.assertTrue(result.compaction_failed)
        self.assertEqual([("user", "answer")], self.delivered)
        self.assertEqual(1, len(self.store.turns))
        self.assertNotIn("user", orchestrator._states)

    async def test_answer_links_become_numbered_sources(self) -> None:
        found = "https://etf.example/091160"
        given = "https://fund.example/kodex"
        results = [ReadToolResult("News", (ToolLink("KODEX 반도체", found),))]

        await self.orchestrator(
            self.script(
                [("search", {"query": "KODEX"})],
                f"SK하이닉스 36.8% <{found}>, 삼성전자 23.9% [3] {given}.\n"
                f"기준일은 8월 [ETF쇼핑]({found}). 참고 https://made.up/x 와 "
                "[다른 곳](https://made.up/y)",
            ),
            read_tools=(self.search(results),),
        ).submit(user_key="user", message=f"비중 알려줘 {given}", accepted_at=self.now)

        # Links become numbers in order of first use, the model's own numbers
        # and links that no tool or message gave are dropped, and the list
        # carries the links the tools actually returned.
        self.assertEqual(
            [
                (
                    "user",
                    "SK하이닉스 36.8% [1], 삼성전자 23.9% [2].\n"
                    "기준일은 8월 ETF쇼핑 [1]. 참고 와 다른 곳\n\n"
                    "출처\n"
                    f"[1] KODEX 반도체 {found}\n"
                    f"[2] {given}",
                )
            ],
            self.delivered,
        )


if __name__ == "__main__":
    unittest.main()
