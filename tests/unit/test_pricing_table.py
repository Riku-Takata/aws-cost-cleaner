"""Unit tests for pricing.table."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

import pytest

from aws_cost_cleaner.pricing.table import BundledPriceTable, PriceLookup, PriceValue

SAMPLE = Path(__file__).resolve().parents[1] / "fixtures" / "prices_sample.json"


def _sample_data() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(SAMPLE.read_text(encoding="utf-8"))
    return data


def test_price_value_fields_and_frozen() -> None:
    """AC-2: 属性の値が合い、代入は FrozenInstanceError になる。"""
    v = PriceValue(usd=0.1, kind="bundled", as_of="2026-10-01")
    assert (v.usd, v.kind, v.as_of) == (0.1, "bundled", "2026-10-01")
    for name, val in (("usd", 1.0), ("kind", "cache"), ("as_of", "x")):
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(v, name, val)


def test_price_value_equality() -> None:
    """AC-3: 同じ引数の PriceValue は等しい。"""
    a = PriceValue(usd=0.1, kind="bundled", as_of="2026-10-01")
    b = PriceValue(usd=0.1, kind="bundled", as_of="2026-10-01")
    assert a == b


def test_price_lookup_importable() -> None:
    """AC-1: 3 つの名前が import できる。"""
    assert PriceLookup is not None


def test_load_default_from_other_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-6: カレントディレクトリが外でも load_default できる。"""
    monkeypatch.chdir(tmp_path)
    assert isinstance(BundledPriceTable.load_default(), BundledPriceTable)


@pytest.mark.parametrize(
    "args",
    [
        ("ap-northeast-1", "ebs_gb_month", "gp3"),
        ("us-east-1", "nat_gateway_hour", None),
        ("ap-northeast-1", "ec2_linux_hour", "t3.micro"),
        ("ap-northeast-1", "rds_hour", "mysql|single-az|db.t3.micro"),
    ],
)
def test_load_default_get_returns_none(args: tuple[str, str, str | None]) -> None:
    """AC-7: 同梱の表は空なので None を返す。"""
    assert BundledPriceTable.load_default().get(*args) is None


def test_load_default_get_without_sub() -> None:
    """AC-7: sub を省略しても None を返す。"""
    assert BundledPriceTable.load_default().get("us-east-1", "nat_gateway_hour") is None


def test_get_nested_from_sample() -> None:
    """AC-8, AC-9: 入れ子の値を PriceValue で返す。"""
    data = _sample_data()
    region = data["regions"]["ap-northeast-1"]
    assert region["ebs_gb_month"]["gp3"] > 0
    table = BundledPriceTable.from_file(SAMPLE)
    got = table.get("ap-northeast-1", "ebs_gb_month", "gp3")
    assert got is not None
    assert type(got.usd) is float
    assert got.usd == region["ebs_gb_month"]["gp3"]
    assert got.kind == "bundled"
    assert got.as_of == data["checked_at"]


def test_get_scalar_from_sample() -> None:
    """AC-10: 数値のキーを sub なしで引ける。"""
    data = _sample_data()
    got = BundledPriceTable.from_file(SAMPLE).get("ap-northeast-1", "nat_gateway_hour")
    assert got is not None
    assert type(got.usd) is float
    assert got.usd == data["regions"]["ap-northeast-1"]["nat_gateway_hour"]
    assert got.kind == "bundled"
    assert got.as_of == data["checked_at"]


def test_get_int_value_returns_float(tmp_path: Path) -> None:
    """AC-11: JSON の整数は float で返る。"""
    data = _sample_data()
    data["regions"]["ap-northeast-1"]["nat_gateway_hour"] = 0
    data["regions"]["ap-northeast-1"]["ebs_gb_month"] = {"gp3": 1}
    path = tmp_path / "p.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    table = BundledPriceTable.from_file(path)
    a = table.get("ap-northeast-1", "nat_gateway_hour")
    b = table.get("ap-northeast-1", "ebs_gb_month", "gp3")
    assert a is not None and b is not None
    assert type(a.usd) is float and a.usd == 0
    assert type(b.usd) is float and b.usd == 1


@pytest.mark.parametrize(
    "args",
    [
        ("xx-nowhere-1", "ebs_gb_month", "gp3"),
        ("ap-northeast-1", "no_such_key", "gp3"),
        ("ap-northeast-1", "no_such_key", None),
        ("ap-northeast-1", "ebs_gb_month", "no_such_volume_type"),
        ("ap-northeast-1", "ebs_gb_month", None),
        ("ap-northeast-1", "nat_gateway_hour", "gp3"),
    ],
)
def test_get_returns_none_for_missing(args: tuple[str, str, str | None]) -> None:
    """AC-12〜16: 見つからない引き方は None を返す。"""
    assert BundledPriceTable.from_file(SAMPLE).get(*args) is None
