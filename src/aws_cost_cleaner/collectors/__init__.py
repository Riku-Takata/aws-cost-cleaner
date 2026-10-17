"""Resource collectors package."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from aws_cost_cleaner.aws.session import ClientFactory
from aws_cost_cleaner.collectors import ebs, eip, nat
from aws_cost_cleaner.models import Resource, ResourceType, Skipped


@dataclass(frozen=True)
class CollectResult:
    """Result of collecting resources in a region."""

    resources: tuple[Resource, ...]
    skipped: tuple[Skipped, ...]


CollectorFn = Callable[[ClientFactory, str], CollectResult]

COLLECTORS: Mapping[ResourceType, CollectorFn] = {
    "ebs_volume": ebs.collect,
    "elastic_ip": eip.collect,
    "nat_gateway": nat.collect,
}

__all__ = [
    "COLLECTORS",
    "CollectResult",
]
