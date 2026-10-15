"""Unit tests for catalog."""

from __future__ import annotations

import ast
import importlib
import sys
from pathlib import Path

import pytest

import aws_cost_cleaner.catalog
from aws_cost_cleaner.catalog import (
    ALLOWED,
    RESOURCE_TYPES,
    is_allowed,
    service_label,
)
from aws_cost_cleaner.models import ResourceType


def test_public_names_importable() -> None:
    """AC-1: 公開定数と関数が aws_cost_cleaner.catalog から import できる。"""
    from aws_cost_cleaner.catalog import (
        ALLOWED,
        RESOURCE_TYPES,
        is_allowed,
        service_label,
    )

    assert ALLOWED is not None
    assert RESOURCE_TYPES is not None
    assert callable(is_allowed)
    assert callable(service_label)


def test_allowed_is_frozenset_of_eight() -> None:
    """AC-2: ALLOWED は frozenset で要素数が 8 である。"""
    assert isinstance(ALLOWED, frozenset)
    assert len(ALLOWED) == 8


def test_allowed_matches_design_table() -> None:
    """AC-3: ALLOWED は設計書データ 2 の 8 組の集合と一致する。"""
    expected = {
        ("ebs_volume", "Delete", "ebs-unattached"),
        ("elastic_ip", "Delete", "eip-unassociated"),
        ("nat_gateway", "Delete", "nat-idle"),
        ("load_balancer", "Delete", "elb-no-targets"),
        ("ec2_instance", "Stop", "ec2-low-cpu"),
        ("ec2_instance", "Delete", "ec2-stopped-elapsed"),
        ("rds_instance", "Stop", "rds-no-connections"),
        ("rds_instance", "Delete", "rds-stopped-elapsed"),
    }
    assert ALLOWED == expected


def test_ec2_delete_low_cpu_not_allowed() -> None:
    """AC-4: is_allowed('ec2_instance', 'Delete', 'ec2-low-cpu') は False を返す。"""
    assert is_allowed("ec2_instance", "Delete", "ec2-low-cpu") is False


def test_ec2_delete_stopped_elapsed_allowed() -> None:
    """AC-5: ec2-stopped-elapsed の Delete は True を返す。"""
    assert is_allowed("ec2_instance", "Delete", "ec2-stopped-elapsed") is True


@pytest.mark.parametrize(
    ("resource_type", "action", "reason_code"),
    [
        ("ebs_volume", "Delete", "ebs-unattached"),
        ("elastic_ip", "Delete", "eip-unassociated"),
        ("nat_gateway", "Delete", "nat-idle"),
        ("load_balancer", "Delete", "elb-no-targets"),
        ("ec2_instance", "Stop", "ec2-low-cpu"),
        ("ec2_instance", "Delete", "ec2-stopped-elapsed"),
        ("rds_instance", "Stop", "rds-no-connections"),
        ("rds_instance", "Delete", "rds-stopped-elapsed"),
    ],
)
def test_is_allowed_true_for_all_rows(
    resource_type: str, action: str, reason_code: str
) -> None:
    """AC-6: 許可表の 8 組それぞれについて is_allowed が True を返す。"""
    assert is_allowed(resource_type, action, reason_code) is True


@pytest.mark.parametrize(
    ("resource_type", "action", "reason_code"),
    [
        ("rds_instance", "Delete", "rds-no-connections"),
        ("ebs_volume", "Stop", "ebs-unattached"),
        ("snapshot", "Delete", "ebs-unattached"),
    ],
)
def test_is_allowed_false_for_other_combinations(
    resource_type: str, action: str, reason_code: str
) -> None:
    """AC-6: 許可表にない組では is_allowed が False を返す。"""
    assert is_allowed(resource_type, action, reason_code) is False


def test_resource_types_order() -> None:
    """AC-7: RESOURCE_TYPES の並び順と型が仕様どおりである。"""
    assert RESOURCE_TYPES == (
        "ebs_volume",
        "elastic_ip",
        "nat_gateway",
        "load_balancer",
        "ec2_instance",
        "rds_instance",
    )
    assert type(RESOURCE_TYPES) is tuple


def test_service_label_alb() -> None:
    """AC-8: load_balancer の application は ELB (ALB) を返す。"""
    assert service_label("load_balancer", "application") == "ELB (ALB)"


def test_service_label_nlb() -> None:
    """AC-9: load_balancer の network は ELB (NLB) を返す。"""
    assert service_label("load_balancer", "network") == "ELB (NLB)"


@pytest.mark.parametrize(
    ("resource_type", "expected"),
    [
        ("ebs_volume", "EC2 (EBS)"),
        ("elastic_ip", "VPC (EIP)"),
        ("nat_gateway", "VPC (NAT)"),
        ("ec2_instance", "EC2"),
        ("rds_instance", "RDS"),
    ],
)
def test_service_label_non_lb(resource_type: ResourceType, expected: str) -> None:
    """AC-10: load_balancer 以外の 5 種類について正しい表示名を返す。"""
    assert service_label(resource_type) == expected
    assert service_label(resource_type, "application") == expected


def test_service_label_lb_invalid() -> None:
    """AC-11: load_balancer で lb_type が不正または省略時は ValueError を出す。"""
    with pytest.raises(ValueError):
        service_label("load_balancer")
    with pytest.raises(ValueError):
        service_label("load_balancer", None)
    with pytest.raises(ValueError):
        service_label("load_balancer", "gateway")


def test_service_label_unknown_resource_type() -> None:
    """未知の resource_type の場合は ValueError を出す。"""
    with pytest.raises(ValueError):
        service_label("unknown_type")  # type: ignore[arg-type]


def test_catalog_does_not_import_boto() -> None:
    """AC-12: catalog.py のソースに boto3 と botocore の import 文がない。"""
    catalog_path = Path(aws_cost_cleaner.catalog.__file__)
    source = catalog_path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith(
                    ("boto3", "botocore")
                ), f"Found forbidden import: {alias.name}"
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                assert not node.module.startswith(
                    ("boto3", "botocore")
                ), f"Found forbidden import from: {node.module}"


def test_import_without_boto3(monkeypatch: pytest.MonkeyPatch) -> None:
    """boto3 と botocore が import 不可でも catalog モジュールを import できる。"""
    monkeypatch.setitem(sys.modules, "boto3", None)
    monkeypatch.setitem(sys.modules, "botocore", None)
    monkeypatch.delitem(sys.modules, "aws_cost_cleaner.catalog", raising=False)

    mod = importlib.import_module("aws_cost_cleaner.catalog")
    assert mod.RESOURCE_TYPES == RESOURCE_TYPES
