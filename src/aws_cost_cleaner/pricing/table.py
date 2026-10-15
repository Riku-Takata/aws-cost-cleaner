"""Price value type, lookup protocol and bundled price table."""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any, Literal, Protocol


@dataclass(frozen=True)
class PriceValue:
    """A price in USD with its origin."""

    usd: float
    kind: Literal["price_list_api", "cache", "bundled"]
    as_of: str


class PriceLookup(Protocol):
    """Looks up a price by region, key and optional sub key."""

    def get(self, region: str, key: str, sub: str | None = None) -> PriceValue | None:
        """料金を返す。見つからなければ None。"""
        ...


class BundledPriceTable:
    """Price table loaded from the bundled (or a given) prices.json."""

    def __init__(self, data: dict[str, Any]) -> None:
        self._checked_at = str(data["checked_at"])
        self._regions: dict[str, Any] = data["regions"]

    @classmethod
    def from_file(cls, path: Path) -> BundledPriceTable:
        """JSON ファイルから表を読む。"""
        return cls(json.loads(path.read_text(encoding="utf-8")))

    @classmethod
    def load_default(cls) -> BundledPriceTable:
        """パッケージに同梱した表を読む。"""
        text = (
            files("aws_cost_cleaner.pricing")
            .joinpath("data/prices.json")
            .read_text(encoding="utf-8")
        )
        return cls(json.loads(text))

    def get(self, region: str, key: str, sub: str | None = None) -> PriceValue | None:
        """料金を返す。見つからなければ None。"""
        region_table = self._regions.get(region)
        if not isinstance(region_table, dict):
            return None
        entry = region_table.get(key)
        if entry is None:
            return None
        if isinstance(entry, dict):
            if sub is None:
                return None
            value = entry.get(sub)
        else:
            if sub is not None:
                return None
            value = entry
        if isinstance(value, bool) or not isinstance(value, int | float):
            return None
        return PriceValue(usd=float(value), kind="bundled", as_of=self._checked_at)
