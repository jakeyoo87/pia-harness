from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol
from enum import StrEnum

from .budget import ModelTokenBudget
from .session import ConversationContext, MemoryDocument, ToolObservation


class PromptContextKind(StrEnum):
    SYSTEM = "SYSTEM"
    MEMORY = "MEMORY"
    SUMMARY = "SUMMARY"
    USER_TURN = "USER_TURN"
    ASSISTANT_TURN = "ASSISTANT_TURN"
    CURRENT_USER = "CURRENT_USER"
    TOOL_REQUEST = "TOOL_REQUEST"
    TOOL_RESULT = "TOOL_RESULT"
    # Harness text inside the conversation: where earlier turns end, actions
    # waiting for confirmation, or the note that research hit its limit.
    HARNESS_NOTE = "HARNESS_NOTE"


# Earlier turns replay as they happened, tool calls included; without a line
# between them, research from an earlier question can read as this one's.
EARLIER_TURNS_NOTE = "The messages above are earlier turns, each already answered."


class PromptTrust(StrEnum):
    TRUSTED_INSTRUCTION = "TRUSTED_INSTRUCTION"
    UNTRUSTED_DATA = "UNTRUSTED_DATA"


@dataclass(frozen=True, slots=True)
class PromptContextPart:
    kind: PromptContextKind
    content: str
    trust: PromptTrust
    # Tool parts only: the call they belong to. A TOOL_REQUEST's content is its
    # arguments JSON; consecutive requests came in one model response.
    call_id: str = ""
    tool_name: str = ""


@dataclass(frozen=True, slots=True)
class AssembledPromptContext:
    parts: tuple[PromptContextPart, ...]
    estimated_input_tokens: int
    input_budget: int
    user_key: str = ""
    # The tools this request offers the model; none means it must answer.
    tools: tuple[ToolSpec, ...] = ()


class ToolSpec(Protocol):
    """What the model sees of a tool."""

    @property
    def name(self) -> str: ...

    @property
    def description(self) -> str: ...

    @property
    def arguments_schema(self) -> Mapping[str, Any] | None: ...


class PromptContextValidationError(RuntimeError):
    pass


class ContextBudgetExceeded(RuntimeError):
    def __init__(
        self,
        *,
        required_input_tokens: int,
        input_budget: int,
        token_budget: ModelTokenBudget,
    ) -> None:
        super().__init__(
            f"input requires {required_input_tokens} tokens but budget is {input_budget}"
        )
        self.required_input_tokens = required_input_tokens
        self.input_budget = input_budget
        self.token_budget = token_budget


class PromptContextAssembler:
    def __init__(
        self,
        # Counts the whole request: the parts and the tools offered with them.
        count_input_tokens: Callable[
            [tuple[PromptContextPart, ...], tuple[ToolSpec, ...]], int
        ],
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
        token_budget: ModelTokenBudget,
        tool_observations: tuple[ToolObservation, ...] = (),
        tools: tuple[ToolSpec, ...] = (),
        note: str | None = None,
        closing_note: str | None = None,
    ) -> AssembledPromptContext:
        user_key = _required_text("user_key", user_key)
        session_id = _required_text("session_id", session_id)
        _required_text("system_prompt", system_prompt)
        _required_text("current_user_message", current_user_message)
        if not isinstance(token_budget, ModelTokenBudget):
            raise PromptContextValidationError(
                "token_budget must be a ModelTokenBudget"
            )
        input_budget = token_budget.input_tokens
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
            tool_observations=tool_observations,
            note=note,
            closing_note=closing_note,
        )
        estimated_input_tokens = self._count_input_tokens(parts, tools)
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
                token_budget=token_budget,
            )
        return AssembledPromptContext(
            parts, estimated_input_tokens, input_budget, user_key, tools
        )


def _parts(
    *,
    system_prompt: str,
    memory: MemoryDocument | None,
    conversation: ConversationContext,
    current_user_message: str,
    tool_observations: tuple[ToolObservation, ...],
    note: str | None,
    closing_note: str | None,
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
        # Each earlier Turn replays as it happened: request, tool calls and
        # results, answer. Rounds restart in every Turn, so each is closed here.
        parts.append(
            PromptContextPart(
                PromptContextKind.USER_TURN,
                turn.user_message,
                PromptTrust.UNTRUSTED_DATA,
            )
        )
        parts.extend(_tool_parts(turn.tool_observations))
        parts.append(
            PromptContextPart(
                PromptContextKind.ASSISTANT_TURN,
                turn.assistant_message,
                PromptTrust.UNTRUSTED_DATA,
            )
        )
    if conversation.summary is not None or conversation.turns:
        parts.append(_note(EARLIER_TURNS_NOTE))
    parts.append(
        PromptContextPart(
            PromptContextKind.CURRENT_USER,
            current_user_message,
            PromptTrust.UNTRUSTED_DATA,
        )
    )
    if note is not None:
        parts.append(_note(note))
    parts.extend(_tool_parts(tool_observations))
    if closing_note is not None:
        parts.append(_note(closing_note))
    return tuple(parts)


def _tool_parts(
    tool_observations: tuple[ToolObservation, ...],
) -> list[PromptContextPart]:
    """One Turn's tool calls: each response's calls, then one result for each."""
    parts: list[PromptContextPart] = []
    rounds: dict[int, list[ToolObservation]] = {}
    for observation in tool_observations:
        if not isinstance(observation, ToolObservation):
            raise PromptContextValidationError("tool observation is invalid")
        if (
            not observation.name
            or not observation.arguments_json
            or not observation.result_text
            or not observation.call_id
        ):
            raise PromptContextValidationError("tool observation is incomplete")
        rounds.setdefault(observation.round, []).append(observation)
    for _round, observations in sorted(rounds.items()):
        parts.extend(
            PromptContextPart(
                PromptContextKind.TOOL_REQUEST,
                observation.arguments_json,
                PromptTrust.UNTRUSTED_DATA,
                call_id=observation.call_id,
                tool_name=observation.name,
            )
            for observation in observations
        )
        parts.extend(
            PromptContextPart(
                PromptContextKind.TOOL_RESULT,
                observation.result_text,
                PromptTrust.UNTRUSTED_DATA,
                call_id=observation.call_id,
                tool_name=observation.name,
            )
            for observation in observations
        )
    return parts


def _note(text: str) -> PromptContextPart:
    return PromptContextPart(
        PromptContextKind.HARNESS_NOTE,
        _required_text("note", text),
        PromptTrust.TRUSTED_INSTRUCTION,
    )


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
