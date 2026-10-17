"""Rebuild the bundled prices.json via AWS Price List Query API."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any

import aws_cost_cleaner.pricing
from aws_cost_cleaner.aws.session import ClientFactory
from aws_cost_cleaner.clock import SystemClock
from aws_cost_cleaner.errors import (
    EXIT_OK,
    EXIT_PARTIAL_FAILURE,
    AwsCostCleanerError,
)
from aws_cost_cleaner.pricing.price_list import (
    DEFAULT_CACHE_DIR,
    PriceListLookup,
)
from aws_cost_cleaner.pricing.table import PriceLookup

REGIONS: tuple[str, ...] = (
    "ap-northeast-1",
    "ap-northeast-3",
    "us-east-1",
    "us-west-2",
)
EC2_TYPES: tuple[str, ...] = (
    "t3.micro",
    "t3.small",
    "t3.medium",
    "t3.large",
    "m5.large",
    "m6i.large",
)
RDS_ENGINES = ("mysql", "postgres")
RDS_AZS = ("single-az", "multi-az")
RDS_CLASSES = ("db.t3.micro", "db.t3.small", "db.t3.medium", "db.m5.large")

KEYS: dict[str, tuple[str, ...] | None] = {
    "ebs_gb_month": (
        "gp2",
        "gp3",
        "io1",
        "io2",
        "st1",
        "sc1",
        "standard",
    ),
    "ebs_iops_month": ("gp3", "io1", "io2"),
    "ebs_throughput_mibps_month": ("gp3",),
    "public_ipv4_hour": None,
    "nat_gateway_hour": None,
    "alb_hour": None,
    "nlb_hour": None,
    "ec2_linux_hour": EC2_TYPES,
    "rds_hour": tuple(
        f"{e}|{az}|{c}" for e in RDS_ENGINES for az in RDS_AZS for c in RDS_CLASSES
    ),
    "rds_storage_gb_month": tuple(
        f"{t}|{az}" for t in ("gp2", "gp3", "io1", "io2") for az in RDS_AZS
    ),
    "rds_iops_month": tuple(f"{t}|{az}" for t in ("io1", "io2") for az in RDS_AZS),
}

SOURCE = (
    "AWS Price List Query API (pricing:GetProducts) / 取得スクリプト"
    " scripts/update_prices.py"
)
DEFAULT_OUTPUT: Path = (
    Path(aws_cost_cleaner.pricing.__file__).resolve().parent / "data" / "prices.json"
)


class PriceUnavailableError(AwsCostCleanerError):
    """Raised when a price cannot be fetched."""


def build_table(lookup: PriceLookup, today: date) -> dict[str, Any]:
    """Build price table dict for all regions."""
    regions: dict[str, Any] = {}
    for region in REGIONS:
        table: dict[str, Any] = {}
        for key, subs in KEYS.items():
            if subs is None:
                value = lookup.get(region, key)
                if value is None:
                    raise PriceUnavailableError(
                        f"{region}: {key} の料金を取得できなかった"
                    )
                table[key] = value.usd
            else:
                nested: dict[str, float] = {}
                for sub in subs:
                    val = lookup.get(region, key, sub)
                    if val is not None:
                        nested[sub] = val.usd
                table[key] = nested
        regions[region] = table
    return {
        "schema_version": 1,
        "currency": "USD",
        "hours_per_month": 730,
        "checked_at": today.isoformat(),
        "source": SOURCE,
        "regions": regions,
    }


def main(argv: Sequence[str] | None = None) -> int:
    """Update bundled prices.json using Price List Query API."""
    parser = argparse.ArgumentParser(
        description="Rebuild bundled prices.json via AWS Price List Query API"
    )
    parser.add_argument("--profile", default=None, help="AWS profile name")
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Path to output prices.json",
    )
    args = parser.parse_args(argv)

    clock = SystemClock()
    lookup = PriceListLookup(
        ClientFactory("read", args.profile), clock, DEFAULT_CACHE_DIR
    )
    today = clock.now().date()

    try:
        table = build_table(lookup, today)
    except PriceUnavailableError as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_PARTIAL_FAILURE

    tmp = args.output.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(table, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, args.output)
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
