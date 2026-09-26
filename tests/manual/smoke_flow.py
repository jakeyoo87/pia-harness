"""Run the README section 1 flow with real Jev, OpenRouter, NAVER, and web pages.

Manual only: it calls paid APIs and model decisions vary, so it is not part of pytest
or CI. Scenarios run in order in one conversation, so later ones can refer to earlier
answers. Keys come from the environment and are never printed.

    OPENROUTER_API_KEY=... NAVER_API_HUB_CLIENT_ID=... NAVER_API_HUB_CLIENT_SECRET=... \\
        python -m tests.manual.smoke_flow [--only 1,2] [--model openai/gpt-6-luna]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pia_harness import (
    ConversationOrchestrator,
    JevDecisionAdapter,
    ModelTokenBudget,
    NaverNewsSearch,
    OpenRouterModelAdapter,
    UrlReader,
)
from pia_harness.compaction import TokenCompactor
from pia_harness.context import PromptContextAssembler
from pia_harness.memory import AutomaticMemoryReviewer
from pia_harness.testing import InMemoryConversationStore

SCENARIOS = Path(__file__).with_name("scenarios.json")
USER = "smoke-flow-user"
SYSTEM_PROMPT = (
    "You are PIA, a Korean personal investment assistant. Answer in Korean, "
    "concisely. Do not give personalized buy/sell advice."
)
MEMORY_INSTRUCTION = (
    "Keep durable facts a financial investment agent should remember about the user: "
    "investment style, risk tolerance, holdings or interests the user states. "
    "Do not keep news content or one-off questions."
)


@dataclass
class Record:
    """What one scenario did; filled by the instrumentation below."""

    jev: list[dict[str, Any]] = field(default_factory=list)
    current_user: str = ""


def _log(started: float, event: str, detail: str = "") -> None:
    print(
        f"  [{time.monotonic() - started:5.1f}s] {event} {detail}".rstrip(), flush=True
    )


def _instrument(jev: JevDecisionAdapter, model: OpenRouterModelAdapter, search, reader):
    """Wrap calls to log decisions. Jev probabilities and token counts are only in
    the raw response, so this reaches into the adapter's private post method."""
    state: dict[str, Any] = {"record": Record(), "started": time.monotonic()}
    post_async = jev._post_async

    async def jev_post(payload: dict[str, Any]) -> dict[str, Any]:
        result = await post_async(payload)
        answers = result.get("answers", {})
        choice = answers.get("next_action", {}).get("choice")
        memory = answers.get("memory_action", {}).get("choice")
        tokens = result.get("usage", {}).get("input_tokens")
        record: Record = state["record"]
        record.jev.append({"choice": choice, "memory": memory, "tokens": tokens})
        record.current_user = next(
            (p["content"] for p in payload["state"] if p["kind"] == "CURRENT_USER"), ""
        )
        probs = json.dumps(answers.get("next_action", {}).get("probabilities"))
        _log(
            state["started"], "JEV", f"{choice} memory={memory} tokens={tokens} {probs}"
        )
        return result

    jev._post_async = jev_post  # type: ignore[method-assign]

    tool_call = model.generate_tool_call

    async def logged_tool_call(context, tool):
        call = await tool_call(context, tool)
        _log(state["started"], "TOOL_ARGS", call.arguments_json)
        return call

    model.generate_tool_call = logged_tool_call  # type: ignore[method-assign]

    execute = search.execute

    async def logged_search(user_key, call, inputs):
        result = await execute(user_key, call, inputs)
        _log(state["started"], "SEARCH", result.observation_text)
        for link in result.links:
            _log(
                state["started"],
                "  cand",
                f"{link.published} {link.title} <{link.url}>",
            )
        return result

    read = reader.read

    async def logged_read(url: str) -> str:
        try:
            text = await read(url)
        except Exception as error:
            _log(state["started"], "READ failed", f"{url} ({error})")
            raise
        _log(state["started"], "READ", f"{url} chars={len(text)}")
        return text

    return state, logged_search, logged_read


def _check(scenario: dict[str, Any], record: Record) -> str:
    expect = scenario.get("expect")
    if not expect or not record.jev:
        return "OBSERVE"
    first = str(record.jev[0]["choice"]).split(":")[0]
    ok = first == expect
    if "expect_memory" in scenario:
        ok = ok and record.jev[-1]["memory"] == scenario["expect_memory"]
    if "then" in scenario:
        ok = ok and scenario["then"] in record.current_user
    return "PASS" if ok else "CHECK"


async def run(
    scenarios: list[dict[str, Any]], model_id: str, environ: dict[str, str]
) -> int:
    budget = ModelTokenBudget(context_limit=1_050_000, response_tokens=4_096)
    key = environ["OPENROUTER_API_KEY"]
    model = OpenRouterModelAdapter(
        api_key=key,
        model_id=model_id,
        token_budget=budget,
        timeout_seconds=60,
        max_attempts=2,
    )
    jev = JevDecisionAdapter(api_key=key, timeout_seconds=20)
    search = NaverNewsSearch(
        client_id=environ["NAVER_API_HUB_CLIENT_ID"],
        client_secret=environ["NAVER_API_HUB_CLIENT_SECRET"],
    )
    reader = UrlReader()
    state, logged_search, logged_read = _instrument(jev, model, search, reader)
    tool = search.tool()
    tool = type(tool)(
        name=tool.name,
        description=tool.description,
        execute=logged_search,
        arguments_schema=tool.arguments_schema,
        progress=tool.progress,
    )
    store = InMemoryConversationStore()

    async def deliver(user_key: str, text: str) -> None:
        print("  ----- answer -----\n" + text + "\n  ------------------", flush=True)

    orchestrator = ConversationOrchestrator(
        store=store,
        assembler=PromptContextAssembler(model.count_input_tokens),
        memory_reviewer=AutomaticMemoryReviewer(
            store,
            model.review_memory,
            decide_change=jev.decide_memory_change,
            instruction=MEMORY_INSTRUCTION,
        ),
        compactor=TokenCompactor(store, model.summarize),
        generate_answer=model.generate_answer,
        deliver=deliver,
        system_prompt=SYSTEM_PROMPT,
        token_budget=budget,
        model_id=model_id,
        explicit_memory_failure_notice="(메모리 변경에 실패했습니다.)",
        read_tools=(tool,),
        read_url=logged_read,
        choose_next=jev.choose_next,
        build_tool_call=model.generate_tool_call,
    )
    rows = []
    try:
        for scenario in scenarios:
            print(f"\n===== {scenario['id']}: {scenario['message']}", flush=True)
            state["record"], state["started"] = Record(), time.monotonic()
            first = asyncio.create_task(
                orchestrator.submit(user_key=USER, message=scenario["message"])
            )
            tasks = [first]
            if "then" in scenario:
                await asyncio.sleep(scenario.get("then_after", 3))
                print(f"  + while processing: {scenario['then']}", flush=True)
                tasks.append(
                    asyncio.create_task(
                        orchestrator.submit(user_key=USER, message=scenario["then"])
                    )
                )
            results = await asyncio.gather(*tasks)
            record: Record = state["record"]
            memory = store.get_memory(USER)
            _log(state["started"], "RESULT", " ".join(str(r.status) for r in results))
            _log(
                state["started"],
                "MEMORY",
                json.dumps(memory.memory_text if memory else None, ensure_ascii=False),
            )
            tokens = [j["tokens"] for j in record.jev if j["tokens"] is not None]
            rows.append(
                (
                    scenario["id"],
                    scenario.get("expect", "-"),
                    " > ".join(str(j["choice"]) for j in record.jev),
                    _check(scenario, record),
                    f"{time.monotonic() - state['started']:.1f}s",
                    max(tokens, default=0),
                )
            )
    finally:
        await search.aclose()
        await reader.aclose()
        await jev.aclose()
        await model.aclose()

    print("\n===== summary (id | expect | Jev choices | check | time | max Jev tokens)")
    for row in rows:
        print(" | ".join(str(value) for value in row))
    return 0 if all(row[3] != "CHECK" for row in rows) else 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", default="openai/gpt-6-luna")
    parser.add_argument("--only", help="comma-separated scenario ids, in file order")
    args = parser.parse_args()
    scenarios = json.loads(SCENARIOS.read_text(encoding="utf-8"))
    if args.only:
        wanted = set(args.only.split(","))
        scenarios = [s for s in scenarios if s["id"] in wanted]
    missing = [
        name
        for name in (
            "OPENROUTER_API_KEY",
            "NAVER_API_HUB_CLIENT_ID",
            "NAVER_API_HUB_CLIENT_SECRET",
        )
        if not os.environ.get(name)
    ]
    if missing:
        print(f"missing environment variables: {', '.join(missing)}", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(asyncio.run(run(scenarios, args.model, dict(os.environ))))


if __name__ == "__main__":
    main()
