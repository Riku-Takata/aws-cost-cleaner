"""NAT Gateway collector."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from aws_cost_cleaner.models import Resource

if TYPE_CHECKING:
    from aws_cost_cleaner.aws.session import ClientFactory
    from aws_cost_cleaner.collectors import CollectResult


def _extract_tags(item: Mapping[str, Any]) -> dict[str, str]:
    """Extract key-value tags dictionary from describe item."""
    raw_tags = item.get("Tags")
    if not raw_tags or not isinstance(raw_tags, list):
        return {}
    return {
        str(t["Key"]): str(t["Value"])
        for t in raw_tags
        if isinstance(t, Mapping) and "Key" in t and "Value" in t
    }


def to_resource(item: Mapping[str, Any], region: str) -> Resource:
    """Normalize DescribeNatGateways single item dictionary into Resource."""
    tags = _extract_tags(item)
    name = tags.get("Name")
    return Resource(
        resource_type="nat_gateway",
        region=region,
        resource_id=str(item["NatGatewayId"]),
        arn=None,
        name=name,
        tags=tags,
        state=str(item.get("State", "")),
        created_at=item.get("CreateTime"),
        attrs={
            "ConnectivityType": item.get("ConnectivityType"),
        },
    )


def collect(factory: ClientFactory, region: str) -> CollectResult:
    """Collect available NAT Gateways in the specified region."""
    from aws_cost_cleaner.collectors import CollectResult

    client = factory.client("ec2", region)
    resources: list[Resource] = []
    params: dict[str, Any] = {}
    while True:
        resp = client.describe_nat_gateways(**params)
        for item in resp.get("NatGateways", []):
            if item.get("State") == "available":
                resources.append(to_resource(item, region))
        token = resp.get("NextToken")
        if not token:
            break
        params = {"NextToken": token}

    return CollectResult(resources=tuple(resources), skipped=())
