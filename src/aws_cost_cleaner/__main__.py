"""Module entrypoint for python -m aws_cost_cleaner."""

from __future__ import annotations

from aws_cost_cleaner.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
