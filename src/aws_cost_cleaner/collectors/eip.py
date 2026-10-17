"""Elastic IP collector."""

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
    """Normalize DescribeAddresses single item dictionary into Resource."""
    tags = _extract_tags(item)
    public_ip = item.get("PublicIp")
    return Resource(
        resource_type="elastic_ip",
        region=region,
        resource_id=str(item["AllocationId"]),
        arn=None,
        name=public_ip,
        tags=tags,
        state="unassociated",
        created_at=None,
        attrs={
            "PublicIp": public_ip,
        },
    )


def collect(factory: ClientFactory, region: str) -> CollectResult:
    """Collect unassociated Elastic IPs in the specified region."""
    from aws_cost_cleaner.collectors import CollectResult

    client = factory.client("ec2", region)
    resources: list[Resource] = []
    params: dict[str, Any] = {}
    while True:
        resp = client.describe_addresses(**params)
        for item in resp.get("Addresses", []):
            if not item.get("AssociationId"):
                resources.append(to_resource(item, region))
        token = resp.get("NextToken")
        if not token:
            break
        params = {"NextToken": token}

    return CollectResult(resources=tuple(resources), skipped=())
