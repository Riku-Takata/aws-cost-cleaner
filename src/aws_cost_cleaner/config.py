"""Configuration loading and validation."""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from aws_cost_cleaner.errors import ConfigError

_SECTION_KEYS: dict[str, tuple[str, ...]] = {
    "scan": ("resource_explorer_region", "regions", "max_metric_requests"),
    "thresholds": (
        "period_days",
        "ec2_cpu_max_percent",
        "ec2_network_bytes_max",
        "rds_connections_max",
        "nat_bytes_max",
    ),
    "actions": ("stop_wait_days",),
    "pricing": ("use_price_list_api", "cache_ttl_hours"),
}

_DEFAULTS: dict[str, object] = {
    "resource_explorer_region": "ap-northeast-1",
    "regions": (),
    "max_metric_requests": 1000,
    "period_days": 7,
    "ec2_cpu_max_percent": 5.0,
    "ec2_network_bytes_max": -1,
    "rds_connections_max": 0,
    "nat_bytes_max": 0,
    "stop_wait_days": 7,
    "use_price_list_api": True,
    "cache_ttl_hours": 24,
}

_STR_KEYS = {"resource_explorer_region"}
_STR_TUPLE_KEYS = {"regions"}
_BOOL_KEYS = {"use_price_list_api"}
_FLOAT_KEYS = {"ec2_cpu_max_percent"}
_INT_KEYS = {
    "max_metric_requests",
    "period_days",
    "ec2_network_bytes_max",
    "rds_connections_max",
    "nat_bytes_max",
    "stop_wait_days",
    "cache_ttl_hours",
}


@dataclass(frozen=True)
class ScanConfig:
    resource_explorer_region: str
    regions: tuple[str, ...]
    max_metric_requests: int


@dataclass(frozen=True)
class Thresholds:
    period_days: int
    ec2_cpu_max_percent: float
    ec2_network_bytes_max: int
    rds_connections_max: int
    nat_bytes_max: int
    stop_wait_days: int


@dataclass(frozen=True)
class PricingConfig:
    use_price_list_api: bool
    cache_ttl_hours: int


@dataclass(frozen=True)
class Config:
    scan: ScanConfig
    thresholds: Thresholds
    pricing: PricingConfig


def _read_toml(path: Path) -> dict[str, object]:
    """TOML ファイルを読み込んで辞書を返す。"""
    try:
        with path.open("rb") as f:
            return tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise ConfigError(f"設定ファイルを読み込めません: {path} ({e})") from e


def _flatten_toml(data: Mapping[str, object]) -> dict[str, object]:
    """TOML データの節を検証し、平らな辞書にして返す。"""
    result: dict[str, object] = {}
    for section, content in data.items():
        if section not in _SECTION_KEYS:
            raise ConfigError(f"未知の節またはキーです: {section!r}")
        if not isinstance(content, dict):
            type_name = type(content).__name__
            raise ConfigError(
                f"節 {section!r} の値はテーブルである必要があります: {type_name}"
            )
        allowed_keys = _SECTION_KEYS[section]
        for key, value in content.items():
            if key not in allowed_keys:
                raise ConfigError(f"節 {section!r} 内の未知のキーです: {key!r}")
            result[key] = value
    return result


def _validate(key: str, value: object) -> object:
    """設定値の型と範囲を検証して正規化された値を返す。"""
    if key in _STR_KEYS:
        if not isinstance(value, str):
            raise ConfigError(f"{key} は文字列である必要があります: {value!r}")
        return value

    if key in _STR_TUPLE_KEYS:
        if not isinstance(value, (list, tuple)):
            raise ConfigError(
                f"{key} は文字列のリストまたはタプルである必要があります: {value!r}"
            )
        for item in value:
            if not isinstance(item, str):
                raise ConfigError(
                    f"{key} の要素は文字列である必要があります: {item!r}"
                )
        return tuple(value)

    if key in _BOOL_KEYS:
        if not isinstance(value, bool):
            raise ConfigError(f"{key} は真偽値である必要があります: {value!r}")
        return value

    if key in _FLOAT_KEYS:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ConfigError(f"{key} は数値である必要があります: {value!r}")
        float_val = float(value)
        if not (0.0 <= float_val <= 100.0):
            raise ConfigError(
                f"{key} は 0 以上 100 以下の範囲である必要があります: {value!r}"
            )
        return float_val

    if key in _INT_KEYS:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ConfigError(f"{key} は整数である必要があります: {value!r}")
        if key in ("period_days", "stop_wait_days") and value < 1:
            raise ConfigError(f"{key} は 1 以上である必要があります: {value!r}")
        if key == "max_metric_requests" and value < 0:
            raise ConfigError(f"{key} は 0 以上である必要があります: {value!r}")
        if key == "ec2_network_bytes_max" and value < -1:
            raise ConfigError(f"{key} は -1 以上である必要があります: {value!r}")
        return value

    raise ConfigError(f"未知の設定キーです: {key!r}")


def load_config(
    path: Path | None, overrides: Mapping[str, object] | None = None
) -> Config:
    """設定ファイルを読み込み、overrides を適用した Config を返す。"""
    values = dict(_DEFAULTS)
    if path is not None:
        values.update(_flatten_toml(_read_toml(path)))
    if overrides is not None:
        for key, value in overrides.items():
            if key not in _DEFAULTS:
                raise ConfigError(f"未知の override キーです: {key!r}")
            if value is not None:
                values[key] = value

    validated: dict[str, object] = {}
    for key, value in values.items():
        validated[key] = _validate(key, value)

    scan = ScanConfig(
        resource_explorer_region=cast(str, validated["resource_explorer_region"]),
        regions=cast(tuple[str, ...], validated["regions"]),
        max_metric_requests=cast(int, validated["max_metric_requests"]),
    )
    thresholds = Thresholds(
        period_days=cast(int, validated["period_days"]),
        ec2_cpu_max_percent=cast(float, validated["ec2_cpu_max_percent"]),
        ec2_network_bytes_max=cast(int, validated["ec2_network_bytes_max"]),
        rds_connections_max=cast(int, validated["rds_connections_max"]),
        nat_bytes_max=cast(int, validated["nat_bytes_max"]),
        stop_wait_days=cast(int, validated["stop_wait_days"]),
    )
    pricing = PricingConfig(
        use_price_list_api=cast(bool, validated["use_price_list_api"]),
        cache_ttl_hours=cast(int, validated["cache_ttl_hours"]),
    )
    return Config(scan=scan, thresholds=thresholds, pricing=pricing)
