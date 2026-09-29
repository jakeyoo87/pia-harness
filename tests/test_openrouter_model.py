from __future__ import annotations

import asyncio
import json
import unittest
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx

from pia_harness import (
    AssembledPromptContext,
    CompletedTurn,
    CurrentMemoryInput,
    MemoryReviewAction,
    MemoryReviewRequest,
    ModelTokenBudget,
    OpenRouterModelAdapter,
    OpenRouterModelError,
    PromptContextKind,
    PromptContextPart,
    PromptTrust,
    ReadToolDefinition,
    SummaryRequest,
    ToolObservation,
)


FAKE_KEY = "sk-or-v1-TEST-ONLY-synthetic-model-adapter-key"
BASE_URL = "https://openrouter.ai/api/v1"


def chat_response(
    content: str | None,
    *,
    tool_calls: list[dict[str, Any]] | None = None,
    status: int = 200,
    model: str = "vendor/exact-model",
    usage: dict[str, Any] | None = None,
    finish_reason: str | None = None,
    annotations: list[dict[str, Any]] | None = None,
) -> httpx.Response:
    message: dict[str, Any] = {"content": content}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    if annotations is not None:
        message["annotations"] = annotations
    choice: dict[str, Any] = {"message": message}
    if finish_reason is not None:
        choice["finish_reason"] = finish_reason
    payload: dict[str, Any] = {
        "model": model,
        "choices": [choice],
    }
    if usage is not None:
        payload["usage"] = usage
    return httpx.Response(status, json=payload)


def completed_turn() -> CompletedTurn:
    now = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)
    return CompletedTurn(
        user_key="user",
        session_id="session",
        turn_id="0000000000000-turn",
        user_message="질문",
        assistant_message="답변",
        created_at=now,
        expires_at=int(now.timestamp()) + 30 * 86400,
    )


def answer_content() -> str:
    return "answer"


def tool_call(call_id: str, name: str, arguments: str) -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


SEARCH = ReadToolDefinition(
    "search", "Find public sources", None, arguments_schema={"type": "object"}
)


async def answer(adapter: OpenRouterModelAdapter, context: AssembledPromptContext):
    reply = await adapter.generate_reply(context)
    assert reply.answer is not None
    return reply.answer


def answer_parts() -> tuple[PromptContextPart, ...]:
    return (
        PromptContextPart(
            PromptContextKind.SYSTEM,
            "system",
            PromptTrust.TRUSTED_INSTRUCTION,
        ),
        PromptContextPart(
            PromptContextKind.MEMORY,
            "장기투자 선호",
            PromptTrust.UNTRUSTED_DATA,
        ),
        PromptContextPart(
            PromptContextKind.SUMMARY,
            "이전 대화 요약",
            PromptTrust.UNTRUSTED_DATA,
        ),
        PromptContextPart(
            PromptContextKind.USER_TURN,
            "과거 질문",
            PromptTrust.UNTRUSTED_DATA,
        ),
        PromptContextPart(
            PromptContextKind.ASSISTANT_TURN,
            "과거 답변",
            PromptTrust.UNTRUSTED_DATA,
        ),
        PromptContextPart(
            PromptContextKind.CURRENT_USER,
            "앞으로 핵심만 답해줘",
            PromptTrust.UNTRUSTED_DATA,
        ),
    )


class OpenRouterModelAdapterTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.sync_clients: list[httpx.Client] = []
        self.async_clients: list[httpx.AsyncClient] = []

    async def asyncTearDown(self) -> None:
        for client in self.sync_clients:
            if not client.is_closed:
                client.close()
        for client in self.async_clients:
            if not client.is_closed:
                await client.aclose()

    def adapter(
        self,
        handler: Any,
        *,
        max_attempts: int | None = None,
    ) -> OpenRouterModelAdapter:
        # Omitting the argument exercises the Adapter default, which is what the
        # smoke tool relies on to make exactly one call per scenario.
        sync_client = httpx.Client(
            transport=httpx.MockTransport(handler), base_url=BASE_URL
        )
        async_client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url=BASE_URL
        )
        self.sync_clients.append(sync_client)
        self.async_clients.append(async_client)
        chosen = {} if max_attempts is None else {"max_attempts": max_attempts}
        return OpenRouterModelAdapter(
            api_key=FAKE_KEY,
            model_id="vendor/exact-model",
            token_budget=ModelTokenBudget(1_000, 100),
            timeout_seconds=5,
            sync_client=sync_client,
            async_client=async_client,
            **chosen,
        )

    async def test_offered_tools_are_sent_and_calls_come_back(self) -> None:
        requests = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(json.loads(request.content))
            return chat_response(
                None,
                tool_calls=[
                    tool_call("c1", "search", '{"query":"market news"}'),
                    tool_call("c2", "memory", ""),
                ],
                finish_reason="tool_calls",
            )

        adapter = self.adapter(handler)
        bare = ReadToolDefinition("latest", "Latest prices", None)
        parts = answer_parts()
        context = AssembledPromptContext(parts, 10, 900, tools=(SEARCH, bare))
        reply = await adapter.generate_reply(context)

        self.assertIsNone(reply.answer)
        self.assertEqual(
            [("search", '{"query":"market news"}', "c1"), ("memory", "{}", "c2")],
            [
                (call.name, call.arguments_json, call.call_id)
                for call in reply.tool_calls
            ],
        )
        body = requests[0]
        self.assertEqual("auto", body["tool_choice"])
        self.assertNotIn("response_format", body)
        self.assertEqual(
            [
                {
                    "type": "function",
                    "function": {
                        "name": "search",
                        "description": "Find public sources",
                        "parameters": {"type": "object"},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "latest",
                        "description": "Latest prices",
                        "parameters": {
                            "type": "object",
                            "properties": {},
                            "additionalProperties": False,
                        },
                    },
                },
            ],
            body["tools"],
        )
        # The tools count toward the input the budget checks.
        self.assertGreater(
            adapter.count_input_tokens(parts, (SEARCH, bare)),
            adapter.count_input_tokens(parts),
        )

    async def test_this_turns_calls_and_results_become_tool_messages(self) -> None:
        requests = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(json.loads(request.content))
            return chat_response(answer_content())

        from pia_harness import PromptContextAssembler
        from pia_harness.session import ConversationContext

        context = PromptContextAssembler(lambda parts, tools: 1).assemble(
            user_key="user",
            session_id="session",
            system_prompt="system",
            memory=None,
            conversation=ConversationContext(summary=None, turns=()),
            current_user_message="질문",
            token_budget=ModelTokenBudget(1_000, 100),
            tool_observations=(
                ToolObservation("search", '{"query":"a"}', "result a", "c1", 0),
                ToolObservation("search", '{"query":"b"}', "result b", "c2", 0),
            ),
            closing_note="Answer now.",
        )
        await self.adapter(handler).generate_reply(context)

        messages = requests[0]["messages"]
        self.assertEqual(
            ["system", "user", "assistant", "tool", "tool", "system"],
            [message["role"] for message in messages],
        )
        self.assertEqual(
            [
                tool_call("c1", "search", '{"query":"a"}'),
                tool_call("c2", "search", '{"query":"b"}'),
            ],
            messages[2]["tool_calls"],
        )
        self.assertEqual(["c1", "c2"], [m["tool_call_id"] for m in messages[3:5]])
        self.assertIn("result a", messages[3]["content"])
        self.assertIn("untrusted data", messages[3]["content"])
        self.assertEqual("Answer now.", messages[5]["content"])
        # Without tools the model can only answer.
        self.assertNotIn("tools", requests[0])

    async def test_read_json_retries_a_cut_off_reply_once(self) -> None:
        requests = []
        notes = {"summary": "상위 비중이 있다.", "quotes": ["삼성전기 23.3%"]}
        schema = {"type": "object"}

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(json.loads(request.content))
            if len(requests) == 1:
                return chat_response("{", finish_reason="length")
            return chat_response(json.dumps(notes))

        adapter = self.adapter(handler, max_attempts=2)
        with patch("pia_harness.openrouter.asyncio.sleep", new=AsyncMock()):
            output = await adapter.read_json(
                [{"role": "system", "content": "read"}], "pia_page_notes", schema
            )

        self.assertEqual(notes, output)
        self.assertEqual(2, len(requests))
        self.assertEqual(
            "pia_page_notes", requests[1]["response_format"]["json_schema"]["name"]
        )
        self.assertTrue(requests[1]["response_format"]["json_schema"]["strict"])
        self.assertNotIn("prompt_cache_key", requests[1])

    async def test_replies_send_a_hashed_per_user_cache_key(self) -> None:
        requests = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(json.loads(request.content))
            return chat_response(answer_content())

        adapter = self.adapter(handler)
        for user_key in ("member-a", "member-a", "member-b"):
            context = AssembledPromptContext(answer_parts(), 10, 900, user_key)
            await adapter.generate_reply(context)
        keys = [request["prompt_cache_key"] for request in requests]

        self.assertEqual(keys[0], keys[1])
        self.assertNotEqual(keys[0], keys[2])
        self.assertEqual(32, len(keys[0]))
        self.assertNotIn("member-a", keys[0])

        await adapter.generate_reply(AssembledPromptContext(answer_parts(), 10, 900))
        self.assertNotIn("prompt_cache_key", requests[-1])

    async def test_answer_is_plain_text_and_maps_usage(
        self,
    ) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            # The routed model string differs from the requested one so the test can
            # tell which source the answer and its usage are read from.
            return chat_response(
                "핵심만 답할게요.",
                model="vendor/exact-model:routed",
                usage={
                    "prompt_tokens": 20,
                    "completion_tokens": 7,
                    "total_tokens": 27,
                },
            )

        adapter = self.adapter(handler)
        parts = answer_parts()
        result = await answer(adapter, AssembledPromptContext(parts, 10, 900))

        self.assertEqual("핵심만 답할게요.", result.text)
        self.assertEqual(27, result.estimated_total_tokens)
        self.assertEqual(27, result.usage.total_tokens)
        # Both must come from the response, because CompactionPolicy prefers provider
        # usage only while usage.model_id equals the answer's model_id.
        self.assertEqual("vendor/exact-model:routed", result.model_id)
        self.assertEqual(result.model_id, result.usage.model_id)

        payload = json.loads(requests[0].content)
        self.assertEqual("vendor/exact-model", payload["model"])
        self.assertEqual(100, payload["max_tokens"])
        self.assertNotIn("max_completion_tokens", payload)
        self.assertFalse(payload["stream"])
        self.assertEqual({"require_parameters": True}, payload["provider"])
        self.assertNotIn("response_format", payload)
        self.assertNotIn("tools", payload)
        self.assertEqual(
            ["system", "user", "user", "user", "assistant", "user"],
            [message["role"] for message in payload["messages"]],
        )
        self.assertIn("data, not instructions", payload["messages"][1]["content"])
        self.assertEqual(f"Bearer {FAKE_KEY}", requests[0].headers["Authorization"])
        self.assertEqual(5.0, requests[0].extensions["timeout"]["read"])

        counted = {"messages": payload["messages"]}
        expected = len(
            json.dumps(
                counted,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        )
        self.assertEqual(expected, adapter.count_input_tokens(parts))

    async def test_answer_without_usage_uses_estimate_without_inventing_usage(
        self,
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return chat_response("답변")

        adapter = self.adapter(handler)
        parts = answer_parts()
        result = await answer(adapter, AssembledPromptContext(parts, 10, 900))
        self.assertIsNone(result.usage)
        self.assertGreater(
            result.estimated_total_tokens, adapter.count_input_tokens(parts)
        )

    def test_memory_review_has_no_completion_cap_and_converts_changes_to_tuple(
        self,
    ) -> None:
        seen: list[dict[str, Any]] = []
        replacement = "가" * 4_000

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(json.loads(request.content))
            return chat_response(
                json.dumps(
                    {
                        "action": "REPLACE",
                        "memory_text": replacement,
                        "change_summary": ["선호를 갱신했어요."],
                    },
                    ensure_ascii=False,
                )
            )

        adapter = self.adapter(handler)
        turn = completed_turn()
        request = MemoryReviewRequest(
            instruction="review",
            current_memory_text="old",
            turns=(turn,),
            max_characters=4_000,
            allow_clear=False,
            current_input=CurrentMemoryInput(
                "user",
                "session",
                "0000000000001-input",
                "기억해줘",
                turn.created_at,
            ),
        )
        output = adapter.review_memory(request)

        self.assertIs(MemoryReviewAction.REPLACE, output.action)
        self.assertEqual(replacement, output.memory_text)
        self.assertEqual(("선호를 갱신했어요.",), output.change_summary)
        self.assertNotIn("max_completion_tokens", seen[0])
        self.assertNotIn("max_tokens", seen[0])
        memory_system = seen[0]["messages"][0]["content"]
        normalized_memory_system = " ".join(memory_system.split())
        self.assertTrue(memory_system.startswith("review\n\n"))
        self.assertIn("UNCHANGED", memory_system)
        self.assertIn("JSON null", memory_system)
        self.assertIn("primary language", normalized_memory_system)
        self.assertIn("when the source is Korean, use Korean", normalized_memory_system)
        self.assertIn("Memory Review data", seen[0]["messages"][1]["content"])
        self.assertEqual(
            4_000,
            seen[0]["response_format"]["json_schema"]["schema"]["properties"][
                "memory_text"
            ]["maxLength"],
        )

    def test_memory_review_maps_every_domain_action(self) -> None:
        outputs = (
            {"action": "UNCHANGED", "memory_text": None, "change_summary": []},
            {"action": "REPLACE", "memory_text": "new", "change_summary": ["changed"]},
            {"action": "CLEAR", "memory_text": None, "change_summary": ["removed"]},
        )
        responses = iter(outputs)

        def handler(request: httpx.Request) -> httpx.Response:
            return chat_response(json.dumps(next(responses)))

        adapter = self.adapter(handler)
        request = MemoryReviewRequest(
            instruction="review",
            current_memory_text="old",
            turns=(completed_turn(),),
            max_characters=4_000,
            allow_clear=True,
        )
        self.assertIs(
            MemoryReviewAction.UNCHANGED, adapter.review_memory(request).action
        )
        self.assertIs(MemoryReviewAction.REPLACE, adapter.review_memory(request).action)
        self.assertIs(MemoryReviewAction.CLEAR, adapter.review_memory(request).action)

    def test_summary_uses_provider_tokens_only(self) -> None:
        responses = iter(
            (
                chat_response(json.dumps({"summary": "요약"}, ensure_ascii=False)),
                chat_response(
                    json.dumps({"summary": "다른 요약"}, ensure_ascii=False),
                    usage={
                        "prompt_tokens": 10,
                        "completion_tokens": 3,
                        "total_tokens": 13,
                    },
                ),
            )
        )
        payloads: list[dict[str, Any]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            payloads.append(json.loads(request.content))
            return next(responses)

        adapter = self.adapter(handler)
        request = SummaryRequest("summarize", "old", (completed_turn(),), 77)
        estimated = adapter.summarize(request)
        reported = adapter.summarize(request)

        self.assertIsNone(estimated.token_count)
        self.assertEqual(3, reported.token_count)
        self.assertEqual(77, payloads[0]["max_tokens"])
        self.assertNotIn("max_completion_tokens", payloads[0])
        summary_system = payloads[0]["messages"][0]["content"]
        normalized_summary_system = " ".join(summary_system.split())
        self.assertTrue(summary_system.startswith("summarize\n\n"))
        self.assertIn("primary language", normalized_summary_system)
        self.assertIn("when the source is Korean", normalized_summary_system)
        self.assertIn("Summary source", payloads[0]["messages"][1]["content"])

    def test_zeroed_usage_is_treated_as_unavailable(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return chat_response(
                json.dumps({"summary": "요약"}, ensure_ascii=False),
                usage={
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                },
            )

        output = self.adapter(handler).summarize(
            SummaryRequest("summarize", None, (completed_turn(),), 77)
        )
        self.assertIsNone(output.token_count)

    async def test_invalid_outputs_and_usage_fail_closed(self) -> None:
        offered = AssembledPromptContext(answer_parts(), 10, 900, tools=(SEARCH,))
        cases = (
            (chat_response(""), offered),
            (chat_response(None), offered),
            (chat_response(None, tool_calls=[{"id": "c1"}]), offered),
            (
                chat_response(
                    None,
                    tool_calls=[
                        tool_call("c1", "search", "{}"),
                        tool_call("c1", "search", "{}"),
                    ],
                ),
                offered,
            ),
            (chat_response(None, tool_calls=[tool_call("", "search", "{}")]), offered),
            # A tool call when no tool was offered.
            (
                chat_response(None, tool_calls=[tool_call("c1", "search", "{}")]),
                AssembledPromptContext(answer_parts(), 10, 900),
            ),
        )
        for index, (response, context) in enumerate(cases):
            with self.subTest(case=index):
                adapter = self.adapter(lambda request, response=response: response)
                with self.assertRaises(OpenRouterModelError):
                    await adapter.generate_reply(context)

        adapter = self.adapter(
            lambda request: chat_response(
                answer_content(),
                usage={
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 3,
                },
            )
        )
        with self.assertRaisesRegex(OpenRouterModelError, "openrouter.invalid_usage"):
            await adapter.generate_reply(
                AssembledPromptContext(answer_parts(), 10, 900)
            )

    async def test_errors_do_not_retain_secrets_prompts_or_response_bodies(
        self,
    ) -> None:
        secret_body = "provider-body-must-not-escape"

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text=secret_body)

        adapter = self.adapter(handler)
        with self.assertRaises(OpenRouterModelError) as caught:
            await adapter.generate_reply(
                AssembledPromptContext(answer_parts(), 10, 900)
            )
        error = caught.exception
        rendered = f"{error!s} {error!r}"
        self.assertEqual(500, error.status)
        self.assertIsNone(error.__cause__)
        self.assertNotIn(FAKE_KEY, rendered)
        self.assertNotIn("앞으로 핵심만 답해줘", rendered)
        self.assertNotIn(secret_body, rendered)

    async def test_transport_timeout_refusal_and_invalid_envelope_are_safe(
        self,
    ) -> None:
        failures = (
            (
                lambda request: (_ for _ in ()).throw(
                    httpx.ConnectError("secret transport detail", request=request)
                ),
                "openrouter.transport_error",
            ),
            (
                lambda request: (_ for _ in ()).throw(
                    httpx.ReadTimeout("secret timeout detail", request=request)
                ),
                "openrouter.timeout",
            ),
            (
                lambda request: httpx.Response(
                    200,
                    json={
                        "model": "vendor/exact-model",
                        "choices": [
                            {
                                "message": {
                                    "content": "{}",
                                    "refusal": "secret refusal detail",
                                }
                            }
                        ],
                    },
                ),
                "openrouter.refusal",
            ),
            (
                lambda request: httpx.Response(200, json={"unexpected": True}),
                "openrouter.invalid_response",
            ),
        )
        for handler, event in failures:
            with self.subTest(event=event):
                adapter = self.adapter(handler)
                with self.assertRaises(OpenRouterModelError) as caught:
                    await adapter.generate_reply(
                        AssembledPromptContext(answer_parts(), 10, 900)
                    )
                self.assertEqual(event, caught.exception.event)
                self.assertIsNone(caught.exception.__cause__)
                rendered = f"{caught.exception!s} {caught.exception!r}"
                self.assertNotIn(FAKE_KEY, rendered)
                self.assertNotIn("secret", rendered)

    async def test_default_is_one_attempt_and_retry_reuses_the_exact_payload(
        self,
    ) -> None:
        calls: list[bytes] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request.content)
            return chat_response("" if len(calls) == 1 else answer_content())

        context = AssembledPromptContext(answer_parts(), 10, 900)
        self.assertEqual(1, self.adapter(handler).max_attempts)
        with self.assertRaises(OpenRouterModelError):
            await self.adapter(handler).generate_reply(context)
        self.assertEqual(1, len(calls))

        calls.clear()
        sleeper = AsyncMock()
        with patch("pia_harness.openrouter.asyncio.sleep", sleeper):
            result = await answer(self.adapter(handler, max_attempts=2), context)
        self.assertEqual("answer", result.text)
        self.assertEqual(2, len(calls))
        self.assertEqual(calls[0], calls[1])
        sleeper.assert_awaited_once_with(1.0)

    async def test_retryable_http_statuses_retry_once(self) -> None:
        context = AssembledPromptContext(answer_parts(), 10, 900)
        for status in (408, 500, 520, 599):
            with self.subTest(status=status):
                calls = 0

                def handler(
                    request: httpx.Request, status: int = status
                ) -> httpx.Response:
                    nonlocal calls
                    calls += 1
                    if calls == 1:
                        return chat_response("ignored", status=status)
                    return chat_response(answer_content())

                with patch("pia_harness.openrouter.asyncio.sleep", new=AsyncMock()):
                    result = await answer(
                        self.adapter(handler, max_attempts=2), context
                    )
                self.assertEqual("answer", result.text)
                self.assertEqual(2, calls)

    async def test_retryable_failure_stops_after_two_attempts(self) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return chat_response("ignored", status=503)

        sleeper = AsyncMock()
        with (
            patch("pia_harness.openrouter.asyncio.sleep", sleeper),
            self.assertRaisesRegex(OpenRouterModelError, "openrouter.http_error"),
        ):
            await self.adapter(handler, max_attempts=2).generate_reply(
                AssembledPromptContext(answer_parts(), 10, 900)
            )
        self.assertEqual(2, calls)
        sleeper.assert_awaited_once_with(1.0)

    async def test_permanent_failures_and_truncation_do_not_retry(self) -> None:
        context = AssembledPromptContext(answer_parts(), 10, 900)
        for status in (400, 401, 403, 404, 429, 501):
            with self.subTest(status=status):
                calls = 0

                def handler(
                    request: httpx.Request, status: int = status
                ) -> httpx.Response:
                    nonlocal calls
                    calls += 1
                    return chat_response("ignored", status=status)

                with self.assertRaises(OpenRouterModelError):
                    await self.adapter(handler, max_attempts=2).generate_reply(context)
                self.assertEqual(1, calls)

        calls = 0

        def truncated(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return chat_response("", finish_reason="length")

        with self.assertRaisesRegex(
            OpenRouterModelError, "openrouter.output_truncated"
        ):
            await self.adapter(truncated, max_attempts=2).generate_reply(context)
        self.assertEqual(1, calls)

        calls = 0

        def invalid_usage(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return chat_response(
                answer_content(),
                usage={
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 3,
                },
            )

        with self.assertRaisesRegex(OpenRouterModelError, "openrouter.invalid_usage"):
            await self.adapter(invalid_usage, max_attempts=2).generate_reply(context)
        self.assertEqual(1, calls)

    def test_sync_memory_review_retries_invalid_output_once(self) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            if calls == 1:
                return chat_response("not-json")
            return chat_response(
                json.dumps(
                    {
                        "action": "REPLACE",
                        "memory_text": "new",
                        "change_summary": ["changed"],
                    }
                )
            )

        with patch("pia_harness.openrouter.time.sleep") as sleeper:
            output = self.adapter(handler, max_attempts=2).review_memory(
                MemoryReviewRequest("review", "old", (completed_turn(),), 4_000, False)
            )
        self.assertIs(MemoryReviewAction.REPLACE, output.action)
        self.assertEqual(2, calls)
        sleeper.assert_called_once_with(1.0)

    async def test_cancellation_during_retry_delay_propagates(self) -> None:
        calls = 0
        sleeping = asyncio.Event()

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return chat_response("ignored", status=503)

        async def blocked_sleep(delay: float) -> None:
            sleeping.set()
            await asyncio.Event().wait()

        adapter = self.adapter(handler, max_attempts=2)
        with patch("pia_harness.openrouter.asyncio.sleep", new=blocked_sleep):
            task = asyncio.create_task(
                adapter.generate_reply(AssembledPromptContext(answer_parts(), 10, 900))
            )
            await sleeping.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(1, calls)

    async def test_envelope_failures_retry_only_when_transient(self) -> None:
        context = AssembledPromptContext(answer_parts(), 10, 900)
        refused = {
            "model": "vendor/exact-model",
            "choices": [{"message": {"content": "", "refusal": "declined"}}],
        }
        cases = (
            ("openrouter.empty_response", lambda: chat_response(""), 2),
            (
                "openrouter.invalid_response",
                lambda: httpx.Response(200, json={"model": "vendor/exact-model"}),
                2,
            ),
            ("openrouter.refusal", lambda: httpx.Response(200, json=refused), 1),
        )
        for event, build, expected in cases:
            with self.subTest(event=event):
                calls = 0

                def handler(
                    request: httpx.Request, build: Any = build
                ) -> httpx.Response:
                    nonlocal calls
                    calls += 1
                    return build()

                with (
                    patch("pia_harness.openrouter.asyncio.sleep", new=AsyncMock()),
                    self.assertRaisesRegex(OpenRouterModelError, event),
                ):
                    await self.adapter(handler, max_attempts=2).generate_reply(context)
                self.assertEqual(expected, calls)

    def test_sync_summary_retries_invalid_output_once(self) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            if calls == 1:
                return chat_response("not-json")
            return chat_response(json.dumps({"summary": "짧은 요약"}))

        with patch("pia_harness.openrouter.time.sleep") as sleeper:
            output = self.adapter(handler, max_attempts=2).summarize(
                SummaryRequest("summarize", None, (completed_turn(),), 77)
            )
        self.assertEqual("짧은 요약", output.text)
        self.assertEqual(2, calls)
        sleeper.assert_called_once_with(1.0)

    async def test_async_answer_cancellation_propagates(self) -> None:
        started = asyncio.Event()

        async def handler(request: httpx.Request) -> httpx.Response:
            started.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        adapter = self.adapter(handler)
        task = asyncio.create_task(
            adapter.generate_reply(AssembledPromptContext(answer_parts(), 10, 900))
        )
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

    async def test_injected_clients_remain_caller_owned(self) -> None:
        adapter = self.adapter(
            lambda request: chat_response(json.dumps({"summary": "summary"}))
        )
        sync_client = self.sync_clients[-1]
        async_client = self.async_clients[-1]
        adapter.close()
        await adapter.aclose()
        self.assertFalse(sync_client.is_closed)
        self.assertFalse(async_client.is_closed)

    async def test_owned_clients_close_and_constructor_rejects_invalid_values(
        self,
    ) -> None:
        adapter = OpenRouterModelAdapter(
            api_key=FAKE_KEY,
            model_id="vendor/exact-model",
            token_budget=ModelTokenBudget(1_000, 100),
            timeout_seconds=5,
        )
        sync_client = adapter._sync_client
        async_client = adapter._async_client
        await adapter.aclose()
        self.assertTrue(sync_client.is_closed)
        self.assertTrue(async_client.is_closed)

        invalid = (
            {"api_key": ""},
            {"model_id": ""},
            {"timeout_seconds": 0},
            {"timeout_seconds": float("inf")},
            {"max_attempts": 0},
            {"max_attempts": 3},
            {"max_attempts": True},
            {"max_attempts": 1.5},
        )
        defaults = {
            "api_key": FAKE_KEY,
            "model_id": "vendor/exact-model",
            "token_budget": ModelTokenBudget(1_000, 100),
            "timeout_seconds": 5,
        }
        for override in invalid:
            with self.subTest(override=override), self.assertRaises(ValueError):
                OpenRouterModelAdapter(**(defaults | override))


if __name__ == "__main__":
    unittest.main()
