"""The member's investment plan: the one place for portfolio strategy and operation.

Six sections; every change saves the whole plan as a new version with what
changed and why, so the version list is the decision log. Every change waits
for the user's confirmation. The host stores versions (PlanStore) and adds
PLAN_PROMPT to its system prompt and PLAN_MEMORY_RULE to its Memory guidance.
Plans: pia plans/2026-10-09_investment-plan.md, plans/2026-10-09_investment-plan-fixes.md.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, timezone
from typing import Any, Protocol

from .orchestrator import (
    ConversationInput,
    ExecutionToolDefinition,
    PreparationResult,
    PreparedAction,
    ReadToolDefinition,
    ReadToolResult,
    ToolCall,
)
from .persistence import SessionStoreError

# (name, label); labels are what the user sees and what the model must keep.
SECTIONS = (
    ("goal", "투자 목표"),
    ("rules", "운용 원칙"),
    ("portfolio", "목표 포트폴리오"),
    ("watchlist", "관심 종목"),
    ("market", "시장 판단"),
    ("checks", "점검할 일"),
)
LABELS = dict(SECTIONS)
SECTION_MAX_CHARS = 1_500
PLAN_MAX_CHARS = 6_000
REASON_MAX_CHARS = 300
HISTORY_LIMIT = 30
_KST = timezone(timedelta(hours=9))

PLAN_PROMPT = (
    "The host document in the context is the user's investment plan, the only "
    "source of the user's current strategy; strategy mentioned in Memory, the "
    "Summary or earlier turns is a past record and never overrides the plan. "
    "Change the plan only when the user explicitly asks to, or agrees to an option "
    "you offered; a question or a passing remark gets an answer, not a plan "
    "change. When the user asks how to set weights or a strategy, offer two or "
    "three options with their trade-offs and let the user choose; never decide "
    "for the user. When the plan is empty and the user wants to make one, first "
    "ask a few short questions (goal, horizon, risk profile, how they invest now; "
    "read holdings with the broker tool instead of asking); once they answer, "
    "propose saving what they decided with the plan tool, leaving undecided "
    "sections empty, and offer options only for what is still open. When showing the plan, keep its section names as written. Refer "
    "to plan versions by their date and time, never by version number."
)
PLAN_MEMORY_RULE = (
    "Do not keep investment goals, risk profile, strategy, target weights, watched "
    "stocks or market views: those belong only to the investment plan."
)
PLAN_TOOL_DESCRIPTION = (
    "Change the user's investment plan; every change waits for the user's "
    "confirmation. Give only the sections that change, each as its complete new "
    "text, and why. Sections: goal (goal, horizon, risk profile), rules (rules "
    "always kept, such as weight caps, cash level, review triggers), portfolio "
    "(target weights by country, cash and stock, the reason for each holding, "
    "planned buys and sells), watchlist (stocks followed and their conditions), "
    "market (the current market view and its date), checks (things to review "
    "later). Write only the plan's content: no notes about drafts or confirmation, "
    "and never current holdings, balances or orders. Give an empty string to clear "
    "a section. To restore an earlier version, read it with plan_history and give "
    "its sections."
)
HISTORY_TOOL_DESCRIPTION = (
    "Read earlier versions of the user's investment plan. Without as_of, lists the "
    "latest changes (date and time, changed sections, reason). With as_of, returns "
    "the whole plan as it was then: a date YYYY-MM-DD (end of that day, KST) or a "
    "listed date and time YYYY-MM-DD HH:MM. The current plan is already in the "
    "context."
)


@dataclass(frozen=True, slots=True)
class PlanVersion:
    version: int
    sections: Mapping[str, str]
    changed: tuple[str, ...]
    reason: str
    created_at: datetime


class PlanStore(Protocol):
    def get_plan(self, user_key: str) -> PlanVersion | None:
        """The latest version."""

    def list_plans(
        self, user_key: str, *, limit: int, saved_through: datetime | None = None
    ) -> tuple[PlanVersion, ...]:
        """Newest first; with saved_through, only versions saved by then."""

    def save_plan(self, user_key: str, plan: PlanVersion) -> bool:
        """Store a new version; False if that version already exists."""


class InvestmentPlan:
    def __init__(
        self, store: PlanStore, *, now: Callable[[], datetime] | None = None
    ) -> None:
        self._store = store
        self._now = now or (lambda: datetime.now(UTC))

    def document(self, user_key: str) -> str:
        """The current plan as the conversation sees it (host document)."""
        plan = self._store.get_plan(user_key)
        return "투자 계획: 아직 없음" if plan is None else render(plan)

    def plan_tool(self) -> ExecutionToolDefinition:
        return ExecutionToolDefinition(
            "plan",
            PLAN_TOOL_DESCRIPTION,
            {
                "type": "object",
                "properties": {
                    "sections": {
                        "type": "object",
                        "properties": {
                            name: {"type": "string", "description": label}
                            for name, label in SECTIONS
                        },
                        "additionalProperties": False,
                        "minProperties": 1,
                    },
                    "reason": {
                        "type": "string",
                        "description": "What changes and why, in Korean.",
                    },
                },
                "required": ["sections", "reason"],
                "additionalProperties": False,
            },
            self._prepare,
            self._execute,
        )

    def history_tool(self) -> ReadToolDefinition:
        return ReadToolDefinition(
            "plan_history",
            HISTORY_TOOL_DESCRIPTION,
            self._history,
            arguments_schema={
                "type": "object",
                "properties": {
                    "as_of": {
                        "type": "string",
                        "description": "YYYY-MM-DD or YYYY-MM-DD HH:MM (KST)",
                    },
                },
                "additionalProperties": False,
            },
        )

    async def _prepare(self, user_key: str, call: ToolCall) -> PreparationResult:
        request = _change_request(call.arguments_json)
        if isinstance(request, str):
            return PreparationResult(f"Not prepared: {request}")
        sections, reason = request
        current = await asyncio.to_thread(self._store.get_plan, user_key)
        merged, changed = _merge(current, sections)
        if not changed:
            return PreparationResult("No change: the plan already says this.")
        problem = _size_problem(merged)
        if problem is not None:
            return PreparationResult(f"Not prepared: {problem}")
        changes = {name: sections[name] for name in changed}
        shown = "\n\n".join(
            f"[{LABELS[name]}]\n{changes[name] or '(비움)'}" for name in changed
        )
        return PreparationResult(
            "Plan change prepared; the user must confirm it.",
            PreparedAction(
                f"투자 계획 변경: {_labels(changed)}",
                f"투자 계획을 이렇게 바꿀까요?\n\n{shown}\n\n이유: {reason}",
                json.dumps({"sections": changes, "reason": reason}, ensure_ascii=False),
            ),
        )

    async def _execute(self, user_key: str, action: PreparedAction) -> str:
        """Apply only the prepared sections to the plan as it is now, so other
        changes saved meanwhile are kept."""
        request = json.loads(action.arguments_json)
        try:
            current = await asyncio.to_thread(self._store.get_plan, user_key)
            merged, changed = _merge(current, request["sections"])
            if not changed:
                return "투자 계획: 바뀐 내용이 없어 저장하지 않았습니다."
            if _size_problem(merged) is not None:
                return "투자 계획: 크기 한도를 넘어 저장하지 않았습니다. 다시 요청해 주세요."
            plan = PlanVersion(
                version=1 if current is None else current.version + 1,
                sections=merged,
                changed=changed,
                reason=request["reason"],
                created_at=self._now(),
            )
            if not await asyncio.to_thread(self._store.save_plan, user_key, plan):
                return (
                    "투자 계획: 그 사이 계획이 바뀌어 저장하지 않았습니다. "
                    "다시 요청해 주세요."
                )
        except SessionStoreError:
            return "투자 계획을 저장하지 못했습니다. 다시 요청해 주세요."
        return f"투자 계획을 저장했습니다 (바뀐 칸: {_labels(changed)})."

    async def _history(
        self,
        user_key: str,
        call: ToolCall,
        inputs: tuple[ConversationInput, ...],
    ) -> ReadToolResult:
        try:
            arguments = json.loads(call.arguments_json or "{}")
        except json.JSONDecodeError:
            arguments = None
        if not isinstance(arguments, dict):
            return ReadToolResult("Not read: the arguments must be an object.")
        as_of = arguments.get("as_of")
        if as_of:
            end = _end_of(str(as_of))
            if end is None:
                return ReadToolResult(
                    "Not read: as_of must be YYYY-MM-DD or YYYY-MM-DD HH:MM."
                )
            plans = await asyncio.to_thread(
                self._store.list_plans, user_key, limit=1, saved_through=end
            )
            return ReadToolResult(
                render(plans[0]) if plans else f"{as_of} 이전의 투자 계획이 없습니다."
            )
        plans = await asyncio.to_thread(
            self._store.list_plans, user_key, limit=HISTORY_LIMIT
        )
        if not plans:
            return ReadToolResult("투자 계획 기록이 없습니다.")
        return ReadToolResult(
            "\n".join(
                f"{_kst_time(plan.created_at)} [{_labels(plan.changed)}] {plan.reason}"
                for plan in plans
            )
        )


def render(plan: PlanVersion) -> str:
    """The plan as the model reads it. An empty section stays empty, so text
    copied back from here restores it as it was."""
    lines = [
        f"투자 계획 ({_kst_time(plan.created_at)} 저장)",
        f"이번 변경: {_labels(plan.changed)} — {plan.reason}",
    ]
    for name, label in SECTIONS:
        lines.append(f"\n[{label}]\n{plan.sections.get(name, '')}")
    return "\n".join(lines)


def _end_of(value: str) -> datetime | None:
    """The last moment of a KST day, or of a listed minute."""
    try:
        if len(value) == len("YYYY-MM-DD"):
            return datetime.combine(date.fromisoformat(value), time.max, _KST)
        minute = datetime.strptime(value, "%Y-%m-%d %H:%M").replace(tzinfo=_KST)
    except ValueError:
        return None
    return minute + timedelta(minutes=1) - timedelta(microseconds=1)


def _change_request(arguments_json: str) -> tuple[dict[str, str], str] | str:
    try:
        arguments = json.loads(arguments_json)
    except json.JSONDecodeError:
        return "the arguments must be an object."
    sections = arguments.get("sections") if isinstance(arguments, dict) else None
    reason = arguments.get("reason") if isinstance(arguments, dict) else None
    if not isinstance(sections, dict) or not sections:
        return "sections must name at least one section."
    unknown = [name for name in sections if name not in LABELS]
    if unknown:
        return f"unknown sections {', '.join(map(str, unknown))}."
    if not all(isinstance(text, str) for text in sections.values()):
        return "each section must be text."
    if not isinstance(reason, str) or not reason.strip():
        return "reason is required."
    if len(reason.strip()) > REASON_MAX_CHARS:
        return f"reason is over {REASON_MAX_CHARS} characters."
    return {name: text.strip() for name, text in sections.items()}, reason.strip()


def _merge(
    current: PlanVersion | None, sections: Mapping[str, Any]
) -> tuple[dict[str, str], tuple[str, ...]]:
    base = {} if current is None else dict(current.sections)
    changed = tuple(
        name
        for name, _ in SECTIONS
        if name in sections and sections[name] != base.get(name, "")
    )
    return {**base, **{name: sections[name] for name in changed}}, changed


def _size_problem(sections: Mapping[str, str]) -> str | None:
    for name, text in sections.items():
        if len(text) > SECTION_MAX_CHARS:
            return f"{name} is over {SECTION_MAX_CHARS} characters."
    if sum(len(text) for text in sections.values()) > PLAN_MAX_CHARS:
        return f"the whole plan is over {PLAN_MAX_CHARS} characters."
    return None


def _labels(names: tuple[str, ...]) -> str:
    return ", ".join(LABELS.get(name, name) for name in names)


def _kst_time(value: datetime) -> str:
    return value.astimezone(_KST).strftime("%Y-%m-%d %H:%M")
