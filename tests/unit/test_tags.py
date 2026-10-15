"""Unit tests for tags."""

from __future__ import annotations

import importlib
import sys
from datetime import UTC, datetime, timedelta, timezone

import pytest

from aws_cost_cleaner.tags import (
    CLEANUP_CANDIDATE,
    CLEANUP_REASON,
    CLEANUP_STOPPED_AT,
    KEEP,
    format_stopped_at,
    is_candidate,
    is_protected,
    parse_stopped_at,
)


def test_public_names_importable() -> None:
    """AC-1: 公開定数と関数が aws_cost_cleaner.tags から import できる。"""
    from aws_cost_cleaner.tags import (
        CLEANUP_CANDIDATE,
        CLEANUP_REASON,
        CLEANUP_STOPPED_AT,
        KEEP,
        format_stopped_at,
        is_candidate,
        is_protected,
        parse_stopped_at,
    )

    assert KEEP == "Keep"
    assert CLEANUP_CANDIDATE == "CleanupCandidate"
    assert CLEANUP_REASON == "CleanupReason"
    assert CLEANUP_STOPPED_AT == "CleanupStoppedAt"
    assert callable(is_protected)
    assert callable(is_candidate)
    assert callable(parse_stopped_at)
    assert callable(format_stopped_at)


def test_tag_key_constants() -> None:
    """AC-2: 各タグキー定数の値が仕様どおりである。"""
    assert KEEP == "Keep"
    assert CLEANUP_CANDIDATE == "CleanupCandidate"
    assert CLEANUP_REASON == "CleanupReason"
    assert CLEANUP_STOPPED_AT == "CleanupStoppedAt"


def test_import_without_boto3(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC-3: boto3 と botocore が import 不可でも tags モジュールを import できる。"""
    monkeypatch.setitem(sys.modules, "boto3", None)
    monkeypatch.setitem(sys.modules, "botocore", None)
    monkeypatch.delitem(sys.modules, "aws_cost_cleaner.tags", raising=False)

    mod = importlib.import_module("aws_cost_cleaner.tags")
    assert mod.KEEP == "Keep"


@pytest.mark.parametrize("value", ["true", "True", " TRUE "])
def test_is_protected_true_variants(value: str) -> None:
    """AC-4: Keep タグの大文字小文字や空白の揺れを許容して True を返す。"""
    assert is_protected({"Keep": value}) is True


@pytest.mark.parametrize("tags", [{"Keep": "false"}, {}])
def test_is_protected_false_cases(tags: dict[str, str]) -> None:
    """AC-5: Keep が false の場合やタグがない場合は False を返す。"""
    assert is_protected(tags) is False


def test_is_candidate_exact_true() -> None:
    """AC-6: CleanupCandidate が完全一致で 'true' のとき True を返す。"""
    assert is_candidate({"CleanupCandidate": "true"}) is True


@pytest.mark.parametrize(
    "tags",
    [
        {"CleanupCandidate": "True"},
        {"CleanupCandidate": "TRUE"},
        {},
    ],
)
def test_is_candidate_false_cases(tags: dict[str, str]) -> None:
    """AC-7: CleanupCandidate は完全一致のみ判定し、揺れやタグなしは False を返す。"""
    assert is_candidate(tags) is False


def test_parse_stopped_at_utc_z() -> None:
    """AC-8: UTC の Z 表記文字列を aware な datetime として正しくパースする。"""
    dt = parse_stopped_at("2026-10-08T03:00:00Z")
    assert dt is not None
    assert dt.tzinfo is not None
    assert dt == datetime(2026, 10, 8, 3, 0, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    "value",
    [
        "2026-10-08T03:00:00",
        "abc",
        None,
    ],
)
def test_parse_stopped_at_invalid(value: str | None) -> None:
    """AC-9: タイムゾーンなし、不正形式、None は例外を出さず None を返す。"""
    assert parse_stopped_at(value) is None


def test_format_stopped_at_converts_to_utc() -> None:
    """AC-10: 任意のタイムゾーンを持つ datetime を UTC の ISO-8601 文字列に整形する。"""
    dt = datetime(2026, 10, 15, 12, 0, 0, tzinfo=timezone(timedelta(hours=9)))
    assert format_stopped_at(dt) == "2026-10-15T03:00:00Z"


def test_format_then_parse_roundtrip() -> None:
    """AC-11: format と parse で元の時刻と等しい値に戻る。"""
    input_dt = datetime(2026, 10, 15, 3, 0, 0, tzinfo=UTC)
    assert parse_stopped_at(format_stopped_at(input_dt)) == input_dt
