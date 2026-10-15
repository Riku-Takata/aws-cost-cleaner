"""API call and tag key guard for boto3 clients."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from botocore.client import BaseClient  # type: ignore[import-not-found]
from botocore.model import OperationModel  # type: ignore[import-not-found]

from aws_cost_cleaner.errors import DisallowedApiCallError
from aws_cost_cleaner.tags import (
    CLEANUP_CANDIDATE,
    CLEANUP_REASON,
    CLEANUP_STOPPED_AT,
)

Mode = Literal["read", "tag", "execute"]

_DESCRIBE_OPERATIONS: frozenset[str] = frozenset(
    {
        "ec2:DescribeRegions",
        "ec2:DescribeVolumes",
        "ec2:DescribeAddresses",
        "ec2:DescribeNatGateways",
        "ec2:DescribeInstances",
        "elbv2:DescribeLoadBalancers",
        "elbv2:DescribeTargetGroups",
        "elbv2:DescribeTargetHealth",
        "elbv2:DescribeTags",
        "rds:DescribeDBInstances",
    }
)

ALLOWED_OPERATIONS: Mapping[Mode, frozenset[str]] = {
    "read": frozenset(
        {
            "sts:GetCallerIdentity",
            "resource-explorer-2:Search",
            "cloudwatch:GetMetricStatistics",
            "pricing:GetProducts",
        }
        | _DESCRIBE_OPERATIONS
    ),
    "tag": frozenset(
        {
            "sts:GetCallerIdentity",
            "ec2:CreateTags",
            "rds:AddTagsToResource",
            "elbv2:AddTags",
        }
        | _DESCRIBE_OPERATIONS
    ),
    "execute": frozenset(
        {
            "sts:GetCallerIdentity",
            "ec2:StopInstances",
            "ec2:TerminateInstances",
            "ec2:DeleteVolume",
            "ec2:ReleaseAddress",
            "ec2:DeleteNatGateway",
            "rds:StopDBInstance",
            "rds:DeleteDBInstance",
            "elbv2:DeleteLoadBalancer",
            "ec2:CreateTags",
            "rds:AddTagsToResource",
        }
        | _DESCRIBE_OPERATIONS
    ),
}

ALLOWED_TAG_KEYS: Mapping[Mode, frozenset[str]] = {
    "read": frozenset(),
    "tag": frozenset({CLEANUP_CANDIDATE, CLEANUP_REASON}),
    "execute": frozenset({CLEANUP_STOPPED_AT}),
}

_TAGGING_OPERATIONS: frozenset[str] = frozenset(
    {
        "ec2:CreateTags",
        "rds:AddTagsToResource",
        "elbv2:AddTags",
    }
)


def install_guard(client: BaseClient, mode: Mode) -> None:
    """Attach guard handlers to boto3 client to restrict operations and tag keys."""
    service: str = client.meta.service_model.service_name

    def _check_operation(model: OperationModel, **kwargs: Any) -> None:
        operation = f"{service}:{model.name}"
        allowed = ALLOWED_OPERATIONS.get(mode, frozenset())
        if operation not in allowed:
            raise DisallowedApiCallError(
                f"Operation '{operation}' is not allowed in '{mode}' mode"
            )

    def _check_tags(
        params: Mapping[str, Any], model: OperationModel, **kwargs: Any
    ) -> None:
        operation = f"{service}:{model.name}"
        if operation in _TAGGING_OPERATIONS:
            tags = params.get("Tags") or []
            disallowed_keys: set[str] = set()
            allowed_keys = ALLOWED_TAG_KEYS.get(mode, frozenset())
            for item in tags:
                if isinstance(item, Mapping) and "Key" in item:
                    key = item["Key"]
                    if key not in allowed_keys:
                        disallowed_keys.add(str(key))
                else:
                    disallowed_keys.add("<missing Key>")
            if disallowed_keys:
                sorted_keys = ", ".join(sorted(disallowed_keys))
                msg = (
                    f"Operation '{operation}' with disallowed tag key(s) "
                    f"[{sorted_keys}] is not allowed in '{mode}' mode"
                )
                raise DisallowedApiCallError(msg)

    client.meta.events.register("before-call.*.*", _check_operation)
    client.meta.events.register("before-parameter-build.*.*", _check_tags)
