"""Unit tests for CLI."""

from __future__ import annotations

import pytest

from aws_cost_cleaner.cli import SUBCOMMANDS, main


@pytest.mark.parametrize("cmd", SUBCOMMANDS)
def test_subcommand_unimplemented(cmd: str, capsys: pytest.CaptureFixture[str]) -> None:
    """AC-3: 各サブコマンドを実行すると未実装エラーとなり終了コード 2 を返す。"""
    exit_code = main([cmd])
    assert exit_code == 2
    captured = capsys.readouterr()
    assert "未実装" in captured.err


def test_help_lists_all_subcommands(capsys: pytest.CaptureFixture[str]) -> None:
    """AC-1: --help で 5 つのサブコマンドが表示される。"""
    with pytest.raises(SystemExit) as exc_info:
        main(["--help"])
    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    for sub in SUBCOMMANDS:
        assert sub in captured.out
