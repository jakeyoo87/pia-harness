from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from .compaction import DEFAULT_MAX_RESPONSE_TOKENS
from .session import ConversationContext, MemoryDocument


class PromptContextKind(StrEnum):
    SYSTEM = "SYSTEM"
    MEMORY = "MEMORY"
    SUMMARY = "SUMMARY"
    USER_TURN = "USER_TURN"
    ASSISTANT_TURN = "ASSISTANT_TURN"
    CURRENT_USER = "CURRENT_USER"


class PromptTrust(StrEnum):
    TRUSTED_INSTRUCTION = "TRUSTED_INSTRUCTION"
    UNTRUSTED_DATA = "UNTRUSTED_DATA"


@dataclass(frozen=True, slots=True)
class PromptContextPart:
    kind: PromptContextKind
    content: str
    trust: PromptTrust


@dataclass(frozen=True, slots=True)
class AssembledPromptContext:
    parts: tuple[PromptContextPart, ...]
    estimated_input_tokens: int
    input_budget: int


class PromptContextValidationError(RuntimeError):
    pass


class ContextBudgetExceeded(RuntimeError):
    def __init__(
        self,
        *,
        required_input_tokens: int,
        input_budget: int,
        context_limit: int,
        reserved_response_tokens: int,
    ) -> None:
        super().__init__(
            f"input requires {required_input_tokens} tokens but budget is {input_budget}"
        )
        self.required_input_tokens = required_input_tokens
        self.input_budget = input_budget
        self.context_limit = context_limit
        self.reserved_response_tokens = reserved_response_tokens


class PromptContextAssembler:
    def __init__(
        self,
        count_input_tokens: Callable[[tuple[PromptContextPart, ...]], int],
    ) -> None:
        if not callable(count_input_tokens):
            raise ValueError("count_input_tokens must be callable")
        self._count_input_tokens = count_input_tokens

    def assemble(
        self,
        *,
        user_key: str,
        session_id: str,
        system_prompt: str,
        memory: MemoryDocument | None,
        conversation: ConversationContext,
        current_user_message: str,
        context_limit: int,
        reserved_response_tokens: int = DEFAULT_MAX_RESPONSE_TOKENS,
    ) -> AssembledPromptContext:
        user_key = _required_text("user_key", user_key)
        session_id = _required_text("session_id", session_id)
        _required_text("system_prompt", system_prompt)
        _required_text("current_user_message", current_user_message)
        context_limit = _positive_int("context_limit", context_limit)
        reserved_response_tokens = _positive_int(
            "reserved_response_tokens", reserved_response_tokens
        )
        input_budget = context_limit - reserved_response_tokens
        if input_budget <= 0:
            raise PromptContextValidationError(
                "context_limit must exceed reserved_response_tokens"
            )
        if not isinstance(conversation, ConversationContext):
            raise PromptContextValidationError(
                "conversation must be a ConversationContext"
            )

        _validate_identities_and_boundaries(
            user_key=user_key,
            session_id=session_id,
            memory=memory,
            conversation=conversation,
        )
        parts = _parts(
            system_prompt=system_prompt,
            memory=memory,
            conversation=conversation,
            current_user_message=current_user_message,
        )
        estimated_input_tokens = self._count_input_tokens(parts)
        if (
            isinstance(estimated_input_tokens, bool)
            or not isinstance(estimated_input_tokens, int)
            or estimated_input_tokens < 0
        ):
            raise PromptContextValidationError(
                "count_input_tokens must return a non-negative integer"
            )
        if estimated_input_tokens > input_budget:
            raise ContextBudgetExceeded(
                required_input_tokens=estimated_input_tokens,
                input_budget=input_budget,
                context_limit=context_limit,
                reserved_response_tokens=reserved_response_tokens,
            )
        return AssembledPromptContext(parts, estimated_input_tokens, input_budget)


def _parts(
    *,
    system_prompt: str,
    memory: MemoryDocument | None,
    conversation: ConversationContext,
    current_user_message: str,
) -> tuple[PromptContextPart, ...]:
    parts = [
        PromptContextPart(
            PromptContextKind.SYSTEM,
            system_prompt,
            PromptTrust.TRUSTED_INSTRUCTION,
        )
    ]
    if memory is not None and memory.memory_text.strip():
        parts.append(
            PromptContextPart(
                PromptContextKind.MEMORY,
                memory.memory_text,
                PromptTrust.UNTRUSTED_DATA,
            )
        )
    if conversation.summary is not None:
        parts.append(
            PromptContextPart(
                PromptContextKind.SUMMARY,
                conversation.summary.summary_text,
                PromptTrust.UNTRUSTED_DATA,
            )
        )
    for turn in conversation.turns:
        parts.extend(
            (
                PromptContextPart(
                    PromptContextKind.USER_TURN,
                    turn.user_message,
                    PromptTrust.UNTRUSTED_DATA,
                ),
                PromptContextPart(
                    PromptContextKind.ASSISTANT_TURN,
                    turn.assistant_message,
                    PromptTrust.UNTRUSTED_DATA,
                ),
            )
        )
    parts.append(
        PromptContextPart(
            PromptContextKind.CURRENT_USER,
            current_user_message,
            PromptTrust.UNTRUSTED_DATA,
        )
    )
    return tuple(parts)


def _validate_identities_and_boundaries(
    *,
    user_key: str,
    session_id: str,
    memory: MemoryDocument | None,
    conversation: ConversationContext,
) -> None:
    if memory is not None:
        if not isinstance(memory, MemoryDocument):
            raise PromptContextValidationError("memory must be a MemoryDocument")
        if memory.user_key != user_key:
            raise PromptContextValidationError("memory user does not match")

    summary = conversation.summary
    if summary is not None and (
        summary.user_key != user_key or summary.session_id != session_id
    ):
        raise PromptContextValidationError("summary identity does not match")

    previous_turn_id: str | None = None
    for turn in conversation.turns:
        if turn.user_key != user_key or turn.session_id != session_id:
            raise PromptContextValidationError("turn identity does not match")
        if previous_turn_id is not None and turn.turn_id <= previous_turn_id:
            raise PromptContextValidationError("turn IDs must be strictly increasing")
        if summary is not None and turn.turn_id <= summary.through_turn_id:
            raise PromptContextValidationError("turn overlaps the summary boundary")
        previous_turn_id = turn.turn_id


def _required_text(name: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PromptContextValidationError(f"{name} is required")
    return value


def _positive_int(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise PromptContextValidationError(f"{name} must be a positive integer")
    return value
