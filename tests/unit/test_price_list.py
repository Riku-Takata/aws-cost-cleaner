"""Unit tests for pricing.price_list."""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from botocore.stub import Stubber  # type: ignore[import-not-found]

from aws_cost_cleaner.aws.session import ClientFactory
from aws_cost_cleaner.clock import FixedClock
from aws_cost_cleaner.pricing.price_list import (
    DEFAULT_CACHE_DIR,
    FILTERS,
    CachedPriceLookup,
    PriceFilter,
    PriceListLookup,
)
from aws_cost_cleaner.pricing.table import PriceValue

FIXED_NOW = datetime(2026, 10, 15, 23, 59, tzinfo=UTC)
NAT_FILTER = PriceFilter("AmazonEC2", (("productFamily", "NAT Gateway"),))


@pytest.fixture
def aws_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-northeast-1")
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    yield


def _make_factory_and_stubber() -> tuple[ClientFactory, Any, Stubber]:
    factory = ClientFactory("read")
    client = factory.client("pricing", "us-east-1")
    stubber = Stubber(client)
    stubber.activate()
    return factory, client, stubber


def _price_item(
    usd: str | None = "0.0620000000",
    *,
    terms: int = 1,
    dims: int = 1,
) -> str:
    price_per_unit: dict[str, Any] = {} if usd is None else {"USD": usd}
    dim_map = {f"DIM.{i}": {"pricePerUnit": price_per_unit} for i in range(dims)}
    term_map = {f"TERM.{j}": {"priceDimensions": dim_map} for j in range(terms)}
    payload = {
        "product": {"sku": "SKU123"},
        "terms": {"OnDemand": term_map},
    }
    return json.dumps(payload)


def test_public_names_and_default_cache_dir(aws_env: None, tmp_path: Path) -> None:
    """AC-1, AC-2, AC-22: 公開名・位置引数・PriceFilter・DEFAULT_CACHE_DIR。"""
    factory = ClientFactory("read")
    clock = FixedClock(FIXED_NOW)

    lookup = PriceListLookup(factory, clock, tmp_path)
    cached = CachedPriceLookup(tmp_path, clock, 24)
    assert lookup is not None
    assert cached is not None

    with pytest.raises(TypeError):
        PriceListLookup(factory, clock, tmp_path, {})  # type: ignore[call-arg]

    pf = PriceFilter("AmazonEC2")
    assert [f.name for f in dataclasses.fields(PriceFilter)] == [
        "service_code",
        "fields",
        "sub_fields",
    ]
    assert pf.service_code == "AmazonEC2"
    assert pf.fields == ()
    assert pf.sub_fields == ()
    with pytest.raises(dataclasses.FrozenInstanceError):
        pf.service_code = "AmazonRDS"  # type: ignore[misc]

    assert isinstance(FILTERS, Mapping)
    assert DEFAULT_CACHE_DIR == Path("~/.cache/aws-cost-cleaner/prices").expanduser()


def test_filters_all_unconfirmed() -> None:
    """AC-3: FILTERS には 11 キーがあり、すべて None。"""
    expected_keys = {
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
    assert set(FILTERS.keys()) == expected_keys
    for key in expected_keys:
        assert FILTERS[key] is None


def test_default_filters_never_call_api(aws_env: None, tmp_path: Path) -> None:
    """AC-4, AC-5, AC-21: 未確認キーおよび未定義キーでは API を呼ばず None を返す。"""
    factory, _, stubber = _make_factory_and_stubber()
    lookup = PriceListLookup(factory, FixedClock(FIXED_NOW), tmp_path)

    cases: list[tuple[str, str | None]] = [
        ("ebs_gb_month", "gp3"),
        ("ebs_iops_month", "gp3"),
        ("ebs_throughput_mibps_month", "gp3"),
        ("public_ipv4_hour", None),
        ("nat_gateway_hour", None),
        ("alb_hour", None),
        ("nlb_hour", None),
        ("ec2_linux_hour", "t3.micro"),
        ("rds_hour", "mysql|single-az|db.t3.micro"),
        ("rds_storage_gb_month", "gp2|single-az"),
        ("rds_iops_month", "io1|single-az"),
        ("no_such_key", None),
        ("no_such_key", "gp3"),
    ]
    for key, sub in cases:
        assert lookup.get("us-east-1", key, sub) is None

    stubber.assert_no_pending_responses()
    assert list(tmp_path.glob("*.json")) == []


def test_get_success_returns_price_list_api_value(
    aws_env: None, tmp_path: Path
) -> None:
    """AC-6, AC-7, AC-8, AC-14: GetProducts で取得して PriceValue を返す。"""
    factory, client, stubber = _make_factory_and_stubber()
    stubber.add_response(
        "get_products",
        {"PriceList": [_price_item("0.0620000000")]},
        expected_params={
            "ServiceCode": "AmazonEC2",
            "Filters": [
                {
                    "Type": "TERM_MATCH",
                    "Field": "regionCode",
                    "Value": "ap-northeast-1",
                },
                {
                    "Type": "TERM_MATCH",
                    "Field": "productFamily",
                    "Value": "NAT Gateway",
                },
            ],
        },
    )

    lookup = PriceListLookup(
        factory,
        FixedClock(FIXED_NOW),
        tmp_path,
        filters={"nat_gateway_hour": NAT_FILTER},
    )
    got = lookup.get("ap-northeast-1", "nat_gateway_hour")

    assert got == PriceValue(usd=0.062, kind="price_list_api", as_of="2026-10-15")
    assert client.meta.region_name == "us-east-1"
    stubber.assert_no_pending_responses()


def test_sub_fields_are_mapped_in_order(aws_env: None, tmp_path: Path) -> None:
    """AC-9: sub を '|' で分割した各要素が sub_fields の順で Filters に加わる。"""
    factory, _, stubber = _make_factory_and_stubber()
    rds_filter = PriceFilter(
        "AmazonRDS",
        (),
        ("databaseEngine", "deploymentOption", "instanceType"),
    )
    stubber.add_response(
        "get_products",
        {"PriceList": [_price_item("0.0280000000")]},
        expected_params={
            "ServiceCode": "AmazonRDS",
            "Filters": [
                {
                    "Type": "TERM_MATCH",
                    "Field": "regionCode",
                    "Value": "ap-northeast-1",
                },
                {
                    "Type": "TERM_MATCH",
                    "Field": "databaseEngine",
                    "Value": "MySQL",
                },
                {
                    "Type": "TERM_MATCH",
                    "Field": "deploymentOption",
                    "Value": "Single-AZ",
                },
                {
                    "Type": "TERM_MATCH",
                    "Field": "instanceType",
                    "Value": "db.t3.micro",
                },
            ],
        },
    )

    lookup = PriceListLookup(
        factory,
        FixedClock(FIXED_NOW),
        tmp_path,
        filters={"rds_hour": rds_filter},
    )
    got = lookup.get("ap-northeast-1", "rds_hour", "MySQL|Single-AZ|db.t3.micro")
    assert got == PriceValue(usd=0.028, kind="price_list_api", as_of="2026-10-15")
    stubber.assert_no_pending_responses()


@pytest.mark.parametrize(
    ("spec", "sub"),
    [
        (NAT_FILTER, "unexpected_sub"),
        (
            PriceFilter("AmazonEC2", (), ("volumeApiName",)),
            None,
        ),
        (
            PriceFilter(
                "AmazonRDS",
                (),
                ("databaseEngine", "deploymentOption", "instanceType"),
            ),
            "MySQL|Single-AZ",
        ),
        (
            PriceFilter(
                "AmazonRDS",
                (),
                ("databaseEngine", "deploymentOption", "instanceType"),
            ),
            "MySQL|Single-AZ|db.t3.micro|extra",
        ),
    ],
)
def test_sub_mismatch_returns_none_without_call(
    aws_env: None,
    tmp_path: Path,
    spec: PriceFilter,
    sub: str | None,
) -> None:
    """AC-10, AC-21: sub と sub_fields が合わないときは API を呼ばず None。"""
    factory, _, stubber = _make_factory_and_stubber()
    lookup = PriceListLookup(
        factory,
        FixedClock(FIXED_NOW),
        tmp_path,
        filters={"k": spec},
    )
    assert lookup.get("ap-northeast-1", "k", sub) is None
    stubber.assert_no_pending_responses()
    assert list(tmp_path.glob("*.json")) == []


def test_client_error_returns_none(aws_env: None, tmp_path: Path) -> None:
    """AC-11, AC-21: GetProducts が ClientError のとき例外を出さず None。"""
    factory, _, stubber = _make_factory_and_stubber()
    stubber.add_client_error(
        "get_products",
        service_error_code="AccessDeniedException",
    )
    lookup = PriceListLookup(
        factory,
        FixedClock(FIXED_NOW),
        tmp_path,
        filters={"nat_gateway_hour": NAT_FILTER},
    )
    assert lookup.get("ap-northeast-1", "nat_gateway_hour") is None
    stubber.assert_no_pending_responses()
    assert list(tmp_path.glob("*.json")) == []


def test_empty_price_list_returns_none(aws_env: None, tmp_path: Path) -> None:
    """AC-12, AC-21: PriceList が空のとき None。"""
    factory, _, stubber = _make_factory_and_stubber()
    stubber.add_response("get_products", {"PriceList": []})
    lookup = PriceListLookup(
        factory,
        FixedClock(FIXED_NOW),
        tmp_path,
        filters={"nat_gateway_hour": NAT_FILTER},
    )
    assert lookup.get("ap-northeast-1", "nat_gateway_hour") is None
    stubber.assert_no_pending_responses()
    assert list(tmp_path.glob("*.json")) == []


@pytest.mark.parametrize(
    "price_list",
    [
        [_price_item("0.062"), _price_item("0.062")],
        [_price_item("0.062", terms=0)],
        [_price_item("0.062", terms=2)],
        [_price_item("0.062", dims=0)],
        [_price_item("0.062", dims=2)],
        [_price_item(None)],
        [_price_item("abc")],
    ],
)
def test_ambiguous_or_broken_response_returns_none(
    aws_env: None,
    tmp_path: Path,
    price_list: list[str],
) -> None:
    """AC-13, AC-21: 複数要素や欠損・不正値の応答では None を返しキャッシュしない。"""
    factory, _, stubber = _make_factory_and_stubber()
    stubber.add_response("get_products", {"PriceList": price_list})
    lookup = PriceListLookup(
        factory,
        FixedClock(FIXED_NOW),
        tmp_path,
        filters={"nat_gateway_hour": NAT_FILTER},
    )
    assert lookup.get("ap-northeast-1", "nat_gateway_hour") is None
    stubber.assert_no_pending_responses()
    assert list(tmp_path.glob("*.json")) == []


def test_cache_file_written_and_readable(aws_env: None, tmp_path: Path) -> None:
    """AC-15: 取得成功後、cache_dir に .json が作られ json.loads で読める。"""
    factory, _, stubber = _make_factory_and_stubber()
    stubber.add_response("get_products", {"PriceList": [_price_item("0.0620000000")]})
    lookup = PriceListLookup(
        factory,
        FixedClock(FIXED_NOW),
        tmp_path,
        filters={"nat_gateway_hour": NAT_FILTER},
    )
    assert lookup.get("ap-northeast-1", "nat_gateway_hour") is not None

    files = list(tmp_path.glob("*.json"))
    assert len(files) >= 1
    for p in files:
        parsed = json.loads(p.read_text(encoding="utf-8"))
        assert parsed is not None


def test_missing_cache_dir_is_created(aws_env: None, tmp_path: Path) -> None:
    """AC-16: 存在しない cache_dir を渡してもディレクトリが作られて保存される。"""
    cache_dir = tmp_path / "a" / "b"
    factory, _, stubber = _make_factory_and_stubber()
    stubber.add_response("get_products", {"PriceList": [_price_item("0.0620000000")]})
    lookup = PriceListLookup(
        factory,
        FixedClock(FIXED_NOW),
        cache_dir,
        filters={"nat_gateway_hour": NAT_FILTER},
    )
    got = lookup.get("ap-northeast-1", "nat_gateway_hour")
    assert got == PriceValue(usd=0.062, kind="price_list_api", as_of="2026-10-15")
    assert cache_dir.is_dir()
    files = list(cache_dir.glob("*.json"))
    assert len(files) >= 1
    for p in files:
        json.loads(p.read_text(encoding="utf-8"))


def test_cached_lookup_returns_cache_kind(aws_env: None, tmp_path: Path) -> None:
    """AC-17: CachedPriceLookup が保存済みの単価を kind='cache' で返す。"""
    factory, _, stubber = _make_factory_and_stubber()
    stubber.add_response("get_products", {"PriceList": [_price_item("0.0620000000")]})
    clock = FixedClock(FIXED_NOW)
    lookup = PriceListLookup(
        factory,
        clock,
        tmp_path,
        filters={"nat_gateway_hour": NAT_FILTER},
    )
    lookup.get("ap-northeast-1", "nat_gateway_hour")

    cached = CachedPriceLookup(tmp_path, clock, ttl_hours=24)
    got = cached.get("ap-northeast-1", "nat_gateway_hour")
    assert got == PriceValue(usd=0.062, kind="cache", as_of="2026-10-15")


def test_cached_lookup_ttl_boundary(aws_env: None, tmp_path: Path) -> None:
    """AC-18: 保存時刻 +24h ちょうどは有効、+24h+1s は None。"""
    factory, _, stubber = _make_factory_and_stubber()
    stubber.add_response("get_products", {"PriceList": [_price_item("0.0620000000")]})
    lookup = PriceListLookup(
        factory,
        FixedClock(FIXED_NOW),
        tmp_path,
        filters={"nat_gateway_hour": NAT_FILTER},
    )
    lookup.get("ap-northeast-1", "nat_gateway_hour")

    at_boundary = CachedPriceLookup(
        tmp_path,
        FixedClock(FIXED_NOW + timedelta(hours=24)),
        ttl_hours=24,
    )
    assert at_boundary.get("ap-northeast-1", "nat_gateway_hour") == PriceValue(
        usd=0.062, kind="cache", as_of="2026-10-15"
    )

    past_boundary = CachedPriceLookup(
        tmp_path,
        FixedClock(FIXED_NOW + timedelta(hours=24, seconds=1)),
        ttl_hours=24,
    )
    assert past_boundary.get("ap-northeast-1", "nat_gateway_hour") is None


def test_cached_lookup_key_mismatch(aws_env: None, tmp_path: Path) -> None:
    """AC-19: region, key, sub のいずれかが異なれば None。"""
    factory, _, stubber = _make_factory_and_stubber()
    stubber.add_response("get_products", {"PriceList": [_price_item("0.0620000000")]})
    clock = FixedClock(FIXED_NOW)
    lookup = PriceListLookup(
        factory,
        clock,
        tmp_path,
        filters={"nat_gateway_hour": NAT_FILTER},
    )
    lookup.get("ap-northeast-1", "nat_gateway_hour")

    cached = CachedPriceLookup(tmp_path, clock, ttl_hours=24)
    assert cached.get("us-east-1", "nat_gateway_hour") is None
    assert cached.get("ap-northeast-1", "alb_hour") is None
    assert cached.get("ap-northeast-1", "nat_gateway_hour", "x") is None


def test_cached_lookup_missing_dir_and_broken_json(
    aws_env: None, tmp_path: Path
) -> None:
    """AC-20: cache_dir が存在しない場合・壊れた JSON の場合も None。"""
    clock = FixedClock(FIXED_NOW)
    missing = CachedPriceLookup(tmp_path / "does_not_exist", clock, 24)
    assert missing.get("ap-northeast-1", "nat_gateway_hour") is None

    factory, _, stubber = _make_factory_and_stubber()
    stubber.add_response("get_products", {"PriceList": [_price_item("0.0620000000")]})
    lookup = PriceListLookup(
        factory,
        clock,
        tmp_path,
        filters={"nat_gateway_hour": NAT_FILTER},
    )
    lookup.get("ap-northeast-1", "nat_gateway_hour")

    for p in tmp_path.glob("*.json"):
        p.write_text("{", encoding="utf-8")

    broken = CachedPriceLookup(tmp_path, clock, 24)
    assert broken.get("ap-northeast-1", "nat_gateway_hour") is None
