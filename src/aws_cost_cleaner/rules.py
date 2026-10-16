"""Cleanup decision rules (pure functions, no AWS calls)."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta

from aws_cost_cleaner import catalog
from aws_cost_cleaner.config import Thresholds
from aws_cost_cleaner.models import Action, Finding, Resource, Skipped, SkipReason


def has_elapsed(now: datetime, since: datetime, days: int) -> bool:
    """基準日時から指定日数が経過しているかを判定する。"""
    return now - since >= timedelta(days=days)


def _skip(resource: Resource, reason: SkipReason) -> Skipped:
    """スキップレコードを生成する。"""
    return Skipped(
        resource_type=resource.resource_type,
        region=resource.region,
        resource_id=resource.resource_id,
        skip_reason=reason,
    )


def _finding(
    resource: Resource,
    metrics: Mapping[str, float | None],
    action: Action,
    reason_code: str,
    state_summary: str,
    reason_text: str,
) -> Finding:
    """クリーンアップ候補の Finding を生成する。"""
    if resource.resource_type == "load_balancer":
        label = catalog.service_label(
            resource.resource_type, resource.attrs.get("Type")
        )
    else:
        label = catalog.service_label(resource.resource_type)

    return Finding(
        resource_type=resource.resource_type,
        service_label=label,
        region=resource.region,
        resource_id=resource.resource_id,
        arn=resource.arn,
        name=resource.name,
        state_summary=state_summary,
        metrics=dict(metrics),
        recommended_action=action,
        reason_code=reason_code,
        reason_text=reason_text,
        estimated_monthly_usd=None,
        price_source=None,
    )


def _is_too_new(resource: Resource, thresholds: Thresholds, now: datetime) -> bool:
    """リソース作成からしきい値の日数が経過していないかを判定する。"""
    if resource.created_at is None:
        return True
    return not has_elapsed(now, resource.created_at, thresholds.period_days)


def _evaluate_ebs(
    resource: Resource,
    metrics: Mapping[str, float | None],
    thresholds: Thresholds,
    now: datetime,
) -> Finding | Skipped:
    """EBS ボリュームのクリーンアップ要否を判定する。"""
    if resource.state != "available":
        return _skip(resource, "active")
    return _finding(
        resource=resource,
        metrics=metrics,
        action="Delete",
        reason_code="ebs-unattached",
        state_summary="available (未接続)",
        reason_text="どのEC2にもアタッチされておらず課金のみ発生",
    )


def _evaluate_eip(
    resource: Resource,
    metrics: Mapping[str, float | None],
    thresholds: Thresholds,
    now: datetime,
) -> Finding | Skipped:
    """Elastic IP のクリーンアップ要否を判定する。"""
    if resource.attrs.get("AssociationId") is not None:
        return _skip(resource, "active")
    return _finding(
        resource=resource,
        metrics=metrics,
        action="Delete",
        reason_code="eip-unassociated",
        state_summary="関連付けなし",
        reason_text=(
            "どのインスタンスや ENI にも関連付けられていない。"
            "パブリック IPv4 アドレスは時間単位で課金されるため、"
            "使われないまま料金だけが発生している"
        ),
    )


def _evaluate_elb(
    resource: Resource,
    metrics: Mapping[str, float | None],
    thresholds: Thresholds,
    now: datetime,
) -> Finding | Skipped:
    """ロードバランサーのクリーンアップ要否を判定する。"""
    count = resource.attrs["registered_target_count"]
    if count != 0:
        return _skip(resource, "active")
    return _finding(
        resource=resource,
        metrics=metrics,
        action="Delete",
        reason_code="elb-no-targets",
        state_summary=f"登録ターゲット {int(count)} 件",
        reason_text="登録済みのターゲットがなく、使われないまま料金だけが発生している",
    )


def _evaluate_nat(
    resource: Resource,
    metrics: Mapping[str, float | None],
    thresholds: Thresholds,
    now: datetime,
) -> Finding | Skipped:
    """NAT ゲートウェイのクリーンアップ要否を判定する。"""
    if resource.state != "available":
        return _skip(resource, "active")
    if _is_too_new(resource, thresholds, now):
        return _skip(resource, "too_new")

    nat_bytes_total = metrics.get("nat_bytes_total")
    if nat_bytes_total is None:
        return _skip(resource, "no_metrics")
    if nat_bytes_total > thresholds.nat_bytes_max:
        return _skip(resource, "active")

    return _finding(
        resource=resource,
        metrics=metrics,
        action="Delete",
        reason_code="nat-idle",
        state_summary=(
            f"通信量 {int(nat_bytes_total)} Byte ({thresholds.period_days}日間)"
        ),
        reason_text="直近1週間トラフィックなし。高額課金原因",
    )


def _evaluate_ec2(
    resource: Resource,
    metrics: Mapping[str, float | None],
    thresholds: Thresholds,
    now: datetime,
) -> Finding | Skipped:
    """EC2 インスタンスのクリーンアップ要否を判定する。"""
    if resource.state != "running":
        return _skip(resource, "active")
    if _is_too_new(resource, thresholds, now):
        return _skip(resource, "too_new")

    cpu = metrics.get("cpu_max_percent")
    if cpu is None:
        return _skip(resource, "no_metrics")

    net_check_enabled = thresholds.ec2_network_bytes_max >= 0
    if net_check_enabled:
        net = metrics.get("network_bytes_total")
        if net is None:
            return _skip(resource, "no_metrics")

    if cpu >= thresholds.ec2_cpu_max_percent:
        return _skip(resource, "active")

    if net_check_enabled:
        net = metrics.get("network_bytes_total")
        assert net is not None
        if net > thresholds.ec2_network_bytes_max:
            return _skip(resource, "active")

    return _finding(
        resource=resource,
        metrics=metrics,
        action="Stop",
        reason_code="ec2-low-cpu",
        state_summary=f"running (最大CPU {float(cpu)}% / {thresholds.period_days}日)",
        reason_text="放置検証機の可能性大。まずは停止を推奨",
    )


def _evaluate_rds(
    resource: Resource,
    metrics: Mapping[str, float | None],
    thresholds: Thresholds,
    now: datetime,
) -> Finding | Skipped:
    """RDS インスタンスのクリーンアップ要否を判定する。"""
    if resource.state != "available":
        return _skip(resource, "active")
    if _is_too_new(resource, thresholds, now):
        return _skip(resource, "too_new")

    conn = metrics.get("db_connections_max")
    if conn is None:
        return _skip(resource, "no_metrics")
    if conn > thresholds.rds_connections_max:
        return _skip(resource, "active")

    return _finding(
        resource=resource,
        metrics=metrics,
        action="Stop",
        reason_code="rds-no-connections",
        state_summary=(
            f"available (最大接続数 {int(conn)} / {thresholds.period_days}日)"
        ),
        reason_text="直近の期間に DB への接続がない。まずは停止を推奨",
    )


def evaluate(
    resource: Resource,
    metrics: Mapping[str, float | None],
    thresholds: Thresholds,
    now: datetime,
) -> Finding | Skipped:
    """リソースの状態とメトリクスからクリーンアップ候補またはスキップ理由を判定する。"""
    if resource.resource_type == "ebs_volume":
        return _evaluate_ebs(resource, metrics, thresholds, now)
    if resource.resource_type == "elastic_ip":
        return _evaluate_eip(resource, metrics, thresholds, now)
    if resource.resource_type == "load_balancer":
        return _evaluate_elb(resource, metrics, thresholds, now)
    if resource.resource_type == "nat_gateway":
        return _evaluate_nat(resource, metrics, thresholds, now)
    if resource.resource_type == "ec2_instance":
        return _evaluate_ec2(resource, metrics, thresholds, now)
    if resource.resource_type == "rds_instance":
        return _evaluate_rds(resource, metrics, thresholds, now)
    raise ValueError(f"unknown resource_type: {resource.resource_type!r}")
