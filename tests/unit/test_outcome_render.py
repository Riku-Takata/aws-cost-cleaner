"""Unit tests for render_outcomes and exit_code_for_outcomes."""

from __future__ import annotations

from typing import Any

import pytest

from aws_cost_cleaner.errors import (
    AccountMismatchError,
    StateMismatchError,
)
from aws_cost_cleaner.models import (
    Outcome,
    exit_code_for_outcomes,
    render_outcomes,
)


def _outcome(**overrides: Any) -> Outcome:
    defaults: dict[str, Any] = {
        "resource_type": "ebs_volume",
        "region": "us-east-1",
        "resource_id": "vol-1",
        "action": "tag",
        "status": "planned",
        "error": None,
        "aws_error_code": None,
        "note": None,
    }
    defaults.update(overrides)
    return Outcome(**defaults)


def test_importable() -> None:
    """AC-1: render_outcomes と exit_code_for_outcomes を import できる。"""
    from aws_cost_cleaner.models import exit_code_for_outcomes, render_outcomes

    assert callable(render_outcomes)
    assert callable(exit_code_for_outcomes)


def test_returns_str_for_list_and_tuple() -> None:
    """AC-2: render_outcomes は Outcome の列を受け取り、str を返す。"""
    items_list = [_outcome()]
    items_tuple = (_outcome(),)
    assert isinstance(render_outcomes(items_list), str)
    assert isinstance(render_outcomes(items_tuple), str)


def test_two_outcomes_three_lines() -> None:
    """AC-3: Outcome 2 件を渡すと、戻り値の splitlines() は 3 要素になる。"""
    outcomes = [_outcome(resource_id="vol-1"), _outcome(resource_id="vol-2")]
    lines = render_outcomes(outcomes).splitlines()
    assert len(lines) == 3


@pytest.mark.parametrize("n", [0, 1, 3])
def test_line_count_n_plus_one(n: int) -> None:
    """AC-4: N 件の Outcome を渡すと、splitlines() は N+1 要素になる。"""
    outcomes = [_outcome(resource_id=f"vol-{i}") for i in range(n)]
    lines = render_outcomes(outcomes).splitlines()
    assert len(lines) == n + 1


def test_empty_summary_only() -> None:
    """AC-4: 空のリストを渡すと、要約の 1 行だけになる。"""
    lines = render_outcomes([]).splitlines()
    assert lines == ["planned=0 done=0 skipped=0 failed=0"]


def test_order_preserved() -> None:
    """AC-5: splitlines() の各行は、入力の各 Outcome に対応する。"""
    outcomes = [
        _outcome(resource_id="a"),
        _outcome(resource_id="b"),
        _outcome(resource_id="c"),
    ]
    lines = render_outcomes(outcomes).splitlines()
    for i, res_id in enumerate(["a", "b", "c"]):
        fields = lines[i].split("\t")
        assert fields[4] == res_id


def test_eight_fields() -> None:
    """AC-6: 項目の行を split(\"\\t\") すると 8 欄になり、順に各欄になる。"""
    o = _outcome(
        status="done",
        action="stop",
        resource_type="ec2_instance",
        region="ap-northeast-1",
        resource_id="i-1234567890",
        error=None,
        aws_error_code=None,
        note="test note",
    )
    lines = render_outcomes([o]).splitlines()
    fields = lines[0].split("\t")
    assert len(fields) == 8
    assert fields[0] == "done"
    assert fields[1] == "stop"
    assert fields[2] == "ec2_instance"
    assert fields[3] == "ap-northeast-1"
    assert fields[4] == "i-1234567890"
    assert fields[5] == "-"
    assert fields[6] == "-"
    assert fields[7] == "test note"


def test_failed_row_exact() -> None:
    """AC-7: Outcome(...) の行が spec の期待値文字列と完全に一致する。"""
    o = Outcome(
        resource_type="ebs_volume",
        region="us-east-1",
        resource_id="vol-1",
        action="delete",
        status="failed",
        error=StateMismatchError("x"),
        aws_error_code="IncorrectState",
        note="memo",
    )
    lines = render_outcomes([o]).splitlines()
    expected = (
        "failed\tdelete\tebs_volume\tus-east-1\tvol-1\t"
        "StateMismatchError\tIncorrectState\tmemo"
    )
    assert lines[0] == expected


def test_state_mismatch_sixth_field() -> None:
    """AC-8: error が StateMismatchError の行は 6 番目の欄がその名前になる。"""
    o = _outcome(error=StateMismatchError("some error"))
    lines = render_outcomes([o]).splitlines()
    fields = lines[0].split("\t")
    assert fields[5] == "StateMismatchError"


def test_subclass_name_and_no_message() -> None:
    """AC-9: 6 番目の欄は具象クラス名になり親クラス名にならずメッセージも出ない。"""
    secret = "secret-msg-should-not-appear"
    o = _outcome(error=AccountMismatchError(secret))
    lines = render_outcomes([o]).splitlines()
    fields = lines[0].split("\t")
    assert fields[5] == "AccountMismatchError"
    assert secret not in lines[0]


def test_none_fields_dash() -> None:
    """AC-10: error、aws_error_code、note が None の欄は - になる。"""
    o = Outcome(
        resource_type="elastic_ip",
        region="ap-northeast-1",
        resource_id="eipalloc-1",
        action="tag",
        status="planned",
        error=None,
        aws_error_code=None,
        note=None,
    )
    lines = render_outcomes([o]).splitlines()
    expected = "planned\ttag\telastic_ip\tap-northeast-1\teipalloc-1\t-\t-\t-"
    assert lines[0] == expected


def test_summary_counts() -> None:
    """AC-11: 最後の行は planned=<n> ... の形式で各状態の件数と一致する。"""
    outcomes = [
        _outcome(status="planned"),
        _outcome(status="done"),
        _outcome(status="done"),
        _outcome(status="failed"),
    ]
    lines = render_outcomes(outcomes).splitlines()
    assert lines[-1] == "planned=1 done=2 skipped=0 failed=1"


def test_inputs_unchanged() -> None:
    """AC-12: render_outcomes は渡された Outcome を変更しない。"""
    o1 = _outcome(status="planned")
    o2 = _outcome(status="done")
    original = [o1, o2]
    snapshot = list(original)

    render_outcomes(original)

    assert original == snapshot
    assert original[0] == o1
    assert original[1] == o2


def test_exit_code_failed() -> None:
    """AC-13: failed の Outcome が 1 件でもあれば 1 を返す。"""
    outcomes = [
        _outcome(status="planned"),
        _outcome(status="failed"),
        _outcome(status="done"),
    ]
    assert exit_code_for_outcomes(outcomes) == 1

    single_failed = [_outcome(status="failed")]
    assert exit_code_for_outcomes(single_failed) == 1


def test_exit_code_empty() -> None:
    """AC-14: exit_code_for_outcomes は空のリストを渡すと 0 を返す。"""
    assert exit_code_for_outcomes([]) == 0


def test_exit_code_no_failed() -> None:
    """AC-15: planned, done, skipped だけなら 0 を返す。"""
    outcomes = [
        _outcome(status="planned"),
        _outcome(status="done"),
        _outcome(status="skipped"),
    ]
    assert exit_code_for_outcomes(outcomes) == 0
