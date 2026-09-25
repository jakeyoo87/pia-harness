from __future__ import annotations

import asyncio
import json
import math
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, TypeVar

from .budget import ModelTokenBudget
from .compaction import ContextUsage, TokenCompactor
from .context import (
    AssembledPromptContext,
    ContextBudgetExceeded,
    PromptContextAssembler,
    ToolObservation,
)
from .memory import (
    MAX_CHANGE_SUMMARY_CHARS,
    MAX_CHANGE_SUMMARY_ITEMS,
    AutomaticMemoryReviewer,
    CurrentMemoryInput,
    MemoryReviewResult,
    MemoryReviewStatus,
)
from .persistence import (
    ConversationAbandoned,
    ConversationStore,
    validate_active_session,
)
from .session import ActiveSession, as_utc, new_turn_id

MESSAGE_SEPARATOR = "\n\n--- additional user message ---\n\n"
READ_ROUTING_TIMEOUT_SECONDS = 120.0
PROGRESS_TIMEOUT_SECONDS = 5.0
READ_OPTION_PREFIX = "read:"
_TOOL_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
_MESSAGE_URL = re.compile(r"https?://[^\s<>\"'()\[\]{}]+", re.IGNORECASE)
_URL_TRAILING = ".,;:!?…。，、"
_T = TypeVar("_T")


class MemoryAction(StrEnum):
    NONE = "NONE"
    UPDATE = "UPDATE"
    FORGET = "FORGET"


@dataclass(frozen=True, slots=True)
class NextActionDecision:
    next_action: str
    memory_action: MemoryAction = MemoryAction.NONE


class OrchestratorStatus(StrEnum):
    DELIVERED = "DELIVERED"
    SUPERSEDED = "SUPERSEDED"
    ABANDONED = "ABANDONED"
    CONTEXT_OVERFLOW = "CONTEXT_OVERFLOW"
    GENERATION_FAILED = "GENERATION_FAILED"
    DELIVERY_FAILED = "DELIVERY_FAILED"
    PERSISTENCE_FAILED = "PERSISTENCE_FAILED"
    TOOL_FAILED = "TOOL_FAILED"


class ConversationProgress(StrEnum):
    WEB_SEARCH_STARTED = "WEB_SEARCH_STARTED"
    COMPLETE = "COMPLETE"


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
    progress: ConversationProgress | None = None


@dataclass(frozen=True, slots=True)
class NextActionOption:
    name: str
    description: str


@dataclass(frozen=True, slots=True)
class GeneratedAnswer:
    text: str
    model_id: str
    estimated_total_tokens: int
    usage: ContextUsage | None = None


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


@dataclass(slots=True)
class _Link:
    link_id: str
    link: ToolLink
    read: bool = False

    @property
    def option_name(self) -> str:
        return f"{READ_OPTION_PREFIX}{self.link_id}"


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
        memory_reviewer: AutomaticMemoryReviewer,
        compactor: TokenCompactor,
        generate_answer: Callable[[AssembledPromptContext], Awaitable[GeneratedAnswer]],
        deliver: Callable[[str, str], Awaitable[None]],
        system_prompt: str,
        token_budget: ModelTokenBudget,
        model_id: str,
        explicit_memory_failure_notice: str,
        progress: Callable[[str, str, ConversationProgress], Awaitable[None]]
        | None = None,
        read_tools: tuple[ReadToolDefinition, ...] = (),
        read_url: Callable[[str], Awaitable[str]] | None = None,
        choose_next: Callable[
            [AssembledPromptContext, tuple[NextActionOption, ...]],
            Awaitable[NextActionDecision],
        ],
        build_tool_call: Callable[
            [AssembledPromptContext, ReadToolDefinition], Awaitable[ToolCall]
        ]
        | None = None,
        read_routing_timeout_seconds: float = READ_ROUTING_TIMEOUT_SECONDS,
    ) -> None:
        if not callable(generate_answer):
            raise ValueError("generate_answer must be callable")
        if not callable(deliver):
            raise ValueError("deliver must be callable")
        if progress is not None and not callable(progress):
            raise ValueError("progress must be callable")
        if not callable(choose_next):
            raise ValueError("Jev next-action selection is required")
        if read_url is not None and not callable(read_url):
            raise ValueError("read_url must be callable")
        # Tool names become Jev option names, so they must not collide with
        # "answer" or the "read:" options.
        names = [tool.name for tool in read_tools]
        if any(
            _TOOL_NAME.fullmatch(name) is None or name == "answer" for name in names
        ) or len(set(names)) != len(names):
            raise ValueError("read tool names are invalid")
        if not callable(build_tool_call) and any(
            tool.arguments_schema is not None for tool in read_tools
        ):
            raise ValueError("tools with arguments require tool argument generation")
        if (
            isinstance(read_routing_timeout_seconds, bool)
            or not isinstance(read_routing_timeout_seconds, (int, float))
            or not math.isfinite(read_routing_timeout_seconds)
            or read_routing_timeout_seconds <= 0
        ):
            raise ValueError("read routing timeout must be positive and finite")
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
        self._generate_answer = generate_answer
        self._progress = progress
        self._read_tools = read_tools
        self._read_url = read_url
        self._choose_next = choose_next
        self._build_tool_call = build_tool_call
        self._read_routing_timeout_seconds = float(read_routing_timeout_seconds)
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
            (
                assembled,
                overflow_result,
                overflow_memory_failed,
                overflow_compaction_failed,
            ) = await self._assemble_with_overflow(
                user_key,
                state,
                generation_id,
                batch,
                session,
            )
            memory_failed = memory_failed or overflow_memory_failed
            compaction_failed = compaction_failed or overflow_compaction_failed
            if overflow_result is not None:
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
            tool_observations: tuple[ToolObservation, ...] = ()
            memory_action = MemoryAction.NONE
            links: list[_Link] = (
                []
                if self._read_url is None
                else _message_links(_combined_message(batch))
            )
            deadline = (
                asyncio.get_running_loop().time() + self._read_routing_timeout_seconds
            )
            while True:
                if not await self._is_current(state, generation_id):
                    return
                options = self._next_action_options(links)
                choice = await _before_deadline(
                    deadline, self._choose_next(assembled, options)
                )
                _validate_next_action_decision(choice, options)
                if choice.next_action == "answer":
                    memory_action = choice.memory_action
                    break
                read_link = next(
                    (
                        link
                        for link in links
                        if not link.read and link.option_name == choice.next_action
                    ),
                    None,
                )
                if read_link is not None:
                    if not await self._is_current(state, generation_id):
                        return
                    observation = await self._read_link(deadline, read_link)
                else:
                    tool = next(
                        tool
                        for tool in self._read_tools
                        if tool.name == choice.next_action
                    )
                    if tool.arguments_schema is None:
                        call = ToolCall(tool.name, "{}")
                    else:
                        assert self._build_tool_call is not None
                        call = await _before_deadline(
                            deadline, self._build_tool_call(assembled, tool)
                        )
                        _validate_read_tool_call(call, tool.name)
                    if not await self._is_current(state, generation_id):
                        return
                    if tool.progress is not None and not progress_started:
                        progress_started = True
                        await self._report_progress(
                            user_key, batch[-1].value.turn_id, tool.progress
                        )
                    try:
                        tool_result = await _before_deadline(
                            deadline,
                            tool.execute(
                                user_key, call, tuple(item.value for item in batch)
                            ),
                        )
                        _validate_read_tool_result(tool_result)
                    except ConversationAbandoned:
                        raise
                    except TimeoutError:
                        raise
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
                    observation = ToolObservation(
                        call.name,
                        call.arguments_json,
                        _tool_result_text(tool_result, links),
                    )
                if not await self._is_current(state, generation_id):
                    return
                tool_observations += (observation,)
                (
                    assembled,
                    overflow_result,
                    review_failed,
                    compact_failed,
                ) = await _before_deadline(
                    deadline,
                    self._assemble_with_overflow(
                        user_key,
                        state,
                        generation_id,
                        batch,
                        session,
                        tool_observations,
                    ),
                )
                memory_failed = memory_failed or review_failed
                compaction_failed = compaction_failed or compact_failed
                if overflow_result is not None:
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

            answer = await self._generate_answer(assembled)
            _validate_generated_answer(answer)
            if not await self._claim_commit(state, generation_id, len(batch)):
                return
            commit_task = asyncio.create_task(
                self._commit_response(
                    user_key=user_key,
                    state=state,
                    batch=batch,
                    session=session,
                    answer=answer,
                    memory_action=memory_action,
                    memory_failed=memory_failed,
                    compaction_failed=compaction_failed,
                )
            )
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
        except TimeoutError:
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
                clear_pending=bool(self._read_tools) or self._read_url is not None,
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
                await self._report_progress(
                    user_key, batch[-1].value.turn_id, ConversationProgress.COMPLETE
                )

    def _next_action_options(self, links: list[_Link]) -> tuple[NextActionOption, ...]:
        options = [
            NextActionOption(tool.name, tool.description) for tool in self._read_tools
        ]
        if self._read_url is not None:
            for link in links:
                if link.read:
                    continue
                if link.link_id.startswith("u"):
                    description = (
                        f"Read the body of the URL the user gave: {link.link.url}"
                    )
                else:
                    dated = f"[{link.link.published}] " if link.link.published else ""
                    description = f"Read the article body of {link.link_id}: {dated}{link.link.title}"
                options.append(NextActionOption(link.option_name, description))
        return tuple(options)

    async def _read_link(self, deadline: float, link: _Link) -> ToolObservation:
        assert self._read_url is not None
        link.read = True
        arguments = json.dumps(
            {"id": link.link_id, "url": link.link.url},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        try:
            body = await _before_deadline(deadline, self._read_url(link.link.url))
            if not isinstance(body, str) or not body.strip():
                raise ValueError("empty body")
            result = body
        except ConversationAbandoned:
            raise
        except TimeoutError:
            raise
        except Exception:
            result = (
                f"The body of {link.link_id} could not be read. "
                "Do not describe its content as read."
            )
        return ToolObservation("read", arguments, result)

    async def _report_progress(
        self,
        user_key: str,
        progress_id: str,
        event: ConversationProgress,
    ) -> None:
        # Progress is best effort; a stuck callback must not hold the Turn.
        if self._progress is None:
            return
        try:
            await asyncio.wait_for(
                self._progress(user_key, progress_id, event), PROGRESS_TIMEOUT_SECONDS
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
    ) -> tuple[
        AssembledPromptContext | None,
        ConversationResult | None,
        bool,
        bool,
    ]:
        combined = _combined_message(batch)
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
                user_key, session, memory, conversation, combined, tool_observations
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
            return assembled, None, False, False

        memory_failed = False
        compaction_failed = False
        async with state.commit_lock:
            if not await self._is_current(state, generation_id):
                raise asyncio.CancelledError from None
            try:
                await _durable_call(
                    self._memory_reviewer.force_review,
                    user_key=user_key,
                    session_id=session.session_id,
                    now=batch[-1].value.accepted_at,
                )
            except ConversationAbandoned:
                raise
            except Exception:
                memory_failed = True
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
                user_key, session, memory, conversation, combined, tool_observations
            )
        except ContextBudgetExceeded:
            return (
                None,
                ConversationResult(
                    OrchestratorStatus.CONTEXT_OVERFLOW,
                    memory_failed=memory_failed,
                    compaction_failed=compaction_failed,
                ),
                memory_failed,
                compaction_failed,
            )
        return assembled, None, memory_failed, compaction_failed

    def _assemble(
        self,
        user_key: str,
        session: ActiveSession,
        memory: Any,
        conversation: Any,
        current_user_message: str,
        tool_observations: tuple[ToolObservation, ...] = (),
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
        )

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
            else:
                try:
                    review = await _durable_call(
                        self._memory_reviewer.review_if_due,
                        user_key=user_key,
                        session_id=session.session_id,
                        request_at=now,
                    )
                    _add_changes(changes, review)
                except ConversationAbandoned:
                    raise
                except Exception:
                    memory_failed = True

            if explicit_memory_failed:
                _add_notice(changes, self._explicit_memory_failure_notice)

            final_text = _final_text(answer.text, changes)
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

            try:
                await _durable_call(
                    self._store.append_completed_turn,
                    user_key=user_key,
                    session_id=session.session_id,
                    turn_id=batch[-1].value.turn_id,
                    user_message=combined,
                    assistant_message=final_text,
                    created_at=batch[-1].value.accepted_at,
                )
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
        async with state.state_lock:
            return (
                state.phase is _Phase.GENERATING
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


def _combined_message(batch: tuple[_Submission, ...]) -> str:
    return MESSAGE_SEPARATOR.join(item.value.message for item in batch)


def _validate_generated_answer(answer: GeneratedAnswer) -> None:
    if not isinstance(answer, GeneratedAnswer):
        raise ValueError("generate_answer returned the wrong type")
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


def _validate_next_action_decision(
    decision: NextActionDecision, options: tuple[NextActionOption, ...]
) -> None:
    if not isinstance(decision, NextActionDecision):
        raise ValueError("Jev decision has the wrong type")
    if decision.next_action == "answer":
        if not isinstance(decision.memory_action, MemoryAction):
            raise ValueError("Jev memory action is invalid")
    elif decision.next_action not in {option.name for option in options}:
        raise ValueError("Jev selected an unavailable action")
    elif decision.memory_action is not MemoryAction.NONE:
        raise ValueError("tool selection must defer the Memory action")


def _validate_read_tool_call(call: ToolCall, expected_name: str) -> None:
    if not isinstance(call, ToolCall) or call.name != expected_name:
        raise ValueError("read tool call is invalid")
    try:
        arguments = json.loads(call.arguments_json)
    except (TypeError, ValueError):
        raise ValueError("read tool arguments are invalid") from None
    if not isinstance(arguments, dict):
        raise ValueError("read tool arguments must be an object")


def _validate_read_tool_result(result: ReadToolResult) -> None:
    # Links become readable URLs, so only http(s) links are accepted.
    if not result.observation_text.strip() or any(
        not link.url.startswith(("http://", "https://")) for link in result.links
    ):
        raise ValueError("read tool result is invalid")


def _message_links(message: str) -> list[_Link]:
    links: list[_Link] = []
    for match in _MESSAGE_URL.finditer(message):
        url = match.group(0).rstrip(_URL_TRAILING)
        if url and all(link.link.url != url for link in links):
            links.append(_Link(f"u{len(links) + 1}", ToolLink(title=url, url=url)))
    return links


def _tool_result_text(result: ReadToolResult, links: list[_Link]) -> str:
    if not result.links:
        return result.observation_text
    known = {link.link.url for link in links}
    lines = [result.observation_text]
    added = 0
    for link in result.links:
        if link.url in known:
            continue
        known.add(link.url)
        candidate_number = sum(1 for item in links if item.link_id.startswith("c")) + 1
        entry = _Link(f"c{candidate_number}", link)
        links.append(entry)
        added += 1
        parts = [entry.link_id]
        if link.published:
            parts.append(f"[{link.published}]")
        parts.append(link.title)
        line = " ".join(parts)
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
