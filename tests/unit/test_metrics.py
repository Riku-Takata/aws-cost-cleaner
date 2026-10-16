"""Unit tests for MetricsFetcher (metrics.py)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from botocore.exceptions import ClientError  # type: ignore[import-not-found]

from aws_cost_cleaner.clock import FixedClock
from aws_cost_cleaner.config import Thresholds
from aws_cost_cleaner.metrics import MetricsFetcher
from aws_cost_cleaner.models import Resource, ResourceType

_FIXED_TIME = datetime(2026, 10, 17, 12, 0, 0, tzinfo=UTC)


class FakeCloudWatchClient:
    """Fake CloudWatch client recording calls and returning preset datapoints."""

    def __init__(
        self,
        datapoints_by_metric: dict[tuple[str, str], list[dict[str, Any]]] | None = None,
        error_on_call: Exception | None = None,
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self._datapoints = datapoints_by_metric or {}
        self._error = error_on_call

    def get_metric_statistics(self, **kwargs: Any) -> dict[str, Any]:
        if self._error is not None:
            raise self._error
        self.calls.append(kwargs)
        key = (kwargs.get("Namespace", ""), kwargs.get("MetricName", ""))
        return {"Datapoints": self._datapoints.get(key, [])}


class FakeClientFactory:
    """Fake ClientFactory recording client requests."""

    def __init__(self, client: FakeCloudWatchClient) -> None:
        self.client_calls: list[tuple[str, str]] = []
        self._client = client

    def client(self, service: str, region: str) -> Any:
        self.client_calls.append((service, region))
        return self._client


def _make_thresholds(
    period_days: int = 7,
    ec2_network_bytes_max: int = -1,
) -> Thresholds:
    return Thresholds(
        period_days=period_days,
        ec2_cpu_max_percent=5.0,
        ec2_network_bytes_max=ec2_network_bytes_max,
        rds_connections_max=0,
        nat_bytes_max=0,
        stop_wait_days=7,
    )


def _make_resource(
    resource_type: ResourceType,
    resource_id: str = "res-1",
    region: str = "ap-northeast-1",
) -> Resource:
    return Resource(
        resource_type=resource_type,
        region=region,
        resource_id=resource_id,
        arn=None,
        name=None,
        tags={},
        state="running",
        created_at=None,
        attrs={},
    )


def test_ac1_init_positional_and_keyword() -> None:
    client = FakeCloudWatchClient()
    factory = FakeClientFactory(client)
    thresholds = _make_thresholds()
    clock = FixedClock(_FIXED_TIME)

    # Positional arguments
    fetcher1 = MetricsFetcher(factory, thresholds, 10, clock)
    assert fetcher1.requests_used == 0
    assert isinstance(fetcher1.requests_used, int)

    # Keyword arguments
    fetcher2 = MetricsFetcher(
        factory=factory,
        thresholds=thresholds,
        max_requests=10,
        clock=clock,
    )
    assert fetcher2.requests_used == 0
    assert isinstance(fetcher2.requests_used, int)


def test_ac2_fetch_only_calls_cloudwatch_client_with_resource_region() -> None:
    client = FakeCloudWatchClient()
    factory = FakeClientFactory(client)
    fetcher = MetricsFetcher(factory, _make_thresholds(), 10, FixedClock(_FIXED_TIME))

    resource = _make_resource("ec2_instance", "i-1", region="us-west-2")
    fetcher.fetch(resource)

    assert factory.client_calls == [("cloudwatch", "us-west-2")]
    assert len(client.calls) == 1


def test_ac3_get_metric_statistics_period_end_start_time() -> None:
    client = FakeCloudWatchClient()
    factory = FakeClientFactory(client)

    # period_days = 7
    fetcher7 = MetricsFetcher(
        factory, _make_thresholds(period_days=7), 10, FixedClock(_FIXED_TIME)
    )
    fetcher7.fetch(_make_resource("ec2_instance", "i-1"))

    assert len(client.calls) == 1
    call7 = client.calls[0]
    assert call7["Period"] == 86400
    assert call7["EndTime"] == _FIXED_TIME
    assert call7["StartTime"] == _FIXED_TIME - timedelta(days=7)

    # period_days = 3
    client.calls.clear()
    fetcher3 = MetricsFetcher(
        factory, _make_thresholds(period_days=3), 10, FixedClock(_FIXED_TIME)
    )
    fetcher3.fetch(_make_resource("ec2_instance", "i-1"))

    assert len(client.calls) == 1
    call3 = client.calls[0]
    assert call3["Period"] == 86400
    assert call3["EndTime"] == _FIXED_TIME
    assert call3["StartTime"] == _FIXED_TIME - timedelta(days=3)


def test_ac4_ec2_without_network_metrics() -> None:
    client = FakeCloudWatchClient()
    factory = FakeClientFactory(client)
    fetcher = MetricsFetcher(
        factory, _make_thresholds(ec2_network_bytes_max=-1), 10, FixedClock(_FIXED_TIME)
    )

    result = fetcher.fetch(_make_resource("ec2_instance", "i-1"))

    assert len(client.calls) == 1
    assert client.calls[0] == {
        "Namespace": "AWS/EC2",
        "MetricName": "CPUUtilization",
        "Statistics": ["Maximum"],
        "Dimensions": [{"Name": "InstanceId", "Value": "i-1"}],
        "Period": 86400,
        "StartTime": _FIXED_TIME - timedelta(days=7),
        "EndTime": _FIXED_TIME,
    }
    assert result is not None
    assert set(result.keys()) == {"cpu_max_percent"}
    assert fetcher.requests_used == 1


def test_ac5_ec2_with_network_metrics_calls() -> None:
    client = FakeCloudWatchClient()
    factory = FakeClientFactory(client)
    fetcher = MetricsFetcher(
        factory, _make_thresholds(ec2_network_bytes_max=0), 10, FixedClock(_FIXED_TIME)
    )

    result = fetcher.fetch(_make_resource("ec2_instance", "i-1"))

    assert len(client.calls) == 3
    # Call 1: CPUUtilization
    assert client.calls[0] == {
        "Namespace": "AWS/EC2",
        "MetricName": "CPUUtilization",
        "Statistics": ["Maximum"],
        "Dimensions": [{"Name": "InstanceId", "Value": "i-1"}],
        "Period": 86400,
        "StartTime": _FIXED_TIME - timedelta(days=7),
        "EndTime": _FIXED_TIME,
    }
    # Call 2: NetworkIn
    assert client.calls[1] == {
        "Namespace": "AWS/EC2",
        "MetricName": "NetworkIn",
        "Statistics": ["Sum"],
        "Dimensions": [{"Name": "InstanceId", "Value": "i-1"}],
        "Period": 86400,
        "StartTime": _FIXED_TIME - timedelta(days=7),
        "EndTime": _FIXED_TIME,
    }
    # Call 3: NetworkOut
    assert client.calls[2] == {
        "Namespace": "AWS/EC2",
        "MetricName": "NetworkOut",
        "Statistics": ["Sum"],
        "Dimensions": [{"Name": "InstanceId", "Value": "i-1"}],
        "Period": 86400,
        "StartTime": _FIXED_TIME - timedelta(days=7),
        "EndTime": _FIXED_TIME,
    }
    assert result is not None
    assert set(result.keys()) == {"cpu_max_percent", "network_bytes_total"}
    assert fetcher.requests_used == 3


def test_ac6_ec2_network_bytes_total_calculation() -> None:
    datapoints = {
        ("AWS/EC2", "CPUUtilization"): [{"Maximum": 3.0}],
        ("AWS/EC2", "NetworkIn"): [{"Sum": 100.0}, {"Sum": 50.0}],
        ("AWS/EC2", "NetworkOut"): [{"Sum": 25.0}],
    }
    client = FakeCloudWatchClient(datapoints)
    factory = FakeClientFactory(client)
    fetcher = MetricsFetcher(
        factory, _make_thresholds(ec2_network_bytes_max=0), 10, FixedClock(_FIXED_TIME)
    )

    result = fetcher.fetch(_make_resource("ec2_instance", "i-1"))

    assert result is not None
    assert result["network_bytes_total"] == 175.0


def test_ac7_ec2_cpu_max_percent_calculation() -> None:
    datapoints = {
        ("AWS/EC2", "CPUUtilization"): [
            {"Maximum": 1.5},
            {"Maximum": 4.2},
            {"Maximum": 3.0},
        ],
    }
    client = FakeCloudWatchClient(datapoints)
    factory = FakeClientFactory(client)
    fetcher = MetricsFetcher(
        factory, _make_thresholds(ec2_network_bytes_max=-1), 10, FixedClock(_FIXED_TIME)
    )

    result = fetcher.fetch(_make_resource("ec2_instance", "i-1"))

    assert result is not None
    assert result["cpu_max_percent"] == 4.2


def test_ac8_rds_calls() -> None:
    client = FakeCloudWatchClient()
    factory = FakeClientFactory(client)
    fetcher = MetricsFetcher(factory, _make_thresholds(), 10, FixedClock(_FIXED_TIME))

    result = fetcher.fetch(_make_resource("rds_instance", "db-1"))

    assert len(client.calls) == 2
    assert client.calls[0] == {
        "Namespace": "AWS/RDS",
        "MetricName": "DatabaseConnections",
        "Statistics": ["Maximum"],
        "Dimensions": [{"Name": "DBInstanceIdentifier", "Value": "db-1"}],
        "Period": 86400,
        "StartTime": _FIXED_TIME - timedelta(days=7),
        "EndTime": _FIXED_TIME,
    }
    assert client.calls[1] == {
        "Namespace": "AWS/RDS",
        "MetricName": "CPUUtilization",
        "Statistics": ["Maximum"],
        "Dimensions": [{"Name": "DBInstanceIdentifier", "Value": "db-1"}],
        "Period": 86400,
        "StartTime": _FIXED_TIME - timedelta(days=7),
        "EndTime": _FIXED_TIME,
    }
    assert result is not None
    assert set(result.keys()) == {"db_connections_max", "rds_cpu_max_percent"}
    assert fetcher.requests_used == 2


def test_ac9_rds_values_calculation() -> None:
    datapoints = {
        ("AWS/RDS", "DatabaseConnections"): [
            {"Maximum": 0.0},
            {"Maximum": 3.0},
            {"Maximum": 2.0},
        ],
        ("AWS/RDS", "CPUUtilization"): [
            {"Maximum": 10.0},
            {"Maximum": 20.5},
        ],
    }
    client = FakeCloudWatchClient(datapoints)
    factory = FakeClientFactory(client)
    fetcher = MetricsFetcher(factory, _make_thresholds(), 10, FixedClock(_FIXED_TIME))

    result = fetcher.fetch(_make_resource("rds_instance", "db-1"))

    assert result is not None
    assert result["db_connections_max"] == 3.0
    assert result["rds_cpu_max_percent"] == 20.5


def test_ac10_nat_calls() -> None:
    client = FakeCloudWatchClient()
    factory = FakeClientFactory(client)
    fetcher = MetricsFetcher(factory, _make_thresholds(), 10, FixedClock(_FIXED_TIME))

    result = fetcher.fetch(_make_resource("nat_gateway", "nat-1"))

    assert len(client.calls) == 2
    assert client.calls[0] == {
        "Namespace": "AWS/NATGateway",
        "MetricName": "BytesInFromSource",
        "Statistics": ["Sum"],
        "Dimensions": [{"Name": "NatGatewayId", "Value": "nat-1"}],
        "Period": 86400,
        "StartTime": _FIXED_TIME - timedelta(days=7),
        "EndTime": _FIXED_TIME,
    }
    assert client.calls[1] == {
        "Namespace": "AWS/NATGateway",
        "MetricName": "BytesInFromDestination",
        "Statistics": ["Sum"],
        "Dimensions": [{"Name": "NatGatewayId", "Value": "nat-1"}],
        "Period": 86400,
        "StartTime": _FIXED_TIME - timedelta(days=7),
        "EndTime": _FIXED_TIME,
    }
    assert result is not None
    assert set(result.keys()) == {"nat_bytes_total"}
    assert fetcher.requests_used == 2


def test_ac11_nat_values_calculation() -> None:
    datapoints = {
        ("AWS/NATGateway", "BytesInFromSource"): [
            {"Sum": 10.0},
            {"Sum": 20.0},
        ],
        ("AWS/NATGateway", "BytesInFromDestination"): [
            {"Sum": 5.0},
        ],
    }
    client = FakeCloudWatchClient(datapoints)
    factory = FakeClientFactory(client)
    fetcher = MetricsFetcher(factory, _make_thresholds(), 10, FixedClock(_FIXED_TIME))

    result = fetcher.fetch(_make_resource("nat_gateway", "nat-1"))

    assert result is not None
    assert result["nat_bytes_total"] == 35.0


def test_ac12_empty_datapoints_yield_none() -> None:
    client = FakeCloudWatchClient()  # all empty
    factory = FakeClientFactory(client)
    fetcher = MetricsFetcher(
        factory, _make_thresholds(ec2_network_bytes_max=-1), 10, FixedClock(_FIXED_TIME)
    )

    ec2_result = fetcher.fetch(_make_resource("ec2_instance", "i-1"))
    assert ec2_result is not None
    assert ec2_result["cpu_max_percent"] is None

    rds_result = fetcher.fetch(_make_resource("rds_instance", "db-1"))
    assert rds_result is not None
    assert rds_result["db_connections_max"] is None
    assert rds_result["rds_cpu_max_percent"] is None


def test_ac13_sum_metrics_with_one_empty_yield_none() -> None:
    # EC2: NetworkIn has data, NetworkOut is empty
    datapoints_ec2 = {
        ("AWS/EC2", "CPUUtilization"): [{"Maximum": 2.0}],
        ("AWS/EC2", "NetworkIn"): [{"Sum": 100.0}],
        ("AWS/EC2", "NetworkOut"): [],
    }
    client_ec2 = FakeCloudWatchClient(datapoints_ec2)
    factory_ec2 = FakeClientFactory(client_ec2)
    fetcher_ec2 = MetricsFetcher(
        factory_ec2,
        _make_thresholds(ec2_network_bytes_max=0),
        10,
        FixedClock(_FIXED_TIME),
    )
    result_ec2 = fetcher_ec2.fetch(_make_resource("ec2_instance", "i-1"))
    assert result_ec2 is not None
    assert result_ec2["network_bytes_total"] is None

    # NAT: BytesInFromSource is empty, BytesInFromDestination has data
    datapoints_nat = {
        ("AWS/NATGateway", "BytesInFromSource"): [],
        ("AWS/NATGateway", "BytesInFromDestination"): [{"Sum": 50.0}],
    }
    client_nat = FakeCloudWatchClient(datapoints_nat)
    factory_nat = FakeClientFactory(client_nat)
    fetcher_nat = MetricsFetcher(
        factory_nat, _make_thresholds(), 10, FixedClock(_FIXED_TIME)
    )
    result_nat = fetcher_nat.fetch(_make_resource("nat_gateway", "nat-1"))
    assert result_nat is not None
    assert result_nat["nat_bytes_total"] is None


def test_ac14_non_none_values_are_float() -> None:
    datapoints = {
        ("AWS/EC2", "CPUUtilization"): [{"Maximum": 5}],  # int
        ("AWS/EC2", "NetworkIn"): [{"Sum": 10}],  # int
        ("AWS/EC2", "NetworkOut"): [{"Sum": 20}],  # int
    }
    client = FakeCloudWatchClient(datapoints)
    factory = FakeClientFactory(client)
    fetcher = MetricsFetcher(
        factory, _make_thresholds(ec2_network_bytes_max=0), 10, FixedClock(_FIXED_TIME)
    )

    result = fetcher.fetch(_make_resource("ec2_instance", "i-1"))
    assert result is not None
    assert isinstance(result["cpu_max_percent"], float)
    assert result["cpu_max_percent"] == 5.0
    assert isinstance(result["network_bytes_total"], float)
    assert result["network_bytes_total"] == 30.0


def test_ac15_other_resource_types_return_empty_dict() -> None:
    client = FakeCloudWatchClient()
    factory = FakeClientFactory(client)
    fetcher = MetricsFetcher(factory, _make_thresholds(), 10, FixedClock(_FIXED_TIME))

    for res_type in ("ebs_volume", "elastic_ip", "load_balancer"):
        result = fetcher.fetch(_make_resource(res_type))  # type: ignore[arg-type]
        assert result == {}
        assert len(factory.client_calls) == 0
        assert len(client.calls) == 0
        assert fetcher.requests_used == 0


def test_ac16_max_requests_budget_exhaustion() -> None:
    client = FakeCloudWatchClient()
    factory = FakeClientFactory(client)
    fetcher = MetricsFetcher(
        factory,
        _make_thresholds(ec2_network_bytes_max=-1),
        max_requests=1,
        clock=FixedClock(_FIXED_TIME),
    )

    r1 = fetcher.fetch(_make_resource("ec2_instance", "i-1"))
    assert r1 is not None
    assert set(r1.keys()) == {"cpu_max_percent"}
    assert fetcher.requests_used == 1

    r2 = fetcher.fetch(_make_resource("ec2_instance", "i-2"))
    assert r2 is None
    assert len(client.calls) == 1
    assert fetcher.requests_used == 1


def test_ac17_budget_exceeded_before_calling() -> None:
    client = FakeCloudWatchClient()
    factory = FakeClientFactory(client)

    # max_requests=2 with EC2 requiring 3 requests
    fetcher_ec2 = MetricsFetcher(
        factory,
        _make_thresholds(ec2_network_bytes_max=0),
        max_requests=2,
        clock=FixedClock(_FIXED_TIME),
    )
    r_ec2 = fetcher_ec2.fetch(_make_resource("ec2_instance", "i-1"))
    assert r_ec2 is None
    assert len(client.calls) == 0
    assert fetcher_ec2.requests_used == 0

    # max_requests=2 with RDS requiring 2 requests
    fetcher_rds = MetricsFetcher(
        factory,
        _make_thresholds(),
        max_requests=2,
        clock=FixedClock(_FIXED_TIME),
    )
    r_rds = fetcher_rds.fetch(_make_resource("rds_instance", "db-1"))
    assert r_rds is not None
    assert len(client.calls) == 2
    assert fetcher_rds.requests_used == 2


def test_ac18_max_requests_zero() -> None:
    client = FakeCloudWatchClient()
    factory = FakeClientFactory(client)
    fetcher = MetricsFetcher(
        factory, _make_thresholds(), max_requests=0, clock=FixedClock(_FIXED_TIME)
    )

    for res in (
        _make_resource("ec2_instance", "i-1"),
        _make_resource("rds_instance", "db-1"),
        _make_resource("nat_gateway", "nat-1"),
    ):
        result = fetcher.fetch(res)
        assert result is None

    assert len(client.calls) == 0
    assert fetcher.requests_used == 0


def test_ac19_exhausted_persists_for_all_subsequent_resources() -> None:
    client = FakeCloudWatchClient()
    factory = FakeClientFactory(client)

    # Case 1: max_requests=2, ec2_network_bytes_max=-1: RDS -> RDS -> EC2
    fetcher1 = MetricsFetcher(
        factory,
        _make_thresholds(ec2_network_bytes_max=-1),
        max_requests=2,
        clock=FixedClock(_FIXED_TIME),
    )
    r1_1 = fetcher1.fetch(_make_resource("rds_instance", "db-1"))
    assert r1_1 is not None
    r1_2 = fetcher1.fetch(_make_resource("rds_instance", "db-2"))
    assert r1_2 is None
    r1_3 = fetcher1.fetch(_make_resource("ec2_instance", "i-1"))
    assert r1_3 is None
    assert len(client.calls) == 2
    assert fetcher1.requests_used == 2

    # Case 2: max_requests=3, ec2_network_bytes_max=-1
    # RDS(2) -> NAT(2, exceeds) -> EC2(1)
    client.calls.clear()
    fetcher2 = MetricsFetcher(
        factory,
        _make_thresholds(ec2_network_bytes_max=-1),
        max_requests=3,
        clock=FixedClock(_FIXED_TIME),
    )
    r2_1 = fetcher2.fetch(_make_resource("rds_instance", "db-1"))
    assert r2_1 is not None
    r2_2 = fetcher2.fetch(_make_resource("nat_gateway", "nat-1"))
    assert r2_2 is None
    r2_3 = fetcher2.fetch(_make_resource("ec2_instance", "i-1"))
    assert r2_3 is None
    assert len(client.calls) == 2
    assert fetcher2.requests_used == 2

    # EBS after exhausted returns None
    r2_ebs = fetcher2.fetch(_make_resource("ebs_volume", "vol-1"))
    assert r2_ebs is None


def test_ac20_client_error_propagates() -> None:
    err = ClientError(
        {"Error": {"Code": "ResourceNotFoundException", "Message": "Not found"}},
        "GetMetricStatistics",
    )
    client = FakeCloudWatchClient(error_on_call=err)
    factory = FakeClientFactory(client)
    fetcher = MetricsFetcher(factory, _make_thresholds(), 10, FixedClock(_FIXED_TIME))

    with pytest.raises(ClientError) as exc_info:
        fetcher.fetch(_make_resource("ec2_instance", "i-1"))

    assert exc_info.value is err
    assert (
        fetcher.requests_used == 0
    )  # failed inside get_metric_statistics before incrementing
