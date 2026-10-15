"""Schema checks for the bundled prices.json."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import date
from importlib.resources import files
from pathlib import Path
from typing import Any

TOP_LEVEL_KEYS = {
    "schema_version",
    "currency",
    "hours_per_month",
    "checked_at",
    "source",
    "regions",
}
REGION_KEYS = {
    "ebs_gb_month",
    "ebs_iops_month",
    "ebs_throughput_mibps_month",
    "public_ipv4_hour",
    "nat_gateway_hour",
    "alb_hour",
    "nlb_hour",
    "ec2_linux_hour",
    "rds_hour",
    "rds_storage_gb_month",
    "rds_iops_month",
}
SAMPLE = Path(__file__).resolve().parents[1] / "fixtures" / "prices_sample.json"
REGION = "ap-northeast-1"


def _is_price(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and value >= 0


def _schema_errors(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    missing = TOP_LEVEL_KEYS - set(data)
    if missing:
        errors.append(f"missing top-level keys: {sorted(missing)}")
    regions = data.get("regions")
    if not isinstance(regions, dict):
        errors.append("regions is not a dict")
    else:
        for name, table in regions.items():
            if not isinstance(table, dict):
                errors.append(f"{name}: not a dict")
                continue
            lacking = REGION_KEYS - set(table)
            if lacking:
                errors.append(f"{name}: missing keys {sorted(lacking)}")
            for key, value in table.items():
                values = value.values() if isinstance(value, dict) else [value]
                for v in values:
                    if not _is_price(v):
                        errors.append(f"{name}.{key}: bad value {v!r}")
    checked_at = data.get("checked_at")
    if not isinstance(checked_at, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}", checked_at
    ):
        errors.append("checked_at is not YYYY-MM-DD")
    else:
        try:
            date.fromisoformat(checked_at)
        except ValueError:
            errors.append("checked_at is not a valid date")
    return errors


def _bundled() -> dict[str, Any]:
    text = (
        files("aws_cost_cleaner.pricing")
        .joinpath("data/prices.json")
        .read_text(encoding="utf-8")
    )
    data: dict[str, Any] = json.loads(text)
    return data


def _sample() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(SAMPLE.read_text(encoding="utf-8"))
    return data


def test_bundled_prices_json_matches_schema() -> None:
    """AC-17, AC-18: 同梱の prices.json がスキーマに合う。"""
    assert _schema_errors(_bundled()) == []


def test_bundled_prices_json_values() -> None:
    """AC-5: 同梱の prices.json の値。"""
    data = _bundled()
    assert set(data) == TOP_LEVEL_KEYS
    assert data["schema_version"] == 1
    assert data["currency"] == "USD"
    assert data["hours_per_month"] == 730
    assert data["regions"] == {}


def test_sample_prices_json_matches_schema() -> None:
    """AC-8: 見本の表がスキーマに合う。"""
    assert _schema_errors(_sample()) == []


def _drop_source(d: dict[str, Any]) -> None:
    d.pop("source")


def _drop_region_key(d: dict[str, Any]) -> None:
    d["regions"][REGION].pop("alb_hour")


def _negative(d: dict[str, Any]) -> None:
    d["regions"][REGION]["alb_hour"] = -1


def _bool_value(d: dict[str, Any]) -> None:
    d["regions"][REGION]["alb_hour"] = True


def _negative_nested(d: dict[str, Any]) -> None:
    d["regions"][REGION]["ebs_gb_month"]["gp3"] = -0.1


def _bad_date(d: dict[str, Any]) -> None:
    d["checked_at"] = "2026/10/01"


def test_schema_errors_detects_violations() -> None:
    """AC-17: 検査が違反を見つける。"""
    mutations: list[Callable[[dict[str, Any]], None]] = [
        _drop_source,
        _drop_region_key,
        _negative,
        _bool_value,
        _negative_nested,
        _bad_date,
    ]
    for mutate in mutations:
        data = _sample()
        mutate(data)
        assert _schema_errors(data) != []
