"""Load and validate approval files."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aws_cost_cleaner import catalog
from aws_cost_cleaner.errors import AccountMismatchError, ApprovalFileError
from aws_cost_cleaner.models import Action, ApprovalItem

if TYPE_CHECKING:
    from aws_cost_cleaner.aws.session import ClientFactory

_SCHEMA_VERSION = 1
_TOP_LEVEL_KEYS = ("schema_version", "scan_id", "account_id", "items")
_ITEM_KEYS = (
    "resource_type",
    "region",
    "resource_id",
    "arn",
    "name",
    "recommended_action",
    "reason_code",
    "estimated_monthly_usd",
    "approved",
)
_ARN_REQUIRED_TYPES = frozenset({"load_balancer", "rds_instance"})
_STS_REGION = "us-east-1"


@dataclass(frozen=True)
class ApprovalFile:
    """Parsed and validated approval file."""

    schema_version: int
    scan_id: str
    account_id: str
    items: tuple[ApprovalItem, ...]


def _parse_item(index: int, raw: Any) -> ApprovalItem:
    """1つの承認項目を検証して ApprovalItem を生成する。"""
    if not isinstance(raw, dict):
        raise ApprovalFileError(
            f"Item {index} must be an object, got {type(raw).__name__}"
        )

    missing = [k for k in _ITEM_KEYS if k not in raw]
    if missing:
        raise ApprovalFileError(f"Item {index} missing keys: {missing}")

    approved = raw["approved"]
    if type(approved) is not bool:
        raise ApprovalFileError(
            f"Item {index} 'approved' must be bool, got {type(approved).__name__}"
        )

    region = raw["region"]
    resource_id = raw["resource_id"]
    if not isinstance(region, str) or region == "":
        raise ApprovalFileError(
            f"Item {index} 'region' must be non-empty string, got {region!r}"
        )
    if not isinstance(resource_id, str) or resource_id == "":
        raise ApprovalFileError(
            f"Item {index} 'resource_id' must be non-empty string, got {resource_id!r}"
        )

    resource_type = raw["resource_type"]
    recommended_action = raw["recommended_action"]
    reason_code = raw["reason_code"]

    if (
        not isinstance(resource_type, str)
        or not isinstance(recommended_action, str)
        or not isinstance(reason_code, str)
    ):
        raise ApprovalFileError(
            f"Item {index} ({region}/{resource_id}): "
            "resource_type, recommended_action, reason_code must be strings"
        )

    if not catalog.is_allowed(resource_type, recommended_action, reason_code):
        raise ApprovalFileError(
            f"Item {index} ({region}/{resource_id}): disallowed combination: "
            f"({resource_type!r}, {recommended_action!r}, {reason_code!r})"
        )

    arn = raw["arn"]
    if resource_type in _ARN_REQUIRED_TYPES:
        if not isinstance(arn, str) or arn == "":
            raise ApprovalFileError(
                f"Item {index} ({region}/{resource_id}): 'arn' must be non-empty "
                f"string for {resource_type}, got {arn!r}"
            )

    return ApprovalItem.from_dict(raw)


def load_approval(path: Path | str) -> ApprovalFile:
    """承認ファイル(JSON)を読み込んで検証する。"""
    file_path = Path(path)
    try:
        content = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise ApprovalFileError(f"Failed to read approval file {file_path}: {e}") from e

    try:
        data = json.loads(content)
    except json.JSONDecodeError as e:
        raise ApprovalFileError(f"Failed to parse JSON in {file_path}: {e}") from e

    if not isinstance(data, dict):
        raise ApprovalFileError(
            f"Top-level JSON in {file_path} must be an object, "
            f"got {type(data).__name__}"
        )

    missing_keys = [k for k in _TOP_LEVEL_KEYS if k not in data]
    if missing_keys:
        raise ApprovalFileError(
            f"Top-level keys missing in {file_path}: {missing_keys}"
        )

    version = data["schema_version"]
    if type(version) is not int or version != _SCHEMA_VERSION:
        raise ApprovalFileError(
            f"Invalid schema_version in {file_path}: {version!r} "
            f"(expected {_SCHEMA_VERSION})"
        )

    items_raw = data["items"]
    if not isinstance(items_raw, list):
        raise ApprovalFileError(
            f"'items' in {file_path} must be a list, got {type(items_raw).__name__}"
        )

    parsed_items: list[ApprovalItem] = []
    seen: set[tuple[str, str]] = set()

    for idx, raw_item in enumerate(items_raw):
        item = _parse_item(idx, raw_item)
        pair = (item.region, item.resource_id)
        if pair in seen:
            raise ApprovalFileError(
                f"Duplicate resource ({item.region}, {item.resource_id}) "
                f"found at item {idx}"
            )
        seen.add(pair)
        parsed_items.append(item)

    return ApprovalFile(
        schema_version=data["schema_version"],
        scan_id=data["scan_id"],
        account_id=data["account_id"],
        items=tuple(parsed_items),
    )


def verify_account(approval: ApprovalFile, factory: ClientFactory) -> None:
    """現在の認証情報のアカウントIDと承認ファイルのアカウントIDを照合する。"""
    client = factory.client("sts", _STS_REGION)
    identity = client.get_caller_identity()
    account: str = str(identity["Account"])
    if account != approval.account_id:
        raise AccountMismatchError(
            f"Account ID mismatch: current={account}, approval={approval.account_id}"
        )


def select(approval: ApprovalFile, action: Action | str) -> list[ApprovalItem]:
    """承認済みかつ指定したアクションの項目をファイルの順のまま抽出する。"""
    return [
        item
        for item in approval.items
        if item.approved is True and item.recommended_action == action
    ]
