"""EC2 instance collector."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from aws_cost_cleaner.models import Resource
from aws_cost_cleaner.tags import is_candidate

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
    """Normalize DescribeInstances single instance dictionary into Resource."""
    tags = _extract_tags(item)
    return Resource(
        resource_type="ec2_instance",
        region=region,
        resource_id=str(item["InstanceId"]),
        arn=None,
        name=tags.get("Name"),
        tags=tags,
        state=str((item.get("State") or {}).get("Name", "")),
        created_at=item.get("LaunchTime"),
        attrs={
            "InstanceType": item.get("InstanceType"),
            "Platform": item.get("Platform"),
        },
    )


def collect(factory: ClientFactory, region: str) -> CollectResult:
    """Collect running and candidate-stopped EC2 instances in the region."""
    from aws_cost_cleaner.collectors import CollectResult

    client = factory.client("ec2", region)
    resources: list[Resource] = []
    params: dict[str, Any] = {}
    while True:
        resp = client.describe_instances(**params)
        for reservation in resp.get("Reservations", []):
            for inst in reservation.get("Instances", []):
                state = (inst.get("State") or {}).get("Name")
                if state == "running" or (
                    state == "stopped" and is_candidate(_extract_tags(inst))
                ):
                    resources.append(to_resource(inst, region))
        token = resp.get("NextToken")
        if not token:
            break
        params = {"NextToken": token}

    return CollectResult(resources=tuple(resources), skipped=())
