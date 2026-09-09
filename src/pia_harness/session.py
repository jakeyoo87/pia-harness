from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4


MEMORY_MAX_CHARS = 4_000
TURN_ID_PATTERN = re.compile(r"^[0-9]{8}T[0-9]{12}Z_[0-9a-f]{32}$")


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(UTC)


def utc_text(value: datetime) -> str:
    return as_utc(value).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def new_turn_id(created_at: datetime) -> str:
    prefix = as_utc(created_at).strftime("%Y%m%dT%H%M%S%fZ")
    return f"{prefix}_{uuid4().hex}"


def is_valid_turn_id(value: str) -> bool:
    return isinstance(value, str) and TURN_ID_PATTERN.fullmatch(value) is not None


def turn_id_matches_created_at(turn_id: str, created_at: datetime) -> bool:
    if not is_valid_turn_id(turn_id):
        return False
    expected_prefix = as_utc(created_at).strftime("%Y%m%dT%H%M%S%fZ_")
    return turn_id.startswith(expected_prefix)


@dataclass(frozen=True, slots=True)
class ActiveSession:
    user_key: str
    session_id: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class CompletedTurn:
    user_key: str
    session_id: str
    turn_id: str
    user_message: str
    assistant_message: str
    created_at: datetime
    expires_at: int


@dataclass(frozen=True, slots=True)
class RollingSummary:
    user_key: str
    session_id: str
    summary_text: str
    through_turn_id: str
    summary_tokens: int
    model_id: str
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class MemoryDocument:
    user_key: str
    memory_text: str
    last_reviewed_turn_id: str
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ConversationContext:
    summary: RollingSummary | None
    turns: tuple[CompletedTurn, ...]
