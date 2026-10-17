"""AWS Price List Query API lookup and local file cache."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from botocore.exceptions import ClientError  # type: ignore[import-not-found]

from aws_cost_cleaner.aws.session import ClientFactory
from aws_cost_cleaner.clock import Clock
from aws_cost_cleaner.pricing.table import PriceValue

DEFAULT_CACHE_DIR: Path = Path("~/.cache/aws-cost-cleaner/prices").expanduser()

_PRICING_REGION = "us-east-1"


@dataclass(frozen=True)
class PriceFilter:
    """Filter definition for a price key in AWS Price List Query API."""

    service_code: str
    fields: tuple[tuple[str, str], ...] = ()
    sub_fields: tuple[str, ...] = ()


# docs/verification-notes.md #6: regionCode 以外の属性名は未確認のため、
# すべて None(API を呼ばない)
FILTERS: Mapping[str, PriceFilter | None] = {
    "ebs_gb_month": None,
    "ebs_iops_month": None,
    "ebs_throughput_mibps_month": None,
    "public_ipv4_hour": None,
    "nat_gateway_hour": None,
    "alb_hour": None,
    "nlb_hour": None,
    "ec2_linux_hour": None,
    "rds_hour": None,
    "rds_storage_gb_month": None,
    "rds_iops_month": None,
}


def _build_filters(
    spec: PriceFilter, region: str, sub: str | None
) -> list[dict[str, str]] | None:
    if not spec.sub_fields:
        if sub is not None:
            return None
        sub_pairs: list[tuple[str, str]] = []
    else:
        if sub is None:
            return None
        parts = sub.split("|")
        if len(parts) != len(spec.sub_fields):
            return None
        sub_pairs = list(zip(spec.sub_fields, parts, strict=True))

    result: list[dict[str, str]] = [
        {"Type": "TERM_MATCH", "Field": "regionCode", "Value": region}
    ]
    for field_name, value in spec.fields:
        result.append({"Type": "TERM_MATCH", "Field": field_name, "Value": value})
    for field_name, value in sub_pairs:
        result.append({"Type": "TERM_MATCH", "Field": field_name, "Value": value})
    return result


def _parse_usd(price_list: Any) -> float | None:
    if not isinstance(price_list, list) or len(price_list) != 1:
        return None
    raw_item = price_list[0]
    if isinstance(raw_item, str):
        try:
            item = json.loads(raw_item)
        except ValueError:
            return None
    elif isinstance(raw_item, dict):
        item = raw_item
    else:
        return None

    if not isinstance(item, dict):
        return None

    terms = item.get("terms")
    if not isinstance(terms, dict):
        return None
    on_demand = terms.get("OnDemand")
    if not isinstance(on_demand, dict) or len(on_demand) != 1:
        return None

    term = next(iter(on_demand.values()))
    if not isinstance(term, dict):
        return None
    dimensions = term.get("priceDimensions")
    if not isinstance(dimensions, dict) or len(dimensions) != 1:
        return None

    dimension = next(iter(dimensions.values()))
    if not isinstance(dimension, dict):
        return None
    price_per_unit = dimension.get("pricePerUnit")
    if not isinstance(price_per_unit, dict):
        return None
    usd_raw = price_per_unit.get("USD")
    if usd_raw is None or isinstance(usd_raw, bool):
        return None
    try:
        return float(usd_raw)
    except (TypeError, ValueError):
        return None


def _cache_path(cache_dir: Path, region: str, key: str, sub: str | None) -> Path:
    digest = hashlib.sha256(json.dumps([region, key, sub]).encode("utf-8")).hexdigest()
    return cache_dir / f"{digest}.json"


class PriceListLookup:
    """Looks up prices via AWS Price List Query API and saves to local cache."""

    def __init__(
        self,
        factory: ClientFactory,
        clock: Clock,
        cache_dir: Path,
        *,
        filters: Mapping[str, PriceFilter | None] | None = None,
    ) -> None:
        self._factory = factory
        self._clock = clock
        self._cache_dir = Path(cache_dir)
        self._filters: Mapping[str, PriceFilter | None] = (
            FILTERS if filters is None else filters
        )

    def get(self, region: str, key: str, sub: str | None = None) -> PriceValue | None:
        """Price List Query API から料金を取得して返す。取得できなければ None。"""
        spec = self._filters.get(key)
        if spec is None:
            return None
        api_filters = _build_filters(spec, region, sub)
        if api_filters is None:
            return None

        client = self._factory.client("pricing", _PRICING_REGION)
        try:
            resp = client.get_products(
                ServiceCode=spec.service_code,
                Filters=api_filters,
            )
        except ClientError:
            return None

        usd = _parse_usd(resp.get("PriceList", []))
        if usd is None:
            return None

        now = self._clock.now()
        utc_now = now.astimezone(UTC) if now.tzinfo is not None else now
        as_of = utc_now.date().isoformat()
        self._save(region, key, sub, usd, as_of, now)
        return PriceValue(usd=usd, kind="price_list_api", as_of=as_of)

    def _save(
        self,
        region: str,
        key: str,
        sub: str | None,
        usd: float,
        as_of: str,
        now: datetime,
    ) -> None:
        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            target = _cache_path(self._cache_dir, region, key, sub)
            tmp = target.with_suffix(".tmp")
            payload = {
                "region": region,
                "key": key,
                "sub": sub,
                "usd": usd,
                "as_of": as_of,
                "saved_at": now.isoformat(),
            }
            tmp.write_text(json.dumps(payload), encoding="utf-8")
            os.replace(tmp, target)
        except OSError:
            return


class CachedPriceLookup:
    """Looks up prices from the local JSON cache directory within TTL."""

    def __init__(self, cache_dir: Path, clock: Clock, ttl_hours: int) -> None:
        self._cache_dir = Path(cache_dir)
        self._clock = clock
        self._ttl_hours = ttl_hours

    def get(self, region: str, key: str, sub: str | None = None) -> PriceValue | None:
        """キャッシュから料金を返す。期限切れや未保存なら None。"""
        path = _cache_path(self._cache_dir, region, key, sub)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

        if not isinstance(data, dict):
            return None
        if (
            data.get("region") != region
            or data.get("key") != key
            or data.get("sub") != sub
        ):
            return None

        usd = data.get("usd")
        if isinstance(usd, bool) or not isinstance(usd, int | float):
            return None
        as_of = data.get("as_of")
        if not isinstance(as_of, str):
            return None
        saved_at_raw = data.get("saved_at")
        if not isinstance(saved_at_raw, str):
            return None
        try:
            saved_at = datetime.fromisoformat(saved_at_raw)
        except ValueError:
            return None

        try:
            elapsed = self._clock.now() - saved_at
        except TypeError:
            return None
        if elapsed <= timedelta(hours=self._ttl_hours):
            return PriceValue(usd=float(usd), kind="cache", as_of=as_of)
        return None
