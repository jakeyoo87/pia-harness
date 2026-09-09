from __future__ import annotations

from dataclasses import dataclass


DEFAULT_MAX_RESPONSE_TOKENS = 4_096


@dataclass(frozen=True, slots=True)
class ModelTokenBudget:
    context_limit: int
    response_tokens: int = DEFAULT_MAX_RESPONSE_TOKENS

    def __post_init__(self) -> None:
        if (
            isinstance(self.context_limit, bool)
            or not isinstance(self.context_limit, int)
            or self.context_limit <= 0
        ):
            raise ValueError("context_limit must be a positive integer")
        if (
            isinstance(self.response_tokens, bool)
            or not isinstance(self.response_tokens, int)
            or self.response_tokens <= 0
        ):
            raise ValueError("response_tokens must be a positive integer")
        if self.context_limit <= self.response_tokens:
            raise ValueError("context_limit must exceed response_tokens")

    @property
    def input_tokens(self) -> int:
        return self.context_limit - self.response_tokens
