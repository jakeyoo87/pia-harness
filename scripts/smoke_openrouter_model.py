from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import sys
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from time import monotonic
from typing import Any, Protocol

from pia_harness import (
    MAX_CHANGE_SUMMARY_CHARS,
    MAX_CHANGE_SUMMARY_ITEMS,
    MEMORY_MAX_CHARS,
    MEMORY_REVIEW_INSTRUCTION,
    AssembledPromptContext,
    CompletedTurn,
    CurrentMemoryInput,
    GeneratedAnswer,
    MemoryReviewAction,
    MemoryReviewOutput,
    MemoryReviewRequest,
    ModelTokenBudget,
    OpenRouterModelAdapter,
    OpenRouterModelError,
    PromptContextKind,
    PromptContextPart,
    PromptTrust,
    SummaryOutput,
    SummaryRequest,
    new_turn_id,
)
from pia_harness.compaction import SUMMARY_INSTRUCTION


SYSTEM_PROMPT = (
    "한국어로 자연스럽고 간결하게 답하세요. 내부 메타데이터는 노출하지 마세요."
)
MOVING_MODEL_IDS = {"openrouter/auto", "openrouter/auto-beta", "openrouter/free"}


class SmokeAdapter(Protocol):
    def count_input_tokens(self, parts: tuple[PromptContextPart, ...]) -> int: ...

    async def generate_answer(
        self, context: AssembledPromptContext
    ) -> GeneratedAnswer: ...

    def review_memory(self, request: MemoryReviewRequest) -> MemoryReviewOutput: ...

    def summarize(self, request: SummaryRequest) -> SummaryOutput: ...

    async def aclose(self) -> None: ...


@dataclass(frozen=True, slots=True)
class SmokeConfig:
    model_id: str
    context_limit: int
    response_tokens: int = 4_096
    timeout_seconds: float = 90.0


@dataclass(frozen=True, slots=True)
class SmokeResult:
    scenario: str
    status: str
    elapsed_ms: int
    response_model: str | None = None
    expected_action: str | None = None
    actual_action: str | None = None
    total_tokens: int | None = None
    completion_tokens: int | None = None
    output_text: str | None = None
    error_event: str | None = None
    error_status: int | None = None
    error_type: str | None = None


AdapterFactory = Callable[..., SmokeAdapter]
Emitter = Callable[[str], None]


async def run_smoke(
    *,
    config: SmokeConfig,
    api_key: str,
    emit: Emitter = print,
    adapter_factory: AdapterFactory = OpenRouterModelAdapter,
) -> int:
    _validate_config(config, api_key)
    budget = ModelTokenBudget(config.context_limit, config.response_tokens)
    adapter = adapter_factory(
        api_key=api_key,
        model_id=config.model_id,
        token_budget=budget,
        timeout_seconds=config.timeout_seconds,
    )
    results: list[SmokeResult] = []
    started = monotonic()
    try:
        for name, parts in _answer_scenarios():
            result = await _run_answer(
                adapter,
                budget,
                name=name,
                parts=parts,
            )
            results.append(result)
            _emit(emit, asdict(result))

        replace = await _run_memory_replace(adapter)
        results.append(replace)
        _emit(emit, asdict(replace))

        unchanged = await _run_memory_unchanged(adapter)
        results.append(unchanged)
        _emit(emit, asdict(unchanged))

        summary = await _run_summary(adapter)
        results.append(summary)
        _emit(emit, asdict(summary))
    finally:
        await adapter.aclose()

    observed_models = sorted(
        {
            result.response_model
            for result in results
            if result.response_model is not None
        }
    )
    passed = sum(result.status == "PASS" for result in results)
    model_consistent = len(observed_models) == 1
    aggregate_pass = passed == len(results) and model_consistent
    aggregate = {
        "scenario": "aggregate",
        "status": "PASS" if aggregate_pass else "FAIL",
        "requested_model": config.model_id,
        "observed_models": observed_models,
        "total": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "model_consistent": model_consistent,
        "elapsed_ms": _elapsed_ms(started),
    }
    _emit(emit, aggregate)
    return 0 if aggregate_pass else 1


async def _run_answer(
    adapter: SmokeAdapter,
    budget: ModelTokenBudget,
    *,
    name: str,
    parts: tuple[PromptContextPart, ...],
) -> SmokeResult:
    started = monotonic()
    try:
        estimate = adapter.count_input_tokens(parts)
        answer = await adapter.generate_answer(
            AssembledPromptContext(parts, estimate, budget.input_tokens)
        )
        passed = bool(answer.text.strip())
        return SmokeResult(
            scenario=name,
            status="PASS" if passed else "FAIL",
            elapsed_ms=_elapsed_ms(started),
            response_model=answer.model_id,
            total_tokens=(None if answer.usage is None else answer.usage.total_tokens),
            output_text=answer.text,
        )
    except OpenRouterModelError as error:
        return _adapter_failure(name, started, error)
    except Exception as error:
        return _unexpected_failure(name, started, error)


async def _run_memory_replace(adapter: SmokeAdapter) -> SmokeResult:
    started = monotonic()
    now = datetime.now(UTC)
    try:
        output = await asyncio.to_thread(
            adapter.review_memory,
            MemoryReviewRequest(
                instruction=MEMORY_REVIEW_INSTRUCTION,
                current_memory_text="",
                turns=(),
                max_characters=MEMORY_MAX_CHARS,
                allow_clear=False,
                current_input=CurrentMemoryInput(
                    user_key="synthetic-user",
                    session_id="synthetic-session",
                    turn_id=new_turn_id(now),
                    user_message="앞으로 답변은 핵심부터 짧게 설명해줘.",
                    created_at=now,
                ),
            ),
        )
        valid = (
            output.action is MemoryReviewAction.REPLACE
            and isinstance(output.memory_text, str)
            and bool(output.memory_text.strip())
            and len(output.memory_text) <= MEMORY_MAX_CHARS
            and _valid_changes(output.change_summary)
        )
        return SmokeResult(
            scenario="memory_replace",
            status="PASS" if valid else "FAIL",
            elapsed_ms=_elapsed_ms(started),
            expected_action=MemoryReviewAction.REPLACE.value,
            actual_action=str(output.action),
            output_text=output.memory_text,
        )
    except OpenRouterModelError as error:
        return _adapter_failure("memory_replace", started, error)
    except Exception as error:
        return _unexpected_failure("memory_replace", started, error)


async def _run_memory_unchanged(adapter: SmokeAdapter) -> SmokeResult:
    started = monotonic()
    try:
        output = await asyncio.to_thread(
            adapter.review_memory,
            MemoryReviewRequest(
                instruction=MEMORY_REVIEW_INSTRUCTION,
                current_memory_text="사용자는 답변을 핵심부터 짧게 받는 것을 선호한다.",
                turns=(),
                max_characters=MEMORY_MAX_CHARS,
                allow_clear=False,
            ),
        )
        valid = (
            output.action is MemoryReviewAction.UNCHANGED
            and output.memory_text is None
            and output.change_summary == ()
        )
        return SmokeResult(
            scenario="memory_unchanged_null",
            status="PASS" if valid else "FAIL",
            elapsed_ms=_elapsed_ms(started),
            expected_action=MemoryReviewAction.UNCHANGED.value,
            actual_action=str(output.action),
            output_text=output.memory_text,
        )
    except OpenRouterModelError as error:
        return _adapter_failure("memory_unchanged_null", started, error)
    except Exception as error:
        return _unexpected_failure("memory_unchanged_null", started, error)


async def _run_summary(adapter: SmokeAdapter) -> SmokeResult:
    started = monotonic()
    now = datetime.now(UTC)
    try:
        output = await asyncio.to_thread(
            adapter.summarize,
            SummaryRequest(
                instruction=SUMMARY_INSTRUCTION,
                previous_summary=None,
                turns=(
                    CompletedTurn(
                        user_key="synthetic-user",
                        session_id="synthetic-session",
                        turn_id=new_turn_id(now),
                        user_message="삼성전자는 다음 실적 발표 후 판단할게.",
                        assistant_message="다음 실적에서 수익성을 확인해보겠습니다.",
                        created_at=now,
                        expires_at=int(now.timestamp()) + 30 * 86400,
                    ),
                ),
                max_output_tokens=4_096,
            ),
        )
        valid_tokens = output.token_count is None or (
            not isinstance(output.token_count, bool)
            and isinstance(output.token_count, int)
            and output.token_count > 0
        )
        valid = bool(output.text.strip()) and valid_tokens
        return SmokeResult(
            scenario="rolling_summary",
            status="PASS" if valid else "FAIL",
            elapsed_ms=_elapsed_ms(started),
            response_model=output.model_id,
            completion_tokens=output.token_count,
            output_text=output.text,
        )
    except OpenRouterModelError as error:
        return _adapter_failure("rolling_summary", started, error)
    except Exception as error:
        return _unexpected_failure("rolling_summary", started, error)


def _answer_scenarios() -> tuple[tuple[str, tuple[PromptContextPart, ...]], ...]:
    return (
        (
            "answer_text",
            _parts("삼성전자 실적에서 중요한 점을 한 문장으로 설명해줘."),
        ),
    )


def _parts(
    current: str,
    *,
    history: tuple[tuple[str, str], ...] = (),
) -> tuple[PromptContextPart, ...]:
    parts = [
        PromptContextPart(
            PromptContextKind.SYSTEM,
            SYSTEM_PROMPT,
            PromptTrust.TRUSTED_INSTRUCTION,
        )
    ]
    for role, content in history:
        kind = (
            PromptContextKind.USER_TURN
            if role == "user"
            else PromptContextKind.ASSISTANT_TURN
        )
        parts.append(PromptContextPart(kind, content, PromptTrust.UNTRUSTED_DATA))
    parts.append(
        PromptContextPart(
            PromptContextKind.CURRENT_USER,
            current,
            PromptTrust.UNTRUSTED_DATA,
        )
    )
    return tuple(parts)


def _valid_changes(changes: Any) -> bool:
    # A replacement must report at least one change. The Orchestrator's only
    # success signal for an explicit remember or forget is this summary, so a
    # model that returns an empty array would pass every other contract while
    # silently removing the user's confirmation from the reply.
    return (
        isinstance(changes, tuple)
        and 1 <= len(changes) <= MAX_CHANGE_SUMMARY_ITEMS
        and all(
            isinstance(item, str)
            and bool(item.strip())
            and len(item) <= MAX_CHANGE_SUMMARY_CHARS
            for item in changes
        )
    )


def _adapter_failure(
    scenario: str, started: float, error: OpenRouterModelError
) -> SmokeResult:
    return SmokeResult(
        scenario=scenario,
        status="FAIL",
        elapsed_ms=_elapsed_ms(started),
        error_event=error.event,
        error_status=error.status,
        error_type=error.error_type,
    )


def _unexpected_failure(scenario: str, started: float, error: Exception) -> SmokeResult:
    return SmokeResult(
        scenario=scenario,
        status="FAIL",
        elapsed_ms=_elapsed_ms(started),
        error_event="smoke.unexpected_error",
        error_type=type(error).__name__,
    )


def _elapsed_ms(started: float) -> int:
    return max(0, round((monotonic() - started) * 1_000))


def _emit(emit: Emitter, value: Mapping[str, Any]) -> None:
    emit(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def _validate_config(config: SmokeConfig, api_key: str) -> None:
    if not isinstance(api_key, str) or not api_key.strip():
        raise ValueError("OPENROUTER_API_KEY is required")
    if not isinstance(config.model_id, str) or not config.model_id.strip():
        raise ValueError("model must be an exact model ID")
    model_id = config.model_id.strip()
    if model_id in MOVING_MODEL_IDS or model_id.startswith("~"):
        raise ValueError("model must not be a moving or router alias")
    if (
        isinstance(config.timeout_seconds, bool)
        or not isinstance(config.timeout_seconds, (int, float))
        or not math.isfinite(config.timeout_seconds)
        or config.timeout_seconds <= 0
    ):
        raise ValueError("timeout must be positive and finite")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run fixed synthetic smoke scenarios through OpenRouterModelAdapter."
    )
    parser.add_argument("--model", required=True)
    parser.add_argument("--context-limit", required=True, type=int)
    parser.add_argument("--response-tokens", type=int, default=4_096)
    parser.add_argument("--timeout-seconds", type=float, default=90.0)
    return parser


def cli(
    argv: list[str] | None = None,
    *,
    environ: Mapping[str, str] = os.environ,
    emit: Emitter = print,
    adapter_factory: AdapterFactory = OpenRouterModelAdapter,
) -> int:
    args = _parser().parse_args(argv)
    try:
        config = SmokeConfig(
            model_id=args.model,
            context_limit=args.context_limit,
            response_tokens=args.response_tokens,
            timeout_seconds=args.timeout_seconds,
        )
        return asyncio.run(
            run_smoke(
                config=config,
                api_key=environ.get("OPENROUTER_API_KEY", ""),
                emit=emit,
                adapter_factory=adapter_factory,
            )
        )
    except (TypeError, ValueError) as error:
        _emit(
            emit,
            {
                "scenario": "configuration",
                "status": "FAIL",
                "error_event": "smoke.invalid_configuration",
                "error_type": type(error).__name__,
            },
        )
        return 2


def main() -> None:
    raise SystemExit(cli(sys.argv[1:]))


if __name__ == "__main__":
    main()
