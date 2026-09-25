from __future__ import annotations

import json
import unittest
from dataclasses import dataclass

import httpx

from pia_harness.context import (
    AssembledPromptContext,
    PromptContextKind,
    PromptContextPart,
    PromptTrust,
)
from pia_harness.jev import JevDecisionAdapter, JevDecisionError
from pia_harness.memory import MemoryReviewRequest
from pia_harness.orchestrator import MemoryAction, NextActionDecision


@dataclass(frozen=True)
class _Tool:
    name: str
    description: str


class JevDecisionAdapterTest(unittest.IsolatedAsyncioTestCase):
    async def test_selects_only_registered_next_action(self) -> None:
        requests = []
        paths = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(json.loads(request.content))
            paths.append(request.url.path)
            return httpx.Response(
                200,
                json={
                    "model": "typesafe/jev-1.13",
                    "answers": {
                        "next_action": {
                            "type": "choice",
                            "choice": "search",
                            "confidence": 0.9,
                            "probabilities": {"answer": 0.1, "search": 0.9},
                        },
                        "memory_action": {"type": "choice", "choice": "NONE"},
                    },
                    "usage": {"input_tokens": 20, "output_tokens": 2},
                },
            )

        transport = httpx.MockTransport(handler)
        adapter = JevDecisionAdapter(
            api_key="synthetic-key",
            sync_client=httpx.Client(
                transport=transport, base_url="https://openrouter.ai"
            ),
            async_client=httpx.AsyncClient(
                transport=transport, base_url="https://openrouter.ai"
            ),
        )
        context = AssembledPromptContext(
            (
                PromptContextPart(
                    PromptContextKind.SYSTEM,
                    "Be helpful",
                    PromptTrust.TRUSTED_INSTRUCTION,
                ),
                PromptContextPart(
                    PromptContextKind.CURRENT_USER,
                    "오늘 뉴스",
                    PromptTrust.UNTRUSTED_DATA,
                ),
            ),
            10,
            100,
        )
        choice = await adapter.choose_next(
            context, (_Tool("search", "Find public sources"),)
        )
        self.assertEqual(NextActionDecision("search"), choice)
        self.assertEqual(["/api/alpha/decisions"], paths)
        self.assertEqual("~typesafe/jev-latest", requests[0]["model"])
        self.assertEqual(
            {"answer", "search"},
            set(requests[0]["questions"]["next_action"]["criteria"]),
        )
        self.assertEqual("CURRENT_USER", requests[0]["state"][-1]["kind"])
        self.assertEqual(
            {"NONE", "UPDATE", "FORGET"},
            set(requests[0]["questions"]["memory_action"]["criteria"]),
        )

    async def test_routes_only_the_current_request_and_its_tool_results(self) -> None:
        requests = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(json.loads(request.content))
            return httpx.Response(
                200,
                json={
                    "answers": {
                        "next_action": {"type": "choice", "choice": "answer"},
                        "memory_action": {"type": "choice", "choice": "NONE"},
                    }
                },
            )

        adapter = JevDecisionAdapter(
            api_key="synthetic-key",
            async_client=httpx.AsyncClient(
                transport=httpx.MockTransport(handler),
                base_url="https://openrouter.ai",
            ),
        )
        trusted = PromptTrust.TRUSTED_INSTRUCTION
        data = PromptTrust.UNTRUSTED_DATA
        context = AssembledPromptContext(
            (
                PromptContextPart(PromptContextKind.SYSTEM, "Be helpful", trusted),
                PromptContextPart(PromptContextKind.MEMORY, "Likes chips", data),
                PromptContextPart(PromptContextKind.SUMMARY, "Earlier talk", data),
                PromptContextPart(PromptContextKind.USER_TURN, "Old question", data),
                PromptContextPart(PromptContextKind.ASSISTANT_TURN, "Old answer", data),
                PromptContextPart(PromptContextKind.CURRENT_USER, "오늘 뉴스", data),
                PromptContextPart(PromptContextKind.TOOL_REQUEST, "search", data),
                PromptContextPart(PromptContextKind.TOOL_RESULT, "c1 title", data),
            ),
            10,
            100,
        )
        await adapter.choose_next(context, (_Tool("search", "Find news"),))
        self.assertEqual(
            [
                {"kind": "CURRENT_USER", "content": "오늘 뉴스"},
                {"kind": "TOOL_REQUEST", "content": "search"},
                {"kind": "TOOL_RESULT", "content": "c1 title"},
            ],
            requests[0]["state"],
        )

    async def test_answer_selects_memory_action_in_the_same_request(self) -> None:
        requests = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(json.loads(request.content))
            return httpx.Response(
                200,
                json={
                    "answers": {
                        "next_action": {"type": "choice", "choice": "answer"},
                        "memory_action": {"type": "choice", "choice": "UPDATE"},
                    }
                },
            )

        adapter = JevDecisionAdapter(
            api_key="synthetic-key",
            async_client=httpx.AsyncClient(
                transport=httpx.MockTransport(handler),
                base_url="https://openrouter.ai",
            ),
        )
        context = AssembledPromptContext(
            (
                PromptContextPart(
                    PromptContextKind.SYSTEM, "System", PromptTrust.TRUSTED_INSTRUCTION
                ),
                PromptContextPart(
                    PromptContextKind.CURRENT_USER,
                    "배당주 선호를 기억해줘",
                    PromptTrust.UNTRUSTED_DATA,
                ),
            ),
            10,
            100,
        )
        self.assertEqual(
            NextActionDecision("answer", MemoryAction.UPDATE),
            await adapter.choose_next(context, ()),
        )
        self.assertEqual(1, len(requests))

    async def test_rejects_invented_tool_name(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "answers": {
                        "next_action": {"type": "choice", "choice": "buy"},
                        "memory_action": {"type": "choice", "choice": "NONE"},
                    }
                },
            )

        adapter = JevDecisionAdapter(
            api_key="synthetic-key",
            async_client=httpx.AsyncClient(
                transport=httpx.MockTransport(handler),
                base_url="https://openrouter.ai",
            ),
        )
        context = AssembledPromptContext(
            (
                PromptContextPart(
                    PromptContextKind.SYSTEM, "System", PromptTrust.TRUSTED_INSTRUCTION
                ),
            ),
            1,
            100,
        )
        with self.assertRaises(JevDecisionError):
            await adapter.choose_next(context, (_Tool("search", "Search"),))

    async def test_tool_choice_defers_memory_action(self) -> None:
        adapter = JevDecisionAdapter(
            api_key="synthetic-key",
            async_client=httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(
                        200,
                        json={
                            "answers": {
                                "next_action": {"type": "choice", "choice": "search"},
                                "memory_action": {"type": "choice", "choice": "UPDATE"},
                            }
                        },
                    )
                ),
                base_url="https://openrouter.ai",
            ),
        )
        context = AssembledPromptContext(
            (
                PromptContextPart(
                    PromptContextKind.SYSTEM, "System", PromptTrust.TRUSTED_INSTRUCTION
                ),
            ),
            1,
            100,
        )
        self.assertEqual(
            NextActionDecision("search"),
            await adapter.choose_next(context, (_Tool("search", "Search"),)),
        )

    async def test_memory_choice_is_independent_of_writer(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            self.assertEqual("Memory rules", payload["state"]["instruction"])
            return httpx.Response(
                200,
                json={
                    "answers": {
                        "memory_change": {"type": "choice", "choice": "unchanged"}
                    }
                },
            )

        adapter = JevDecisionAdapter(
            api_key="synthetic-key",
            sync_client=httpx.Client(
                transport=httpx.MockTransport(handler),
                base_url="https://openrouter.ai",
            ),
        )
        self.assertFalse(
            adapter.decide_memory_change(
                MemoryReviewRequest("Memory rules", "", (), 4000, False)
            )
        )
