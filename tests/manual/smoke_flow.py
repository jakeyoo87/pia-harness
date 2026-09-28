"""Run the README section 1 flow with real Jev, OpenRouter, Exa, and Jina.

Manual only: it calls paid APIs and model decisions vary, so it is not part of pytest
or CI. Scenarios run in order in one conversation, so later ones can refer to earlier
answers. Keys come from the environment and are never printed; Exa and Jina are
called without keys. The execution set uses a fake pia-broker with fixed replies; no
real order is ever sent.

    OPENROUTER_API_KEY=... python -m tests.manual.smoke_flow \\
        [--set read|execution] [--only 1,2]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from pia_harness import (
    BrokerOrderTool,
    ConversationOrchestrator,
    ExaWebSearch,
    JevDecisionAdapter,
    JinaPageExtractor,
    ModelTokenBudget,
    OpenRouterModelAdapter,
    PageExcerpt,
)
from pia_harness.compaction import TokenCompactor
from pia_harness.context import PromptContextAssembler
from pia_harness.memory import AutomaticMemoryReviewer
from pia_harness.testing import InMemoryConversationStore

SCENARIOS = Path(__file__).with_name("scenarios")
SCENARIO_SETS = ("read", "execution")
# name: (code, market, current price). The fake order reply is UNKNOWN for 005935
# so one scenario sees the "check the broker app" result.
FAKE_INSTRUMENTS = {
    "삼성전자": ("005930", "KOSPI", 285500),
    "삼성전자우": ("005935", "KOSPI", 231000),
    "SK하이닉스": ("000660", "KOSPI", 351000),
}
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
    # Model and provider calls besides Jev, for the cost of one question.
    calls: dict[str, int] = field(
        default_factory=lambda: dict.fromkeys(
            ("tool_args", "search", "page", "passages"), 0
        )
    )


def _log(started: float, event: str, detail: str = "") -> None:
    print(
        f"  [{time.monotonic() - started:5.1f}s] {event} {detail}".rstrip(), flush=True
    )


def _instrument(
    jev: JevDecisionAdapter, model: OpenRouterModelAdapter, search: ExaWebSearch
):
    """Wrap calls to log decisions. Jev probabilities and token counts are only in
    the raw response, so this reaches into the adapter's private post method."""
    state: dict[str, Any] = {"record": Record(), "started": time.monotonic()}
    post_async = jev._post_async

    async def jev_post(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            result = await post_async(payload)
        except Exception as error:
            _log(state["started"], "JEV failed", type(error).__name__)
            raise
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
        state["record"].calls["tool_args"] += 1
        call = await tool_call(context, tool)
        _log(state["started"], "TOOL_ARGS", call.arguments_json)
        return call

    model.generate_tool_call = logged_tool_call  # type: ignore[method-assign]

    notes = model.read_page_notes

    async def counted_notes(goal: str, request: str, page: str):
        state["record"].calls["passages"] += 1
        return await notes(goal, request, page)

    model.read_page_notes = counted_notes  # type: ignore[method-assign]

    # Each HTTP attempt to Jev or the model: host, status, time. No content.
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

    answer = model.generate_answer

    async def logged_answer(context):
        try:
            return await answer(context)
        except Exception as error:
            event = getattr(error, "event", "")
            _log(state["started"], "ANSWER failed", f"{type(error).__name__} {event}")
            raise

    model.generate_answer = logged_answer  # type: ignore[method-assign]

    hooks = {"request": [on_request], "response": [on_response]}
    jev._async.event_hooks = hooks
    model._async_client.event_hooks = hooks

    execute = search.execute

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

    return state, logged_search


def _logged_extract(state: dict[str, Any], extractor: JinaPageExtractor):
    async def logged_extract(url: str, goal: str, request: str) -> PageExcerpt:
        state["record"].calls["page"] += 1
        try:
            excerpt = await extractor.extract(url, goal, request)
        except Exception as error:
            _log(state["started"], "EXTRACT failed", f"{url} ({error})")
            raise
        chars = len(excerpt.summary) + sum(map(len, excerpt.passages))
        _log(state["started"], "EXTRACT", f"{url} {excerpt.status} chars={chars}")
        return excerpt

    return logged_extract


def _fake_broker(started: dict[str, Any], orders: list[dict[str, Any]]):
    """pia-broker's internal routes with fixed data (README 4·5 of pia-broker)."""

    def item(name: str) -> dict[str, str]:
        code, market, _ = FAKE_INSTRUMENTS[name]
        return {"code": code, "name": name, "market": market}

    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/internal/instruments":
            query = "".join(request.url.params["query"].split()).casefold()
            exact = [
                name
                for name, (code, _, _) in FAKE_INSTRUMENTS.items()
                if name.casefold() == query or code == query
            ]
            found = exact or [n for n in FAKE_INSTRUMENTS if query in n.casefold()]
            _log(started["started"], "BROKER search", f"{query} -> {found}")
            if len(found) == 1:
                return httpx.Response(200, json={"status": "FOUND", **item(found[0])})
            if found:
                candidates = [item(name) for name in found]
                return httpx.Response(
                    200, json={"status": "AMBIGUOUS", "candidates": candidates}
                )
            return httpx.Response(200, json={"status": "NOT_FOUND"})
        if path.endswith("/quote"):
            code = request.url.params["code"]
            price = next(v[2] for v in FAKE_INSTRUMENTS.values() if v[0] == code)
            now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
            return httpx.Response(
                200, json={"code": code, "price": price, "observed_at": now}
            )
        if path.endswith("/orders"):
            body = json.loads(request.content)
            orders.append(body)
            _log(started["started"], "BROKER ORDER", json.dumps(body))
            if body["code"] == "005935":
                return httpx.Response(
                    200, json={"status": "UNKNOWN", "order_id": body["request_id"]}
                )
            return httpx.Response(
                200,
                json={
                    "status": "ACCEPTED",
                    "order_id": body["request_id"],
                    "broker_order_no": f"{len(orders):010d}",
                    "ordered_at": datetime.now(UTC).isoformat(),
                },
            )
        return httpx.Response(404)

    return httpx.AsyncClient(
        base_url="https://broker.invalid", transport=httpx.MockTransport(handle)
    )


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
    scenarios: list[dict[str, Any]],
    model_id: str,
    environ: dict[str, str],
    scenario_set: str = "read",
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
    search = ExaWebSearch()
    state, logged_search = _instrument(jev, model, search)
    # Built after _instrument so it calls the counted passages method.
    extractor = JinaPageExtractor(model.read_page_notes)
    tool = search.tool()
    tool = type(tool)(
        name=tool.name,
        description=tool.description,
        execute=logged_search,
        arguments_schema=tool.arguments_schema,
    )
    store = InMemoryConversationStore()
    orders: list[dict[str, Any]] = []
    execution_tools = ()
    broker_client = None
    if scenario_set == "execution":
        broker_client = _fake_broker(state, orders)
        execution_tools = (
            BrokerOrderTool(
                base_url="https://broker.invalid", client=broker_client
            ).definition(),
        )

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
        execution_tools=execution_tools,
        extract_page=_logged_extract(state, extractor),
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
            _log(state["started"], "CALLS", f"jev={len(record.jev)} {record.calls}")
            if scenario_set == "execution":
                _log(state["started"], "ORDERS SENT", str(len(orders)))
            rows.append(
                (
                    scenario["id"],
                    scenario.get("expect", "-"),
                    " > ".join(str(j["choice"]) for j in record.jev),
                    _check(scenario, record),
                    f"{time.monotonic() - state['started']:.1f}s",
                    max(tokens, default=0),
                    "/".join(str(n) for n in (len(record.jev), *record.calls.values())),
                )
            )
    finally:
        await search.aclose()
        await extractor.aclose()
        await jev.aclose()
        await model.aclose()
        if broker_client is not None:
            await broker_client.aclose()

    print(
        "\n===== summary (id | expect | Jev choices | check | time | max Jev tokens"
        " | calls jev/args/search/page/passages)"
    )
    for row in rows:
        print(" | ".join(str(value) for value in row))
    return 0 if all(row[3] != "CHECK" for row in rows) else 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", default="openai/gpt-6-luna")
    parser.add_argument("--set", default="read", choices=SCENARIO_SETS)
    parser.add_argument("--only", help="comma-separated scenario ids, in file order")
    args = parser.parse_args()
    scenarios = json.loads((SCENARIOS / f"{args.set}.json").read_text(encoding="utf-8"))
    if args.only:
        wanted = set(args.only.split(","))
        scenarios = [s for s in scenarios if s["id"] in wanted]
    missing = [name for name in ("OPENROUTER_API_KEY",) if not os.environ.get(name)]
    if missing:
        print(f"missing environment variables: {', '.join(missing)}", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(
        asyncio.run(run(scenarios, args.model, dict(os.environ), args.set))
    )


if __name__ == "__main__":
    main()
