"""Allowed (resource_type, action, reason_code) combinations and display labels."""

from __future__ import annotations

from aws_cost_cleaner.models import Action, ResourceType

RESOURCE_TYPES: tuple[ResourceType, ...] = (
    "ebs_volume",
    "elastic_ip",
    "nat_gateway",
    "load_balancer",
    "ec2_instance",
    "rds_instance",
)

ALLOWED: frozenset[tuple[ResourceType, Action, str]] = frozenset(
    {
        ("ebs_volume", "Delete", "ebs-unattached"),
        ("elastic_ip", "Delete", "eip-unassociated"),
        ("nat_gateway", "Delete", "nat-idle"),
        ("load_balancer", "Delete", "elb-no-targets"),
        ("ec2_instance", "Stop", "ec2-low-cpu"),
        ("ec2_instance", "Delete", "ec2-stopped-elapsed"),
        ("rds_instance", "Stop", "rds-no-connections"),
        ("rds_instance", "Delete", "rds-stopped-elapsed"),
    }
)

_LABELS: dict[str, str] = {
    "ebs_volume": "EC2 (EBS)",
    "elastic_ip": "VPC (EIP)",
    "nat_gateway": "VPC (NAT)",
    "ec2_instance": "EC2",
    "rds_instance": "RDS",
}

_LB_LABELS: dict[str, str] = {
    "application": "ELB (ALB)",
    "network": "ELB (NLB)",
}


def is_allowed(resource_type: str, action: str, reason_code: str) -> bool:
    """リソース種別、アクション、理由コードの組み合わせが許可されているかを判定する。"""
    return (resource_type, action, reason_code) in ALLOWED


def service_label(resource_type: ResourceType, lb_type: str | None = None) -> str:
    """リソース種別に対応するサービス表示名を返す。"""
    if resource_type == "load_balancer":
        if lb_type is not None and lb_type in _LB_LABELS:
            return _LB_LABELS[lb_type]
        raise ValueError(f"unsupported lb_type for load_balancer: {lb_type!r}")
    if resource_type in _LABELS:
        return _LABELS[resource_type]
    raise ValueError(f"unknown resource_type: {resource_type!r}")
