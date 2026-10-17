"""Load balancer (ALB/NLB) collector."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from aws_cost_cleaner.models import Resource, Skipped

if TYPE_CHECKING:
    from aws_cost_cleaner.aws.session import ClientFactory
    from aws_cost_cleaner.collectors import CollectResult

_SUPPORTED_TYPES = frozenset({"application", "network"})
_DESCRIBE_TAGS_CHUNK = 20


def _tags_from_list(raw: Any) -> dict[str, str]:
    """Convert a [{"Key", "Value"}] list into a dictionary."""
    if not raw or not isinstance(raw, list):
        return {}
    return {
        str(t["Key"]): str(t["Value"])
        for t in raw
        if isinstance(t, Mapping) and "Key" in t and "Value" in t
    }


def to_resource(
    item: Mapping[str, Any],
    region: str,
    tags: Mapping[str, str],
    registered_target_count: int,
) -> Resource:
    """Normalize DescribeLoadBalancers single item into Resource."""
    return Resource(
        resource_type="load_balancer",
        region=region,
        resource_id=str(item["LoadBalancerName"]),
        arn=item.get("LoadBalancerArn"),
        name=tags.get("Name"),
        tags=dict(tags),
        state=str((item.get("State") or {}).get("Code", "")),
        created_at=item.get("CreatedTime"),
        attrs={
            "Type": item.get("Type"),
            "registered_target_count": registered_target_count,
        },
    )


def _count_registered_targets(client: Any, lb_arn: str) -> int:
    """Sum registered targets across all target groups of the load balancer."""
    total = 0
    params: dict[str, Any] = {"LoadBalancerArn": lb_arn}
    while True:
        resp = client.describe_target_groups(**params)
        for tg in resp.get("TargetGroups", []):
            health = client.describe_target_health(TargetGroupArn=tg["TargetGroupArn"])
            total += len(health.get("TargetHealthDescriptions", []))
        marker = resp.get("NextMarker")
        if not marker:
            break
        params = {"LoadBalancerArn": lb_arn, "Marker": marker}
    return total


def _fetch_tags(client: Any, arns: list[str]) -> dict[str, dict[str, str]]:
    """Fetch tags for the given ARNs, 20 at a time."""
    result: dict[str, dict[str, str]] = {}
    for i in range(0, len(arns), _DESCRIBE_TAGS_CHUNK):
        chunk = arns[i : i + _DESCRIBE_TAGS_CHUNK]
        resp = client.describe_tags(ResourceArns=chunk)
        for desc in resp.get("TagDescriptions", []):
            result[str(desc["ResourceArn"])] = _tags_from_list(desc.get("Tags"))
    return result


def collect(factory: ClientFactory, region: str) -> CollectResult:
    """Collect application and network load balancers in the region."""
    from aws_cost_cleaner.collectors import CollectResult

    client = factory.client("elbv2", region)
    supported: list[Mapping[str, Any]] = []
    skipped: list[Skipped] = []
    params: dict[str, Any] = {}
    while True:
        resp = client.describe_load_balancers(**params)
        for item in resp.get("LoadBalancers", []):
            if item.get("Type") in _SUPPORTED_TYPES:
                supported.append(item)
            else:
                skipped.append(
                    Skipped(
                        resource_type="load_balancer",
                        region=region,
                        resource_id=str(item["LoadBalancerName"]),
                        skip_reason="unsupported_lb_type",
                    )
                )
        marker = resp.get("NextMarker")
        if not marker:
            break
        params = {"Marker": marker}

    arns = [str(i["LoadBalancerArn"]) for i in supported]
    tags_by_arn = _fetch_tags(client, arns)
    resources = [
        to_resource(
            item,
            region,
            tags_by_arn.get(arn, {}),
            _count_registered_targets(client, arn),
        )
        for item, arn in zip(supported, arns, strict=True)
    ]
    return CollectResult(resources=tuple(resources), skipped=tuple(skipped))
