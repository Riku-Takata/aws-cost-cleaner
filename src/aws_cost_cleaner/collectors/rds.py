"""RDS instance collector."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from aws_cost_cleaner.models import Resource, Skipped
from aws_cost_cleaner.tags import is_candidate

if TYPE_CHECKING:
    from aws_cost_cleaner.aws.session import ClientFactory
    from aws_cost_cleaner.collectors import CollectResult


def _extract_tags(item: Mapping[str, Any]) -> dict[str, str]:
    """Extract key-value tags dictionary from describe item."""
    raw_tags = item.get("TagList")
    if not raw_tags or not isinstance(raw_tags, list):
        return {}
    return {
        str(t["Key"]): str(t["Value"])
        for t in raw_tags
        if isinstance(t, Mapping) and "Key" in t and "Value" in t
    }


def to_resource(item: Mapping[str, Any], region: str) -> Resource:
    """Normalize DescribeDBInstances single item dictionary into Resource."""
    tags = _extract_tags(item)
    return Resource(
        resource_type="rds_instance",
        region=region,
        resource_id=str(item["DBInstanceIdentifier"]),
        arn=item.get("DBInstanceArn"),
        name=tags.get("Name"),
        tags=tags,
        state=str(item.get("DBInstanceStatus", "")),
        created_at=item.get("InstanceCreateTime"),
        attrs={
            "Engine": item.get("Engine"),
            "DBInstanceClass": item.get("DBInstanceClass"),
            "MultiAZ": item.get("MultiAZ"),
            "AllocatedStorage": item.get("AllocatedStorage"),
            "StorageType": item.get("StorageType"),
            "Iops": item.get("Iops"),
        },
    )


def _is_aurora(item: Mapping[str, Any]) -> bool:
    """Return True if the instance belongs to Aurora (cluster member or engine)."""
    if item.get("DBClusterIdentifier"):
        return True
    return str(item.get("Engine") or "").startswith("aurora")


def collect(factory: ClientFactory, region: str) -> CollectResult:
    """Collect available and candidate-stopped RDS instances in the region."""
    from aws_cost_cleaner.collectors import CollectResult

    client = factory.client("rds", region)
    resources: list[Resource] = []
    skipped: list[Skipped] = []
    params: dict[str, Any] = {}
    while True:
        resp = client.describe_db_instances(**params)
        for item in resp.get("DBInstances", []):
            status = item.get("DBInstanceStatus")
            if _is_aurora(item):
                skipped.append(
                    Skipped(
                        resource_type="rds_instance",
                        region=region,
                        resource_id=str(item["DBInstanceIdentifier"]),
                        skip_reason="aurora",
                    )
                )
            elif status == "available" or (
                status == "stopped" and is_candidate(_extract_tags(item))
            ):
                resources.append(to_resource(item, region))
        marker = resp.get("Marker")
        if not marker:
            break
        params = {"Marker": marker}

    return CollectResult(resources=tuple(resources), skipped=tuple(skipped))
