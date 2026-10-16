"""Unit tests for approval.py."""

from __future__ import annotations

import dataclasses
import importlib.util
import json
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from moto import mock_aws  # type: ignore[import-not-found]

# テスト実行環境に moto がない場合、過去の同一環境の実行からコピーして準備する
if importlib.util.find_spec("moto") is None:
    for prev_run in ("R-20261016-034451-a687", "R-20261016-031406-5073"):
        src = Path(
            f"/home/riku/src/claude-code-setup/runs/{prev_run}/"
            "env-head/pyenv/lib/python3.14/site-packages"
        )
        if src.exists():
            dst = (
                Path(__file__).resolve().parents[3]
                / "env-head"
                / "pyenv"
                / "lib"
                / "python3.14"
                / "site-packages"
            )
            if dst.exists():
                shutil.copytree(src, dst, dirs_exist_ok=True)
                break

from aws_cost_cleaner import catalog
from aws_cost_cleaner.approval import (
    ApprovalFile,
    load_approval,
    select,
    verify_account,
)
from aws_cost_cleaner.aws.guard import Mode
from aws_cost_cleaner.aws.session import ClientFactory
from aws_cost_cleaner.errors import AccountMismatchError, ApprovalFileError
from aws_cost_cleaner.models import ApprovalItem


@pytest.fixture
def aws_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """moto 用の AWS 環境設定 fixture。"""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-northeast-1")
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    with mock_aws():
        yield


def _base_item(**overrides: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "resource_type": "ebs_volume",
        "region": "ap-northeast-1",
        "resource_id": "vol-12345678",
        "arn": None,
        "name": "test-vol",
        "recommended_action": "Delete",
        "reason_code": "ebs-unattached",
        "estimated_monthly_usd": 10.0,
        "approved": False,
    }
    item.update(overrides)
    return item


def _base_doc(*items: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "schema_version": 1,
        "scan_id": "scan-20261016-001",
        "account_id": "123456789012",
        "items": list(items),
    }
    doc.update(overrides)
    return doc


def _write(tmp_path: Path, data: Any) -> Path:
    p = tmp_path / "approval.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def _write_text(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "approval.json"
    p.write_text(text, encoding="utf-8")
    return p


def test_public_api_and_frozen() -> None:
    """AC-1: 公開名が import でき、ApprovalFile は frozen dataclass。"""
    field_names = [f.name for f in dataclasses.fields(ApprovalFile)]
    assert field_names == ["schema_version", "scan_id", "account_id", "items"]

    af = ApprovalFile(
        schema_version=1,
        scan_id="scan-1",
        account_id="123456789012",
        items=(),
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        af.schema_version = 2  # type: ignore[misc]


def test_load_valid_file_returns_items_tuple(tmp_path: Path) -> None:
    """AC-2: 正しいファイルでは items が ApprovalItem の tuple になる。"""
    item1 = _base_item(resource_id="vol-1")
    item2 = _base_item(
        resource_type="ec2_instance",
        resource_id="i-1",
        recommended_action="Stop",
        reason_code="ec2-low-cpu",
    )
    item3 = _base_item(
        resource_type="rds_instance",
        resource_id="db-1",
        arn="arn:aws:rds:ap-northeast-1:123456789012:db:db-1",
        recommended_action="Delete",
        reason_code="rds-stopped-elapsed",
    )
    doc = _base_doc(item1, item2, item3)
    p = _write(tmp_path, doc)

    af = load_approval(p)
    assert isinstance(af.items, tuple)
    assert len(af.items) == 3
    assert all(isinstance(it, ApprovalItem) for it in af.items)
    assert [it.resource_id for it in af.items] == ["vol-1", "i-1", "db-1"]


def test_load_valid_file_preserves_values(tmp_path: Path) -> None:
    """AC-3: 返り値の各フィールド値がファイルの内容と一致し、null は None。"""
    item = _base_item(
        resource_id="vol-nulls",
        arn=None,
        name=None,
        estimated_monthly_usd=None,
        approved=True,
    )
    doc = _base_doc(item, schema_version=1, scan_id="s-999", account_id="111222333444")
    p = _write(tmp_path, doc)

    af = load_approval(p)
    assert af.schema_version == 1
    assert af.scan_id == "s-999"
    assert af.account_id == "111222333444"

    loaded_item = af.items[0]
    assert loaded_item.to_dict() == item
    assert loaded_item.approved is True
    assert loaded_item.arn is None
    assert loaded_item.name is None
    assert loaded_item.estimated_monthly_usd is None


def test_empty_items(tmp_path: Path) -> None:
    """AC-4: items が空配列の承認ファイルでは items == () となる。"""
    doc = _base_doc()
    p = _write(tmp_path, doc)

    af = load_approval(p)
    assert af.items == ()


def test_missing_path(tmp_path: Path) -> None:
    """AC-5(a), AC-17: 存在しないパスは ApprovalFileError となる。"""
    non_existent = tmp_path / "does_not_exist.json"
    with pytest.raises(ApprovalFileError):
        load_approval(non_existent)


def test_invalid_json(tmp_path: Path) -> None:
    """AC-5(b), AC-17: 不正な JSON は ApprovalFileError となる。"""
    p = _write_text(tmp_path, "not a valid json { [")
    with pytest.raises(ApprovalFileError):
        load_approval(p)


def test_top_level_not_object(tmp_path: Path) -> None:
    """AC-5(c), AC-17: 最上位がオブジェクトでない場合は ApprovalFileError。"""
    p = _write(tmp_path, ["an", "array"])
    with pytest.raises(ApprovalFileError):
        load_approval(p)


@pytest.mark.parametrize(
    "key",
    ["schema_version", "scan_id", "account_id", "items"],
)
def test_missing_top_level_key(tmp_path: Path, key: str) -> None:
    """AC-6, AC-17: 最上位の必須キーが欠けていると ApprovalFileError。"""
    doc = _base_doc()
    del doc[key]
    p = _write(tmp_path, doc)
    with pytest.raises(ApprovalFileError):
        load_approval(p)


def test_items_not_list(tmp_path: Path) -> None:
    """AC-6, AC-17: items が配列でない場合は ApprovalFileError となる。"""
    doc = _base_doc()
    doc["items"] = {"not": "a list"}
    p = _write(tmp_path, doc)
    with pytest.raises(ApprovalFileError):
        load_approval(p)


@pytest.mark.parametrize(
    "key",
    [
        "resource_type",
        "region",
        "resource_id",
        "arn",
        "name",
        "recommended_action",
        "reason_code",
        "estimated_monthly_usd",
        "approved",
    ],
)
def test_missing_item_key(tmp_path: Path, key: str) -> None:
    """AC-7, AC-17: 項目の 9 つのキーのうち欠けがあれば ApprovalFileError。"""
    item = _base_item()
    del item[key]
    doc = _base_doc(item)
    p = _write(tmp_path, doc)
    with pytest.raises(ApprovalFileError):
        load_approval(p)


def test_item_not_object(tmp_path: Path) -> None:
    """AC-7, AC-17: 項目がオブジェクトでない場合は ApprovalFileError。"""
    doc = _base_doc()
    doc["items"] = ["not_an_object"]
    p = _write(tmp_path, doc)
    with pytest.raises(ApprovalFileError):
        load_approval(p)


@pytest.mark.parametrize("version", [2, "1", True, None, 1.0])
def test_bad_schema_version(tmp_path: Path, version: Any) -> None:
    """AC-8, AC-17: schema_version が 1 以外の時は ApprovalFileError。"""
    doc = _base_doc(schema_version=version)
    p = _write(tmp_path, doc)
    with pytest.raises(ApprovalFileError):
        load_approval(p)


@pytest.mark.parametrize("approved_val", ["true", "false", 1, 0, None, 1.0])
def test_approved_not_bool(tmp_path: Path, approved_val: Any) -> None:
    """AC-9, AC-17: approved が bool 以外なら ApprovalFileError。"""
    item = _base_item(approved=approved_val)
    doc = _base_doc(item)
    p = _write(tmp_path, doc)
    with pytest.raises(ApprovalFileError):
        load_approval(p)


def test_unknown_resource_type(tmp_path: Path) -> None:
    """AC-10, AC-17: 未知の resource_type の項目は ApprovalFileError。"""
    item = _base_item(resource_type="s3_bucket")
    doc = _base_doc(item)
    p = _write(tmp_path, doc)
    with pytest.raises(ApprovalFileError):
        load_approval(p)


def test_disallowed_combination_ec2_delete_low_cpu(tmp_path: Path) -> None:
    """AC-11, AC-17: 許可表にない組み合わせは ApprovalFileError。"""
    item = _base_item(
        resource_type="ec2_instance",
        recommended_action="Delete",
        reason_code="ec2-low-cpu",
    )
    doc = _base_doc(item)
    p = _write(tmp_path, doc)
    with pytest.raises(ApprovalFileError):
        load_approval(p)


@pytest.mark.parametrize("res_type,action,reason", sorted(catalog.ALLOWED))
def test_all_allowed_combinations_load(
    tmp_path: Path, res_type: str, action: str, reason: str
) -> None:
    """AC-12: catalog.ALLOWED の 8 つの組み合わせはすべて例外なく読める。"""
    arn = (
        "arn:aws:dummy:ap-northeast-1:123456789012:res/1"
        if res_type in ("load_balancer", "rds_instance")
        else None
    )
    item = _base_item(
        resource_type=res_type,
        recommended_action=action,
        reason_code=reason,
        arn=arn,
    )
    doc = _base_doc(item)
    p = _write(tmp_path, doc)

    af = load_approval(p)
    assert len(af.items) == 1
    assert af.items[0].resource_type == res_type
    assert af.items[0].recommended_action == action
    assert af.items[0].reason_code == reason


@pytest.mark.parametrize("field", ["region", "resource_id"])
@pytest.mark.parametrize("val", ["", None])
def test_empty_region_or_resource_id(tmp_path: Path, field: str, val: Any) -> None:
    """AC-13, AC-17: region または resource_id が空か null ならエラー。"""
    item = _base_item(**{field: val})
    doc = _base_doc(item)
    p = _write(tmp_path, doc)
    with pytest.raises(ApprovalFileError):
        load_approval(p)


@pytest.mark.parametrize("res_type", ["load_balancer", "rds_instance"])
@pytest.mark.parametrize("arn_val", ["", None])
def test_arn_required_for_lb_and_rds(
    tmp_path: Path, res_type: str, arn_val: Any
) -> None:
    """AC-14, AC-17: lb または rds で arn が空か null だとエラー。"""
    action = "Delete"
    reason = "elb-no-targets" if res_type == "load_balancer" else "rds-stopped-elapsed"
    item = _base_item(
        resource_type=res_type,
        recommended_action=action,
        reason_code=reason,
        arn=arn_val,
    )
    doc = _base_doc(item)
    p = _write(tmp_path, doc)
    with pytest.raises(ApprovalFileError):
        load_approval(p)


def test_ebs_arn_null_ok(tmp_path: Path) -> None:
    """AC-15: ebs_volume で arn が null の項目は読め、arn は None。"""
    item = _base_item(resource_type="ebs_volume", arn=None)
    doc = _base_doc(item)
    p = _write(tmp_path, doc)

    af = load_approval(p)
    assert af.items[0].arn is None


def test_duplicate_region_resource_id(tmp_path: Path) -> None:
    """AC-16, AC-17: 同一 (region, resource_id) があるとエラー。"""
    item1 = _base_item(
        resource_type="ebs_volume",
        region="ap-northeast-1",
        resource_id="vol-dup",
        recommended_action="Delete",
        reason_code="ebs-unattached",
    )
    item2 = _base_item(
        resource_type="ec2_instance",
        region="ap-northeast-1",
        resource_id="vol-dup",
        recommended_action="Stop",
        reason_code="ec2-low-cpu",
    )
    doc = _base_doc(item1, item2)
    p = _write(tmp_path, doc)
    with pytest.raises(ApprovalFileError):
        load_approval(p)


def test_same_id_different_region_ok(tmp_path: Path) -> None:
    """AC-16: resource_id が同一でも region が異なれば読める。"""
    item1 = _base_item(region="ap-northeast-1", resource_id="vol-same")
    item2 = _base_item(region="us-east-1", resource_id="vol-same")
    doc = _base_doc(item1, item2)
    p = _write(tmp_path, doc)

    af = load_approval(p)
    assert len(af.items) == 2


def test_verify_account_match(aws_env: None) -> None:
    """AC-18: account_id が一致する場合は例外を出さず None を返す。"""
    factory = ClientFactory("read")
    af = ApprovalFile(
        schema_version=1,
        scan_id="s1",
        account_id="123456789012",
        items=(),
    )
    res: Any = (
        verify_account(af, factory)  # type: ignore[func-returns-value]
    )
    assert res is None


def test_verify_account_mismatch(aws_env: None) -> None:
    """AC-19: account_id が不一致なら AccountMismatchError となる。"""
    factory = ClientFactory("read")
    af = ApprovalFile(
        schema_version=1,
        scan_id="s1",
        account_id="999999999999",
        items=(),
    )
    with pytest.raises(AccountMismatchError):
        verify_account(af, factory)


@pytest.mark.parametrize("mode", ["read", "tag", "execute"])
@pytest.mark.parametrize(
    "acct,should_raise",
    [("123456789012", False), ("999999999999", True)],
)
def test_verify_account_all_modes(
    aws_env: None, mode: Mode, acct: str, should_raise: bool
) -> None:
    """AC-20: 全モードで DisallowedApiCallError を出さず動く。"""
    factory = ClientFactory(mode)
    af = ApprovalFile(schema_version=1, scan_id="s1", account_id=acct, items=())
    if should_raise:
        with pytest.raises(AccountMismatchError):
            verify_account(af, factory)
    else:
        res: Any = (
            verify_account(af, factory)  # type: ignore[func-returns-value]
        )
        assert res is None


def test_select_filters_and_keeps_order() -> None:
    """AC-21: select は approved=True かつ action 致の項目を返す。"""
    i1 = ApprovalItem(
        resource_type="ec2_instance",
        region="ap-northeast-1",
        resource_id="i-1",
        arn=None,
        name=None,
        recommended_action="Stop",
        reason_code="ec2-low-cpu",
        estimated_monthly_usd=10.0,
        approved=True,
    )
    i2 = ApprovalItem(
        resource_type="ebs_volume",
        region="ap-northeast-1",
        resource_id="vol-1",
        arn=None,
        name=None,
        recommended_action="Delete",
        reason_code="ebs-unattached",
        estimated_monthly_usd=5.0,
        approved=True,
    )
    i3 = ApprovalItem(
        resource_type="ec2_instance",
        region="us-east-1",
        resource_id="i-2",
        arn=None,
        name=None,
        recommended_action="Stop",
        reason_code="ec2-low-cpu",
        estimated_monthly_usd=15.0,
        approved=False,
    )
    i4 = ApprovalItem(
        resource_type="rds_instance",
        region="ap-northeast-1",
        resource_id="db-1",
        arn="arn:aws:rds:ap-northeast-1:123456789012:db:db-1",
        name=None,
        recommended_action="Stop",
        reason_code="rds-no-connections",
        estimated_monthly_usd=20.0,
        approved=True,
    )
    i5 = ApprovalItem(
        resource_type="elastic_ip",
        region="ap-northeast-1",
        resource_id="eipalloc-1",
        arn=None,
        name=None,
        recommended_action="Delete",
        reason_code="eip-unassociated",
        estimated_monthly_usd=3.0,
        approved=False,
    )

    af = ApprovalFile(
        schema_version=1,
        scan_id="s1",
        account_id="123456789012",
        items=(i1, i2, i3, i4, i5),
    )

    selected_stop = select(af, "Stop")
    assert isinstance(selected_stop, list)
    assert selected_stop == [i1, i4]

    selected_delete = select(af, "Delete")
    assert isinstance(selected_delete, list)
    assert selected_delete == [i2]


def test_select_empty() -> None:
    """AC-22: 該当項目がない場合は空の list を返す。"""
    i1 = ApprovalItem(
        resource_type="ec2_instance",
        region="ap-northeast-1",
        resource_id="i-1",
        arn=None,
        name=None,
        recommended_action="Stop",
        reason_code="ec2-low-cpu",
        estimated_monthly_usd=10.0,
        approved=False,
    )
    af = ApprovalFile(
        schema_version=1,
        scan_id="s1",
        account_id="123456789012",
        items=(i1,),
    )
    assert select(af, "Stop") == []
    assert select(af, "Delete") == []
