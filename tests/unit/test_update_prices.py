"""Tests for scripts/update_prices.py."""

from __future__ import annotations

import importlib.util
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any, cast

import pytest

from aws_cost_cleaner.errors import AwsCostCleanerError
from aws_cost_cleaner.pricing.table import PriceValue
from tests.unit.test_prices_schema import _schema_errors

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "update_prices.py"
_spec = importlib.util.spec_from_file_location("update_prices", _SCRIPT_PATH)
assert _spec is not None
assert _spec.loader is not None
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

build_table: Callable[[Any, date], dict[str, Any]] = _mod.build_table
main: Callable[[list[str] | None], int] = _mod.main
EC2_TYPES: tuple[str, ...] = _mod.EC2_TYPES
PriceUnavailableError: type[Exception] = _mod.PriceUnavailableError


class FakeLookup:
    """Fake PriceLookup returning fixed PriceValue or None."""

    def __init__(
        self,
        missing: Callable[[str, str, str | None], bool] = lambda r, k, s: False,
        values: dict[tuple[str, str, str | None], float] | None = None,
    ) -> None:
        self._missing = missing
        self._values = values or {}

    def get(self, region: str, key: str, sub: str | None = None) -> PriceValue | None:
        if self._missing(region, key, sub):
            return None
        return PriceValue(
            usd=self._values.get((region, key, sub), 0.01),
            kind="price_list_api",
            as_of="2026-10-17",
        )


def test_module_attributes() -> None:
    """AC-1: モジュールのインポートと公開属性の参照。"""
    assert hasattr(_mod, "build_table")
    assert hasattr(_mod, "main")
    assert hasattr(_mod, "EC2_TYPES")
    assert hasattr(_mod, "PriceUnavailableError")
    assert issubclass(PriceUnavailableError, AwsCostCleanerError)


def test_ec2_types() -> None:
    """AC-2: EC2_TYPES の要素順と件数。"""
    assert list(EC2_TYPES) == [
        "t3.micro",
        "t3.small",
        "t3.medium",
        "t3.large",
        "m5.large",
        "m6i.large",
    ]
    assert len(EC2_TYPES) == 6


def test_full_table_passes_schema() -> None:
    """AC-3: 偽の PriceLookup で組み立てた表がスキーマ検証に通る。"""
    lookup = FakeLookup()
    result = build_table(lookup, date(2026, 10, 17))
    assert _schema_errors(result) == []


def test_top_level_values() -> None:
    """AC-4: 最上位の各フィールドの値。"""
    lookup = FakeLookup()
    result = build_table(lookup, date(2026, 10, 17))
    assert result["schema_version"] == 1
    assert result["currency"] == "USD"
    assert result["hours_per_month"] == 730
    assert isinstance(result["source"], str)
    assert len(result["source"]) > 0


def test_regions_exact() -> None:
    """AC-5: regions のキーの集合が正確に 4 リージョンである。"""
    lookup = FakeLookup()
    result = build_table(lookup, date(2026, 10, 17))
    assert set(result["regions"]) == {
        "ap-northeast-1",
        "ap-northeast-3",
        "us-east-1",
        "us-west-2",
    }


def test_checked_at() -> None:
    """AC-6: checked_at が渡した日付の YYYY-MM-DD になる。"""
    lookup = FakeLookup()
    res1 = build_table(lookup, date(2026, 10, 17))
    assert res1["checked_at"] == "2026-10-17"
    res2 = build_table(lookup, date(2027, 1, 5))
    assert res2["checked_at"] == "2027-01-05"


def test_nested_sub_keys() -> None:
    """AC-7: 各リージョンの入れ子キーに含まれる sub キーの集合。"""
    lookup = FakeLookup()
    result = build_table(lookup, date(2026, 10, 17))

    expected_ec2 = {
        "t3.micro",
        "t3.small",
        "t3.medium",
        "t3.large",
        "m5.large",
        "m6i.large",
    }
    expected_rds = {
        f"{engine}|{az}|{cls}"
        for engine in ("mysql", "postgres")
        for az in ("single-az", "multi-az")
        for cls in (
            "db.t3.micro",
            "db.t3.small",
            "db.t3.medium",
            "db.m5.large",
        )
    }
    expected_ebs_gb = {
        "gp2",
        "gp3",
        "io1",
        "io2",
        "st1",
        "sc1",
        "standard",
    }
    expected_ebs_iops = {"gp3", "io1", "io2"}
    expected_ebs_throughput = {"gp3"}
    expected_rds_storage = {
        f"{t}|{az}"
        for t in ("gp2", "gp3", "io1", "io2")
        for az in ("single-az", "multi-az")
    }
    expected_rds_iops = {
        f"{t}|{az}" for t in ("io1", "io2") for az in ("single-az", "multi-az")
    }

    assert len(expected_rds) == 16
    assert len(expected_rds_storage) == 8
    assert len(expected_rds_iops) == 4

    for region, table in cast(dict[str, Any], result["regions"]).items():
        assert set(table["ec2_linux_hour"]) == expected_ec2, region
        assert set(table["rds_hour"]) == expected_rds, region
        assert set(table["ebs_gb_month"]) == expected_ebs_gb, region
        assert set(table["ebs_iops_month"]) == expected_ebs_iops, region
        assert set(table["ebs_throughput_mibps_month"]) == expected_ebs_throughput, (
            region
        )
        assert set(table["rds_storage_gb_month"]) == expected_rds_storage, region
        assert set(table["rds_iops_month"]) == expected_rds_iops, region


def test_values_come_from_lookup() -> None:
    """AC-8: 結果の値が PriceLookup の返した PriceValue.usd と一致する。"""
    values = {
        ("us-west-2", "nat_gateway_hour", None): 0.045,
        ("us-west-2", "ec2_linux_hour", "t3.micro"): 0.0104,
    }
    lookup = FakeLookup(values=values)
    result = build_table(lookup, date(2026, 10, 17))
    assert result["regions"]["us-west-2"]["nat_gateway_hour"] == 0.045
    assert result["regions"]["us-west-2"]["ec2_linux_hour"]["t3.micro"] == 0.0104


def test_scalar_missing_raises() -> None:
    """AC-9, AC-10: 入れ子でないキーが欠けたときに PriceUnavailableError。"""
    scalar_keys = [
        "public_ipv4_hour",
        "nat_gateway_hour",
        "alb_hour",
        "nlb_hour",
    ]
    # AC-9, AC-10: 全リージョンで 1 つだけ None
    for target_key in scalar_keys:
        lookup = FakeLookup(missing=lambda r, k, s, tk=target_key: k == tk)
        with pytest.raises(PriceUnavailableError):
            build_table(lookup, date(2026, 10, 17))

    # AC-10: 4 リージョンのうち 1 つだけで None
    for target_key in scalar_keys:
        for target_region in (
            "ap-northeast-1",
            "ap-northeast-3",
            "us-east-1",
            "us-west-2",
        ):
            lookup = FakeLookup(
                missing=lambda r, k, s, tr=target_region, tk=target_key: (
                    r == tr and k == tk
                )
            )
            with pytest.raises(PriceUnavailableError):
                build_table(lookup, date(2026, 10, 17))


def test_partial_ec2_types_omitted() -> None:
    """AC-11: ec2_linux_hour の一部が None の場合はスキップされ、例外は出ない。"""
    omitted = {"t3.large", "m6i.large"}
    lookup = FakeLookup(missing=lambda r, k, s: k == "ec2_linux_hour" and s in omitted)
    result = build_table(lookup, date(2026, 10, 17))

    expected_ec2 = {"t3.micro", "t3.small", "t3.medium", "m5.large"}
    for region, table in cast(dict[str, Any], result["regions"]).items():
        assert set(table["ec2_linux_hour"]) == expected_ec2, region
        # 他のキーは完全であること
        assert len(table["rds_hour"]) == 16
        assert len(table["ebs_gb_month"]) == 7
        assert len(table["ebs_iops_month"]) == 3
        assert len(table["ebs_throughput_mibps_month"]) == 1
        assert len(table["rds_storage_gb_month"]) == 8
        assert len(table["rds_iops_month"]) == 4


def test_nested_all_missing_is_empty_dict() -> None:
    """AC-12: 入れ子のキーで値が 1 つも取れない場合、空辞書として残る。"""
    lookup = FakeLookup(missing=lambda r, k, s: k == "rds_iops_month")
    result = build_table(lookup, date(2026, 10, 17))

    for region, table in cast(dict[str, Any], result["regions"]).items():
        assert table["rds_iops_month"] == {}, region


def test_main_fails_without_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-13: main は FILTERS が None の状態で失敗し、出力ファイルを作成しない。"""
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.delenv("AWS_DEFAULT_PROFILE", raising=False)

    out_file = tmp_path / "out.json"
    ret = main(["--output", str(out_file)])

    assert ret != 0
    assert not out_file.exists()
    assert not out_file.with_suffix(".tmp").exists()
