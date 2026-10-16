"""Unit tests for config."""

from __future__ import annotations

import dataclasses
import tomllib
from dataclasses import FrozenInstanceError, is_dataclass
from pathlib import Path

import pytest

import aws_cost_cleaner.errors as errors
from aws_cost_cleaner.config import (
    Config,
    ConfigError,
    PricingConfig,
    ScanConfig,
    Thresholds,
    load_config,
)


def test_public_names_importable() -> None:
    """AC-1: 公開名が import 可能で ConfigError が一致する。"""
    from aws_cost_cleaner.config import (
        Config,
        ConfigError,
        PricingConfig,
        ScanConfig,
        Thresholds,
        load_config,
    )

    assert is_dataclass(ScanConfig)
    assert is_dataclass(Thresholds)
    assert is_dataclass(PricingConfig)
    assert is_dataclass(Config)
    assert callable(load_config)
    assert ConfigError is errors.ConfigError


def test_dataclasses_are_frozen() -> None:
    """AC-2: 各 dataclass は frozen であり、指定されたフィールド名と順序を持つ。"""
    config = load_config(None)

    assert is_dataclass(config.scan)
    assert is_dataclass(config.thresholds)
    assert is_dataclass(config.pricing)
    assert is_dataclass(config)

    with pytest.raises(FrozenInstanceError):
        config.scan.resource_explorer_region = "us-east-1"  # type: ignore[misc]

    with pytest.raises(FrozenInstanceError):
        config.thresholds.period_days = 10  # type: ignore[misc]

    with pytest.raises(FrozenInstanceError):
        config.pricing.use_price_list_api = False  # type: ignore[misc]

    with pytest.raises(FrozenInstanceError):
        config.scan = config.scan  # type: ignore[misc]

    scan_fields = [f.name for f in dataclasses.fields(ScanConfig)]
    assert scan_fields == ["resource_explorer_region", "regions", "max_metric_requests"]

    thresholds_fields = [f.name for f in dataclasses.fields(Thresholds)]
    assert thresholds_fields == [
        "period_days",
        "ec2_cpu_max_percent",
        "ec2_network_bytes_max",
        "rds_connections_max",
        "nat_bytes_max",
        "stop_wait_days",
    ]

    pricing_fields = [f.name for f in dataclasses.fields(PricingConfig)]
    assert pricing_fields == ["use_price_list_api", "cache_ttl_hours"]

    config_fields = [f.name for f in dataclasses.fields(Config)]
    assert config_fields == ["scan", "thresholds", "pricing"]


def test_defaults() -> None:
    """AC-3: load_config(None) は仕様で規定された標準値を返す。"""
    cfg = load_config(None)

    assert cfg.scan.resource_explorer_region == "ap-northeast-1"
    assert cfg.scan.regions == ()
    assert cfg.scan.max_metric_requests == 1000
    assert cfg.thresholds.period_days == 7
    assert cfg.thresholds.ec2_cpu_max_percent == 5.0
    assert cfg.thresholds.ec2_network_bytes_max == -1
    assert cfg.thresholds.rds_connections_max == 0
    assert cfg.thresholds.nat_bytes_max == 0
    assert cfg.thresholds.stop_wait_days == 7
    assert cfg.pricing.use_price_list_api is True
    assert cfg.pricing.cache_ttl_hours == 24


def test_regions_tuple_preserves_order(tmp_path: Path) -> None:
    """AC-4: scan.regions は常に tuple で、TOML のリストの順序を保持する。"""
    toml_path = tmp_path / "config.toml"
    toml_path.write_text(
        '[scan]\nregions = ["us-east-1", "ap-northeast-1"]\n',
        encoding="utf-8",
    )
    cfg = load_config(toml_path)
    assert cfg.scan.regions == ("us-east-1", "ap-northeast-1")
    assert isinstance(cfg.scan.regions, tuple)


def test_default_regions_is_empty_tuple() -> None:
    """AC-4: scan.regions の標準値は空タプルである。"""
    cfg = load_config(None)
    assert cfg.scan.regions == ()
    assert isinstance(cfg.scan.regions, tuple)


def test_toml_partial_override(tmp_path: Path) -> None:
    """AC-5: TOML に書いたキーのみが標準値を上書きし、残りは標準値のまま。"""
    toml_path = tmp_path / "config.toml"
    toml_path.write_text(
        "[thresholds]\nperiod_days = 14\n",
        encoding="utf-8",
    )
    cfg = load_config(toml_path)
    default_cfg = load_config(None)

    assert cfg.thresholds.period_days == 14
    assert cfg.scan == default_cfg.scan
    assert cfg.pricing == default_cfg.pricing
    assert (
        cfg.thresholds.ec2_cpu_max_percent
        == default_cfg.thresholds.ec2_cpu_max_percent
    )
    assert (
        cfg.thresholds.ec2_network_bytes_max
        == default_cfg.thresholds.ec2_network_bytes_max
    )
    assert (
        cfg.thresholds.rds_connections_max
        == default_cfg.thresholds.rds_connections_max
    )
    assert cfg.thresholds.nat_bytes_max == default_cfg.thresholds.nat_bytes_max
    assert cfg.thresholds.stop_wait_days == default_cfg.thresholds.stop_wait_days


def test_stop_wait_days_from_actions(tmp_path: Path) -> None:
    """AC-6: stop_wait_days は [actions] 節から読み込む。"""
    toml_path = tmp_path / "config.toml"
    toml_path.write_text(
        "[actions]\nstop_wait_days = 10\n",
        encoding="utf-8",
    )
    cfg = load_config(toml_path)
    assert cfg.thresholds.stop_wait_days == 10


def test_stop_wait_days_in_thresholds_rejected(tmp_path: Path) -> None:
    """AC-6: [thresholds] 節に stop_wait_days を記述すると ConfigError になる。"""
    toml_path = tmp_path / "config.toml"
    toml_path.write_text(
        "[thresholds]\nstop_wait_days = 10\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_config(toml_path)


def test_overrides_beat_toml(tmp_path: Path) -> None:
    """AC-7: overrides の値は TOML の値を上書きする。"""
    toml_path = tmp_path / "config.toml"
    toml_path.write_text(
        "[thresholds]\nperiod_days = 14\n",
        encoding="utf-8",
    )
    cfg = load_config(toml_path, overrides={"period_days": 30})
    assert cfg.thresholds.period_days == 30


def test_overrides_without_path() -> None:
    """AC-7: path=None で overrides を渡したときは標準値を上書きする。"""
    cfg = load_config(None, overrides={"period_days": 30})
    assert cfg.thresholds.period_days == 30


def test_overrides_flat_keys() -> None:
    """AC-8: overrides のキーは節名なしのキー名であり、正しく反映される。"""
    cfg = load_config(
        None,
        overrides={"stop_wait_days": 3, "regions": ["us-west-2"]},
    )
    assert cfg.thresholds.stop_wait_days == 3
    assert cfg.scan.regions == ("us-west-2",)


def test_overrides_none_ignored(tmp_path: Path) -> None:
    """AC-9: overrides で値が None のキーは無視される。"""
    toml_path = tmp_path / "config.toml"
    toml_path.write_text(
        "[thresholds]\nperiod_days = 14\n",
        encoding="utf-8",
    )
    cfg = load_config(toml_path, overrides={"period_days": None})
    assert cfg.thresholds.period_days == 14


@pytest.mark.parametrize(
    ("key", "val"),
    [
        ("perod_days", None),
        ("perod_days", 1),
        ("thresholds.period_days", None),
        ("thresholds.period_days", 1),
    ],
)
def test_unknown_override_key(key: str, val: object) -> None:
    """AC-10: 未知の override キーは値が None でも ConfigError になる。"""
    with pytest.raises(ConfigError):
        load_config(None, overrides={key: val})


@pytest.mark.parametrize(
    ("key", "section", "invalid_val"),
    [
        ("period_days", "thresholds", 0),
        ("period_days", "thresholds", -1),
        ("stop_wait_days", "actions", 0),
        ("stop_wait_days", "actions", -1),
        ("ec2_cpu_max_percent", "thresholds", 101),
        ("ec2_cpu_max_percent", "thresholds", 100.1),
        ("ec2_cpu_max_percent", "thresholds", -0.1),
        ("max_metric_requests", "scan", -1),
        ("ec2_network_bytes_max", "thresholds", -2),
    ],
)
def test_range_violation(
    tmp_path: Path, key: str, section: str, invalid_val: object
) -> None:
    """AC-11: 範囲違反の値は TOML と overrides の両方で ConfigError になる。"""
    with pytest.raises(ConfigError):
        load_config(None, overrides={key: invalid_val})

    toml_path = tmp_path / "config.toml"
    toml_path.write_text(f"[{section}]\n{key} = {invalid_val}\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(toml_path)


@pytest.mark.parametrize(
    ("key", "section", "boundary_val", "expected_val"),
    [
        ("period_days", "thresholds", 1, 1),
        ("stop_wait_days", "actions", 1, 1),
        ("ec2_cpu_max_percent", "thresholds", 0, 0.0),
        ("ec2_cpu_max_percent", "thresholds", 100, 100.0),
        ("max_metric_requests", "scan", 0, 0),
        ("ec2_network_bytes_max", "thresholds", -1, -1),
    ],
)
def test_range_boundaries_accepted(
    tmp_path: Path,
    key: str,
    section: str,
    boundary_val: object,
    expected_val: object,
) -> None:
    """AC-12: 範囲の境界値は受け付けられる。"""
    cfg_override = load_config(None, overrides={key: boundary_val})
    if key in (
        "period_days",
        "stop_wait_days",
        "ec2_cpu_max_percent",
        "ec2_network_bytes_max",
    ):
        assert getattr(cfg_override.thresholds, key) == expected_val
    elif key == "max_metric_requests":
        assert getattr(cfg_override.scan, key) == expected_val

    toml_path = tmp_path / "config.toml"
    toml_path.write_text(f"[{section}]\n{key} = {boundary_val}\n", encoding="utf-8")
    cfg_toml = load_config(toml_path)
    if key in (
        "period_days",
        "stop_wait_days",
        "ec2_cpu_max_percent",
        "ec2_network_bytes_max",
    ):
        assert getattr(cfg_toml.thresholds, key) == expected_val
    elif key == "max_metric_requests":
        assert getattr(cfg_toml.scan, key) == expected_val


@pytest.mark.parametrize(
    ("key", "section", "toml_str", "py_val"),
    [
        ("period_days", "thresholds", '"7"', "7"),
        ("period_days", "thresholds", "7.5", 7.5),
        ("use_price_list_api", "pricing", '"true"', "true"),
        ("use_price_list_api", "pricing", "1", 1),
        ("regions", "scan", '"us-east-1"', "us-east-1"),
        ("regions", "scan", "[1]", [1]),
        ("resource_explorer_region", "scan", "1", 1),
    ],
)
def test_type_violation(
    tmp_path: Path, key: str, section: str, toml_str: str, py_val: object
) -> None:
    """AC-13: 型違反の値は TOML と overrides の両方で ConfigError になる。"""
    with pytest.raises(ConfigError):
        load_config(None, overrides={key: py_val})

    toml_path = tmp_path / "config.toml"
    toml_path.write_text(f"[{section}]\n{key} = {toml_str}\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(toml_path)


@pytest.mark.parametrize(
    ("key", "section"),
    [
        ("max_metric_requests", "scan"),
        ("period_days", "thresholds"),
        ("ec2_network_bytes_max", "thresholds"),
        ("rds_connections_max", "thresholds"),
        ("nat_bytes_max", "thresholds"),
        ("stop_wait_days", "actions"),
        ("cache_ttl_hours", "pricing"),
    ],
)
def test_bool_for_int_rejected(tmp_path: Path, key: str, section: str) -> None:
    """AC-14: 整数フィールドに bool を渡すと ConfigError になる。"""
    with pytest.raises(ConfigError):
        load_config(None, overrides={key: True})

    with pytest.raises(ConfigError):
        load_config(None, overrides={key: False})

    toml_path = tmp_path / "config.toml"
    toml_path.write_text(f"[{section}]\n{key} = true\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(toml_path)


def test_cpu_int_becomes_float(tmp_path: Path) -> None:
    """AC-15: ec2_cpu_max_percent に整数を渡すと float に変換される。"""
    cfg1 = load_config(None, overrides={"ec2_cpu_max_percent": 10})
    assert cfg1.thresholds.ec2_cpu_max_percent == 10.0
    assert isinstance(cfg1.thresholds.ec2_cpu_max_percent, float)

    toml_path = tmp_path / "config.toml"
    toml_path.write_text(
        "[thresholds]\nec2_cpu_max_percent = 10\n", encoding="utf-8"
    )
    cfg2 = load_config(toml_path)
    assert cfg2.thresholds.ec2_cpu_max_percent == 10.0
    assert isinstance(cfg2.thresholds.ec2_cpu_max_percent, float)


def test_cpu_bool_rejected(tmp_path: Path) -> None:
    """AC-15: ec2_cpu_max_percent に bool を渡すと ConfigError になる。"""
    with pytest.raises(ConfigError):
        load_config(None, overrides={"ec2_cpu_max_percent": True})

    toml_path = tmp_path / "config.toml"
    toml_path.write_text(
        "[thresholds]\nec2_cpu_max_percent = true\n", encoding="utf-8"
    )
    with pytest.raises(ConfigError):
        load_config(toml_path)


def test_unknown_key_in_section(tmp_path: Path) -> None:
    """AC-16: 節内に未知のキーがあると ConfigError になる。"""
    toml_path = tmp_path / "config.toml"
    toml_path.write_text("[thresholds]\nperod_days = 7\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(toml_path)


def test_unknown_section(tmp_path: Path) -> None:
    """AC-16: 未知の節があると ConfigError になる。"""
    toml_path = tmp_path / "config.toml"
    toml_path.write_text("[foo]\nbar = 1\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(toml_path)


def test_top_level_key(tmp_path: Path) -> None:
    """AC-16: トップレベルにキーが書かれていると ConfigError になる。"""
    toml_path = tmp_path / "config.toml"
    toml_path.write_text("period_days = 7\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(toml_path)


def test_missing_file(tmp_path: Path) -> None:
    """AC-17: 存在しないパスを指定した場合は ConfigError になる。"""
    missing = tmp_path / "nonexistent.toml"
    with pytest.raises(ConfigError):
        load_config(missing)


def test_invalid_toml(tmp_path: Path) -> None:
    """AC-17: TOML 構文が壊れている場合は ConfigError になる。"""
    bad_toml = tmp_path / "bad.toml"
    bad_toml.write_text("[scan\nfoo = ", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(bad_toml)


def test_section_not_table(tmp_path: Path) -> None:
    """AC-17: 節がテーブルでない場合は ConfigError になる。"""
    toml_path = tmp_path / "config.toml"
    toml_path.write_text("scan = 1\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(toml_path)


def test_example_equals_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC-18: example.toml の読み込み結果は load_config(None) と等しい。"""
    repo_root = Path(__file__).resolve().parents[2]
    monkeypatch.chdir(repo_root)

    cfg_from_file = load_config(Path("aws-cost-cleaner.example.toml"))
    cfg_defaults = load_config(None)

    assert cfg_from_file == cfg_defaults


def test_example_file_structure() -> None:
    """AC-19: example.toml は 4 つの節と 11 キーを持ち、コメントを含む。"""
    repo_root = Path(__file__).resolve().parents[2]
    example_path = repo_root / "aws-cost-cleaner.example.toml"
    assert example_path.is_file()

    text = example_path.read_text(encoding="utf-8")
    assert "#" in text

    with example_path.open("rb") as f:
        data = tomllib.load(f)

    expected_sections = {"scan", "thresholds", "actions", "pricing"}
    assert set(data.keys()) == expected_sections

    all_keys: set[str] = set()
    for section_dict in data.values():
        assert isinstance(section_dict, dict)
        all_keys.update(section_dict.keys())

    expected_keys = {
        "resource_explorer_region",
        "regions",
        "max_metric_requests",
        "period_days",
        "ec2_cpu_max_percent",
        "ec2_network_bytes_max",
        "rds_connections_max",
        "nat_bytes_max",
        "stop_wait_days",
        "use_price_list_api",
        "cache_ttl_hours",
    }
    assert all_keys == expected_keys
