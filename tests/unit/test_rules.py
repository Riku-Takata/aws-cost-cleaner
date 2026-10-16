"""Unit tests for rules.py."""

from __future__ import annotations

import ast
import dataclasses
import importlib
import os
import sys
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

import aws_cost_cleaner.rules
from aws_cost_cleaner import catalog
from aws_cost_cleaner.config import Thresholds
from aws_cost_cleaner.models import Finding, Resource, ResourceType, Skipped
from aws_cost_cleaner.rules import evaluate, has_elapsed

NOW = datetime(2026, 10, 15, 3, 0, 0, tzinfo=UTC)

DEFAULT = Thresholds(
    period_days=7,
    ec2_cpu_max_percent=5.0,
    ec2_network_bytes_max=-1,
    rds_connections_max=0,
    nat_bytes_max=0,
    stop_wait_days=7,
)


def _res(
    resource_type: ResourceType,
    state: str = "available",
    created_at: datetime | None = None,
    attrs: Mapping[str, Any] | None = None,
    tags: Mapping[str, str] | None = None,
    resource_id: str = "r-1",
    region: str = "ap-northeast-1",
    arn: str | None = "arn:aws:dummy:ap-northeast-1:123456789012:res/1",
    name: str | None = "res-name",
) -> Resource:
    return Resource(
        resource_type=resource_type,
        region=region,
        resource_id=resource_id,
        arn=arn,
        name=name,
        tags=tags if tags is not None else {},
        state=state,
        created_at=created_at,
        attrs=attrs if attrs is not None else {},
    )


def test_importable_and_no_boto3() -> None:
    """AC-1: has_elapsed と evaluate の import と boto3/botocore 不使用。"""
    assert callable(has_elapsed)
    assert callable(evaluate)

    rules_path = Path(aws_cost_cleaner.rules.__file__)
    product_dir = os.environ.get("HARNESS_PRODUCT_DIR")
    if product_dir:
        candidate = Path(product_dir) / "src" / "aws_cost_cleaner" / "rules.py"
        if candidate.exists():
            rules_path = candidate

    source = rules_path.read_text(encoding="utf-8")
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
    """boto3 と botocore が import 不可でも rules モジュールを import できる。"""
    monkeypatch.setitem(sys.modules, "boto3", None)
    monkeypatch.setitem(sys.modules, "botocore", None)
    monkeypatch.delitem(sys.modules, "aws_cost_cleaner.rules", raising=False)

    mod = importlib.import_module("aws_cost_cleaner.rules")
    assert callable(mod.has_elapsed)
    assert callable(mod.evaluate)


@pytest.mark.parametrize(
    ("now", "since", "days", "expected"),
    [
        (
            datetime(2026, 10, 15, 3, 0, 0, tzinfo=UTC),
            datetime(2026, 10, 8, 3, 0, 0, tzinfo=UTC),
            7,
            True,
        ),
        (
            datetime(2026, 10, 15, 2, 59, 59, tzinfo=UTC),
            datetime(2026, 10, 8, 3, 0, 0, tzinfo=UTC),
            7,
            False,
        ),
    ],
)
def test_has_elapsed_boundary(
    now: datetime, since: datetime, days: int, expected: bool
) -> None:
    """AC-2, AC-3: has_elapsed の境界値判定。"""
    assert has_elapsed(now=now, since=since, days=days) is expected


def test_ec2_cpu_boundary() -> None:
    """AC-4, AC-5: running の EC2 で cpu_max_percent の境界値判定。"""
    res = _res(
        "ec2_instance",
        state="running",
        created_at=NOW - timedelta(days=7),
    )

    f = evaluate(res, {"cpu_max_percent": 4.99}, DEFAULT, NOW)
    assert isinstance(f, Finding)
    assert f.recommended_action == "Stop"
    assert f.reason_code == "ec2-low-cpu"

    s = evaluate(res, {"cpu_max_percent": 5.0}, DEFAULT, NOW)
    assert isinstance(s, Skipped)
    assert s.skip_reason == "active"


def test_rds_connections_boundary() -> None:
    """AC-6, AC-7: available の RDS で db_connections_max の境界値判定。"""
    res = _res(
        "rds_instance",
        state="available",
        created_at=NOW - timedelta(days=7),
    )

    f = evaluate(
        res,
        {"db_connections_max": 0.0, "rds_cpu_max_percent": 1.0},
        DEFAULT,
        NOW,
    )
    assert isinstance(f, Finding)
    assert f.recommended_action == "Stop"
    assert f.reason_code == "rds-no-connections"

    s = evaluate(
        res,
        {"db_connections_max": 1.0, "rds_cpu_max_percent": 1.0},
        DEFAULT,
        NOW,
    )
    assert isinstance(s, Skipped)
    assert s.skip_reason == "active"


def test_rds_cpu_none_ignored() -> None:
    """AC-8: rds_cpu_max_percent=None でも db_connections_max=0.0 なら Finding。"""
    res = _res(
        "rds_instance",
        state="available",
        created_at=NOW - timedelta(days=7),
    )
    f = evaluate(
        res,
        {"db_connections_max": 0.0, "rds_cpu_max_percent": None},
        DEFAULT,
        NOW,
    )
    assert isinstance(f, Finding)
    assert f.recommended_action == "Stop"
    assert f.reason_code == "rds-no-connections"


def test_ec2_period_boundary() -> None:
    """AC-9, AC-10, AC-11: running の EC2 で経過日数の境界値判定および判定優先順位。"""
    res_exact = _res(
        "ec2_instance",
        state="running",
        created_at=NOW - timedelta(days=7),
    )
    f = evaluate(res_exact, {"cpu_max_percent": 1.0}, DEFAULT, NOW)
    assert isinstance(f, Finding)
    assert f.recommended_action == "Stop"
    assert f.reason_code == "ec2-low-cpu"

    res_too_new = _res(
        "ec2_instance",
        state="running",
        created_at=NOW - (timedelta(days=7) - timedelta(seconds=1)),
    )
    s1 = evaluate(res_too_new, {"cpu_max_percent": 1.0}, DEFAULT, NOW)
    assert isinstance(s1, Skipped)
    assert s1.skip_reason == "too_new"

    s2 = evaluate(res_too_new, {"cpu_max_percent": None}, DEFAULT, NOW)
    assert isinstance(s2, Skipped)
    assert s2.skip_reason == "too_new"


def test_ec2_no_metrics() -> None:
    """AC-12: cpu_max_percent が None またはキーなしのときは no_metrics。"""
    res = _res(
        "ec2_instance",
        state="running",
        created_at=NOW - timedelta(days=7),
    )
    s1 = evaluate(res, {"cpu_max_percent": None}, DEFAULT, NOW)
    assert isinstance(s1, Skipped)
    assert s1.skip_reason == "no_metrics"

    s2 = evaluate(res, {}, DEFAULT, NOW)
    assert isinstance(s2, Skipped)
    assert s2.skip_reason == "no_metrics"


def test_ec2_network_disabled() -> None:
    """AC-13: ec2_network_bytes_max=-1 のとき network_bytes_total は見ない。"""
    res = _res(
        "ec2_instance",
        state="running",
        created_at=NOW - timedelta(days=7),
    )
    f1 = evaluate(
        res,
        {"cpu_max_percent": 1.0, "network_bytes_total": 10.0},
        DEFAULT,
        NOW,
    )
    assert isinstance(f1, Finding)
    assert f1.recommended_action == "Stop"
    assert f1.reason_code == "ec2-low-cpu"

    f2 = evaluate(
        res,
        {"cpu_max_percent": 1.0, "network_bytes_total": None},
        DEFAULT,
        NOW,
    )
    assert isinstance(f2, Finding)
    assert f2.recommended_action == "Stop"
    assert f2.reason_code == "ec2-low-cpu"


def test_ec2_network_enabled() -> None:
    """AC-14: ec2_network_bytes_max=0 のときの network_bytes_total 判定。"""
    thresholds = dataclasses.replace(DEFAULT, ec2_network_bytes_max=0)
    res = _res(
        "ec2_instance",
        state="running",
        created_at=NOW - timedelta(days=7),
    )

    s_active = evaluate(
        res,
        {"cpu_max_percent": 1.0, "network_bytes_total": 10.0},
        thresholds,
        NOW,
    )
    assert isinstance(s_active, Skipped)
    assert s_active.skip_reason == "active"

    f = evaluate(
        res,
        {"cpu_max_percent": 1.0, "network_bytes_total": 0.0},
        thresholds,
        NOW,
    )
    assert isinstance(f, Finding)
    assert f.recommended_action == "Stop"
    assert f.reason_code == "ec2-low-cpu"

    s_no_metrics = evaluate(
        res,
        {"cpu_max_percent": 1.0, "network_bytes_total": None},
        thresholds,
        NOW,
    )
    assert isinstance(s_no_metrics, Skipped)
    assert s_no_metrics.skip_reason == "no_metrics"


def test_rds_no_metrics_and_too_new() -> None:
    """AC-15: available の RDS で db_connections_max=None および too_new 判定。"""
    res_old = _res(
        "rds_instance",
        state="available",
        created_at=NOW - timedelta(days=7),
    )
    s1 = evaluate(res_old, {"db_connections_max": None}, DEFAULT, NOW)
    assert isinstance(s1, Skipped)
    assert s1.skip_reason == "no_metrics"

    res_too_new = _res(
        "rds_instance",
        state="available",
        created_at=NOW - (timedelta(days=7) - timedelta(seconds=1)),
    )
    s2 = evaluate(res_too_new, {"db_connections_max": 0.0}, DEFAULT, NOW)
    assert isinstance(s2, Skipped)
    assert s2.skip_reason == "too_new"

    s3 = evaluate(res_too_new, {"db_connections_max": None}, DEFAULT, NOW)
    assert isinstance(s3, Skipped)
    assert s3.skip_reason == "too_new"


def test_ebs_available() -> None:
    """AC-16: 状態が available の EBS は created_at によらず Finding。"""
    res_none = _res("ebs_volume", state="available", created_at=None)
    f1 = evaluate(res_none, {}, DEFAULT, NOW)
    assert isinstance(f1, Finding)
    assert f1.recommended_action == "Delete"
    assert f1.reason_code == "ebs-unattached"

    res_now = _res("ebs_volume", state="available", created_at=NOW)
    f2 = evaluate(res_now, {}, DEFAULT, NOW)
    assert isinstance(f2, Finding)
    assert f2.recommended_action == "Delete"
    assert f2.reason_code == "ebs-unattached"

    res_in_use = _res("ebs_volume", state="in-use", created_at=None)
    s = evaluate(res_in_use, {}, DEFAULT, NOW)
    assert isinstance(s, Skipped)
    assert s.skip_reason == "active"


def test_eip_association() -> None:
    """AC-17: EIP で AssociationId の有無による判定。"""
    res_unassociated = _res(
        "elastic_ip",
        attrs={},
        created_at=None,
    )
    f = evaluate(res_unassociated, {}, DEFAULT, NOW)
    assert isinstance(f, Finding)
    assert f.recommended_action == "Delete"
    assert f.reason_code == "eip-unassociated"

    res_associated = _res(
        "elastic_ip",
        attrs={"AssociationId": "eipassoc-1"},
        created_at=None,
    )
    s = evaluate(res_associated, {}, DEFAULT, NOW)
    assert isinstance(s, Skipped)
    assert s.skip_reason == "active"


def test_elb_targets() -> None:
    """AC-18: ELB で registered_target_count による判定。"""
    res_zero = _res(
        "load_balancer",
        attrs={"Type": "application", "registered_target_count": 0},
        created_at=NOW - timedelta(days=1),
    )
    f = evaluate(res_zero, {}, DEFAULT, NOW)
    assert isinstance(f, Finding)
    assert f.recommended_action == "Delete"
    assert f.reason_code == "elb-no-targets"

    res_one = _res(
        "load_balancer",
        attrs={"Type": "application", "registered_target_count": 1},
        created_at=NOW - timedelta(days=1),
    )
    s = evaluate(res_one, {}, DEFAULT, NOW)
    assert isinstance(s, Skipped)
    assert s.skip_reason == "active"


def test_nat() -> None:
    """AC-19: NAT ゲートウェイの判定順と結果。"""
    res_old = _res(
        "nat_gateway",
        state="available",
        created_at=NOW - timedelta(days=7),
    )
    f = evaluate(res_old, {"nat_bytes_total": 0.0}, DEFAULT, NOW)
    assert isinstance(f, Finding)
    assert f.recommended_action == "Delete"
    assert f.reason_code == "nat-idle"

    s_active = evaluate(res_old, {"nat_bytes_total": 1.0}, DEFAULT, NOW)
    assert isinstance(s_active, Skipped)
    assert s_active.skip_reason == "active"

    s_no_metrics = evaluate(res_old, {"nat_bytes_total": None}, DEFAULT, NOW)
    assert isinstance(s_no_metrics, Skipped)
    assert s_no_metrics.skip_reason == "no_metrics"

    res_too_new = _res(
        "nat_gateway",
        state="available",
        created_at=NOW - (timedelta(days=7) - timedelta(seconds=1)),
    )
    s_too_new = evaluate(res_too_new, {"nat_bytes_total": 0.0}, DEFAULT, NOW)
    assert isinstance(s_too_new, Skipped)
    assert s_too_new.skip_reason == "too_new"


def test_finding_fields() -> None:
    """AC-20: Finding の各フィールド値の検証。"""
    res_ec2 = _res(
        "ec2_instance",
        state="running",
        created_at=NOW - timedelta(days=7),
        resource_id="i-test",
        region="us-east-1",
        arn="arn:aws:ec2:us-east-1:123456789012:instance/i-test",
        name="web-server",
    )
    metrics_ec2 = {"cpu_max_percent": 2.5}
    f_ec2 = evaluate(res_ec2, metrics_ec2, DEFAULT, NOW)
    assert isinstance(f_ec2, Finding)
    assert f_ec2.resource_type == "ec2_instance"
    assert f_ec2.region == "us-east-1"
    assert f_ec2.resource_id == "i-test"
    assert f_ec2.arn == "arn:aws:ec2:us-east-1:123456789012:instance/i-test"
    assert f_ec2.name == "web-server"
    assert f_ec2.service_label == "EC2"
    assert f_ec2.estimated_monthly_usd is None
    assert f_ec2.price_source is None
    assert isinstance(f_ec2.metrics, dict)
    assert f_ec2.metrics == metrics_ec2

    res_elb = _res(
        "load_balancer",
        attrs={"Type": "application", "registered_target_count": 0},
        resource_id="alb-1",
        region="ap-northeast-1",
        arn="arn:aws:elasticloadbalancing:ap-northeast-1:123456789012:loadbalancer/app/alb-1/1",
        name="alb-test",
    )
    f_elb = evaluate(res_elb, {}, DEFAULT, NOW)
    assert isinstance(f_elb, Finding)
    assert f_elb.service_label == "ELB (ALB)"
    assert f_elb.resource_type == "load_balancer"
    assert f_elb.region == "ap-northeast-1"
    assert f_elb.resource_id == "alb-1"
    assert f_elb.arn == res_elb.arn
    assert f_elb.name == "alb-test"
    assert f_elb.estimated_monthly_usd is None
    assert f_elb.price_source is None
    assert f_elb.metrics == {}


def test_skipped_fields() -> None:
    """AC-21: Skipped の各フィールド値の検証。"""
    res = _res(
        "ec2_instance",
        state="stopped",
        resource_id="i-stopped",
        region="ap-northeast-1",
    )
    s = evaluate(res, {}, DEFAULT, NOW)
    assert isinstance(s, Skipped)
    assert s.resource_type == "ec2_instance"
    assert s.region == "ap-northeast-1"
    assert s.resource_id == "i-stopped"
    assert s.skip_reason == "active"


@pytest.mark.parametrize(
    ("resource", "metrics", "expected_summary"),
    [
        (
            _res("ebs_volume", state="available"),
            {},
            "available (未接続)",
        ),
        (
            _res(
                "ec2_instance",
                state="running",
                created_at=NOW - timedelta(days=7),
            ),
            {"cpu_max_percent": 1.2},
            "running (最大CPU 1.2% / 7日)",
        ),
        (
            _res(
                "nat_gateway",
                state="available",
                created_at=NOW - timedelta(days=7),
            ),
            {"nat_bytes_total": 0.0},
            "通信量 0 Byte (7日間)",
        ),
        (
            _res(
                "load_balancer",
                attrs={"Type": "application", "registered_target_count": 0},
            ),
            {},
            "登録ターゲット 0 件",
        ),
        (
            _res(
                "rds_instance",
                state="available",
                created_at=NOW - timedelta(days=7),
            ),
            {"db_connections_max": 0.0},
            "available (最大接続数 0 / 7日)",
        ),
    ],
)
def test_state_summary(
    resource: Resource,
    metrics: Mapping[str, float | None],
    expected_summary: str,
) -> None:
    """AC-22: 標準値のしきい値での state_summary 書式検証。"""
    res = evaluate(resource, metrics, DEFAULT, NOW)
    assert isinstance(res, Finding)
    assert res.state_summary == expected_summary


def test_reason_text() -> None:
    """AC-23: reason_text と EIP の state_summary の文面検証。"""
    # EBS
    f_ebs = evaluate(_res("ebs_volume", state="available"), {}, DEFAULT, NOW)
    assert isinstance(f_ebs, Finding)
    assert f_ebs.reason_text == "どのEC2にもアタッチされておらず課金のみ発生"

    # NAT
    f_nat = evaluate(
        _res(
            "nat_gateway",
            state="available",
            created_at=NOW - timedelta(days=7),
        ),
        {"nat_bytes_total": 0.0},
        DEFAULT,
        NOW,
    )
    assert isinstance(f_nat, Finding)
    assert f_nat.reason_text == "直近1週間トラフィックなし。高額課金原因"

    # EC2
    f_ec2 = evaluate(
        _res(
            "ec2_instance",
            state="running",
            created_at=NOW - timedelta(days=7),
        ),
        {"cpu_max_percent": 1.0},
        DEFAULT,
        NOW,
    )
    assert isinstance(f_ec2, Finding)
    assert f_ec2.reason_text == "放置検証機の可能性大。まずは停止を推奨"

    # EIP
    f_eip = evaluate(_res("elastic_ip", attrs={}), {}, DEFAULT, NOW)
    assert isinstance(f_eip, Finding)
    assert isinstance(f_eip.reason_text, str) and len(f_eip.reason_text) > 0
    assert isinstance(f_eip.state_summary, str) and len(f_eip.state_summary) > 0

    # ELB
    f_elb = evaluate(
        _res(
            "load_balancer",
            attrs={"Type": "application", "registered_target_count": 0},
        ),
        {},
        DEFAULT,
        NOW,
    )
    assert isinstance(f_elb, Finding)
    assert isinstance(f_elb.reason_text, str) and len(f_elb.reason_text) > 0

    # RDS
    f_rds = evaluate(
        _res(
            "rds_instance",
            state="available",
            created_at=NOW - timedelta(days=7),
        ),
        {"db_connections_max": 0.0},
        DEFAULT,
        NOW,
    )
    assert isinstance(f_rds, Finding)
    assert isinstance(f_rds.reason_text, str) and len(f_rds.reason_text) > 0


def test_findings_allowed() -> None:
    """AC-24: evaluate が返す 6 種の Finding が catalog.is_allowed で True。"""
    resources = [
        (_res("ebs_volume", state="available"), {}),
        (_res("elastic_ip", attrs={}), {}),
        (
            _res(
                "load_balancer",
                attrs={"Type": "application", "registered_target_count": 0},
            ),
            {},
        ),
        (
            _res(
                "nat_gateway",
                state="available",
                created_at=NOW - timedelta(days=7),
            ),
            {"nat_bytes_total": 0.0},
        ),
        (
            _res(
                "ec2_instance",
                state="running",
                created_at=NOW - timedelta(days=7),
            ),
            {"cpu_max_percent": 1.0},
        ),
        (
            _res(
                "rds_instance",
                state="available",
                created_at=NOW - timedelta(days=7),
            ),
            {"db_connections_max": 0.0},
        ),
    ]

    for res, metrics in resources:
        finding = evaluate(res, metrics, DEFAULT, NOW)
        assert isinstance(finding, Finding)
        assert catalog.is_allowed(
            finding.resource_type,
            finding.recommended_action,
            finding.reason_code,
        )


def test_created_at_none_returns_too_new() -> None:
    """決定事項: created_at が None の EC2, RDS, NAT は too_new を返す。"""
    res_ec2 = _res("ec2_instance", state="running", created_at=None)
    res1 = evaluate(res_ec2, {"cpu_max_percent": 1.0}, DEFAULT, NOW)
    assert isinstance(res1, Skipped) and res1.skip_reason == "too_new"

    res_rds = _res("rds_instance", state="available", created_at=None)
    res2 = evaluate(res_rds, {"db_connections_max": 0.0}, DEFAULT, NOW)
    assert isinstance(res2, Skipped) and res2.skip_reason == "too_new"

    res_nat = _res("nat_gateway", state="available", created_at=None)
    res3 = evaluate(res_nat, {"nat_bytes_total": 0.0}, DEFAULT, NOW)
    assert isinstance(res3, Skipped) and res3.skip_reason == "too_new"


def test_non_matching_state_returns_active() -> None:
    """決定事項: 条件の表に当たらない状態は active を返す。"""
    res_ec2 = _res(
        "ec2_instance",
        state="stopped",
        created_at=NOW - timedelta(days=7),
    )
    res1 = evaluate(res_ec2, {"cpu_max_percent": 1.0}, DEFAULT, NOW)
    assert isinstance(res1, Skipped) and res1.skip_reason == "active"

    res_rds = _res(
        "rds_instance",
        state="stopped",
        created_at=NOW - timedelta(days=7),
    )
    res2 = evaluate(res_rds, {"db_connections_max": 0.0}, DEFAULT, NOW)
    assert isinstance(res2, Skipped) and res2.skip_reason == "active"

    res_nat = _res(
        "nat_gateway",
        state="deleted",
        created_at=NOW - timedelta(days=7),
    )
    res3 = evaluate(res_nat, {"nat_bytes_total": 0.0}, DEFAULT, NOW)
    assert isinstance(res3, Skipped) and res3.skip_reason == "active"


def test_unknown_resource_type_raises_value_error() -> None:
    """未知の resource_type のときは ValueError を送出する。"""
    res = _res("unknown" , state="available")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="unknown resource_type"):
        evaluate(res, {}, DEFAULT, NOW)
