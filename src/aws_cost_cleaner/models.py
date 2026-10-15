"""Data models for aws-cost-cleaner."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from aws_cost_cleaner.errors import AwsCostCleanerError

ResourceType = Literal[
    "ebs_volume",
    "elastic_ip",
    "nat_gateway",
    "load_balancer",
    "ec2_instance",
    "rds_instance",
]

Action = Literal["Stop", "Delete"]

SkipReason = Literal[
    "keep_tag",
    "active",
    "no_metrics",
    "too_new",
    "metric_budget_exhausted",
    "aurora",
    "unsupported_lb_type",
    "already_candidate",
    "stop_wait_pending",
    "stop_not_recorded",
    "restarted_after_stop",
]


@dataclass(frozen=True)
class Resource:
    """Normalized AWS resource representation."""

    resource_type: ResourceType
    region: str
    resource_id: str
    arn: str | None
    name: str | None
    tags: Mapping[str, str]
    state: str
    created_at: datetime | None
    attrs: Mapping[str, Any]


@dataclass(frozen=True)
class Finding:
    """Cleanup finding candidate for a resource."""

    resource_type: ResourceType
    service_label: str
    region: str
    resource_id: str
    arn: str | None
    name: str | None
    state_summary: str
    metrics: Mapping[str, Any]
    recommended_action: Action
    reason_code: str
    reason_text: str
    estimated_monthly_usd: float | None
    price_source: Mapping[str, Any] | None

    def to_dict(self) -> dict[str, Any]:
        """Convert finding to dictionary."""
        return {
            "resource_type": self.resource_type,
            "service_label": self.service_label,
            "region": self.region,
            "resource_id": self.resource_id,
            "arn": self.arn,
            "name": self.name,
            "state_summary": self.state_summary,
            "metrics": dict(self.metrics),
            "recommended_action": self.recommended_action,
            "reason_code": self.reason_code,
            "reason_text": self.reason_text,
            "estimated_monthly_usd": self.estimated_monthly_usd,
            "price_source": (
                dict(self.price_source) if self.price_source is not None else None
            ),
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> Finding:
        """Create finding from dictionary."""
        return cls(
            resource_type=d["resource_type"],
            service_label=d["service_label"],
            region=d["region"],
            resource_id=d["resource_id"],
            arn=d["arn"],
            name=d["name"],
            state_summary=d["state_summary"],
            metrics=dict(d["metrics"]),
            recommended_action=d["recommended_action"],
            reason_code=d["reason_code"],
            reason_text=d["reason_text"],
            estimated_monthly_usd=d["estimated_monthly_usd"],
            price_source=(
                dict(d["price_source"]) if d["price_source"] is not None else None
            ),
        )


@dataclass(frozen=True)
class Skipped:
    """Skipped resource record during scan."""

    resource_type: ResourceType
    region: str
    resource_id: str
    skip_reason: SkipReason

    def to_dict(self) -> dict[str, Any]:
        """Convert skipped record to dictionary."""
        return {
            "resource_type": self.resource_type,
            "region": self.region,
            "resource_id": self.resource_id,
            "skip_reason": self.skip_reason,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> Skipped:
        """Create skipped record from dictionary."""
        return cls(
            resource_type=d["resource_type"],
            region=d["region"],
            resource_id=d["resource_id"],
            skip_reason=d["skip_reason"],
        )


@dataclass(frozen=True)
class DiscoveryInfo:
    """Discovery execution information."""

    method: str
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class ScanResult:
    """Result of a scan operation."""

    schema_version: int
    scan_id: str
    generated_at: str
    account_id: str
    thresholds: Mapping[str, Any]
    regions_scanned: tuple[str, ...]
    discovery: DiscoveryInfo
    metric_requests_used: int
    findings: tuple[Finding, ...]
    skipped: tuple[Skipped, ...]

    def to_dict(self) -> dict[str, Any]:
        """Convert scan result to dictionary."""
        return {
            "schema_version": self.schema_version,
            "scan_id": self.scan_id,
            "generated_at": self.generated_at,
            "account_id": self.account_id,
            "thresholds": dict(self.thresholds),
            "regions_scanned": list(self.regions_scanned),
            "discovery": {
                "method": self.discovery.method,
                "warnings": list(self.discovery.warnings),
            },
            "metric_requests_used": self.metric_requests_used,
            "findings": [f.to_dict() for f in self.findings],
            "skipped": [s.to_dict() for s in self.skipped],
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> ScanResult:
        """Create scan result from dictionary."""
        return cls(
            schema_version=d["schema_version"],
            scan_id=d["scan_id"],
            generated_at=d["generated_at"],
            account_id=d["account_id"],
            thresholds=dict(d["thresholds"]),
            regions_scanned=tuple(d["regions_scanned"]),
            discovery=DiscoveryInfo(
                method=d["discovery"]["method"],
                warnings=tuple(d["discovery"]["warnings"]),
            ),
            metric_requests_used=d["metric_requests_used"],
            findings=tuple(Finding.from_dict(f) for f in d["findings"]),
            skipped=tuple(Skipped.from_dict(s) for s in d["skipped"]),
        )


@dataclass(frozen=True)
class ApprovalItem:
    """An item in the approval file."""

    resource_type: ResourceType
    region: str
    resource_id: str
    arn: str | None
    name: str | None
    recommended_action: Action
    reason_code: str
    estimated_monthly_usd: float | None
    approved: bool

    def to_dict(self) -> dict[str, Any]:
        """Convert approval item to dictionary."""
        return {
            "resource_type": self.resource_type,
            "region": self.region,
            "resource_id": self.resource_id,
            "arn": self.arn,
            "name": self.name,
            "recommended_action": self.recommended_action,
            "reason_code": self.reason_code,
            "estimated_monthly_usd": self.estimated_monthly_usd,
            "approved": self.approved,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> ApprovalItem:
        """Create approval item from dictionary."""
        return cls(
            resource_type=d["resource_type"],
            region=d["region"],
            resource_id=d["resource_id"],
            arn=d["arn"],
            name=d["name"],
            recommended_action=d["recommended_action"],
            reason_code=d["reason_code"],
            estimated_monthly_usd=d["estimated_monthly_usd"],
            approved=d["approved"],
        )


@dataclass(frozen=True)
class Outcome:
    """Execution outcome of an action on a resource."""

    resource_type: ResourceType
    region: str
    resource_id: str
    action: Literal["tag", "stop", "tag_stopped_at", "delete"]
    status: Literal["planned", "done", "skipped", "failed"]
    error: AwsCostCleanerError | None
    aws_error_code: str | None
    note: str | None
