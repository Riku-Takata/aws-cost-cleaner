"""Discovery of AWS resources across regions via Resource Explorer."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from botocore.exceptions import ClientError  # type: ignore[import-not-found]

from aws_cost_cleaner.aws.session import ClientFactory
from aws_cost_cleaner.config import Config
from aws_cost_cleaner.models import ResourceType

RESOURCE_EXPLORER_TYPES: Mapping[ResourceType, tuple[str, ...]] = {
    "ebs_volume": ("ec2:volume",),
    "elastic_ip": ("ec2:elastic-ip",),
    "nat_gateway": ("ec2:natgateway",),
    "load_balancer": (
        "elasticloadbalancing:loadbalancer/app",
        "elasticloadbalancing:loadbalancer/net",
    ),
    "ec2_instance": ("ec2:instance",),
    "rds_instance": ("rds:db",),
}

DiscoveryMethod = Literal[
    "resource_explorer", "explicit_regions", "fallback_all_regions"
]


@dataclass(frozen=True)
class DiscoveryResult:
    """Regions to scan per resource type and how they were determined."""

    method: DiscoveryMethod
    regions_by_type: dict[ResourceType, frozenset[str]]
    warnings: tuple[str, ...]


class _SearchFailed(Exception):
    """Internal exception when resource-explorer Search fails with ClientError."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _call_search(rex: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Call rex.search and wrap ClientError into _SearchFailed."""
    try:
        return rex.search(**params)  # type: ignore[no-any-return]
    except ClientError as e:
        code = str(e.response.get("Error", {}).get("Code", ""))
        raise _SearchFailed(code) from e


class _EnabledRegions:
    """Lazily fetch and cache enabled region names from EC2."""

    def __init__(self, factory: ClientFactory, region: str) -> None:
        self._factory = factory
        self._region = region
        self._regions: list[str] | None = None

    def get(self) -> list[str]:
        """Return cached enabled regions or query ec2:DescribeRegions."""
        if self._regions is None:
            ec2 = self._factory.client("ec2", self._region)
            resp = ec2.describe_regions()
            self._regions = [r["RegionName"] for r in resp.get("Regions", [])]
        return self._regions


def _all_types(regions: frozenset[str]) -> dict[ResourceType, frozenset[str]]:
    """Return a mapping of all 6 resource types to the given regions set."""
    return {t: regions for t in RESOURCE_EXPLORER_TYPES}


def _search_type(rex: Any, resourcetype: str, enabled: _EnabledRegions) -> set[str]:
    """Collect regions for a single resource type."""
    found: set[str] = set()
    params: dict[str, Any] = {"QueryString": f"resourcetype:{resourcetype}"}
    while True:
        resp = _call_search(rex, params)
        count = resp.get("Count") or {}
        complete = count.get("Complete", True)
        if not complete:
            hits: set[str] = set()
            for r in enabled.get():
                page = _call_search(
                    rex,
                    {
                        "QueryString": f"resourcetype:{resourcetype} region:{r}",
                        "MaxResults": 1,
                    },
                )
                if page.get("Resources"):
                    hits.add(r)
            return hits

        for item in resp.get("Resources", []):
            if "Region" in item:
                found.add(item["Region"])

        token = resp.get("NextToken")
        if token:
            params = {
                "QueryString": f"resourcetype:{resourcetype}",
                "NextToken": token,
            }
        else:
            return found


def discover(factory: ClientFactory, config: Config) -> DiscoveryResult:
    """Determine regions to scan per resource type."""
    if config.scan.regions:
        return DiscoveryResult(
            method="explicit_regions",
            regions_by_type=_all_types(frozenset(config.scan.regions)),
            warnings=(),
        )

    region = config.scan.resource_explorer_region
    rex = factory.client("resource-explorer-2", region)
    enabled = _EnabledRegions(factory, region)

    failed_code: str | None = None
    regions_by_type: dict[ResourceType, frozenset[str]] = {}

    try:
        for rtype, rtypes in RESOURCE_EXPLORER_TYPES.items():
            union: set[str] = set()
            for t in rtypes:
                union |= _search_type(rex, t, enabled)
            regions_by_type[rtype] = frozenset(union)
    except _SearchFailed as e:
        failed_code = e.code

    if failed_code is not None:
        all_regions = enabled.get()
        return DiscoveryResult(
            method="fallback_all_regions",
            regions_by_type=_all_types(frozenset(all_regions)),
            warnings=(f"resource-explorer unavailable: {failed_code}",),
        )

    return DiscoveryResult(
        method="resource_explorer",
        regions_by_type=regions_by_type,
        warnings=(),
    )
