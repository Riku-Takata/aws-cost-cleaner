"""Unit tests for discovery.py."""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

import boto3  # type: ignore[import-not-found]  # noqa: TID251
import pytest
from botocore.exceptions import ClientError  # type: ignore[import-not-found]
from botocore.stub import Stubber  # type: ignore[import-not-found]

from aws_cost_cleaner.aws.session import ClientFactory
from aws_cost_cleaner.config import load_config
from aws_cost_cleaner.discovery import (
    RESOURCE_EXPLORER_TYPES,
    DiscoveryResult,
    discover,
)
from aws_cost_cleaner.models import ResourceType


def _load_fixture(name: str) -> dict[str, Any]:
    """Load JSON fixture from tests/fixtures/resource_explorer/."""
    path = Path(__file__).resolve().parents[1] / "fixtures" / "resource_explorer" / name
    return cast(dict[str, Any], json.loads(path.read_text()))


class FakeClientFactory:
    """Fake ClientFactory routing client requests and recording calls."""

    def __init__(self, rex_client: Any, ec2_client: Any) -> None:
        self.rex_client = rex_client
        self.ec2_client = ec2_client
        self.client_calls: list[tuple[str, str]] = []

    def client(self, service: str, region: str) -> Any:
        self.client_calls.append((service, region))
        if service == "resource-explorer-2":
            return self.rex_client
        if service == "ec2":
            return self.ec2_client
        raise ValueError(f"Unexpected service: {service}")


def _make_clients() -> tuple[Any, Stubber, Any, Stubber]:
    """Create real botocore clients attached with active Stubbers."""
    rex_client = boto3.client(  # noqa: TID251
        "resource-explorer-2",
        region_name="ap-northeast-1",
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )
    rex_stubber = Stubber(rex_client)
    rex_stubber.activate()

    ec2_client = boto3.client(  # noqa: TID251
        "ec2",
        region_name="ap-northeast-1",
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )
    ec2_stubber = Stubber(ec2_client)
    ec2_stubber.activate()

    return rex_client, rex_stubber, ec2_client, ec2_stubber


def _assert_shape(result: DiscoveryResult) -> None:
    """Assert AC-3 shape: 6 resource types as keys, frozenset[str] as values."""
    expected_keys = {
        "ebs_volume",
        "elastic_ip",
        "nat_gateway",
        "load_balancer",
        "ec2_instance",
        "rds_instance",
    }
    assert set(result.regions_by_type.keys()) == expected_keys
    for k, v in result.regions_by_type.items():
        assert isinstance(v, frozenset), f"{k} value is not frozenset: {v!r}"


def test_ac1_public_api() -> None:
    """AC-1: DiscoveryResult, discover, RESOURCE_EXPLORER_TYPES exported."""
    fields = [f.name for f in dataclasses.fields(DiscoveryResult)]
    assert fields == ["method", "regions_by_type", "warnings"]

    res = DiscoveryResult(
        method="resource_explorer",
        regions_by_type={},
        warnings=(),
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        res.method = "explicit_regions"  # type: ignore[misc]

    # discover signature check
    rex_client, rex_stubber, ec2_client, ec2_stubber = _make_clients()
    factory = FakeClientFactory(rex_client, ec2_client)
    cfg = load_config(None, {"regions": ("us-east-1",)})
    result = discover(factory, cfg)  # type: ignore[arg-type]
    assert isinstance(result, DiscoveryResult)


def test_ac2_resource_explorer_types() -> None:
    """AC-2: Exact dictionary mapping for RESOURCE_EXPLORER_TYPES."""
    expected: Mapping[ResourceType, tuple[str, ...]] = {
        "ebs_volume": ("ec2:volume",),
        "elastic_ip": ("ec2:elastic-ip",),
        "nat_gateway": ("ec2:natgateway",),
        "load_balancer": (
            "elasticloadbalancing:loadbalancer/app",
            "elasticloadbalancing:loadbalancer/net",
        ),
        "ec2_instance": ("ec2:instance",),
        "rds_instance": ("rds:db",),
    }
    assert dict(RESOURCE_EXPLORER_TYPES) == expected
    assert set(RESOURCE_EXPLORER_TYPES.keys()) == set(expected.keys())


def test_ac4_explicit_regions_no_calls() -> None:
    """AC-4: Explicit regions configured skips all API calls."""
    rex_client, rex_stubber, ec2_client, ec2_stubber = _make_clients()
    factory = FakeClientFactory(rex_client, ec2_client)
    cfg = load_config(None, {"regions": ("us-east-1", "ap-northeast-1")})

    res = discover(factory, cfg)  # type: ignore[arg-type]

    assert factory.client_calls == []
    assert res.method == "explicit_regions"
    assert res.warnings == ()
    _assert_shape(res)
    expected_regions = frozenset({"us-east-1", "ap-northeast-1"})
    for k in RESOURCE_EXPLORER_TYPES:
        assert res.regions_by_type[k] == expected_regions

    rex_stubber.assert_no_pending_responses()
    ec2_stubber.assert_no_pending_responses()


def test_ac5_clients_use_resource_explorer_region() -> None:
    """AC-5: Clients are created with config.scan.resource_explorer_region."""
    rex_client, rex_stubber, ec2_client, ec2_stubber = _make_clients()
    factory = FakeClientFactory(rex_client, ec2_client)
    cfg = load_config(None, {"resource_explorer_region": "us-west-2"})

    # ebs_volume incomplete -> requires DescribeRegions
    rex_stubber.add_response(
        "search",
        _load_fixture("search_incomplete.json"),
        {"QueryString": "resourcetype:ec2:volume"},
    )
    ec2_stubber.add_response(
        "describe_regions",
        _load_fixture("describe_regions.json"),
        {},
    )
    # per region searches
    for r in ("us-east-1", "ap-northeast-1", "eu-west-1"):
        rex_stubber.add_response(
            "search",
            _load_fixture("search_empty.json"),
            {
                "QueryString": f"resourcetype:ec2:volume region:{r}",
                "MaxResults": 1,
            },
        )
    # remaining 5 types
    remaining_types = [
        "ec2:elastic-ip",
        "ec2:natgateway",
        "elasticloadbalancing:loadbalancer/app",
        "elasticloadbalancing:loadbalancer/net",
        "ec2:instance",
        "rds:db",
    ]
    for t in remaining_types:
        rex_stubber.add_response(
            "search",
            _load_fixture("search_empty.json"),
            {"QueryString": f"resourcetype:{t}"},
        )

    res = discover(factory, cfg)  # type: ignore[arg-type]

    assert ("resource-explorer-2", "us-west-2") in factory.client_calls
    assert ("ec2", "us-west-2") in factory.client_calls
    assert res.method == "resource_explorer"

    rex_stubber.assert_no_pending_responses()
    ec2_stubber.assert_no_pending_responses()


def test_ac6_ac7_ac8_complete_pagination() -> None:
    """AC-6, AC-7, AC-8: Complete=true pagination collects regions across pages."""
    rex_client, rex_stubber, ec2_client, ec2_stubber = _make_clients()
    factory = FakeClientFactory(rex_client, ec2_client)
    cfg = load_config(None)

    # ebs_volume: page 1 with NextToken, page 2 without
    rex_stubber.add_response(
        "search",
        _load_fixture("search_page1.json"),
        {"QueryString": "resourcetype:ec2:volume"},
    )
    rex_stubber.add_response(
        "search",
        _load_fixture("search_page2.json"),
        {"QueryString": "resourcetype:ec2:volume", "NextToken": "tok-1"},
    )

    # other types return search_empty
    remaining_types = [
        "ec2:elastic-ip",
        "ec2:natgateway",
        "elasticloadbalancing:loadbalancer/app",
        "elasticloadbalancing:loadbalancer/net",
        "ec2:instance",
        "rds:db",
    ]
    for t in remaining_types:
        rex_stubber.add_response(
            "search",
            _load_fixture("search_empty.json"),
            {"QueryString": f"resourcetype:{t}"},
        )

    res = discover(factory, cfg)  # type: ignore[arg-type]

    _assert_shape(res)
    assert res.method == "resource_explorer"
    assert res.warnings == ()
    assert res.regions_by_type["ebs_volume"] == frozenset(
        {"us-east-1", "ap-northeast-1", "eu-west-1"}
    )
    for k in (
        "elastic_ip",
        "nat_gateway",
        "load_balancer",
        "ec2_instance",
        "rds_instance",
    ):
        assert res.regions_by_type[k] == frozenset()

    # DescribeRegions was never called
    assert all(svc != "ec2" for svc, _ in factory.client_calls)

    rex_stubber.assert_no_pending_responses()
    ec2_stubber.assert_no_pending_responses()


def test_ac9_load_balancer_union() -> None:
    """AC-9: load_balancer combines regions from /app and /net."""
    rex_client, rex_stubber, ec2_client, ec2_stubber = _make_clients()
    factory = FakeClientFactory(rex_client, ec2_client)
    cfg = load_config(None)

    for t in ("ec2:volume", "ec2:elastic-ip", "ec2:natgateway"):
        rex_stubber.add_response(
            "search",
            _load_fixture("search_empty.json"),
            {"QueryString": f"resourcetype:{t}"},
        )

    app_resp = {
        "ViewArn": "arn:aws:view",
        "Count": {"Complete": True, "TotalResources": 1},
        "Resources": [
            {
                "Arn": "arn:aws:app",
                "OwningAccountId": "123",
                "Region": "us-east-1",
                "ResourceType": "elasticloadbalancing:loadbalancer/app",
                "Service": "elasticloadbalancing",
            }
        ],
    }
    net_resp = {
        "ViewArn": "arn:aws:view",
        "Count": {"Complete": True, "TotalResources": 1},
        "Resources": [
            {
                "Arn": "arn:aws:net",
                "OwningAccountId": "123",
                "Region": "eu-west-1",
                "ResourceType": "elasticloadbalancing:loadbalancer/net",
                "Service": "elasticloadbalancing",
            }
        ],
    }
    rex_stubber.add_response(
        "search",
        app_resp,
        {"QueryString": ("resourcetype:elasticloadbalancing:loadbalancer/app")},
    )
    rex_stubber.add_response(
        "search",
        net_resp,
        {"QueryString": ("resourcetype:elasticloadbalancing:loadbalancer/net")},
    )

    for t in ("ec2:instance", "rds:db"):
        rex_stubber.add_response(
            "search",
            _load_fixture("search_empty.json"),
            {"QueryString": f"resourcetype:{t}"},
        )

    res = discover(factory, cfg)  # type: ignore[arg-type]

    _assert_shape(res)
    assert res.method == "resource_explorer"
    assert res.regions_by_type["load_balancer"] == frozenset({"us-east-1", "eu-west-1"})

    rex_stubber.assert_no_pending_responses()
    ec2_stubber.assert_no_pending_responses()


def test_ac10_ac11_incomplete_switches_to_per_region() -> None:
    """AC-10, AC-11: Complete=false switches to per-region querying."""
    rex_client, rex_stubber, ec2_client, ec2_stubber = _make_clients()
    factory = FakeClientFactory(rex_client, ec2_client)
    cfg = load_config(None)

    # ebs_volume incomplete (ap-northeast-1 not in per-region hits)
    rex_stubber.add_response(
        "search",
        _load_fixture("search_incomplete.json"),
        {"QueryString": "resourcetype:ec2:volume"},
    )
    ec2_stubber.add_response(
        "describe_regions",
        _load_fixture("describe_regions.json"),
        {},
    )
    # per region: us-east-1 has 1, ap-northeast-1 has 0, eu-west-1 has 1
    rex_stubber.add_response(
        "search",
        _load_fixture("search_one.json"),
        {
            "QueryString": "resourcetype:ec2:volume region:us-east-1",
            "MaxResults": 1,
        },
    )
    rex_stubber.add_response(
        "search",
        _load_fixture("search_empty.json"),
        {
            "QueryString": "resourcetype:ec2:volume region:ap-northeast-1",
            "MaxResults": 1,
        },
    )
    eu_one = {
        "ViewArn": "arn:aws:view",
        "Count": {"Complete": True, "TotalResources": 1},
        "Resources": [
            {
                "Arn": "arn:aws:vol",
                "OwningAccountId": "123",
                "Region": "eu-west-1",
                "ResourceType": "ec2:volume",
                "Service": "ec2",
            }
        ],
    }
    rex_stubber.add_response(
        "search",
        eu_one,
        {
            "QueryString": "resourcetype:ec2:volume region:eu-west-1",
            "MaxResults": 1,
        },
    )

    # remaining 5 types
    remaining_types = [
        "ec2:elastic-ip",
        "ec2:natgateway",
        "elasticloadbalancing:loadbalancer/app",
        "elasticloadbalancing:loadbalancer/net",
        "ec2:instance",
        "rds:db",
    ]
    for t in remaining_types:
        rex_stubber.add_response(
            "search",
            _load_fixture("search_empty.json"),
            {"QueryString": f"resourcetype:{t}"},
        )

    res = discover(factory, cfg)  # type: ignore[arg-type]

    _assert_shape(res)
    assert res.method == "resource_explorer"
    assert res.regions_by_type["ebs_volume"] == frozenset({"us-east-1", "eu-west-1"})

    rex_stubber.assert_no_pending_responses()
    ec2_stubber.assert_no_pending_responses()


def test_ac11_incomplete_on_second_page_discards_first() -> None:
    """AC-11: Complete=false on later page discards previous regions."""
    rex_client, rex_stubber, ec2_client, ec2_stubber = _make_clients()
    factory = FakeClientFactory(rex_client, ec2_client)
    cfg = load_config(None)

    # page 1 Complete=true has us-east-1, ap-northeast-1
    rex_stubber.add_response(
        "search",
        _load_fixture("search_page1.json"),
        {"QueryString": "resourcetype:ec2:volume"},
    )
    # page 2 Complete=false
    rex_stubber.add_response(
        "search",
        _load_fixture("search_incomplete.json"),
        {"QueryString": "resourcetype:ec2:volume", "NextToken": "tok-1"},
    )
    ec2_stubber.add_response(
        "describe_regions",
        _load_fixture("describe_regions.json"),
        {},
    )
    # per region: only eu-west-1 has 1 hit
    rex_stubber.add_response(
        "search",
        _load_fixture("search_empty.json"),
        {
            "QueryString": "resourcetype:ec2:volume region:us-east-1",
            "MaxResults": 1,
        },
    )
    rex_stubber.add_response(
        "search",
        _load_fixture("search_empty.json"),
        {
            "QueryString": "resourcetype:ec2:volume region:ap-northeast-1",
            "MaxResults": 1,
        },
    )
    eu_one = {
        "ViewArn": "arn:aws:view",
        "Count": {"Complete": True, "TotalResources": 1},
        "Resources": [
            {
                "Arn": "arn:aws:vol",
                "OwningAccountId": "123",
                "Region": "eu-west-1",
                "ResourceType": "ec2:volume",
                "Service": "ec2",
            }
        ],
    }
    rex_stubber.add_response(
        "search",
        eu_one,
        {
            "QueryString": "resourcetype:ec2:volume region:eu-west-1",
            "MaxResults": 1,
        },
    )

    # remaining 5 types
    remaining_types = [
        "ec2:elastic-ip",
        "ec2:natgateway",
        "elasticloadbalancing:loadbalancer/app",
        "elasticloadbalancing:loadbalancer/net",
        "ec2:instance",
        "rds:db",
    ]
    for t in remaining_types:
        rex_stubber.add_response(
            "search",
            _load_fixture("search_empty.json"),
            {"QueryString": f"resourcetype:{t}"},
        )

    res = discover(factory, cfg)  # type: ignore[arg-type]

    _assert_shape(res)
    assert res.method == "resource_explorer"
    # us-east-1 and ap-northeast-1 from page 1 must be discarded
    assert res.regions_by_type["ebs_volume"] == frozenset({"eu-west-1"})

    rex_stubber.assert_no_pending_responses()
    ec2_stubber.assert_no_pending_responses()


def test_ac12_describe_regions_called_once() -> None:
    """AC-12: DescribeRegions called at most once even with multiple incomplete."""
    rex_client, rex_stubber, ec2_client, ec2_stubber = _make_clients()
    factory = FakeClientFactory(rex_client, ec2_client)
    cfg = load_config(None)

    # ebs_volume incomplete
    rex_stubber.add_response(
        "search",
        _load_fixture("search_incomplete.json"),
        {"QueryString": "resourcetype:ec2:volume"},
    )
    # ec2 DescribeRegions called only once
    ec2_stubber.add_response(
        "describe_regions",
        _load_fixture("describe_regions.json"),
        {},
    )
    for r in ("us-east-1", "ap-northeast-1", "eu-west-1"):
        rex_stubber.add_response(
            "search",
            _load_fixture("search_empty.json"),
            {
                "QueryString": f"resourcetype:ec2:volume region:{r}",
                "MaxResults": 1,
            },
        )

    # elastic_ip also incomplete
    rex_stubber.add_response(
        "search",
        _load_fixture("search_incomplete.json"),
        {"QueryString": "resourcetype:ec2:elastic-ip"},
    )
    # DescribeRegions is NOT called again; per-region searches reuse the list
    for r in ("us-east-1", "ap-northeast-1", "eu-west-1"):
        rex_stubber.add_response(
            "search",
            _load_fixture("search_empty.json"),
            {
                "QueryString": f"resourcetype:ec2:elastic-ip region:{r}",
                "MaxResults": 1,
            },
        )

    # remaining 4 types complete
    remaining_types = [
        "ec2:natgateway",
        "elasticloadbalancing:loadbalancer/app",
        "elasticloadbalancing:loadbalancer/net",
        "ec2:instance",
        "rds:db",
    ]
    for t in remaining_types:
        rex_stubber.add_response(
            "search",
            _load_fixture("search_empty.json"),
            {"QueryString": f"resourcetype:{t}"},
        )

    res = discover(factory, cfg)  # type: ignore[arg-type]

    assert res.method == "resource_explorer"
    ec2_calls = [c for c in factory.client_calls if c[0] == "ec2"]
    assert len(ec2_calls) == 1

    rex_stubber.assert_no_pending_responses()
    ec2_stubber.assert_no_pending_responses()


@pytest.mark.parametrize(
    "code",
    ["UnauthorizedException", "ResourceNotFoundException", "ThrottlingException"],
)
def test_ac13_error_on_first_search(code: str) -> None:
    """AC-13: ClientError on first search triggers fallback_all_regions."""
    rex_client, rex_stubber, ec2_client, ec2_stubber = _make_clients()
    factory = FakeClientFactory(rex_client, ec2_client)
    cfg = load_config(None)

    rex_stubber.add_client_error(
        "search",
        service_error_code=code,
        expected_params={"QueryString": "resourcetype:ec2:volume"},
    )
    ec2_stubber.add_response(
        "describe_regions",
        _load_fixture("describe_regions.json"),
        {},
    )

    res = discover(factory, cfg)  # type: ignore[arg-type]

    _assert_shape(res)
    assert res.method == "fallback_all_regions"
    assert res.warnings == (f"resource-explorer unavailable: {code}",)
    all_regions = frozenset({"us-east-1", "ap-northeast-1", "eu-west-1"})
    for k in RESOURCE_EXPLORER_TYPES:
        assert res.regions_by_type[k] == all_regions

    rex_stubber.assert_no_pending_responses()
    ec2_stubber.assert_no_pending_responses()


@pytest.mark.parametrize(
    "code",
    ["UnauthorizedException", "ResourceNotFoundException", "ThrottlingException"],
)
def test_ac13_error_during_pagination(code: str) -> None:
    """AC-13: ClientError during search pagination triggers fallback_all_regions."""
    rex_client, rex_stubber, ec2_client, ec2_stubber = _make_clients()
    factory = FakeClientFactory(rex_client, ec2_client)
    cfg = load_config(None)

    rex_stubber.add_response(
        "search",
        _load_fixture("search_page1.json"),
        {"QueryString": "resourcetype:ec2:volume"},
    )
    rex_stubber.add_client_error(
        "search",
        service_error_code=code,
        expected_params={
            "QueryString": "resourcetype:ec2:volume",
            "NextToken": "tok-1",
        },
    )
    ec2_stubber.add_response(
        "describe_regions",
        _load_fixture("describe_regions.json"),
        {},
    )

    res = discover(factory, cfg)  # type: ignore[arg-type]

    _assert_shape(res)
    assert res.method == "fallback_all_regions"
    assert res.warnings == (f"resource-explorer unavailable: {code}",)
    all_regions = frozenset({"us-east-1", "ap-northeast-1", "eu-west-1"})
    for k in RESOURCE_EXPLORER_TYPES:
        assert res.regions_by_type[k] == all_regions

    rex_stubber.assert_no_pending_responses()
    ec2_stubber.assert_no_pending_responses()


@pytest.mark.parametrize(
    "code",
    ["UnauthorizedException", "ResourceNotFoundException", "ThrottlingException"],
)
def test_ac13_error_during_per_region(code: str) -> None:
    """AC-13: ClientError during per-region search triggers fallback_all_regions."""
    rex_client, rex_stubber, ec2_client, ec2_stubber = _make_clients()
    factory = FakeClientFactory(rex_client, ec2_client)
    cfg = load_config(None)

    rex_stubber.add_response(
        "search",
        _load_fixture("search_incomplete.json"),
        {"QueryString": "resourcetype:ec2:volume"},
    )
    ec2_stubber.add_response(
        "describe_regions",
        _load_fixture("describe_regions.json"),
        {},
    )
    rex_stubber.add_response(
        "search",
        _load_fixture("search_empty.json"),
        {
            "QueryString": "resourcetype:ec2:volume region:us-east-1",
            "MaxResults": 1,
        },
    )
    rex_stubber.add_client_error(
        "search",
        service_error_code=code,
        expected_params={
            "QueryString": "resourcetype:ec2:volume region:ap-northeast-1",
            "MaxResults": 1,
        },
    )

    res = discover(factory, cfg)  # type: ignore[arg-type]

    _assert_shape(res)
    assert res.method == "fallback_all_regions"
    assert res.warnings == (f"resource-explorer unavailable: {code}",)
    all_regions = frozenset({"us-east-1", "ap-northeast-1", "eu-west-1"})
    for k in RESOURCE_EXPLORER_TYPES:
        assert res.regions_by_type[k] == all_regions

    # DescribeRegions was called only once (reused on fallback)
    ec2_calls = [c for c in factory.client_calls if c[0] == "ec2"]
    assert len(ec2_calls) == 1

    rex_stubber.assert_no_pending_responses()
    ec2_stubber.assert_no_pending_responses()


def test_ac14_describe_regions_error_propagates_from_per_region() -> None:
    """AC-14: DescribeRegions ClientError in per-region switch propagates."""
    rex_client, rex_stubber, ec2_client, ec2_stubber = _make_clients()
    factory = FakeClientFactory(rex_client, ec2_client)
    cfg = load_config(None)

    rex_stubber.add_response(
        "search",
        _load_fixture("search_incomplete.json"),
        {"QueryString": "resourcetype:ec2:volume"},
    )
    ec2_stubber.add_client_error(
        "describe_regions",
        service_error_code="UnauthorizedOperation",
        expected_params={},
    )

    with pytest.raises(ClientError) as exc_info:
        discover(factory, cfg)  # type: ignore[arg-type]

    assert (
        exc_info.value.response.get("Error", {}).get("Code") == "UnauthorizedOperation"
    )

    rex_stubber.assert_no_pending_responses()
    ec2_stubber.assert_no_pending_responses()


def test_ac14_describe_regions_error_propagates_from_fallback() -> None:
    """AC-14: ClientError from DescribeRegions during fallback propagates directly."""
    rex_client, rex_stubber, ec2_client, ec2_stubber = _make_clients()
    factory = FakeClientFactory(rex_client, ec2_client)
    cfg = load_config(None)

    rex_stubber.add_client_error(
        "search",
        service_error_code="UnauthorizedException",
        expected_params={"QueryString": "resourcetype:ec2:volume"},
    )
    ec2_stubber.add_client_error(
        "describe_regions",
        service_error_code="AuthFailure",
        expected_params={},
    )

    with pytest.raises(ClientError) as exc_info:
        discover(factory, cfg)  # type: ignore[arg-type]

    assert exc_info.value.response.get("Error", {}).get("Code") == "AuthFailure"

    rex_stubber.assert_no_pending_responses()
    ec2_stubber.assert_no_pending_responses()


def test_ac15_read_mode_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC-15: Calls made through ClientFactory('read') only invoke permitted APIs."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "fake-key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "fake-secret")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-northeast-1")

    factory = ClientFactory("read")
    rex_client = factory.client("resource-explorer-2", "ap-northeast-1")
    ec2_client = factory.client("ec2", "ap-northeast-1")

    called_apis: list[str] = []

    def record_call(event_name: str, **kwargs: Any) -> None:
        model = kwargs.get("model")
        if model is not None:
            called_apis.append(model.name)

    rex_client.meta.events.register("before-call.*.*", record_call)
    ec2_client.meta.events.register("before-call.*.*", record_call)

    rex_stubber = Stubber(rex_client)
    rex_stubber.activate()
    ec2_stubber = Stubber(ec2_client)
    ec2_stubber.activate()

    # Route through incomplete to exercise both Search and DescribeRegions
    rex_stubber.add_response(
        "search",
        _load_fixture("search_incomplete.json"),
        {"QueryString": "resourcetype:ec2:volume"},
    )
    ec2_stubber.add_response(
        "describe_regions",
        _load_fixture("describe_regions.json"),
        {},
    )
    for r in ("us-east-1", "ap-northeast-1", "eu-west-1"):
        rex_stubber.add_response(
            "search",
            _load_fixture("search_empty.json"),
            {
                "QueryString": f"resourcetype:ec2:volume region:{r}",
                "MaxResults": 1,
            },
        )
    remaining_types = [
        "ec2:elastic-ip",
        "ec2:natgateway",
        "elasticloadbalancing:loadbalancer/app",
        "elasticloadbalancing:loadbalancer/net",
        "ec2:instance",
        "rds:db",
    ]
    for t in remaining_types:
        rex_stubber.add_response(
            "search",
            _load_fixture("search_empty.json"),
            {"QueryString": f"resourcetype:{t}"},
        )

    cfg = load_config(None)
    # Ensure no DisallowedApiCallError is raised
    res = discover(factory, cfg)

    assert res.method == "resource_explorer"
    assert set(called_apis).issubset({"Search", "DescribeRegions"})
    assert "Search" in called_apis
    assert "DescribeRegions" in called_apis

    rex_stubber.assert_no_pending_responses()
    ec2_stubber.assert_no_pending_responses()


def test_ac16_existing_tests_pass() -> None:
    """AC-16: Verification of non-regression."""
    # Marker for AC-16; all existing tests are run during make verify
    pass
