"""Run the README section 1 flow with the real model, NAVER, Exa, and Jina.

Manual only: it calls paid APIs and model decisions vary, so it is not part of pytest
or CI. Scenarios run in order in one conversation, so later ones can refer to earlier
answers. Keys come from the environment and are never printed; Jina is called
without a key. The execution and broker sets use a fake pia-broker with fixed
replies; no real order or account lookup is ever sent.

    OPENROUTER_API_KEY=... EXA_API_KEY=... NAVER_API_HUB_CLIENT_ID=... \\
    NAVER_API_HUB_CLIENT_SECRET=... python -m tests.manual.smoke_flow \\
        [--set read|execution|broker] [--only 1,2]
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
    BrokerReadTool,
    ConversationOrchestrator,
    ExaWebSearch,
    JinaPageExtractor,
    ModelTokenBudget,
    NaverNewsSearch,
    OpenRouterModelAdapter,
    ReadToolDefinition,
)
from pia_harness.broker_read import _ArgumentProblem, _resolve
from pia_harness.compaction import TokenCompactor
from pia_harness.context import PromptContextAssembler
from pia_harness.memory import MemoryReviewer
from pia_harness.testing import InMemoryConversationStore

SCENARIOS = Path(__file__).with_name("scenarios")
SCENARIO_SETS = ("read", "execution", "broker")
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
# The broker set adds what PIA adds when Broker tools are registered (pia-agent
# app/core.py BROKER_READ_PROMPT and ORDER_PROMPT, broker unification plan §9).
BROKER_PROMPT = (
    " PIA can use the broker tool to look up the user's account (cash, holdings, and "
    "what one stock they can buy or sell), live quotes, price and investor history "
    "for a stock or the KOSPI/KOSDAQ market, and market rankings. PIA can place "
    "Korean stock buy and sell orders, but only after the user agrees to the "
    "confirmation question in the very next message."
)
_MEMORY_ACTIONS = {"update": "UPDATE", "forget": "FORGET"}
_EXECUTION_TOOLS = frozenset({"order", "confirm"})


@dataclass
class Record:
    """What one scenario did; filled by the instrumentation below."""

    # Each model step: the tools it called, or "answer".
    steps: list[str] = field(default_factory=list)
    # The first step's tool calls with their arguments, for expect_args.
    first_calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    memory: str = "NONE"
    current_user: str = ""
    tokens: list[int] = field(default_factory=list)
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
            _log(state["started"], "ANSWER", f"tools_offered={len(context.tools)}")
        else:
            names = []
            for call in result.tool_calls:
                names.append(call.name)
                if not record.steps:
                    arguments = json.loads(call.arguments_json or "{}")
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


def _fake_broker(started: dict[str, Any], orders: list[dict[str, Any]]):
    """pia-broker's internal routes with fixed data (README 4~8 of pia-broker)."""

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
        if path.endswith(("/account", "/history", "/ranking", "/broker-status")):
            return httpx.Response(200, json=_fake_read(path, request.url.params))
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


def _fake_read(path: str, params: httpx.QueryParams) -> dict[str, Any]:
    """Fixed replies in the shapes of pia-broker's read routes."""

    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    position = {
        "code": "005930",
        "name": "삼성전자",
        "quantity": 10,
        "sellable_quantity": 10,
        "average_price": 270000,
        "current_price": 285500,
        "valuation": 2855000,
        "profit": 155000,
        "profit_rate": 5.74,
    }
    if path.endswith("/broker-status"):
        connection = {
            "broker": "KIS",
            "lifecycle_status": "PENDING",
            "verification_status": "VERIFIED",
            "verification_reason": None,
        }
        return {"connection": connection}
    if path.endswith("/account"):
        if "code" in params:
            buyable = {
                "code": params["code"],
                "amount": 2855000,
                "quantity": 10,
                "unit_price": 285500,
                "observed_at": now,
            }
            held = position if params["code"] == "005930" else None
            return {"buyable": buyable, "position": held}
        return {
            "positions": [position],
            "cash": 3000000,
            "cash_d2": 2500000,
            "total_valuation": 5855000,
            "total_profit": 155000,
            "observed_at": now,
        }
    if path.endswith("/history"):
        stock = "code" in params
        if params["data"] == "investors":
            flows = {
                "individual_net_volume": -120000,
                "individual_net_value": -34260,
                "foreign_net_volume": 100000,
                "foreign_net_value": 28550,
                "institution_net_volume": 20000,
                "institution_net_value": 5710,
            }
            rows = [
                {
                    "date": f"2026-09-{day:02d}",
                    "close": 285500 if stock else 812.34,
                    "change": 1500 if stock else 3.21,
                    "flows": flows,
                }
                for day in range(25, 21, -1)
            ]
        else:
            close = 285500 if stock else 2621.07
            rows = [
                {
                    "date": f"2026-09-{day:02d}",
                    "open": close,
                    "high": close,
                    "low": close,
                    "close": close,
                    "volume": 1000000 if stock else None,
                    "trading_value": 285500000000,
                }
                for day in range(25, 21, -1)
            ]
        unit = {"6m": "week", "1y": "week", "3y": "month", "5y": "month"}
        return {
            "data": params["data"],
            "code": params.get("code"),
            "market": params.get("market"),
            "period": params["period"],
            "unit": unit.get(params["period"], "day"),
            "observed_at": now,
            "rows": rows,
        }
    figures = {
        "market_cap": {"market_cap": 16135729, "market_cap_share": 23.84},
        "foreign_buying": {"net_buy_value": 72542, "net_buy_volume": 254000},
        "short_selling": {"short_volume": 1200000, "short_value": 342600000000},
    }.get(params["by"], {})
    row = {
        "code": "005930",
        "name": "삼성전자",
        "price": 285500,
        "change": 1500,
        "change_rate": 0.53,
        "volume": 1000000,
        "figures": figures,
    }
    reply = {
        "by": params["by"],
        "market": params["market"],
        "observed_at": now,
        "rows": [row],
    }
    if params["by"] == "short_selling":
        reply |= {"period": params["period"], "basis_dates": ["20260922", "20260926"]}
    return reply


def _filled(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Broker arguments as the tool runs them, defaults included; {} if it would not run."""

    if name != "broker":
        return arguments
    try:
        return _resolve(arguments)
    except _ArgumentProblem:
        return {}


def _check(scenario: dict[str, Any], record: Record) -> str:
    expect = scenario.get("expect")
    if not expect or not record.steps:
        return "OBSERVE"
    # The first step's tools, without the memory tool that may come with them.
    first = [
        name for name in record.steps[0].split("+") if name != "memory"
    ] or record.steps[0].split("+")
    ok = expect in first or (expect == "answer" and first == ["memory"])
    if "expect_args" in scenario:
        wanted = scenario["expect_args"].items()
        ok = ok and any(
            name == expect
            and all(_filled(name, arguments).get(k) == v for k, v in wanted)
            for name, arguments in record.first_calls
        )
    if expect == "broker":  # a broker lookup must not start an order
        used = {name for step in record.steps for name in step.split("+")}
        ok = ok and not _EXECUTION_TOOLS & used
    if "expect_memory" in scenario:
        ok = ok and record.memory == scenario["expect_memory"]
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
    orders: list[dict[str, Any]] = []
    execution_tools = ()
    broker_tools = ()
    broker_client = None
    if scenario_set in ("execution", "broker"):
        broker_client = _fake_broker(state, orders)
        execution_tools = (
            BrokerOrderTool(
                base_url="https://broker.invalid", client=broker_client
            ).tool(),
        )
    if scenario_set == "broker":
        broker_tools = (
            logged_result(
                BrokerReadTool(
                    base_url="https://broker.invalid", client=broker_client
                ).tool()
            ),
        )

    async def deliver(user_key: str, text: str) -> None:
        print("  ----- answer -----\n" + text + "\n  ------------------", flush=True)

    orchestrator = ConversationOrchestrator(
        store=store,
        assembler=PromptContextAssembler(model.count_input_tokens),
        memory_reviewer=MemoryReviewer(
            store, model.review_memory, instruction=MEMORY_INSTRUCTION
        ),
        compactor=TokenCompactor(store, model.summarize),
        generate_reply=model.generate_reply,
        deliver=deliver,
        system_prompt=SYSTEM_PROMPT
        + (BROKER_PROMPT if scenario_set == "broker" else ""),
        token_budget=budget,
        model_id=model_id,
        explicit_memory_failure_notice="(메모리 변경에 실패했습니다.)",
        read_tools=(*search_tools, extractor.tool(), *broker_tools),
        execution_tools=execution_tools,
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
            if scenario_set in ("execution", "broker"):
                _log(state["started"], "ORDERS SENT", str(len(orders)))
            rows.append(
                (
                    scenario["id"],
                    scenario.get("expect", "-"),
                    " > ".join(record.steps),
                    _check(scenario, record),
                    f"{time.monotonic() - state['started']:.1f}s",
                    max(record.tokens, default=0),
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
        if broker_client is not None:
            await broker_client.aclose()

    print(
        "\n===== summary (id | expect | model steps | check | time"
        " | max input tokens | calls model/search/page/notes)"
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
        asyncio.run(run(scenarios, args.model, dict(os.environ), args.set))
    )


if __name__ == "__main__":
    main()
