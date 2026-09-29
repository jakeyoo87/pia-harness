from __future__ import annotations

import asyncio
import itertools
import json
import math
import re
from collections.abc import Awaitable, Callable, Coroutine, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta, timezone
from enum import StrEnum
from typing import Any, TypeVar

from .budget import ModelTokenBudget
from .compaction import ContextUsage, TokenCompactor
from .context import (
    AssembledPromptContext,
    ContextBudgetExceeded,
    PromptContextAssembler,
    PromptContextKind,
    ToolObservation,
    ToolSpec,
)
from .memory import (
    MAX_CHANGE_SUMMARY_CHARS,
    MAX_CHANGE_SUMMARY_ITEMS,
    MemoryReviewer,
    CurrentMemoryInput,
    MemoryReviewResult,
    MemoryReviewStatus,
)
from .persistence import (
    ConversationAbandoned,
    ConversationStore,
    TurnTooLargeError,
    validate_active_session,
)
from .session import ActiveSession, as_utc, new_turn_id

MESSAGE_SEPARATOR = "\n\n--- additional user message ---\n\n"
RESEARCH_TIMEOUT_SECONDS = 60.0
PROGRESS_TIMEOUT_SECONDS = 5.0
# Research stops at whichever comes first: this many read-tool uses, the
# research deadline, or a request too large to send with tools. All of them
# answer from what was gathered.
MAX_READ_TOOL_CALLS = 20
RESEARCH_LIMIT_NOTICE = (
    "Research stopped at its limit (time, number of reads, or input size). Answer "
    "now from the results above, without tools, and say plainly which parts could "
    "not be confirmed."
)
CONFIRMATION_TTL_SECONDS = 300.0
CONFIRMATION_EXPIRED_NOTICE = "확인 시간이 지났습니다. 다시 요청해 주세요."
# Orders one request may prepare, each waiting for its own confirmation.
MAX_PENDING_ACTIONS = 5
CONFIRM_TOOL = "confirm"
MEMORY_TOOL = "memory"
_RESERVED_TOOLS = frozenset({CONFIRM_TOOL, MEMORY_TOOL})
_TOOL_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
_MESSAGE_URL = re.compile(r"https?://[^\s<>\"'()\[\]{}]+", re.IGNORECASE)
_URL_TRAILING = ".,;:!?…。，、"
# The answer cites a link as a Markdown link or <url>; bare URLs count too.
_ANSWER_LINK = re.compile(
    r"\[([^\]\n]*)\]\((https?://[^\s)]+)\)|<(https?://[^\s>]+)>|"
    + _MESSAGE_URL.pattern,
    re.IGNORECASE,
)
# A citation-style number standing alone, not an index such as items[1].
_MODEL_NUMBER = re.compile(r"(?<![\w\])])\[\d{1,3}\](?!\()")
# Code is left as written: its URLs and brackets are not citations.
_CODE = re.compile(r"(```.*?(?:```|\Z)|`[^`\n]*`)", re.DOTALL)
# Marks a removal so only the space next to it goes, not the answer's own.
_REMOVED = "\x00"
_REMOVED_SPACE = re.compile(r"[ \t]+\x00|\x00[ \t]*")
SOURCES_HEADING = "출처"
_T = TypeVar("_T")
_KST = timezone(timedelta(hours=9))


class MemoryAction(StrEnum):
    NONE = "NONE"
    UPDATE = "UPDATE"
    FORGET = "FORGET"


class OrchestratorStatus(StrEnum):
    DELIVERED = "DELIVERED"
    SUPERSEDED = "SUPERSEDED"
    ABANDONED = "ABANDONED"
    CONTEXT_OVERFLOW = "CONTEXT_OVERFLOW"
    GENERATION_FAILED = "GENERATION_FAILED"
    DELIVERY_FAILED = "DELIVERY_FAILED"
    PERSISTENCE_FAILED = "PERSISTENCE_FAILED"
    TOOL_FAILED = "TOOL_FAILED"


@dataclass(frozen=True, slots=True)
class ConversationStep:
    """A tool call the model made, reported before it runs; not its outcome."""

    next_action: str
    arguments_json: str = "{}"


@dataclass(frozen=True, slots=True)
class ConversationInput:
    user_key: str
    message: str
    accepted_at: datetime
    turn_id: str


@dataclass(frozen=True, slots=True)
class ToolCall:
    name: str
    arguments_json: str
    # The model's ID for this call; its result is paired with it.
    call_id: str = ""


@dataclass(frozen=True, slots=True)
class ToolLink:
    """A readable source a tool found, such as one news search candidate."""

    title: str
    url: str
    published: str | None = None
    summary: str | None = None


@dataclass(frozen=True, slots=True)
class ReadToolResult:
    observation_text: str
    links: tuple[ToolLink, ...] = ()


@dataclass(frozen=True, slots=True)
class ReadToolDefinition:
    name: str
    description: str
    execute: Callable[
        [str, ToolCall, tuple[ConversationInput, ...]], Awaitable[ReadToolResult]
    ]
    # Only a tool that needs model-written input declares a schema.
    arguments_schema: Mapping[str, Any] | None = None
    # The argument holding links to read. The Harness passes on only links
    # that appear in the conversation and were not read yet this Turn.
    url_argument: str | None = None


@dataclass(frozen=True, slots=True)
class PreparedAction:
    """An execution tool's draft; it runs only if the user confirms it next Turn."""

    summary: str  # listed to the model while it waits for confirmation
    confirmation: str  # appended to the answer as the fixed question
    arguments_json: str  # handed to execute() unchanged


@dataclass(frozen=True, slots=True)
class PreparationResult:
    observation_text: str
    # None when the tool still needs something from the user (missing or
    # ambiguous arguments); the observation says what.
    action: PreparedAction | None = None


@dataclass(frozen=True, slots=True)
class ExecutionToolDefinition:
    name: str
    description: str
    arguments_schema: Mapping[str, Any]
    prepare: Callable[[str, ToolCall], Awaitable[PreparationResult]]
    # Runs after the execution claim. It returns the outcome text, which is also
    # sent as-is if the answer cannot be generated, and must not raise once the
    # request may have been sent.
    execute: Callable[[str, PreparedAction], Awaitable[str]]


@dataclass(frozen=True, slots=True)
class GeneratedAnswer:
    text: str
    model_id: str
    estimated_total_tokens: int
    usage: ContextUsage | None = None


@dataclass(frozen=True, slots=True)
class ModelReply:
    """One model step: the tool calls it made, or else its answer."""

    tool_calls: tuple[ToolCall, ...] = ()
    answer: GeneratedAnswer | None = None


@dataclass(frozen=True, slots=True)
class _BuiltinTool:
    name: str
    description: str
    arguments_schema: Mapping[str, Any]


MEMORY_TOOL_SPEC = _BuiltinTool(
    MEMORY_TOOL,
    "Keep or remove durable facts about the user in long-term Memory. Use when the "
    "user asks you to remember or forget something, or states a lasting fact or "
    "preference about themselves (investment goals, risk profile, holdings plans). "
    "The change is made from this request after your answer; do not say it is "
    "already saved.",
    {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["update", "forget"],
                "description": "update to remember or correct, forget to remove.",
            }
        },
        "required": ["action"],
        "additionalProperties": False,
    },
)
CONFIRM_TOOL_SPEC = _BuiltinTool(
    CONFIRM_TOOL,
    "Execute actions that were prepared earlier and are waiting for the user's "
    "confirmation, exactly as prepared. Use only when the user's latest message "
    "clearly agrees to them. If the user changes the conditions, call the action's "
    "tool again instead. It works only as your first tool call in this Turn.",
    {
        "type": "object",
        "properties": {
            "action_ids": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "maxItems": MAX_PENDING_ACTIONS,
                "description": "IDs of the waiting actions the user agreed to.",
            }
        },
        "required": ["action_ids"],
        "additionalProperties": False,
    },
)


@dataclass(frozen=True, slots=True)
class ConversationResult:
    status: OrchestratorStatus
    final_text: str | None = None
    turn_id: str | None = None
    memory_failed: bool = False
    compaction_failed: bool = False


class _Phase(StrEnum):
    IDLE = "IDLE"
    GENERATING = "GENERATING"
    COMMITTING = "COMMITTING"


@dataclass(slots=True)
class _Submission:
    value: ConversationInput
    future: asyncio.Future[ConversationResult]


# Links are readable only where they appear verbatim: the user's messages, the
# stored answers, and this Turn's tool results. Summaries and Memory are
# model-written, so they are left out.
_URL_SOURCE_KINDS = frozenset(
    {
        PromptContextKind.USER_TURN,
        PromptContextKind.ASSISTANT_TURN,
        PromptContextKind.CURRENT_USER,
        PromptContextKind.TOOL_RESULT,
    }
)


@dataclass(frozen=True, slots=True)
class _PendingAction:
    tool: ExecutionToolDefinition
    action: PreparedAction
    created_at: datetime
    action_id: str


@dataclass(slots=True)
class _Round:
    """What one model response's tool calls came to."""

    results: list[str | None]
    read_calls: int = 0
    memory_action: MemoryAction | None = None
    prepared: list[_PendingAction] = field(default_factory=list)
    # Waiting actions the user confirmed; they run after the execution claim.
    confirmed: tuple[_PendingAction, ...] = ()
    # The next model call gets no tools: a draft was made or a limit was hit.
    answer_next: bool = False
    limit_hit: bool = False


@dataclass(slots=True)
class _UserState:
    state_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    commit_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    generation_id: int = 0
    phase: _Phase = _Phase.IDLE
    pending: list[_Submission] = field(default_factory=list)
    active_task: asyncio.Task[None] | None = None
    committing_count: int = 0


class ConversationOrchestrator:
    def __init__(
        self,
        *,
        store: ConversationStore,
        assembler: PromptContextAssembler,
        memory_reviewer: MemoryReviewer,
        compactor: TokenCompactor,
        # One model step over the context and the tools it offers.
        generate_reply: Callable[[AssembledPromptContext], Awaitable[ModelReply]],
        deliver: Callable[[str, str], Awaitable[None]],
        system_prompt: str,
        token_budget: ModelTokenBudget,
        model_id: str,
        explicit_memory_failure_notice: str,
        # (user_key, turn_id, step); step None means the Turn has ended.
        progress: Callable[[str, str, ConversationStep | None], Awaitable[None]]
        | None = None,
        read_tools: tuple[ReadToolDefinition, ...] = (),
        execution_tools: tuple[ExecutionToolDefinition, ...] = (),
        research_timeout_seconds: float = RESEARCH_TIMEOUT_SECONDS,
        confirmation_ttl_seconds: float = CONFIRMATION_TTL_SECONDS,
    ) -> None:
        if not callable(generate_reply):
            raise ValueError("generate_reply must be callable")
        if not callable(deliver):
            raise ValueError("deliver must be callable")
        if progress is not None and not callable(progress):
            raise ValueError("progress must be callable")
        # The built-in confirm and memory tools keep their names.
        names = [tool.name for tool in read_tools] + [
            tool.name for tool in execution_tools
        ]
        if any(
            _TOOL_NAME.fullmatch(name) is None or name in _RESERVED_TOOLS
            for name in names
        ) or len(set(names)) != len(names):
            raise ValueError("tool names are invalid")
        if any(
            tool.url_argument is not None
            and tool.url_argument
            not in (tool.arguments_schema or {}).get("properties", {})
            for tool in read_tools
        ):
            raise ValueError("url_argument must name an argument of the tool")
        if (
            isinstance(confirmation_ttl_seconds, bool)
            or not isinstance(confirmation_ttl_seconds, (int, float))
            or not math.isfinite(confirmation_ttl_seconds)
            or confirmation_ttl_seconds <= 0
        ):
            raise ValueError("confirmation TTL must be positive and finite")
        if (
            isinstance(research_timeout_seconds, bool)
            or not isinstance(research_timeout_seconds, (int, float))
            or not math.isfinite(research_timeout_seconds)
            or research_timeout_seconds <= 0
        ):
            raise ValueError("research timeout must be positive and finite")
        if not isinstance(system_prompt, str) or not system_prompt.strip():
            raise ValueError("system_prompt is required")
        if not isinstance(model_id, str) or not model_id:
            raise ValueError("model_id is required")
        if not isinstance(token_budget, ModelTokenBudget):
            raise ValueError("token_budget must be a ModelTokenBudget")
        if (
            not isinstance(explicit_memory_failure_notice, str)
            or not explicit_memory_failure_notice.strip()
            or len(explicit_memory_failure_notice.strip()) > MAX_CHANGE_SUMMARY_CHARS
        ):
            raise ValueError("explicit_memory_failure_notice is invalid")
        self._store = store
        self._assembler = assembler
        self._memory_reviewer = memory_reviewer
        self._compactor = compactor
        self._generate_reply = generate_reply
        self._progress = progress
        self._read_tools = read_tools
        self._execution_tools = execution_tools
        self._confirmation_ttl_seconds = float(confirmation_ttl_seconds)
        # Actions waiting for confirmation, per user and in memory only: a
        # restart drops them and the user asks again.
        self._pending_actions: dict[str, tuple[_PendingAction, ...]] = {}
        self._action_ids = itertools.count(1)
        self._research_timeout_seconds = float(research_timeout_seconds)
        self._deliver = deliver
        self._system_prompt = system_prompt
        self._token_budget = token_budget
        self._model_id = model_id
        self._explicit_memory_failure_notice = explicit_memory_failure_notice.strip()
        self._states: dict[str, _UserState] = {}
        self._states_lock = asyncio.Lock()

    async def submit(
        self,
        *,
        user_key: str,
        message: str,
        accepted_at: datetime | None = None,
    ) -> ConversationResult:
        value = _conversation_input(
            user_key=user_key,
            message=message,
            accepted_at=accepted_at or datetime.now(UTC),
        )
        state = await self._state_for(user_key)
        future: asyncio.Future[ConversationResult] = (
            asyncio.get_running_loop().create_future()
        )
        submission = _Submission(value, future)

        async with state.state_lock:
            if state.phase is _Phase.GENERATING:
                _resolve_unfinished(state.pending, OrchestratorStatus.SUPERSEDED)
                if state.active_task is not None:
                    state.active_task.cancel()
            elif state.phase is _Phase.COMMITTING:
                _resolve_unfinished(
                    state.pending[state.committing_count :],
                    OrchestratorStatus.SUPERSEDED,
                )
            state.pending.append(submission)
            if state.phase is not _Phase.COMMITTING:
                self._start_generation_locked(user_key, state)

        return await future

    async def _state_for(self, user_key: str) -> _UserState:
        async with self._states_lock:
            state = self._states.get(user_key)
            if state is None:
                state = _UserState()
                self._states[user_key] = state
            return state

    def _start_generation_locked(self, user_key: str, state: _UserState) -> None:
        state.generation_id += 1
        generation_id = state.generation_id
        batch = tuple(state.pending)
        state.phase = _Phase.GENERATING
        state.active_task = asyncio.create_task(
            self._run_generation(user_key, state, generation_id, batch)
        )

    async def _run_generation(
        self,
        user_key: str,
        state: _UserState,
        generation_id: int,
        batch: tuple[_Submission, ...],
    ) -> None:
        memory_failed = False
        compaction_failed = False
        progress_started = False
        request_at = batch[-1].value.accepted_at
        offered = self._pending_actions.get(user_key, ())
        confirmation_expired = False
        if (
            offered
            and (request_at - offered[0].created_at).total_seconds()
            > self._confirmation_ttl_seconds
        ):
            if self._pending_actions.get(user_key) is offered:
                del self._pending_actions[user_key]
            offered = ()
            confirmation_expired = True
        prepared: list[_PendingAction] = []
        try:
            async with state.commit_lock:
                session = validate_active_session(
                    await _durable_call(
                        self._store.get_or_create_active_session,
                        user_key,
                        now=batch[-1].value.accepted_at,
                    ),
                    user_key=user_key,
                )
            tools = self._tools(offered)
            note = _waiting_note(offered)
            observations: tuple[ToolObservation, ...] = ()
            memory_action = MemoryAction.NONE
            # Search links already listed this Turn, so repeated searches show
            # only new ones. Their titles name the answer's sources.
            listed_urls: dict[str, str] = {}
            # A page does not change in seconds, so a link is read once a Turn
            # for each purpose (the tool's other arguments, such as the goal).
            read_urls: set[tuple[str, str]] = set()
            read_calls = 0
            answer_only = False
            closing_note: str | None = None
            compacted = False
            loop = asyncio.get_running_loop()
            deadline = loop.time() + self._research_timeout_seconds

            async def report_step(step: ConversationStep) -> None:
                nonlocal progress_started
                progress_started = True
                await self._report_progress(user_key, batch[-1].value.turn_id, step)

            for round_index in itertools.count():
                if not answer_only and (
                    read_calls >= MAX_READ_TOOL_CALLS or loop.time() >= deadline
                ):
                    # A limit ends the research, not the Turn: results stay.
                    answer_only, closing_note = True, RESEARCH_LIMIT_NOTICE
                (
                    assembled,
                    overflow_result,
                    compact_failed,
                ) = await self._assemble_with_overflow(
                    user_key,
                    state,
                    generation_id,
                    batch,
                    session,
                    observations,
                    tools=() if answer_only else tools,
                    note=note,
                    closing_note=closing_note,
                    compact=not compacted,
                )
                compaction_failed = compaction_failed or compact_failed
                if overflow_result is not None:
                    if not answer_only:
                        # Tools make the request larger: answer without them,
                        # on the context Compaction already produced.
                        answer_only, closing_note = True, RESEARCH_LIMIT_NOTICE
                        compacted = True
                        continue
                    await self._finish_generation(
                        user_key,
                        state,
                        generation_id,
                        batch,
                        overflow_result,
                        clear_pending=True,
                    )
                    return
                assert assembled is not None
                if not await self._is_current(state, generation_id):
                    return
                reply = await self._generate_reply(assembled)
                _validate_reply(reply, answer_only)
                if reply.answer is not None:
                    answer = _with_sources(reply.answer, assembled, listed_urls)
                    break
                if not await self._is_current(state, generation_id):
                    return
                for call in reply.tool_calls:
                    await report_step(ConversationStep(call.name, call.arguments_json))
                try:
                    outcome = await self._run_round(
                        user_key,
                        batch,
                        reply.tool_calls,
                        first=(round_index == 0),
                        offered=offered,
                        allowed=_conversation_urls(assembled),
                        read_urls=read_urls,
                        listed_urls=listed_urls,
                        remaining_reads=MAX_READ_TOOL_CALLS - read_calls,
                        deadline=deadline,
                    )
                except ConversationAbandoned:
                    raise
                # Expected failures come back as results; anything else is a
                # code error in a tool.
                except Exception:
                    await self._finish_generation(
                        user_key,
                        state,
                        generation_id,
                        batch,
                        ConversationResult(
                            OrchestratorStatus.TOOL_FAILED,
                            turn_id=batch[-1].value.turn_id,
                        ),
                        clear_pending=True,
                    )
                    return
                if not await self._is_current(state, generation_id):
                    return
                if outcome.confirmed:
                    # The execution claim: from here new messages wait instead
                    # of cancelling. The actions run exactly as prepared.
                    if not await self._claim_commit(state, generation_id, len(batch)):
                        return
                    if self._pending_actions.get(user_key) is offered:
                        del self._pending_actions[user_key]
                    await self._run_owned(
                        user_key,
                        state,
                        generation_id,
                        batch,
                        self._execute_and_commit(
                            user_key=user_key,
                            state=state,
                            generation_id=generation_id,
                            batch=batch,
                            session=session,
                            confirmed=outcome.confirmed,
                            calls=reply.tool_calls,
                            results=outcome.results,
                            round_index=round_index,
                            tool_observations=observations,
                            note=note,
                            memory_failed=memory_failed,
                            compaction_failed=compaction_failed,
                        ),
                    )
                    return
                observations += tuple(
                    ToolObservation(
                        call.name,
                        call.arguments_json,
                        result or "Not run.",
                        call.call_id,
                        round_index,
                    )
                    for call, result in zip(
                        reply.tool_calls, outcome.results, strict=True
                    )
                )
                read_calls += outcome.read_calls
                if outcome.memory_action is not None:
                    memory_action = outcome.memory_action
                prepared.extend(outcome.prepared)
                if outcome.limit_hit:
                    answer_only, closing_note = True, RESEARCH_LIMIT_NOTICE
                elif outcome.answer_next:
                    # A draft always ends in the answer: the confirmation
                    # question or asking for what is missing.
                    answer_only = True

            if not await self._claim_commit(state, generation_id, len(batch)):
                return
            await self._run_owned(
                user_key,
                state,
                generation_id,
                batch,
                self._commit_response(
                    user_key=user_key,
                    state=state,
                    batch=batch,
                    session=session,
                    answer=answer,
                    memory_action=memory_action,
                    memory_failed=memory_failed,
                    compaction_failed=compaction_failed,
                    prepared=tuple(prepared),
                    confirmation_expired=confirmation_expired,
                    tool_observations=observations,
                ),
            )
        except ConversationAbandoned:
            await self._finish_generation(
                user_key,
                state,
                generation_id,
                batch,
                ConversationResult(
                    OrchestratorStatus.ABANDONED,
                    memory_failed=memory_failed,
                    compaction_failed=compaction_failed,
                ),
                clear_pending=True,
            )
        except asyncio.CancelledError:
            return
        except Exception:
            await self._finish_generation(
                user_key,
                state,
                generation_id,
                batch,
                ConversationResult(
                    OrchestratorStatus.GENERATION_FAILED,
                    memory_failed=memory_failed,
                    compaction_failed=compaction_failed,
                ),
                clear_pending=False,
            )
        finally:
            if progress_started:
                await self._report_progress(user_key, batch[-1].value.turn_id, None)

    async def _run_owned(
        self,
        user_key: str,
        state: _UserState,
        generation_id: int,
        batch: tuple[_Submission, ...],
        owned: Coroutine[Any, Any, tuple[ConversationResult, bool]],
    ) -> None:
        commit_task = asyncio.create_task(owned)
        # Repeated external cancellation must not cancel the owned commit.
        # Supersede only cancels while the phase is GENERATING.
        while not commit_task.done():
            try:
                await asyncio.shield(commit_task)
            except asyncio.CancelledError:
                continue
        result, delivery_succeeded = commit_task.result()
        await self._finish_generation(
            user_key,
            state,
            generation_id,
            batch,
            result,
            clear_pending=delivery_succeeded,
        )

    def _tools(self, offered: tuple[_PendingAction, ...]) -> tuple[ToolSpec, ...]:
        tools: list[ToolSpec] = [
            *self._read_tools,
            *self._execution_tools,
            MEMORY_TOOL_SPEC,
        ]
        if offered:
            tools.append(CONFIRM_TOOL_SPEC)
        return tuple(tools)

    async def _run_round(
        self,
        user_key: str,
        batch: tuple[_Submission, ...],
        calls: tuple[ToolCall, ...],
        *,
        first: bool,
        offered: tuple[_PendingAction, ...],
        allowed: set[str],
        read_urls: set[tuple[str, str]],
        listed_urls: dict[str, str],
        remaining_reads: int,
        deadline: float,
    ) -> _Round:
        """Check one response's calls as a whole, then run them.

        Every call gets exactly one result, in the order the model made them.
        """
        outcome = _Round(results=[None] * len(calls))
        confirming = [i for i, call in enumerate(calls) if call.name == CONFIRM_TOOL]
        if confirming:
            _confirm_round(outcome, calls, confirming, first=first, offered=offered)
            return outcome

        read_tools = {tool.name: tool for tool in self._read_tools}
        execution_tools = {tool.name: tool for tool in self._execution_tools}
        reads: list[tuple[int, ReadToolDefinition, ToolCall, str]] = []
        drafts: list[tuple[int, ExecutionToolDefinition, ToolCall]] = []
        for index, call in enumerate(calls):
            parsed = _arguments(call)
            if parsed is None:
                outcome.results[index] = (
                    "Not run: the arguments were not a JSON object. Call again."
                )
            elif call.name == MEMORY_TOOL:
                value = parsed.get("action")
                action = _MEMORY_ACTIONS.get(value) if isinstance(value, str) else None
                if action is None:
                    outcome.results[index] = (
                        'Not run: action must be "update" or "forget".'
                    )
                else:
                    outcome.memory_action = action
                    outcome.results[index] = (
                        "Noted. Memory is changed from this request after the answer."
                    )
            elif call.name in execution_tools:
                if len(drafts) >= MAX_PENDING_ACTIONS:
                    outcome.results[index] = (
                        f"Not prepared: at most {MAX_PENDING_ACTIONS} at a time."
                    )
                else:
                    drafts.append((index, execution_tools[call.name], call))
            elif call.name in read_tools:
                tool = read_tools[call.name]
                missing = [
                    name
                    for name in (tool.arguments_schema or {}).get("required", ())
                    if parsed.get(name) in (None, "", [], {})
                ]
                if missing:
                    outcome.results[index] = (
                        f"Not run: missing {', '.join(missing)}. Call again with them."
                    )
                    continue
                prefix = ""
                if tool.url_argument is not None:
                    purpose = json.dumps(
                        {
                            key: value
                            for key, value in parsed.items()
                            if key != tool.url_argument
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                    links, prefix = _readable_links(
                        parsed.get(tool.url_argument), allowed, read_urls, purpose
                    )
                    if not links:
                        outcome.results[index] = (
                            f"Not run: no new link to read.{prefix}"
                        )
                        continue
                    if len(reads) >= remaining_reads:
                        outcome.results[index] = (
                            "Not run: the research limit was reached."
                        )
                        outcome.limit_hit = True
                        continue
                    # Reserved now, so two calls in one response read a link once.
                    read_urls.update((link, purpose) for link in links)
                    parsed[tool.url_argument] = links
                    call = ToolCall(
                        call.name, json.dumps(parsed, ensure_ascii=False), call.call_id
                    )
                elif len(reads) >= remaining_reads:
                    outcome.results[index] = "Not run: the research limit was reached."
                    outcome.limit_hit = True
                    continue
                reads.append((index, tool, call, prefix))
            else:
                outcome.results[index] = f"Not run: there is no tool named {call.name}."

        inputs = tuple(item.value for item in batch)

        async def read(
            tool: ReadToolDefinition, call: ToolCall
        ) -> ReadToolResult | None:
            try:
                result = await _before_deadline(
                    deadline, tool.execute(user_key, call, inputs)
                )
            except TimeoutError:
                return None
            _validate_read_tool_result(result)
            return result

        results = await asyncio.gather(
            *(read(tool, call) for _, tool, call, _ in reads)
        )
        for (index, _tool, _call, prefix), result in zip(reads, results, strict=True):
            if result is None:
                outcome.results[index] = "Not finished: the research time ran out."
                outcome.limit_hit = True
            else:
                outcome.results[index] = _tool_result_text(result, listed_urls) + prefix
        outcome.read_calls = len(reads)

        request_at = batch[-1].value.accepted_at
        for index, tool, call in drafts:
            # Preparing only drafts the action; it runs after confirmation.
            try:
                preparation = await _before_deadline(
                    deadline, tool.prepare(user_key, call)
                )
            except TimeoutError:
                outcome.results[index] = "Not prepared: the time ran out."
                outcome.limit_hit = True
                continue
            _validate_preparation(preparation)
            text = preparation.observation_text
            if preparation.action is not None:
                action_id = f"a{next(self._action_ids)}"
                outcome.prepared.append(
                    _PendingAction(tool, preparation.action, request_at, action_id)
                )
                text += f" (action ID {action_id})"
            outcome.results[index] = text
        outcome.answer_next = bool(drafts)
        return outcome

    async def _report_progress(
        self,
        user_key: str,
        progress_id: str,
        step: ConversationStep | None,
    ) -> None:
        # Progress is best effort; a stuck callback must not hold the Turn.
        if self._progress is None:
            return
        try:
            await asyncio.wait_for(
                self._progress(user_key, progress_id, step), PROGRESS_TIMEOUT_SECONDS
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            return

    async def _assemble_with_overflow(
        self,
        user_key: str,
        state: _UserState,
        generation_id: int,
        batch: tuple[_Submission, ...],
        session: ActiveSession,
        tool_observations: tuple[ToolObservation, ...] = (),
        *,
        tools: tuple[ToolSpec, ...] = (),
        note: str | None = None,
        closing_note: str | None = None,
        compact: bool = True,
    ) -> tuple[AssembledPromptContext | None, ConversationResult | None, bool]:
        """(context, overflow result, whether Compaction failed)."""
        combined = _received_line(batch) + _combined_message(batch)
        memory, conversation = await asyncio.gather(
            asyncio.to_thread(self._store.get_memory, user_key),
            asyncio.to_thread(
                self._store.load_context,
                user_key=user_key,
                session_id=session.session_id,
                now=batch[-1].value.accepted_at,
            ),
        )
        assembled = None
        try:
            assembled = self._assemble(
                user_key,
                session,
                memory,
                conversation,
                combined,
                tool_observations,
                tools=tools,
                note=note,
                closing_note=closing_note,
            )
            required_tokens = assembled.estimated_input_tokens
        except ContextBudgetExceeded as overflow:
            required_tokens = overflow.required_input_tokens

        should_compact = self._compactor.should_compact(
            token_budget=self._token_budget,
            model_id=self._model_id,
            estimated_context_tokens=required_tokens,
        )
        if assembled is not None and not should_compact:
            return assembled, None, False
        if not compact:
            # Compaction already ran for this request; running it again would
            # only repeat its model calls.
            if assembled is not None:
                return assembled, None, False
            return None, ConversationResult(OrchestratorStatus.CONTEXT_OVERFLOW), False

        compaction_failed = False
        async with state.commit_lock:
            if not await self._is_current(state, generation_id):
                raise asyncio.CancelledError from None
            try:
                await _durable_call(
                    self._compactor.compact,
                    user_key=user_key,
                    session_id=session.session_id,
                    token_budget=self._token_budget,
                    model_id=self._model_id,
                    estimated_context_tokens=required_tokens,
                    usage=None,
                    now=batch[-1].value.accepted_at,
                )
            except ConversationAbandoned:
                raise
            except Exception:
                compaction_failed = True

        memory, conversation = await asyncio.gather(
            asyncio.to_thread(self._store.get_memory, user_key),
            asyncio.to_thread(
                self._store.load_context,
                user_key=user_key,
                session_id=session.session_id,
                now=batch[-1].value.accepted_at,
            ),
        )
        try:
            assembled = self._assemble(
                user_key,
                session,
                memory,
                conversation,
                combined,
                tool_observations,
                tools=tools,
                note=note,
                closing_note=closing_note,
            )
        except ContextBudgetExceeded:
            return (
                None,
                ConversationResult(
                    OrchestratorStatus.CONTEXT_OVERFLOW,
                    compaction_failed=compaction_failed,
                ),
                compaction_failed,
            )
        return assembled, None, compaction_failed

    def _assemble(
        self,
        user_key: str,
        session: ActiveSession,
        memory: Any,
        conversation: Any,
        current_user_message: str,
        tool_observations: tuple[ToolObservation, ...] = (),
        *,
        tools: tuple[ToolSpec, ...] = (),
        note: str | None = None,
        closing_note: str | None = None,
    ) -> AssembledPromptContext:
        return self._assembler.assemble(
            user_key=user_key,
            session_id=session.session_id,
            system_prompt=self._system_prompt,
            memory=memory,
            conversation=conversation,
            current_user_message=current_user_message,
            token_budget=self._token_budget,
            tool_observations=tool_observations,
            tools=tools,
            note=note,
            closing_note=closing_note,
        )

    async def _execute_and_commit(
        self,
        *,
        user_key: str,
        state: _UserState,
        generation_id: int,
        batch: tuple[_Submission, ...],
        session: ActiveSession,
        confirmed: tuple[_PendingAction, ...],
        calls: tuple[ToolCall, ...],
        results: list[str | None],
        round_index: int,
        tool_observations: tuple[ToolObservation, ...],
        note: str | None,
        memory_failed: bool,
        compaction_failed: bool,
    ) -> tuple[ConversationResult, bool]:
        executed: list[str] = []
        for pending in confirmed:
            text = await pending.tool.execute(user_key, pending.action)
            if not isinstance(text, str) or not text.strip():
                raise ValueError("execution result text is required")
            executed.append(text)
        executed_text = "\n".join(executed)
        # The first confirm call carries the outcome; the response's other
        # calls were not run.
        first_confirm = next(
            index for index, call in enumerate(calls) if call.name == CONFIRM_TOOL
        )
        observations = (
            *tool_observations,
            *(
                ToolObservation(
                    call.name,
                    call.arguments_json,
                    executed_text
                    if index == first_confirm
                    else results[index] or "Not run.",
                    call.call_id,
                    round_index,
                )
                for index, call in enumerate(calls)
            ),
        )
        answer = await self._answer_after_execution(
            user_key,
            state,
            generation_id,
            batch,
            session,
            observations,
            note,
            executed_text,
        )
        return await self._commit_response(
            user_key=user_key,
            state=state,
            batch=batch,
            session=session,
            answer=answer,
            memory_action=MemoryAction.NONE,
            memory_failed=memory_failed,
            compaction_failed=compaction_failed,
            tool_observations=observations,
        )

    async def _answer_after_execution(
        self,
        user_key: str,
        state: _UserState,
        generation_id: int,
        batch: tuple[_Submission, ...],
        session: ActiveSession,
        tool_observations: tuple[ToolObservation, ...],
        note: str | None,
        executed_text: str,
    ) -> GeneratedAnswer:
        fixed = GeneratedAnswer(executed_text, "fixed-text", 0)
        try:
            assembled, _overflow, _ = await self._assemble_with_overflow(
                user_key,
                state,
                generation_id,
                batch,
                session,
                tool_observations,
                note=note,
            )
            if assembled is None:
                return fixed
            reply = await self._generate_reply(assembled)
            _validate_reply(reply, answer_only=True)
            assert reply.answer is not None
            return _with_sources(reply.answer, assembled, {})
        except ConversationAbandoned:
            raise
        # The action already ran, so any failure still reports its outcome with
        # the tool's fixed text instead of failing the Turn.
        except Exception:  # noqa: BLE001
            return fixed

    async def _claim_commit(
        self, state: _UserState, generation_id: int, batch_count: int
    ) -> bool:
        async with state.state_lock:
            if (
                state.phase is not _Phase.GENERATING
                or state.generation_id != generation_id
            ):
                return False
            state.phase = _Phase.COMMITTING
            state.committing_count = batch_count
            return True

    async def _commit_response(
        self,
        *,
        user_key: str,
        state: _UserState,
        batch: tuple[_Submission, ...],
        session: ActiveSession,
        answer: GeneratedAnswer,
        memory_action: MemoryAction,
        memory_failed: bool,
        compaction_failed: bool,
        prepared: tuple[_PendingAction, ...] = (),
        confirmation_expired: bool = False,
        tool_observations: tuple[ToolObservation, ...] = (),
    ) -> tuple[ConversationResult, bool]:
        changes: list[str] = []
        explicit_memory_failed = False
        now = batch[-1].value.accepted_at
        combined = _combined_message(batch)
        async with state.commit_lock:
            action = memory_action
            if action in (MemoryAction.UPDATE, MemoryAction.FORGET):
                try:
                    review = await _durable_call(
                        self._memory_reviewer.review_explicit_input,
                        user_key=user_key,
                        session_id=session.session_id,
                        current_input=CurrentMemoryInput(
                            user_key=user_key,
                            session_id=session.session_id,
                            turn_id=batch[-1].value.turn_id,
                            user_message=combined,
                            created_at=batch[-1].value.accepted_at,
                        ),
                        allow_clear=(action is MemoryAction.FORGET),
                        now=now,
                    )
                    if review is not None and review.status is MemoryReviewStatus.STALE:
                        memory_failed = True
                        explicit_memory_failed = True
                    else:
                        _add_changes(changes, review)
                except ConversationAbandoned:
                    raise
                except Exception:
                    memory_failed = True
                    explicit_memory_failed = True
            if explicit_memory_failed:
                _add_notice(changes, self._explicit_memory_failure_notice)
            if confirmation_expired:
                _add_notice(changes, CONFIRMATION_EXPIRED_NOTICE)

            final_text = _final_text(answer.text, changes)
            if prepared:
                # Last and outside the notice list, so they are never trimmed.
                final_text = "\n\n".join(
                    (final_text, *(item.action.confirmation for item in prepared))
                )
            try:
                await self._deliver(user_key, final_text)
            except ConversationAbandoned:
                raise
            except Exception:
                return ConversationResult(
                    OrchestratorStatus.DELIVERY_FAILED,
                    final_text=final_text,
                    turn_id=batch[-1].value.turn_id,
                    memory_failed=memory_failed,
                    compaction_failed=compaction_failed,
                ), False

            # The Turn is delivered: waiting actions live for the next Turn only.
            if prepared:
                self._pending_actions[user_key] = prepared
            else:
                self._pending_actions.pop(user_key, None)

            turn = {
                "user_key": user_key,
                "session_id": session.session_id,
                "turn_id": batch[-1].value.turn_id,
                "user_message": combined,
                "assistant_message": final_text,
                "created_at": batch[-1].value.accepted_at,
            }
            try:
                try:
                    await _durable_call(
                        self._store.append_completed_turn,
                        **turn,
                        tool_observations=tool_observations,
                    )
                except TurnTooLargeError:
                    if not tool_observations:
                        raise
                    # Too big with its tool records: keep at least what the
                    # user saw, as before tool records were stored.
                    await _durable_call(self._store.append_completed_turn, **turn)
            except ConversationAbandoned:
                raise
            except Exception:
                return ConversationResult(
                    OrchestratorStatus.PERSISTENCE_FAILED,
                    final_text=final_text,
                    turn_id=batch[-1].value.turn_id,
                    memory_failed=memory_failed,
                    compaction_failed=compaction_failed,
                ), True

            # Compaction runs here, after the answer is out, so the user does
            # not wait for it; the next model call still checks before sending.
            size = {
                "token_budget": self._token_budget,
                "model_id": self._model_id,
                "estimated_context_tokens": answer.estimated_total_tokens,
                "usage": answer.usage,
            }
            try:
                if self._compactor.should_compact(**size):
                    await _durable_call(
                        self._compactor.compact,
                        user_key=user_key,
                        session_id=session.session_id,
                        now=now,
                        **size,
                    )
            except ConversationAbandoned:
                raise
            # The answer is delivered and stored; a failed Compaction only
            # leaves the Context larger for the next request.
            except Exception:
                compaction_failed = True

        return ConversationResult(
            OrchestratorStatus.DELIVERED,
            final_text=final_text,
            turn_id=batch[-1].value.turn_id,
            memory_failed=memory_failed,
            compaction_failed=compaction_failed,
        ), True

    async def _finish_generation(
        self,
        user_key: str,
        state: _UserState,
        generation_id: int,
        batch: tuple[_Submission, ...],
        result: ConversationResult,
        *,
        clear_pending: bool,
    ) -> None:
        start_next = False
        async with state.state_lock:
            if state.generation_id != generation_id:
                return
            if clear_pending:
                del state.pending[: len(batch)]
            owner = batch[-1]
            if not owner.future.done():
                owner.future.set_result(result)
            state.phase = _Phase.IDLE
            state.active_task = None
            state.committing_count = 0
            queued_new_input = len(state.pending) > (0 if clear_pending else len(batch))
            if queued_new_input:
                self._start_generation_locked(user_key, state)
                start_next = True
        if not start_next:
            await self._remove_if_idle(user_key, state)

    async def _is_current(self, state: _UserState, generation_id: int) -> bool:
        # COMMITTING belongs to the generation that claimed it, which includes a
        # confirmed execution that still has to write its answer.
        async with state.state_lock:
            return (
                state.phase in (_Phase.GENERATING, _Phase.COMMITTING)
                and state.generation_id == generation_id
            )

    async def _remove_if_idle(self, user_key: str, state: _UserState) -> None:
        async with self._states_lock:
            async with state.state_lock:
                if (
                    self._states.get(user_key) is state
                    and state.phase is _Phase.IDLE
                    and not state.pending
                ):
                    del self._states[user_key]


def _conversation_input(
    *,
    user_key: str,
    message: str,
    accepted_at: datetime,
) -> ConversationInput:
    if not isinstance(user_key, str) or not user_key:
        raise ValueError("user_key is required")
    if not isinstance(message, str) or not message:
        raise ValueError("message is required")
    accepted_at = as_utc(accepted_at)
    return ConversationInput(
        user_key=user_key,
        message=message,
        accepted_at=accepted_at,
        turn_id=new_turn_id(accepted_at),
    )


def _received_line(batch: tuple[_Submission, ...]) -> str:
    # Models do not know today's date; KST because PIA's users are in Korea.
    received = batch[-1].value.accepted_at.astimezone(_KST)
    return (
        f"[Received {received:%Y-%m-%d %H:%M} KST. This is the current time, "
        "not the as-of date of any source.]\n"
    )


_MEMORY_ACTIONS = {"update": MemoryAction.UPDATE, "forget": MemoryAction.FORGET}


def _waiting_note(offered: tuple[_PendingAction, ...]) -> str | None:
    if not offered:
        return None
    lines = [
        "Actions prepared in the previous answer are waiting for the user's "
        "confirmation. Call confirm with the IDs the latest message clearly "
        "agrees to; otherwise do not."
    ]
    lines.extend(f"- {item.action_id}: {item.action.summary}" for item in offered)
    return "\n".join(lines)


def _confirm_round(
    outcome: _Round,
    calls: tuple[ToolCall, ...],
    confirming: list[int],
    *,
    first: bool,
    offered: tuple[_PendingAction, ...],
) -> None:
    """Fill a response that confirms: only confirm runs, and only at the start.

    Before any tool result arrives in this Turn, the model has read nothing but
    the user's messages, so page text cannot talk it into executing an action.
    """
    for index in range(len(calls)):
        if index not in confirming:
            outcome.results[index] = (
                "Not run: confirming comes first. Call it again after the "
                "confirmed actions are reported."
            )
    refusal = None
    if not offered:
        refusal = "Not confirmed: no action is waiting for confirmation."
    elif not first:
        refusal = (
            "Not confirmed: confirm works only as the first tool call of a Turn, "
            "before any other tool result. Ask the user to confirm again."
        )
    ids: set[str] = set()
    for index in confirming:
        requested = (_arguments(calls[index]) or {}).get("action_ids")
        if not isinstance(requested, list) or not all(
            isinstance(item, str) for item in requested
        ):
            refusal = refusal or "Not confirmed: action_ids must be a list of IDs."
        else:
            ids.update(requested)
    known = {item.action_id for item in offered}
    if refusal is None and (not ids or not ids <= known):
        refusal = "Not confirmed: unknown action IDs. Use the IDs listed as waiting."
    if refusal is not None:
        for index in confirming:
            outcome.results[index] = refusal
        return
    outcome.confirmed = tuple(item for item in offered if item.action_id in ids)


def _arguments(call: ToolCall) -> dict[str, Any] | None:
    try:
        arguments = json.loads(call.arguments_json)
    except (TypeError, ValueError):
        return None
    return arguments if isinstance(arguments, dict) else None


def _readable_links(
    requested: Any, allowed: set[str], read_urls: set[tuple[str, str]], purpose: str
) -> tuple[list[str], str]:
    """The requested links that may be read now, and a note on the others.

    Rejected links are only counted: a link written in a result becomes
    readable, so an invented one must not be echoed.
    """
    links: list[str] = []
    for value in requested if isinstance(requested, list) else ():
        link = value.strip() if isinstance(value, str) else ""
        if link and link not in links:
            links.append(link)
    rejected = sum(1 for link in links if link not in allowed)
    done = {link for link, read_for in read_urls if read_for == purpose}
    repeated = sum(1 for link in links if link in allowed and link in done)
    readable = [link for link in links if link in allowed and link not in done]
    note = ""
    if rejected:
        note += (
            f"\n- {rejected} requested link(s) did not appear in this conversation; "
            "not read."
        )
    if repeated:
        note += (
            f"\n- {repeated} requested link(s) were already read in this Turn; "
            "not read again."
        )
    return readable, note


def _combined_message(batch: tuple[_Submission, ...]) -> str:
    return MESSAGE_SEPARATOR.join(item.value.message for item in batch)


def _validate_generated_answer(answer: GeneratedAnswer) -> None:
    if not isinstance(answer, GeneratedAnswer):
        raise ValueError("the answer has the wrong type")
    if not isinstance(answer.text, str) or not answer.text:
        raise ValueError("generated answer text is required")
    if not isinstance(answer.model_id, str) or not answer.model_id:
        raise ValueError("generated answer model_id is required")
    if (
        isinstance(answer.estimated_total_tokens, bool)
        or not isinstance(answer.estimated_total_tokens, int)
        or answer.estimated_total_tokens < 0
    ):
        raise ValueError("estimated_total_tokens must be a non-negative integer")


def _validate_reply(reply: ModelReply, answer_only: bool) -> None:
    if not isinstance(reply, ModelReply):
        raise ValueError("generate_reply returned the wrong type")
    if reply.answer is not None:
        if reply.tool_calls:
            raise ValueError("a reply is either tool calls or an answer")
        _validate_generated_answer(reply.answer)
        return
    if answer_only or not reply.tool_calls:
        raise ValueError("the reply has no answer")
    ids = [call.call_id for call in reply.tool_calls]
    if (
        not all(isinstance(call, ToolCall) for call in reply.tool_calls)
        or not all(ids)
        or len(set(ids)) != len(ids)
    ):
        raise ValueError("tool calls need distinct IDs")


def _validate_preparation(result: PreparationResult) -> None:
    if not isinstance(result, PreparationResult) or not result.observation_text.strip():
        raise ValueError("execution tool preparation is invalid")
    action = result.action
    if action is not None and (
        not isinstance(action, PreparedAction)
        or not action.summary.strip()
        or not action.confirmation.strip()
        or not action.arguments_json.strip()
    ):
        raise ValueError("prepared action is invalid")


def _validate_read_tool_result(result: ReadToolResult) -> None:
    # Links become readable URLs, so only http(s) links are accepted.
    if not result.observation_text.strip() or any(
        not link.url.startswith(("http://", "https://")) for link in result.links
    ):
        raise ValueError("read tool result is invalid")


def _conversation_urls(context: AssembledPromptContext) -> set[str]:
    urls: set[str] = set()
    for part in context.parts:
        if part.kind not in _URL_SOURCE_KINDS:
            continue
        for match in _MESSAGE_URL.finditer(part.content):
            url = match.group(0).rstrip(_URL_TRAILING)
            if url:
                urls.add(url)
    return urls


def _with_sources(
    answer: GeneratedAnswer, context: AssembledPromptContext, titles: dict[str, str]
) -> GeneratedAnswer:
    """Turn the answer's links into [n] and append the list of their sources.

    Only links that appear in the conversation or this Turn's tool results are
    kept, so every listed source is one a tool or the user actually gave. That
    says where a link came from, not that its page was read or backs the claim.
    """
    allowed = _conversation_urls(context)
    numbers: dict[str, int] = {}

    def cite(match: re.Match[str]) -> str:
        text, marked = match.group(1), match.group(2) or match.group(3)
        raw = marked or match.group(0)
        url = raw.rstrip(_URL_TRAILING)
        # A bare URL's closing punctuation belongs to the sentence.
        trailing = "" if marked else raw[len(url) :]
        if url not in allowed:
            return (text or _REMOVED) + trailing
        number = numbers.setdefault(url, len(numbers) + 1)
        return f"{text} [{number}]" if text else f"[{number}]{trailing}"

    def cite_prose(prose: str) -> str:
        # Numbers the model wrote itself (say, copied from an earlier answer's
        # list) point at nothing in this answer's list.
        prose = prose.replace(_REMOVED, "")
        prose = _ANSWER_LINK.sub(cite, _MODEL_NUMBER.sub(_REMOVED, prose))
        return _REMOVED_SPACE.sub("", prose)

    pieces = _CODE.split(answer.text)
    body = "".join(
        piece if index % 2 else cite_prose(piece) for index, piece in enumerate(pieces)
    )
    # Never fall back to the original: that would restore what was removed.
    if not body.strip():
        raise ValueError("the answer is empty once uncited links are removed")
    if not numbers:
        return replace(answer, text=body)
    sources = [
        f"[{number}] {titles[url]} {url}" if titles.get(url) else f"[{number}] {url}"
        for url, number in numbers.items()
    ]
    return replace(
        answer, text=f"{body.rstrip()}\n\n{SOURCES_HEADING}\n" + "\n".join(sources)
    )


def _tool_result_text(result: ReadToolResult, listed_urls: dict[str, str]) -> str:
    if not result.links:
        return result.observation_text
    lines = [result.observation_text]
    added = 0
    for link in result.links:
        if link.url in listed_urls:
            continue
        listed_urls[link.url] = link.title
        added += 1
        line = f"[{link.published}] {link.title}" if link.published else link.title
        if link.summary:
            line += f" — {link.summary}"
        lines.append(f"{line} <{link.url}>")
    if not added:
        lines.append("No new candidates; every returned link was already listed.")
    else:
        lines.append(
            "Candidates are titles and short descriptions only; their bodies are unread."
        )
    return "\n".join(lines)


def _resolve_unfinished(
    submissions: list[_Submission], status: OrchestratorStatus
) -> None:
    result = ConversationResult(status)
    for submission in submissions:
        if not submission.future.done():
            submission.future.set_result(result)


def _add_changes(changes: list[str], result: MemoryReviewResult | None) -> None:
    if result is None or result.status is MemoryReviewStatus.STALE:
        return
    for item in result.change_summary:
        if item not in changes and len(changes) < MAX_CHANGE_SUMMARY_ITEMS:
            changes.append(item)


def _add_notice(changes: list[str], notice: str) -> None:
    # The user's own failed request outranks an incidental automatic summary, so
    # the notice takes a slot instead of being dropped when the budget is full.
    if notice in changes:
        return
    del changes[MAX_CHANGE_SUMMARY_ITEMS - 1 :]
    changes.append(notice)


def _final_text(answer: str, changes: list[str]) -> str:
    if not changes:
        return answer
    return f"{answer}\n\n" + "\n".join(f"- {item}" for item in changes)


async def _before_deadline(deadline: float, operation: Awaitable[_T]) -> _T:
    remaining = max(0.0, deadline - asyncio.get_running_loop().time())
    return await asyncio.wait_for(operation, timeout=remaining)


async def _durable_call(function: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await asyncio.shield(task)
        raise
