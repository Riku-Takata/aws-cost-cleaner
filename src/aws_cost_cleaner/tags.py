"""Tag keys and tag-based predicates."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime

KEEP = "Keep"
CLEANUP_CANDIDATE = "CleanupCandidate"
CLEANUP_REASON = "CleanupReason"
CLEANUP_STOPPED_AT = "CleanupStoppedAt"

_STOPPED_AT_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def is_protected(tags: Mapping[str, str]) -> bool:
    """リソースが Keep タグで保護されているかを判定する。"""
    value = tags.get(KEEP)
    if value is None:
        return False
    return value.strip().lower() == "true"


def is_candidate(tags: Mapping[str, str]) -> bool:
    """リソースが削除候補(CleanupCandidate)としてマークされているかを判定する。"""
    return tags.get(CLEANUP_CANDIDATE) == "true"


def parse_stopped_at(value: str | None) -> datetime | None:
    """CleanupStoppedAt タグの文字列を aware な datetime に変換する。"""
    if value is None:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None or dt.utcoffset() is None:
        return None
    return dt


def format_stopped_at(dt: datetime) -> str:
    """aware な datetime を CleanupStoppedAt の UTC ISO-8601 文字列に変換する。"""
    return dt.astimezone(UTC).strftime(_STOPPED_AT_FORMAT)
