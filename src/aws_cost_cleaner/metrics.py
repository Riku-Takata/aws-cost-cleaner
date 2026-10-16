"""CloudWatch metrics fetching with a request budget."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any

from aws_cost_cleaner.aws.session import ClientFactory
from aws_cost_cleaner.clock import Clock
from aws_cost_cleaner.config import Thresholds
from aws_cost_cleaner.models import Resource

_PERIOD_SECONDS = 86400
_METRIC_TYPES = ("ec2_instance", "rds_instance", "nat_gateway")


def _max_or_none(values: list[float]) -> float | None:
    """Return max of values as float, or None if empty."""
    if not values:
        return None
    return float(max(values))


def _sum_both_or_none(a: list[float], b: list[float]) -> float | None:
    """Return sum of both lists as float, or None if either list is empty."""
    if not a or not b:
        return None
    return float(sum(a) + sum(b))


class MetricsFetcher:
    """Fetches CloudWatch metrics for resources within a request budget."""

    def __init__(
        self,
        factory: ClientFactory,
        thresholds: Thresholds,
        max_requests: int,
        clock: Clock,
    ) -> None:
        self._factory = factory
        self._thresholds = thresholds
        self._max_requests = max_requests
        self._clock = clock
        self.requests_used: int = 0
        self._exhausted: bool = False

    def _required_requests(self, resource: Resource) -> int:
        if resource.resource_type == "ec2_instance":
            return 3 if self._thresholds.ec2_network_bytes_max >= 0 else 1
        if resource.resource_type in ("rds_instance", "nat_gateway"):
            return 2
        return 0

    def _fetch_values(
        self,
        client: Any,
        namespace: str,
        metric_name: str,
        statistic: str,
        dimension_name: str,
        resource_id: str,
        start: datetime,
        end: datetime,
    ) -> list[float]:
        response = client.get_metric_statistics(
            Namespace=namespace,
            MetricName=metric_name,
            Dimensions=[{"Name": dimension_name, "Value": resource_id}],
            StartTime=start,
            EndTime=end,
            Period=_PERIOD_SECONDS,
            Statistics=[statistic],
        )
        self.requests_used += 1
        return [float(dp[statistic]) for dp in response.get("Datapoints", [])]

    def fetch(self, resource: Resource) -> Mapping[str, float | None] | None:
        """Fetch metrics for a resource, or return None if request budget exhausted."""
        if self._exhausted:
            return None

        if resource.resource_type not in _METRIC_TYPES:
            return {}

        need = self._required_requests(resource)
        if self.requests_used + need > self._max_requests:
            self._exhausted = True
            return None

        end = self._clock.now()
        start = end - timedelta(days=self._thresholds.period_days)
        client = self._factory.client("cloudwatch", resource.region)

        if resource.resource_type == "ec2_instance":
            cpu = self._fetch_values(
                client,
                "AWS/EC2",
                "CPUUtilization",
                "Maximum",
                "InstanceId",
                resource.resource_id,
                start,
                end,
            )
            result: dict[str, float | None] = {"cpu_max_percent": _max_or_none(cpu)}
            if self._thresholds.ec2_network_bytes_max >= 0:
                net_in = self._fetch_values(
                    client,
                    "AWS/EC2",
                    "NetworkIn",
                    "Sum",
                    "InstanceId",
                    resource.resource_id,
                    start,
                    end,
                )
                net_out = self._fetch_values(
                    client,
                    "AWS/EC2",
                    "NetworkOut",
                    "Sum",
                    "InstanceId",
                    resource.resource_id,
                    start,
                    end,
                )
                result["network_bytes_total"] = _sum_both_or_none(net_in, net_out)
            return result

        if resource.resource_type == "rds_instance":
            conn = self._fetch_values(
                client,
                "AWS/RDS",
                "DatabaseConnections",
                "Maximum",
                "DBInstanceIdentifier",
                resource.resource_id,
                start,
                end,
            )
            cpu = self._fetch_values(
                client,
                "AWS/RDS",
                "CPUUtilization",
                "Maximum",
                "DBInstanceIdentifier",
                resource.resource_id,
                start,
                end,
            )
            return {
                "db_connections_max": _max_or_none(conn),
                "rds_cpu_max_percent": _max_or_none(cpu),
            }

        if resource.resource_type == "nat_gateway":
            src = self._fetch_values(
                client,
                "AWS/NATGateway",
                "BytesInFromSource",
                "Sum",
                "NatGatewayId",
                resource.resource_id,
                start,
                end,
            )
            dst = self._fetch_values(
                client,
                "AWS/NATGateway",
                "BytesInFromDestination",
                "Sum",
                "NatGatewayId",
                resource.resource_id,
                start,
                end,
            )
            return {"nat_bytes_total": _sum_both_or_none(src, dst)}

        return {}
