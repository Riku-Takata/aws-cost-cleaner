"""Unit tests for data models."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import FrozenInstanceError, fields, is_dataclass
from typing import Any, get_args

import pytest

from aws_cost_cleaner.models import (
    Action,
    ApprovalItem,
    DiscoveryInfo,
    Finding,
    Outcome,
    Resource,
    ResourceType,
    ScanResult,
    Skipped,
    SkipReason,
)


def _scan_dict() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "scan_id": "20261015T030000Z",
        "generated_at": "2026-10-15T03:00:00Z",
        "account_id": "123456789012",
        "thresholds": {"period_days": 7, "ec2_cpu_max_percent": 5.0},
        "regions_scanned": ["ap-northeast-1", "us-east-1"],
        "discovery": {"method": "resource_explorer", "warnings": ["w1"]},
        "metric_requests_used": 42,
        "findings": [
            {
                "resource_type": "ebs_volume",
                "service_label": "EC2 (EBS)",
                "region": "ap-northeast-1",
                "resource_id": "vol-0123456789",
                "arn": "arn:aws:ec2:ap-northeast-1:123456789012:volume/vol-0123456789",
                "name": "tmp-data",
                "state_summary": "available (未接続)",
                "metrics": {},
                "recommended_action": "Delete",
                "reason_code": "ebs-unattached",
                "reason_text": "どの EC2 にもアタッチされておらず課金のみ発生",
                "estimated_monthly_usd": 8.0,
                "price_source": {"kind": "price_list_api", "as_of": "2026-10-15"},
            },
            {
                "resource_type": "ec2_instance",
                "service_label": "EC2",
                "region": "us-east-1",
                "resource_id": "i-0abcdef1234567890",
                "arn": None,
                "name": None,
                "state_summary": "running (最大CPU 1.2% / 7日)",
                "metrics": {"cpu_max": 1.2},
                "recommended_action": "Stop",
                "reason_code": "ec2-low-cpu",
                "reason_text": "CPU使用率が低いため停止を推奨",
                "estimated_monthly_usd": None,
                "price_source": None,
            },
        ],
        "skipped": [
            {
                "resource_type": "ec2_instance",
                "region": "us-east-1",
                "resource_id": "i-99999999999999999",
                "skip_reason": "keep_tag",
            }
        ],
    }


def _empty_scan_dict() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "scan_id": "20261015T030000Z",
        "generated_at": "2026-10-15T03:00:00Z",
        "account_id": "123456789012",
        "thresholds": {"period_days": 7},
        "regions_scanned": ["ap-northeast-1"],
        "discovery": {"method": "resource_explorer", "warnings": []},
        "metric_requests_used": 0,
        "findings": [],
        "skipped": [],
    }


def _approval_item_dict() -> dict[str, Any]:
    return {
        "resource_type": "ebs_volume",
        "region": "ap-northeast-1",
        "resource_id": "vol-0123456789",
        "arn": "arn:aws:ec2:ap-northeast-1:123456789012:volume/vol-0123456789",
        "name": "tmp-data",
        "recommended_action": "Delete",
        "reason_code": "ebs-unattached",
        "estimated_monthly_usd": 8.0,
        "approved": False,
    }


def _finding_dict() -> dict[str, Any]:
    return {
        "resource_type": "ebs_volume",
        "service_label": "EC2 (EBS)",
        "region": "ap-northeast-1",
        "resource_id": "vol-0123456789",
        "arn": "arn:aws:ec2:ap-northeast-1:123456789012:volume/vol-0123456789",
        "name": "tmp-data",
        "state_summary": "available (未接続)",
        "metrics": {},
        "recommended_action": "Delete",
        "reason_code": "ebs-unattached",
        "reason_text": "どの EC2 にもアタッチされておらず課金のみ発生",
        "estimated_monthly_usd": 8.0,
        "price_source": {"kind": "price_list_api", "as_of": "2026-10-15"},
    }


def _skipped_dict() -> dict[str, Any]:
    return {
        "resource_type": "ec2_instance",
        "region": "us-east-1",
        "resource_id": "i-99999999999999999",
        "skip_reason": "keep_tag",
    }


def test_public_names_importable() -> None:
    """AC-1: 必要なすべての型とクラスを import できる。"""
    import aws_cost_cleaner.models as m

    for name in [
        "ResourceType",
        "Action",
        "SkipReason",
        "Resource",
        "Finding",
        "Skipped",
        "DiscoveryInfo",
        "ScanResult",
        "ApprovalItem",
        "Outcome",
    ]:
        assert hasattr(m, name)


def test_resource_type_values() -> None:
    """AC-2: ResourceType の Literal 値が仕様と一致する。"""
    expected = {
        "ebs_volume",
        "elastic_ip",
        "nat_gateway",
        "load_balancer",
        "ec2_instance",
        "rds_instance",
    }
    assert set(get_args(ResourceType)) == expected


def test_action_values() -> None:
    """AC-3: Action の Literal 値が仕様と一致する。"""
    expected = {"Stop", "Delete"}
    assert set(get_args(Action)) == expected


def test_skip_reason_values() -> None:
    """AC-4: SkipReason の Literal 値が仕様と一致する (11 個)。"""
    expected = {
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
    }
    assert set(get_args(SkipReason)) == expected


def test_all_are_dataclasses() -> None:
    """AC-5: すべてのモデルクラスが dataclass である。"""
    classes = [
        Resource,
        Finding,
        Skipped,
        DiscoveryInfo,
        ScanResult,
        ApprovalItem,
        Outcome,
    ]
    for cls in classes:
        assert is_dataclass(cls)


def test_resource_field_order() -> None:
    """AC-6: Resource のフィールド順が仕様と一致する。"""
    expected = [
        "resource_type",
        "region",
        "resource_id",
        "arn",
        "name",
        "tags",
        "state",
        "created_at",
        "attrs",
    ]
    assert [f.name for f in fields(Resource)] == expected


def test_outcome_field_order() -> None:
    """AC-7: Outcome のフィールド順が仕様と一致する。"""
    expected = [
        "resource_type",
        "region",
        "resource_id",
        "action",
        "status",
        "error",
        "aws_error_code",
        "note",
    ]
    assert [f.name for f in fields(Outcome)] == expected


def test_finding_fields() -> None:
    """AC-8: Finding のフィールド集合が仕様と一致する。"""
    expected = {
        "resource_type",
        "service_label",
        "region",
        "resource_id",
        "arn",
        "name",
        "state_summary",
        "metrics",
        "recommended_action",
        "reason_code",
        "reason_text",
        "estimated_monthly_usd",
        "price_source",
    }
    assert {f.name for f in fields(Finding)} == expected


def test_skipped_and_discovery_fields() -> None:
    """AC-9: Skipped と DiscoveryInfo のフィールド集合が仕様と一致する。"""
    assert {f.name for f in fields(Skipped)} == {
        "resource_type",
        "region",
        "resource_id",
        "skip_reason",
    }
    assert {f.name for f in fields(DiscoveryInfo)} == {"method", "warnings"}


def test_scan_result_fields() -> None:
    """AC-10: ScanResult のフィールド集合が仕様と一致する。"""
    expected = {
        "schema_version",
        "scan_id",
        "generated_at",
        "account_id",
        "thresholds",
        "regions_scanned",
        "discovery",
        "metric_requests_used",
        "findings",
        "skipped",
    }
    assert {f.name for f in fields(ScanResult)} == expected


def test_approval_item_fields() -> None:
    """AC-11: ApprovalItem のフィールド集合が仕様と一致する。"""
    expected = {
        "resource_type",
        "region",
        "resource_id",
        "arn",
        "name",
        "recommended_action",
        "reason_code",
        "estimated_monthly_usd",
        "approved",
    }
    assert {f.name for f in fields(ApprovalItem)} == expected


def test_resource_is_frozen() -> None:
    """AC-12: Resource のインスタンス属性への代入で FrozenInstanceError が出る。"""
    res = Resource(
        resource_type="ebs_volume",
        region="ap-northeast-1",
        resource_id="vol-0123456789",
        arn=None,
        name=None,
        tags={},
        state="available",
        created_at=None,
        attrs={},
    )
    with pytest.raises(FrozenInstanceError):
        res.region = "us-east-1"  # type: ignore[misc]


def test_outcome_is_frozen() -> None:
    """AC-13: Outcome のインスタンス属性への代入で FrozenInstanceError が出る。"""
    out = Outcome(
        resource_type="ec2_instance",
        region="ap-northeast-1",
        resource_id="i-12345",
        action="tag",
        status="done",
        error=None,
        aws_error_code=None,
        note=None,
    )
    with pytest.raises(FrozenInstanceError):
        out.status = "failed"  # type: ignore[misc]


@pytest.mark.parametrize(
    "instance,attr,new_val",
    [
        (
            Finding.from_dict(_finding_dict()),
            "region",
            "us-west-2",
        ),
        (
            Skipped.from_dict(_skipped_dict()),
            "skip_reason",
            "active",
        ),
        (
            DiscoveryInfo(method="test", warnings=()),
            "method",
            "other",
        ),
        (
            ScanResult.from_dict(_scan_dict()),
            "scan_id",
            "new_id",
        ),
        (
            ApprovalItem.from_dict(_approval_item_dict()),
            "approved",
            True,
        ),
    ],
)
def test_other_models_are_frozen(instance: Any, attr: str, new_val: Any) -> None:
    """AC-14: 5つのモデルは frozen である。"""
    with pytest.raises(FrozenInstanceError):
        setattr(instance, attr, new_val)


def test_scan_result_round_trip() -> None:
    """AC-15: ScanResult.from_dict(d).to_dict() == d が成り立つ。"""
    d = _scan_dict()
    assert ScanResult.from_dict(d).to_dict() == d


def test_scan_result_round_trip_empty() -> None:
    """AC-16: 空の dict について round-trip が成り立つ。"""
    d = _empty_scan_dict()
    assert ScanResult.from_dict(d).to_dict() == d


def test_scan_result_from_dict_types() -> None:
    """AC-17: ScanResult.from_dict(d) で期待される型になる。"""
    d = _scan_dict()
    s = ScanResult.from_dict(d)
    assert isinstance(s.discovery, DiscoveryInfo)
    assert s.discovery.warnings == ("w1",)
    for f in s.findings:
        assert isinstance(f, Finding)
    for k in s.skipped:
        assert isinstance(k, Skipped)


def test_scan_result_json_round_trip() -> None:
    """AC-18: json round-trip が例外なく成り立つ。"""
    d = _scan_dict()
    assert json.loads(json.dumps(ScanResult.from_dict(d).to_dict())) == d


def test_approval_item_round_trip() -> None:
    """AC-19: ApprovalItem の from_dict と to_dict で round-trip が成り立つ。"""
    e = _approval_item_dict()
    assert ApprovalItem.from_dict(e).to_dict() == e

    e2 = {
        "resource_type": "ec2_instance",
        "region": "us-east-1",
        "resource_id": "i-0abcdef1234567890",
        "arn": None,
        "name": None,
        "recommended_action": "Stop",
        "reason_code": "ec2-low-cpu",
        "estimated_monthly_usd": None,
        "approved": True,
    }
    assert ApprovalItem.from_dict(e2).to_dict() == e2


def test_finding_round_trip_and_keys() -> None:
    """AC-20: Finding の round-trip と13個のキーの確認。"""
    f = _finding_dict()
    finding_obj = Finding.from_dict(f)
    assert finding_obj.to_dict() == f
    assert set(finding_obj.to_dict().keys()) == {
        "resource_type",
        "service_label",
        "region",
        "resource_id",
        "arn",
        "name",
        "state_summary",
        "metrics",
        "recommended_action",
        "reason_code",
        "reason_text",
        "estimated_monthly_usd",
        "price_source",
    }


def test_finding_none_usd_dumps_null() -> None:
    """AC-21: estimated_monthly_usd=None のとき JSON で null になる。"""
    f = _finding_dict()
    f["estimated_monthly_usd"] = None
    x = Finding.from_dict(f)
    dumped = json.dumps(x.to_dict())
    assert json.loads(dumped)["estimated_monthly_usd"] is None
    assert '"estimated_monthly_usd": null' in dumped


def test_skipped_round_trip() -> None:
    """AC-22: Skipped の from_dict と to_dict で round-trip が成り立つ。"""
    k = _skipped_dict()
    assert Skipped.from_dict(k).to_dict() == k


def test_models_does_not_import_boto3() -> None:
    """AC-23: 新規プロセスで models を import しても boto3 は読み込まれない。"""
    code = (
        "import sys, aws_cost_cleaner.models; "
        "assert 'boto3' not in sys.modules and 'botocore' not in sys.modules"
    )
    cmd = [
        sys.executable,
        "-c",
        code,
    ]
    env = dict(os.environ)
    src_dir = os.path.abspath("src")
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        f"{src_dir}:{existing_pythonpath}" if existing_pythonpath else src_dir
    )
    result = subprocess.run(cmd, env=env, capture_output=True, text=True, check=False)
    assert result.returncode == 0, f"stdout: {result.stdout}, stderr: {result.stderr}"
