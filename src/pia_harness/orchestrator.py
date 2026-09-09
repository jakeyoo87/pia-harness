from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from .compaction import ContextUsage, TokenCompactor
from .context import (
    AssembledPromptContext,
    ContextBudgetExceeded,
    PromptContextAssembler,
)
from .dynamodb import DynamoDBConversationStore
from .memory import (
    AutomaticMemoryReviewer,
    CurrentMemoryInput,
    MemoryReviewResult,
    MemoryReviewStatus,
)
from .session import ActiveSession, as_utc, new_turn_id


MESSAGE_SEPARATOR = "\n\n--- additional user message ---\n\n"


class ExplicitMemoryMode(StrEnum):
    NONE = "NONE"
    REMEMBER_OR_CORRECT = "REMEMBER_OR_CORRECT"
    TARGETED_FORGET = "TARGETED_FORGET"


class OrchestratorStatus(StrEnum):
    DELIVERED = "DELIVERED"
    SUPERSEDED = "SUPERSEDED"
    CONTEXT_OVERFLOW = "CONTEXT_OVERFLOW"
    GENERATION_FAILED = "GENERATION_FAILED"
    DELIVERY_FAILED = "DELIVERY_FAILED"
    PERSISTENCE_FAILED = "PERSISTENCE_FAILED"


@dataclass(frozen=True, slots=True)
class ConversationInput:
    user_key: str
    message: str
    accepted_at: datetime
    turn_id: str
    memory_mode: ExplicitMemoryMode = ExplicitMemoryMode.NONE


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


@dataclass(frozen=True, slots=True)
class ConversationResetResult:
    session: ActiveSession
    memory_failed: bool = False


class _Phase(StrEnum):
    IDLE = "IDLE"
    GENERATING = "GENERATING"
    COMMITTING = "COMMITTING"


@dataclass(slots=True)
class _Submission:
    value: ConversationInput
    future: asyncio.Future[ConversationResult]


@dataclass(slots=True)
class _UserState:
    state_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    commit_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    generation_id: int = 0
    phase: _Phase = _Phase.IDLE
    pending: list[_Submission] = field(default_factory=list)
    active_task: asyncio.Task[None] | None = None
    committing_count: int = 0
    reset_requested: bool = False


class ConversationOrchestrator:
    def __init__(
        self,
        *,
        store: DynamoDBConversationStore,
        assembler: PromptContextAssembler,
        memory_reviewer: AutomaticMemoryReviewer,
        compactor: TokenCompactor,
        generate_answer: Callable[[AssembledPromptContext], Awaitable[GeneratedAnswer]],
        deliver: Callable[[str, str], Awaitable[None]],
        system_prompt: str,
        context_limit: int,
        model_id: str,
        reserved_response_tokens: int = 4096,
    ) -> None:
        if not callable(generate_answer):
            raise ValueError("generate_answer must be callable")
        if not callable(deliver):
            raise ValueError("deliver must be callable")
        if not isinstance(system_prompt, str) or not system_prompt.strip():
            raise ValueError("system_prompt is required")
        if not isinstance(model_id, str) or not model_id:
            raise ValueError("model_id is required")
        if isinstance(context_limit, bool) or not isinstance(context_limit, int):
            raise ValueError("context_limit must be an integer")
        if (
            isinstance(reserved_response_tokens, bool)
            or not isinstance(reserved_response_tokens, int)
            or reserved_response_tokens <= 0
        ):
            raise ValueError("reserved_response_tokens must be a positive integer")
        if context_limit <= reserved_response_tokens:
            raise ValueError("context_limit must exceed reserved_response_tokens")
        self._store = store
        self._assembler = assembler
        self._memory_reviewer = memory_reviewer
        self._compactor = compactor
        self._generate_answer = generate_answer
        self._deliver = deliver
        self._system_prompt = system_prompt
        self._context_limit = context_limit
        self._model_id = model_id
        self._reserved_response_tokens = reserved_response_tokens
        self._states: dict[str, _UserState] = {}
        self._states_lock = asyncio.Lock()

    async def submit(
        self,
        *,
        user_key: str,
        message: str,
        accepted_at: datetime | None = None,
        memory_mode: ExplicitMemoryMode | str = ExplicitMemoryMode.NONE,
    ) -> ConversationResult:
        value = _conversation_input(
            user_key=user_key,
            message=message,
            accepted_at=accepted_at or datetime.now(UTC),
            memory_mode=memory_mode,
        )
        state = await self._state_for(user_key)
        future: asyncio.Future[ConversationResult] = (
            asyncio.get_running_loop().create_future()
        )
        submission = _Submission(value, future)

        async with state.state_lock:
            if state.reset_requested:
                future.set_result(ConversationResult(OrchestratorStatus.SUPERSEDED))
            else:
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

    async def reset(
        self,
        *,
        user_key: str,
        now: datetime | None = None,
    ) -> ConversationResetResult:
        now = as_utc(now or datetime.now(UTC))
        state = await self._state_for(user_key)
        async with state.state_lock:
            state.reset_requested = True
            if state.phase is _Phase.GENERATING and state.active_task is not None:
                state.generation_id += 1
                state.active_task.cancel()
                _resolve_unfinished(state.pending, OrchestratorStatus.SUPERSEDED)
            elif state.phase is _Phase.COMMITTING:
                _resolve_unfinished(
                    state.pending[state.committing_count :],
                    OrchestratorStatus.SUPERSEDED,
                )
            else:
                _resolve_unfinished(state.pending, OrchestratorStatus.SUPERSEDED)

        memory_failed = False
        async with state.commit_lock:
            session = await _durable_call(
                self._store.get_or_create_active_session,
                user_key,
                now=now,
            )
            try:
                await _durable_call(
                    self._memory_reviewer.force_review,
                    user_key=user_key,
                    session_id=session.session_id,
                    now=now,
                )
            except Exception:
                memory_failed = True
            replacement = await _durable_call(
                self._store.reset_active_session,
                user_key=user_key,
                expected_session_id=session.session_id,
                now=now,
            )

        async with state.state_lock:
            _resolve_unfinished(state.pending, OrchestratorStatus.SUPERSEDED)
            state.pending.clear()
            state.phase = _Phase.IDLE
            state.active_task = None
            state.committing_count = 0
            state.reset_requested = False
        await self._remove_if_idle(user_key, state)
        return ConversationResetResult(replacement, memory_failed)

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
        try:
            async with state.commit_lock:
                session = await _durable_call(
                    self._store.get_or_create_active_session,
                    user_key,
                    now=batch[-1].value.accepted_at,
                )
            assembled, overflow_result, overflow_memory_failed = (
                await self._assemble_with_overflow(
                    user_key,
                    state,
                    generation_id,
                    batch,
                    session,
                )
            )
            memory_failed = memory_failed or overflow_memory_failed
            if overflow_result is not None:
                await self._finish_generation(
                    user_key,
                    state,
                    generation_id,
                    batch,
                    overflow_result,
                    delivery_succeeded=False,
                )
                return
            answer = await self._generate_answer(assembled)
            _validate_generated_answer(answer)
            if not await self._claim_commit(state, generation_id, len(batch)):
                return
            result, delivery_succeeded = await self._commit_response(
                user_key=user_key,
                state=state,
                batch=batch,
                session=session,
                answer=answer,
                memory_failed=memory_failed,
                compaction_failed=compaction_failed,
            )
            await self._finish_generation(
                user_key,
                state,
                generation_id,
                batch,
                result,
                delivery_succeeded=delivery_succeeded,
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
                delivery_succeeded=False,
            )

    async def _assemble_with_overflow(
        self,
        user_key: str,
        state: _UserState,
        generation_id: int,
        batch: tuple[_Submission, ...],
        session: ActiveSession,
    ) -> tuple[
        AssembledPromptContext | None,
        ConversationResult | None,
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
        try:
            return self._assemble(
                user_key, session, memory, conversation, combined
            ), None, False
        except ContextBudgetExceeded as overflow:
            memory_failed = False
            async with state.commit_lock:
                if not await self._is_current(state, generation_id):
                    raise asyncio.CancelledError
                try:
                    await _durable_call(
                        self._memory_reviewer.force_review,
                        user_key=user_key,
                        session_id=session.session_id,
                        now=batch[-1].value.accepted_at,
                    )
                except Exception:
                    memory_failed = True
                try:
                    compacted = await _durable_call(
                        self._compactor.compact_after_response,
                        user_key=user_key,
                        session_id=session.session_id,
                        context_limit=self._context_limit,
                        model_id=self._model_id,
                        estimated_context_tokens=overflow.required_input_tokens,
                        usage=None,
                        now=batch[-1].value.accepted_at,
                    )
                except Exception:
                    return None, ConversationResult(
                        OrchestratorStatus.CONTEXT_OVERFLOW,
                        memory_failed=memory_failed,
                        compaction_failed=True,
                    ), memory_failed
            if compacted is None:
                return None, ConversationResult(
                    OrchestratorStatus.CONTEXT_OVERFLOW,
                    memory_failed=memory_failed,
                ), memory_failed

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
                    user_key, session, memory, conversation, combined
                )
            except ContextBudgetExceeded:
                return None, ConversationResult(
                    OrchestratorStatus.CONTEXT_OVERFLOW,
                    memory_failed=memory_failed,
                ), memory_failed
            return assembled, None, memory_failed

    def _assemble(
        self,
        user_key: str,
        session: ActiveSession,
        memory: Any,
        conversation: Any,
        current_user_message: str,
    ) -> AssembledPromptContext:
        return self._assembler.assemble(
            user_key=user_key,
            session_id=session.session_id,
            system_prompt=self._system_prompt,
            memory=memory,
            conversation=conversation,
            current_user_message=current_user_message,
            context_limit=self._context_limit,
            reserved_response_tokens=self._reserved_response_tokens,
        )

    async def _claim_commit(
        self, state: _UserState, generation_id: int, batch_count: int
    ) -> bool:
        async with state.state_lock:
            if (
                state.reset_requested
                or state.phase is not _Phase.GENERATING
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
        memory_failed: bool,
        compaction_failed: bool,
    ) -> tuple[ConversationResult, bool]:
        changes: list[str] = []
        now = batch[-1].value.accepted_at
        combined = _combined_message(batch)
        async with state.commit_lock:
            try:
                review = await _durable_call(
                    self._memory_reviewer.review_if_due,
                    user_key=user_key,
                    session_id=session.session_id,
                    request_at=now,
                )
                _add_changes(changes, review)
            except Exception:
                memory_failed = True

            for submission in batch:
                value = submission.value
                if value.memory_mode is ExplicitMemoryMode.NONE:
                    continue
                explicit_text = (
                    combined if value.turn_id == batch[-1].value.turn_id else value.message
                )
                try:
                    review = await _durable_call(
                        self._memory_reviewer.review_explicit_input,
                        user_key=user_key,
                        session_id=session.session_id,
                        current_input=CurrentMemoryInput(
                            user_key=user_key,
                            session_id=session.session_id,
                            turn_id=value.turn_id,
                            user_message=explicit_text,
                            created_at=value.accepted_at,
                        ),
                        allow_clear=(
                            value.memory_mode is ExplicitMemoryMode.TARGETED_FORGET
                        ),
                        now=now,
                    )
                    _add_changes(changes, review)
                except Exception:
                    memory_failed = True

            if self._compactor.should_compact(
                context_limit=self._context_limit,
                model_id=answer.model_id,
                estimated_context_tokens=answer.estimated_total_tokens,
                usage=answer.usage,
            ):
                try:
                    review = await _durable_call(
                        self._memory_reviewer.force_review,
                        user_key=user_key,
                        session_id=session.session_id,
                        now=now,
                    )
                    _add_changes(changes, review)
                except Exception:
                    memory_failed = True
                try:
                    await _durable_call(
                        self._compactor.compact_after_response,
                        user_key=user_key,
                        session_id=session.session_id,
                        context_limit=self._context_limit,
                        model_id=answer.model_id,
                        estimated_context_tokens=answer.estimated_total_tokens,
                        usage=answer.usage,
                        now=now,
                    )
                except Exception:
                    compaction_failed = True

            final_text = _final_text(answer.text, changes)
            try:
                await self._deliver(user_key, final_text)
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
        delivery_succeeded: bool,
    ) -> None:
        start_next = False
        async with state.state_lock:
            if state.generation_id != generation_id:
                return
            if delivery_succeeded:
                del state.pending[: len(batch)]
            owner = batch[-1]
            if not owner.future.done():
                owner.future.set_result(result)
            state.phase = _Phase.IDLE
            state.active_task = None
            state.committing_count = 0
            queued_new_input = len(state.pending) > (0 if delivery_succeeded else len(batch))
            if queued_new_input and not state.reset_requested:
                self._start_generation_locked(user_key, state)
                start_next = True
        if not start_next:
            await self._remove_if_idle(user_key, state)

    async def _is_current(self, state: _UserState, generation_id: int) -> bool:
        async with state.state_lock:
            return (
                not state.reset_requested
                and state.phase is _Phase.GENERATING
                and state.generation_id == generation_id
            )

    async def _remove_if_idle(self, user_key: str, state: _UserState) -> None:
        async with self._states_lock:
            async with state.state_lock:
                if (
                    self._states.get(user_key) is state
                    and state.phase is _Phase.IDLE
                    and not state.pending
                    and not state.reset_requested
                ):
                    del self._states[user_key]


def _conversation_input(
    *,
    user_key: str,
    message: str,
    accepted_at: datetime,
    memory_mode: ExplicitMemoryMode | str,
) -> ConversationInput:
    if not isinstance(user_key, str) or not user_key:
        raise ValueError("user_key is required")
    if not isinstance(message, str) or not message:
        raise ValueError("message is required")
    accepted_at = as_utc(accepted_at)
    try:
        mode = ExplicitMemoryMode(memory_mode)
    except (TypeError, ValueError) as error:
        raise ValueError("memory_mode is invalid") from error
    return ConversationInput(
        user_key=user_key,
        message=message,
        accepted_at=accepted_at,
        turn_id=new_turn_id(accepted_at),
        memory_mode=mode,
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
        if item not in changes and len(changes) < 3:
            changes.append(item)


def _final_text(answer: str, changes: list[str]) -> str:
    if not changes:
        return answer
    return f"{answer}\n\n" + "\n".join(f"- {item}" for item in changes)


async def _durable_call(function: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await asyncio.shield(task)
        raise
