"""Unit tests for run_with_error_handling in cli module."""

from __future__ import annotations

import pytest

from aws_cost_cleaner.cli import run_with_error_handling
from aws_cost_cleaner.errors import (
    AccountMismatchError,
    ApprovalFileError,
    AwsOperationError,
    ConfigError,
    CostCsvError,
    DisallowedApiCallError,
    StateMismatchError,
)


def test_import_run_with_error_handling() -> None:
    """AC-12: run_with_error_handling がインポートできる。"""
    assert callable(run_with_error_handling)


@pytest.mark.parametrize(
    ("exc", "expected_msg"),
    [
        (ConfigError("msg-config"), "msg-config"),
        (CostCsvError("msg-csv"), "msg-csv"),
        (ApprovalFileError("msg-approval"), "msg-approval"),
        (AccountMismatchError("msg-account"), "msg-account"),
    ],
)
def test_returns_usage_on_input_errors(
    exc: Exception,
    expected_msg: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """AC-13〜15: 入力エラー例外時に終了コード 2、stderr 1行、stdout 空。"""

    def raiser() -> int:
        raise exc

    return_code = run_with_error_handling(raiser)
    assert return_code == 2

    captured = capsys.readouterr()
    # AC-15: 標準出力に何も書かない
    assert captured.out == ""

    # AC-14: 空でない行がちょうど 1 行で、例外メッセージが含まれる
    non_empty_lines = [line for line in captured.err.splitlines() if line.strip()]
    assert len(non_empty_lines) == 1
    assert expected_msg in non_empty_lines[0]


@pytest.mark.parametrize("code", [0, 1, 2])
def test_passes_through_return_value(
    code: int,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """AC-16: 正常終了コードをそのまま返し、stdout / stderr に何も書かない。"""
    return_code = run_with_error_handling(lambda: code)
    assert return_code == code

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


@pytest.mark.parametrize(
    "exc",
    [
        StateMismatchError("s"),
        AwsOperationError("x", "C"),
        DisallowedApiCallError("d"),
        ValueError("v"),
    ],
)
def test_other_exceptions_propagate(exc: Exception) -> None:
    """AC-17: 対象外の例外はそのまま呼び出し元へ送出される。"""

    def raiser() -> int:
        raise exc

    with pytest.raises(type(exc)) as exc_info:
        run_with_error_handling(raiser)
    assert exc_info.value is exc
