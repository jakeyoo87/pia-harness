"""Run the README section 1 flow with the real model, NAVER, Exa, and Jina.

Manual only: it calls paid APIs and model decisions vary, so it is not part of pytest
or CI. Scenarios run in order in one conversation, so later ones can refer to earlier
answers. Keys come from the environment and are never printed; Jina is called
without a key. The financials set adds past_financials with fake OpenDART and SEC
replies (tests/manual/fake_financials.py). Only the harness's own tools run here; a
host's tools (PIA's broker, order and feed tools) are checked in the host.

    OPENROUTER_API_KEY=... EXA_API_KEY=... NAVER_API_HUB_CLIENT_ID=... \\
    NAVER_API_HUB_CLIENT_SECRET=... python -m tests.manual.smoke_flow \\
        [--set read|financials] [--only 1,2]
        [--model ID]
        [--context-limit N] [--extra-prompt-file PATH]

--extra-prompt-file appends a host's own instructions. A scenario entry with "seed"
instead of "message" puts earlier Turns or Memory in place first.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from pia_harness import (
    Choices,
    ConversationOrchestrator,
    ExaWebSearch,
    JinaPageExtractor,
    ModelTokenBudget,
    NaverNewsSearch,
    OpenRouterModelAdapter,
    PastFinancialsTool,
    ReadToolDefinition,
)
from pia_harness.compaction import TokenCompactor
from pia_harness.context import PromptContextAssembler
from pia_harness.memory import MemoryReviewer
from pia_harness.session import MemoryDocument, new_turn_id
from pia_harness.testing import InMemoryConversationStore

from .fake_financials import client as fake_financials_client

SCENARIOS = Path(__file__).with_name("scenarios")
SCENARIO_SETS = ("read", "financials")
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
_MEMORY_ACTIONS = {"update": "UPDATE", "forget": "FORGET"}


@dataclass
class Record:
    """What one scenario did; filled by the instrumentation below."""

    # Each model step: the tools it called, or "answer".
    steps: list[str] = field(default_factory=list)
    # The first step's tool calls with their arguments, for expect_args.
    first_calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    # Every tool call with its arguments, and the delivered answer.
    all_calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    answer: str = ""
    memory: str = "NONE"
    current_user: str = ""
    tokens: list[int] = field(default_factory=list)
    # Provider-reported total tokens of each answer (the estimate above is not).
    usage: list[int] = field(default_factory=list)
    # Memory after the Turn.
    memory_text: str = ""
    # The options delivered under the answer, if any.
    choices: Choices | None = None
    # Provider calls besides the loop model, for the cost of one question.
    calls: dict[str, int] = field(
        default_factory=lambda: dict.fromkeys(("search", "page", "notes"), 0)
    )


def _log(started: float, event: str, detail: str = "") -> None:
    print(
        f"  [{time.monotonic() - started:5.1f}s] {event} {detail}".rstrip(), flush=True
    )


def _instrument(model: OpenRouterModelAdapter):
    """Wrap calls to log each model step, tool and HTTP attempt. No content is
    logged beyond the synthetic scenario's own arguments and answers."""
    state: dict[str, Any] = {"record": Record(), "started": time.monotonic()}
    reply = model.generate_reply

    async def logged_reply(context):
        record: Record = state["record"]
        record.current_user = next(
            (p.content for p in context.parts if p.kind.value == "CURRENT_USER"), ""
        )
        record.tokens.append(context.estimated_input_tokens)
        try:
            result = await reply(context)
        except Exception as error:
            event = getattr(error, "event", "")
            _log(state["started"], "MODEL failed", f"{type(error).__name__} {event}")
            raise
        if result.answer is not None:
            record.steps.append("answer")
            if result.answer.usage is not None:
                record.usage.append(result.answer.usage.total_tokens)
            _log(state["started"], "ANSWER", f"tools_offered={len(context.tools)}")
        else:
            names = []
            for call in result.tool_calls:
                names.append(call.name)
                arguments = json.loads(call.arguments_json or "{}")
                record.all_calls.append((call.name, arguments))
                if not record.steps:
                    record.first_calls.append((call.name, arguments))
                _log(state["started"], "CALL", f"{call.name} {call.arguments_json}")
                if call.name == "memory":
                    action = json.loads(call.arguments_json or "{}").get("action")
                    record.memory = _MEMORY_ACTIONS.get(action, record.memory)
            record.steps.append("+".join(names))
        return result

    model.generate_reply = logged_reply  # type: ignore[method-assign]

    read_json = model.read_json

    async def counted_json(messages, schema_name, schema):
        state["record"].calls["notes"] += 1
        return await read_json(messages, schema_name, schema)

    model.read_json = counted_json  # type: ignore[method-assign]

    # Each HTTP attempt to the model: path, status, time. No content.
    async def on_request(request: httpx.Request) -> None:
        request.extensions["started"] = time.monotonic()

    async def on_response(response: httpx.Response) -> None:
        began = response.request.extensions.get("started", time.monotonic())
        path = response.request.url.path.rsplit("/", 1)[-1]
        _log(
            state["started"],
            "HTTP",
            f"{path} {response.status_code} {time.monotonic() - began:.1f}s",
        )

    model._async_client.event_hooks = {
        "request": [on_request],
        "response": [on_response],
    }

    # Both search tools count as searches and log their candidates.
    def logged(tool: ReadToolDefinition) -> ReadToolDefinition:
        execute = tool.execute

        async def logged_search(user_key, call, inputs):
            state["record"].calls["search"] += 1
            result = await execute(user_key, call, inputs)
            _log(state["started"], "SEARCH", result.observation_text)
            for link in result.links:
                _log(
                    state["started"],
                    "  cand",
                    f"{link.published} {link.title} <{link.url}>",
                )
            return result

        return ReadToolDefinition(
            name=tool.name,
            description=tool.description,
            execute=logged_search,
            arguments_schema=tool.arguments_schema,
        )

    return state, logged


def _instrument_extract(state: dict[str, Any], extractor: JinaPageExtractor) -> None:
    extract = extractor.extract

    async def logged_extract(url: str, goal: str, request: str):
        state["record"].calls["page"] += 1
        try:
            excerpt = await extract(url, goal, request)
        except Exception as error:
            _log(state["started"], "EXTRACT failed", f"{url} ({error})")
            raise
        chars = len(excerpt.summary) + sum(map(len, excerpt.passages))
        _log(state["started"], "EXTRACT", f"{url} {excerpt.status} chars={chars}")
        return excerpt

    extractor.extract = logged_extract  # type: ignore[method-assign]


# Scenario keys that make a step checked; a step with none of them is observed.
_CHECK_KEYS = frozenset(
    {
        "expect",
        "forbid",
        "memory_excludes",
        "expect_memory",
        "expect_choices",
        "then",
        "uses",
        "answer_includes",
        "answer_excludes",
    }
)


def _choices_ok(scenario: dict[str, Any], choices: Choices | None) -> bool:
    """expect_choices: true (any), an option that must be offered, or the exact
    list; expect_multiple: whether several may be picked."""
    wanted = scenario["expect_choices"]
    if choices is None:
        return wanted is False
    if (
        "expect_multiple" in scenario
        and choices.multiple != scenario["expect_multiple"]
    ):
        return False
    if isinstance(wanted, str):
        return wanted in choices.options
    if isinstance(wanted, list):
        return list(choices.options) == wanted
    return wanted is True


def _check(scenario: dict[str, Any], record: Record) -> str:
    """Each condition the scenario names is checked on its own."""
    expect = scenario.get("expect")
    if not _CHECK_KEYS & set(scenario):
        return "OBSERVE"
    if not record.steps:  # the model never answered: nothing was shown
        return "CHECK"
    # The first step's tools, without the memory tool that may come with them.
    first = [
        name for name in record.steps[0].split("+") if name != "memory"
    ] or record.steps[0].split("+")
    ok = not expect or expect in first or (expect == "answer" and first == ["memory"])
    if "expect_args" in scenario:
        wanted = scenario["expect_args"].items()
        ok = ok and any(
            name == expect
            and all(arguments.get(k) == v for k, v in wanted)
            for name, arguments in record.first_calls
        )
    if "expect_memory" in scenario:
        ok = ok and record.memory == scenario["expect_memory"]
    if "expect_choices" in scenario:
        ok = ok and _choices_ok(scenario, record.choices)
    if "then" in scenario:
        ok = ok and scenario["then"] in record.current_user
    used = {name for step in record.steps for name in step.split("+")}
    ok = ok and not set(scenario.get("forbid", ())) & used
    ok = ok and set(scenario.get("uses", ())) <= used
    ok = ok and all(
        text in record.answer for text in scenario.get("answer_includes", ())
    )
    ok = ok and not any(
        text in record.answer for text in scenario.get("answer_excludes", ())
    )
    ok = ok and not any(
        word in record.memory_text for word in scenario.get("memory_excludes", ())
    )
    return "PASS" if ok else "CHECK"


async def run(
    scenarios: list[dict[str, Any]],
    model_id: str,
    environ: dict[str, str],
    scenario_set: str = "read",
    context_limit: int = 1_050_000,
    extra_prompt: str = "",
) -> int:
    budget = ModelTokenBudget(context_limit=context_limit, response_tokens=4_096)
    key = environ["OPENROUTER_API_KEY"]
    model = OpenRouterModelAdapter(
        api_key=key,
        model_id=model_id,
        token_budget=budget,
        timeout_seconds=60,
        max_attempts=2,
    )
    search = ExaWebSearch(api_key=environ["EXA_API_KEY"])
    news = NaverNewsSearch(
        client_id=environ["NAVER_API_HUB_CLIENT_ID"],
        client_secret=environ["NAVER_API_HUB_CLIENT_SECRET"],
    )
    state, logged = _instrument(model)

    def logged_result(tool: ReadToolDefinition) -> ReadToolDefinition:
        execute = tool.execute

        async def logged_execute(user_key, call, inputs):
            result = await execute(user_key, call, inputs)
            _log(state["started"], "RESULT " + tool.name, result.observation_text)
            return result

        return ReadToolDefinition(
            name=tool.name,
            description=tool.description,
            execute=logged_execute,
            arguments_schema=tool.arguments_schema,
        )

    extractor = JinaPageExtractor(model.read_json)
    _instrument_extract(state, extractor)
    search_tools = (logged(news.tool()), logged(search.tool()))
    store = InMemoryConversationStore()
    financials_client = None
    financial_tools = ()
    if scenario_set == "financials":
        financials_client = fake_financials_client()
        financial_tools = (
            logged_result(
                PastFinancialsTool(
                    dart_api_key="FAKE-OPENDART-KEY",
                    sec_user_agent="PIA smoke test@example.invalid",
                    async_client=financials_client,
                ).tool()
            ),
        )

    async def deliver(user_key: str, text: str, choices: Choices | None) -> None:
        print("  ----- answer -----\n" + text + "\n  ------------------", flush=True)
        state["record"].answer = text
        state["record"].choices = choices
        if choices is not None:
            kind = "여러 개" if choices.multiple else "하나"
            print(f"  [buttons/{kind}] " + " | ".join(choices.options), flush=True)

    orchestrator = ConversationOrchestrator(
        store=store,
        assembler=PromptContextAssembler(model.count_input_tokens),
        memory_reviewer=MemoryReviewer(
            store,
            model.review_memory,
            instruction=MEMORY_INSTRUCTION,
        ),
        compactor=TokenCompactor(store, model.summarize),
        generate_reply=model.generate_reply,
        deliver=deliver,
        system_prompt=SYSTEM_PROMPT + (f"\n\n{extra_prompt}" if extra_prompt else ""),
        token_budget=budget,
        model_id=model_id,
        explicit_memory_failure_notice="(메모리 변경에 실패했습니다.)",
        read_tools=(*search_tools, extractor.tool(), *financial_tools),
    )
    rows = []
    try:
        for scenario in scenarios:
            if "seed" in scenario:
                _seed(store, scenario["seed"])
                print(f"\n===== {scenario['id']}: seeded {sorted(scenario['seed'])}")
                continue
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
            record.memory_text = memory.memory_text if memory else ""
            _log(state["started"], "RESULT", " ".join(str(r.status) for r in results))
            _log(
                state["started"],
                "MEMORY",
                json.dumps(memory.memory_text if memory else None, ensure_ascii=False),
            )
            _log(state["started"], "CALLS", f"model={len(record.steps)} {record.calls}")
            # Compaction runs after the answer; show the Summary it left.
            session = store.get_or_create_active_session(USER, now=datetime.now(UTC))
            summary = store.get_summary(user_key=USER, session_id=session.session_id)
            if summary is not None and summary.summary_text != state.get("summary"):
                state["summary"] = summary.summary_text
                print(
                    "  ----- new Summary -----\n"
                    + summary.summary_text
                    + "\n  ------------------",
                    flush=True,
                )
            if any(result.compaction_failed for result in results):
                _log(state["started"], "COMPACTION", "failed")
            rows.append(
                (
                    scenario["id"],
                    scenario.get("expect", "-"),
                    " > ".join(record.steps),
                    _check(scenario, record),
                    f"{time.monotonic() - state['started']:.1f}s",
                    max(record.tokens, default=0),
                    sum(record.usage),
                    "/".join(
                        str(n) for n in (len(record.steps), *record.calls.values())
                    ),
                )
            )
    finally:
        await search.aclose()
        await news.aclose()
        await extractor.aclose()
        await model.aclose()
        if financials_client is not None:
            await financials_client.aclose()

    print(
        "\n===== summary (id | expect | model steps | check | time"
        " | max input tokens (estimate) | reported tokens of final answers"
        " | calls model/search/page/notes)"
    )
    for row in rows:
        print(" | ".join(str(value) for value in row))
    return 0 if all(row[3] != "CHECK" for row in rows) else 1


def _seed(store: Any, seed: dict[str, Any]) -> None:
    """Earlier Turns and Memory, as a host would have them already."""
    now = datetime.now(UTC)
    session = store.get_or_create_active_session(USER, now=now)
    turn_id = None
    for index, turn in enumerate(seed.get("turns", ())):
        created = now - timedelta(minutes=10 - index)
        turn_id = new_turn_id(created)
        store.append_completed_turn(
            user_key=USER,
            session_id=session.session_id,
            turn_id=turn_id,
            user_message=turn["user"],
            assistant_message=turn["assistant"],
            created_at=created,
        )
    if "memory" in seed:
        reviewed = turn_id or new_turn_id(now - timedelta(minutes=11))
        store.replace_memory(
            MemoryDocument(USER, seed["memory"], reviewed, now),
            expected_last_reviewed_turn_id=None,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", default="openai/gpt-6-luna")
    parser.add_argument("--set", default="read", choices=SCENARIO_SETS)
    parser.add_argument("--context-limit", type=int, default=1_050_000)
    parser.add_argument("--extra-prompt-file", help="host instructions to append")
    parser.add_argument("--only", help="comma-separated scenario ids, in file order")
    args = parser.parse_args()
    scenarios = json.loads((SCENARIOS / f"{args.set}.json").read_text(encoding="utf-8"))
    if args.only:
        wanted = set(args.only.split(","))
        scenarios = [s for s in scenarios if s["id"] in wanted]
    missing = [
        name
        for name in (
            "OPENROUTER_API_KEY",
            "EXA_API_KEY",
            "NAVER_API_HUB_CLIENT_ID",
            "NAVER_API_HUB_CLIENT_SECRET",
        )
        if not os.environ.get(name)
    ]
    if missing:
        print(f"missing environment variables: {', '.join(missing)}", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(
        asyncio.run(
            run(
                scenarios,
                args.model,
                dict(os.environ),
                args.set,
                args.context_limit,
                Path(args.extra_prompt_file).read_text(encoding="utf-8")
                if args.extra_prompt_file
                else "",
            )
        )
    )


if __name__ == "__main__":
    main()
