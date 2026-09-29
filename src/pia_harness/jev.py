"""Jev through the OpenRouter Decisions API, for the automatic Memory Review's
choice of whether Memory needs a rewrite at all.

The conversation loop itself is one model with native tool calls; Jev no longer
routes it.
"""

from __future__ import annotations

from typing import Any

import httpx

from .memory import MemoryReviewRequest

JEV_BASE_URL = "https://openrouter.ai"
JEV_DECISIONS_PATH = "/api/alpha/decisions"


class JevDecisionError(RuntimeError):
    """A provider or response failure without retaining conversation content."""


class JevDecisionAdapter:
    def __init__(
        self,
        *,
        api_key: str,
        model_id: str = "~typesafe/jev-latest",
        timeout_seconds: float = 10,
        sync_client: httpx.Client | None = None,
    ) -> None:
        if not isinstance(api_key, str) or not api_key:
            raise ValueError("OpenRouter API key is required")
        if not isinstance(model_id, str) or not model_id:
            raise ValueError("Jev model ID is required")
        if timeout_seconds <= 0:
            raise ValueError("Jev timeout must be positive")
        self.model_id = model_id
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._owns_sync = sync_client is None
        self._sync = sync_client or httpx.Client(
            base_url=JEV_BASE_URL, timeout=timeout_seconds
        )

    def decide_memory_change(self, request: MemoryReviewRequest) -> bool:
        payload = {
            "model": self.model_id,
            "state": {
                "instruction": request.instruction,
                "current_memory": request.current_memory_text,
                "turns": [
                    {"user": turn.user_message, "assistant": turn.assistant_message}
                    for turn in request.turns
                ],
                "current_input": (
                    None
                    if request.current_input is None
                    else request.current_input.user_message
                ),
            },
            "questions": {
                "memory_change": {
                    "type": "choice",
                    "instructions": "Does durable Memory need a change?",
                    "criteria": {
                        "unchanged": "The current Memory already captures all durable facts.",
                        "rewrite": "Durable user context must be added, corrected, or forgotten.",
                    },
                }
            },
        }
        return (
            _choice(
                self._post_sync(payload),
                "memory_change",
                frozenset({"unchanged", "rewrite"}),
            )
            == "rewrite"
        )

    def _post_sync(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = self._sync.post(
                JEV_DECISIONS_PATH, json=payload, headers=self._headers
            )
            response.raise_for_status()
            result = response.json()
        except (httpx.HTTPError, ValueError):
            raise JevDecisionError("jev.request_failed") from None
        if not isinstance(result, dict):
            raise JevDecisionError("jev.invalid_response")
        return result

    async def aclose(self) -> None:
        if self._owns_sync:
            self._sync.close()


def _choice(response: dict[str, Any], name: str, allowed: frozenset[str]) -> str:
    answers = response.get("answers")
    answer = answers.get(name) if isinstance(answers, dict) else None
    if not isinstance(answer, dict) or answer.get("type") != "choice":
        raise JevDecisionError("jev.invalid_response")
    choice = answer.get("choice")
    if not isinstance(choice, str) or choice not in allowed:
        raise JevDecisionError("jev.invalid_response")
    return choice
