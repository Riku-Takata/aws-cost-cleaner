"""Unit tests for errors module."""

from __future__ import annotations

import pytest

from aws_cost_cleaner.errors import (
    EXIT_OK,
    EXIT_PARTIAL_FAILURE,
    EXIT_USAGE,
    AccountMismatchError,
    ApprovalFileError,
    AwsCostCleanerError,
    AwsOperationError,
    ConfigError,
    CostCsvError,
    DisallowedApiCallError,
    NotCandidateError,
    ProtectedResourceError,
    StateMismatchError,
    WaitPeriodNotElapsedError,
)

ALL_SUBCLASSES: list[type[AwsCostCleanerError]] = [
    ConfigError,
    CostCsvError,
    ApprovalFileError,
    AccountMismatchError,
    DisallowedApiCallError,
    ProtectedResourceError,
    NotCandidateError,
    StateMismatchError,
    WaitPeriodNotElapsedError,
    AwsOperationError,
]

SINGLE_ARG_CLASSES: list[type[AwsCostCleanerError]] = [
    AwsCostCleanerError,
    ConfigError,
    CostCsvError,
    ApprovalFileError,
    AccountMismatchError,
    DisallowedApiCallError,
    ProtectedResourceError,
    NotCandidateError,
    StateMismatchError,
    WaitPeriodNotElapsedError,
]

OTHER_NINE_CLASSES: list[type[AwsCostCleanerError]] = [
    ConfigError,
    CostCsvError,
    ApprovalFileError,
    DisallowedApiCallError,
    ProtectedResourceError,
    NotCandidateError,
    StateMismatchError,
    WaitPeriodNotElapsedError,
    AwsOperationError,
]


def test_import_all_names() -> None:
    """AC-1: errors から必要な定数・例外クラスがインポートできる。"""
    assert AwsCostCleanerError is not None
    assert ConfigError is not None
    assert CostCsvError is not None
    assert ApprovalFileError is not None
    assert AccountMismatchError is not None
    assert DisallowedApiCallError is not None
    assert ProtectedResourceError is not None
    assert NotCandidateError is not None
    assert StateMismatchError is not None
    assert WaitPeriodNotElapsedError is not None
    assert AwsOperationError is not None
    assert EXIT_OK is not None
    assert EXIT_PARTIAL_FAILURE is not None
    assert EXIT_USAGE is not None


def test_base_is_exception() -> None:
    """AC-2: AwsCostCleanerError が Exception のサブクラスである。"""
    assert issubclass(AwsCostCleanerError, Exception)


@pytest.mark.parametrize("cls", ALL_SUBCLASSES)
def test_subclass_of_base(cls: type[AwsCostCleanerError]) -> None:
    """AC-3: 10 クラスはすべて AwsCostCleanerError のサブクラスである。"""
    assert issubclass(cls, AwsCostCleanerError)


def test_account_mismatch_is_approval_file_error() -> None:
    """AC-4: AccountMismatchError は ApprovalFileError のサブクラスである。"""
    assert issubclass(AccountMismatchError, ApprovalFileError)


@pytest.mark.parametrize("cls", OTHER_NINE_CLASSES)
def test_no_unexpected_parent(cls: type[AwsCostCleanerError]) -> None:
    """AC-5: 9 クラスは自分以外の 10 クラスの子ではない。"""
    for other in ALL_SUBCLASSES:
        if other is cls:
            continue
        assert not issubclass(cls, other)


@pytest.mark.parametrize("cls", SINGLE_ARG_CLASSES)
def test_message_only_constructor(cls: type[AwsCostCleanerError]) -> None:
    """AC-6: AwsOperationError を除く 10 クラスはメッセージ文字列 1 つで作れる。"""
    exc = cls("bad")
    assert "bad" in str(exc)


def test_aws_error_code() -> None:
    """AC-7: AwsOperationError('x', 'SomeCode').aws_error_code == 'SomeCode' である。"""
    exc = AwsOperationError("x", "SomeCode")
    assert exc.aws_error_code == "SomeCode"


def test_aws_error_code_none() -> None:
    """AC-8: AwsOperationError('x', None).aws_error_code is None である。"""
    exc = AwsOperationError("x", None)
    assert exc.aws_error_code is None


def test_aws_operation_error_keywords() -> None:
    """AC-9: AwsOperationError はキーワード引数でも作れる。"""
    exc = AwsOperationError(message="x", aws_error_code="SomeCode")
    assert exc.aws_error_code == "SomeCode"


def test_aws_operation_error_str() -> None:
    """AC-10: 'x' in str(AwsOperationError('x', 'SomeCode')) が True である。"""
    exc = AwsOperationError("x", "SomeCode")
    assert "x" in str(exc)


def test_exit_codes() -> None:
    """AC-11: 終了コード定数の値が正しい。"""
    assert EXIT_OK == 0
    assert EXIT_PARTIAL_FAILURE == 1
    assert EXIT_USAGE == 2
