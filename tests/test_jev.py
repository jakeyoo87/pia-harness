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


@dataclass(frozen=True)
class _Tool:
    name: str
    description: str


class JevDecisionAdapterTest(unittest.IsolatedAsyncioTestCase):
    async def test_selects_only_registered_next_action(self) -> None:
        requests = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(json.loads(request.content))
            return httpx.Response(
                200,
                json={
                    "model": "jev-latest",
                    "answers": {
                        "next_action": {
                            "type": "choice",
                            "choice": "search",
                            "confidence": 0.9,
                            "probabilities": {"answer": 0.1, "search": 0.9},
                        }
                    },
                    "usage": {"input_tokens": 20, "output_tokens": 2},
                },
            )

        transport = httpx.MockTransport(handler)
        adapter = JevDecisionAdapter(
            api_key="synthetic-key",
            sync_client=httpx.Client(
                transport=transport, base_url="https://api.typesafe.ai"
            ),
            async_client=httpx.AsyncClient(
                transport=transport, base_url="https://api.typesafe.ai"
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
        self.assertEqual("search", choice)
        self.assertEqual(
            {"answer", "search"},
            set(requests[0]["questions"]["next_action"]["criteria"]),
        )
        self.assertEqual("CURRENT_USER", requests[0]["state"][-1]["kind"])

    async def test_rejects_invented_tool_name(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"answers": {"next_action": {"type": "choice", "choice": "buy"}}},
            )

        adapter = JevDecisionAdapter(
            api_key="synthetic-key",
            async_client=httpx.AsyncClient(
                transport=httpx.MockTransport(handler),
                base_url="https://api.typesafe.ai",
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
                base_url="https://api.typesafe.ai",
            ),
        )
        self.assertFalse(
            adapter.decide_memory_change(
                MemoryReviewRequest("Memory rules", "", (), 4000, False)
            )
        )
