"""CLI entrypoint for aws-cost-cleaner."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence

from aws_cost_cleaner.errors import (
    EXIT_USAGE,
    ApprovalFileError,
    ConfigError,
    CostCsvError,
)

SUBCOMMANDS: tuple[str, ...] = ("scan", "report", "tag", "stop", "delete")


def _build_parser() -> argparse.ArgumentParser:
    """Build the command-line argument parser."""
    parser = argparse.ArgumentParser(
        prog="aws-cost-cleaner", description="AWS Cost Cleaner"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    descriptions = {
        "scan": "AWSリソースをスキャンします",
        "report": "スキャン結果からレポートを生成します",
        "tag": "リソースにタグを付与します",
        "stop": "リソースを停止します",
        "delete": "リソースを削除します",
    }

    for sub in SUBCOMMANDS:
        subparsers.add_parser(sub, help=descriptions[sub])

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    print(f"{args.command}: 未実装", file=sys.stderr)
    return 2


def run_with_error_handling(fn: Callable[[], int]) -> int:
    """Run fn; turn input errors into exit code 2 with a one-line message on stderr."""
    try:
        return fn()
    except (ConfigError, CostCsvError, ApprovalFileError) as exc:
        message = " ".join(str(exc).splitlines())
        print(f"{type(exc).__name__}: {message}", file=sys.stderr)
        return EXIT_USAGE
