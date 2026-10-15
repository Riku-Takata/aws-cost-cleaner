"""Unit tests for AWS API guard (guard.py)."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Any

import boto3  # type: ignore[import-not-found]
import pytest
from moto import mock_aws

from aws_cost_cleaner.aws.guard import (
    ALLOWED_OPERATIONS,
    ALLOWED_TAG_KEYS,
    install_guard,
)
from aws_cost_cleaner.aws.session import ClientFactory
from aws_cost_cleaner.errors import DisallowedApiCallError


@pytest.fixture
def aws_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-northeast-1")
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    with mock_aws():
        yield


def test_public_names_importable() -> None:
    """AC-1: 公開定数と関数が guard と session から正しく import できる。"""
    from aws_cost_cleaner.aws.guard import (
        ALLOWED_OPERATIONS as OPS,
    )
    from aws_cost_cleaner.aws.guard import (
        ALLOWED_TAG_KEYS as TAGS,
    )
    from aws_cost_cleaner.aws.guard import (
        install_guard as IG,
    )
    from aws_cost_cleaner.aws.session import ClientFactory as CF

    assert isinstance(OPS, dict)
    assert isinstance(TAGS, dict)
    assert callable(IG)
    assert isinstance(CF, type)


def test_allowed_operations_modes() -> None:
    """AC-2: ALLOWED_OPERATIONS のキーは read, tag, execute で各値は frozenset。"""
    assert set(ALLOWED_OPERATIONS.keys()) == {"read", "tag", "execute"}
    for mode in ("read", "tag", "execute"):
        assert isinstance(ALLOWED_OPERATIONS[mode], frozenset)


def test_allowed_operations_read() -> None:
    """AC-3: ALLOWED_OPERATIONS['read'] は仕様で指定された 14 要素と等しい。"""
    expected = frozenset(
        {
            "sts:GetCallerIdentity",
            "resource-explorer-2:Search",
            "ec2:DescribeRegions",
            "ec2:DescribeVolumes",
            "ec2:DescribeAddresses",
            "ec2:DescribeNatGateways",
            "ec2:DescribeInstances",
            "elbv2:DescribeLoadBalancers",
            "elbv2:DescribeTargetGroups",
            "elbv2:DescribeTargetHealth",
            "elbv2:DescribeTags",
            "rds:DescribeDBInstances",
            "cloudwatch:GetMetricStatistics",
            "pricing:GetProducts",
        }
    )
    assert ALLOWED_OPERATIONS["read"] == expected
    assert len(ALLOWED_OPERATIONS["read"]) == 14


def test_allowed_operations_tag() -> None:
    """AC-4: ALLOWED_OPERATIONS['tag'] は仕様で指定された 14 要素と等しい。"""
    expected = frozenset(
        {
            "sts:GetCallerIdentity",
            "ec2:DescribeRegions",
            "ec2:DescribeVolumes",
            "ec2:DescribeAddresses",
            "ec2:DescribeNatGateways",
            "ec2:DescribeInstances",
            "elbv2:DescribeLoadBalancers",
            "elbv2:DescribeTargetGroups",
            "elbv2:DescribeTargetHealth",
            "elbv2:DescribeTags",
            "rds:DescribeDBInstances",
            "ec2:CreateTags",
            "rds:AddTagsToResource",
            "elbv2:AddTags",
        }
    )
    assert ALLOWED_OPERATIONS["tag"] == expected
    assert len(ALLOWED_OPERATIONS["tag"]) == 14


def test_allowed_operations_execute() -> None:
    """AC-5: ALLOWED_OPERATIONS['execute'] は仕様で指定された 21 要素と等しい。"""
    expected = frozenset(
        {
            "sts:GetCallerIdentity",
            "ec2:DescribeRegions",
            "ec2:DescribeVolumes",
            "ec2:DescribeAddresses",
            "ec2:DescribeNatGateways",
            "ec2:DescribeInstances",
            "elbv2:DescribeLoadBalancers",
            "elbv2:DescribeTargetGroups",
            "elbv2:DescribeTargetHealth",
            "elbv2:DescribeTags",
            "rds:DescribeDBInstances",
            "ec2:StopInstances",
            "ec2:TerminateInstances",
            "ec2:DeleteVolume",
            "ec2:ReleaseAddress",
            "ec2:DeleteNatGateway",
            "rds:StopDBInstance",
            "rds:DeleteDBInstance",
            "elbv2:DeleteLoadBalancer",
            "ec2:CreateTags",
            "rds:AddTagsToResource",
        }
    )
    assert ALLOWED_OPERATIONS["execute"] == expected
    assert len(ALLOWED_OPERATIONS["execute"]) == 21


def test_tag_and_execute_exclude_read_only_apis() -> None:
    """AC-6: tag と execute に Search, GetMetricStatistics,
    GetProducts は含まれない。
    """
    excluded = {
        "resource-explorer-2:Search",
        "cloudwatch:GetMetricStatistics",
        "pricing:GetProducts",
    }
    assert ALLOWED_OPERATIONS["tag"].isdisjoint(excluded)
    assert ALLOWED_OPERATIONS["execute"].isdisjoint(excluded)


def test_allowed_tag_keys() -> None:
    """AC-7: ALLOWED_TAG_KEYS の tag と execute のキー制限が正しい。"""
    assert ALLOWED_TAG_KEYS["tag"] == {"CleanupCandidate", "CleanupReason"}
    assert ALLOWED_TAG_KEYS["execute"] == {"CleanupStoppedAt"}


@pytest.mark.parametrize(
    ("service", "call_fn"),
    [
        (
            "ec2",
            lambda c: c.run_instances(ImageId="ami-12345678", MinCount=1, MaxCount=1),
        ),
        (
            "cloudwatch",
            lambda c: c.get_metric_data(
                MetricDataQueries=[
                    {
                        "Id": "m1",
                        "MetricStat": {
                            "Metric": {
                                "Namespace": "AWS/EC2",
                                "MetricName": "CPUUtilization",
                            },
                            "Period": 60,
                            "Stat": "Maximum",
                        },
                    }
                ],
                StartTime=datetime(2026, 1, 1, tzinfo=UTC),
                EndTime=datetime(2026, 1, 2, tzinfo=UTC),
            ),
        ),
        (
            "ce",
            lambda c: c.get_cost_and_usage(
                TimePeriod={"Start": "2026-01-01", "End": "2026-02-01"},
                Granularity="MONTHLY",
                Metrics=["UnblendedCost"],
            ),
        ),
        ("s3", lambda c: c.list_objects_v2(Bucket="example-bucket")),
    ],
)
def test_read_mode_blocks_disallowed_operations(
    aws_env: None, service: str, call_fn: Callable[[Any], Any]
) -> None:
    """AC-8: read モードで許可外の API を呼ぶと DisallowedApiCallError になる。"""
    factory = ClientFactory("read")
    client = factory.client(service, "ap-northeast-1")
    with pytest.raises(DisallowedApiCallError):
        call_fn(client)


def test_run_instances_does_not_reach_aws(aws_env: None) -> None:
    """AC-9: run_instances が拒否された後、AWS 側にインスタンスが作成されていない。"""
    factory = ClientFactory("read")
    guarded_ec2 = factory.client("ec2", "ap-northeast-1")
    with pytest.raises(DisallowedApiCallError):
        guarded_ec2.run_instances(ImageId="ami-12345678", MinCount=1, MaxCount=1)

    plain_ec2 = boto3.client("ec2", region_name="ap-northeast-1")  # noqa: TID251
    reservations = plain_ec2.describe_instances()["Reservations"]
    assert len(reservations) == 0


def test_read_mode_allows_listed_operations(aws_env: None) -> None:
    """AC-10: read モードで許可リストにある sts と ec2 の Describe は正常応答を返す。"""
    factory = ClientFactory("read")
    sts = factory.client("sts", "ap-northeast-1")
    caller_id = sts.get_caller_identity()
    assert "Account" in caller_id

    ec2 = factory.client("ec2", "ap-northeast-1")
    volumes = ec2.describe_volumes()
    assert "Volumes" in volumes


@pytest.mark.parametrize(
    "tags",
    [
        [{"Key": "Keep", "Value": "true"}],
        [{"Key": "CleanupStoppedAt", "Value": "2026-01-01T00:00:00Z"}],
        [
            {"Key": "CleanupCandidate", "Value": "true"},
            {"Key": "Keep", "Value": "true"},
        ],
        [
            {"Key": "CleanupCandidate", "Value": "true"},
            {"Key": "CleanupStoppedAt", "Value": "2026-01-01T00:00:00Z"},
        ],
    ],
)
def test_tag_mode_rejects_disallowed_tag_keys(
    aws_env: None, tags: list[dict[str, str]]
) -> None:
    """AC-11: tag モードで許可外のタグキーを指定すると
    DisallowedApiCallError になりタグは付かない。
    """
    plain_ec2 = boto3.client("ec2", region_name="ap-northeast-1")  # noqa: TID251
    vol = plain_ec2.create_volume(AvailabilityZone="ap-northeast-1a", Size=1)
    vol_id = vol["VolumeId"]

    factory = ClientFactory("tag")
    guarded_ec2 = factory.client("ec2", "ap-northeast-1")
    with pytest.raises(DisallowedApiCallError):
        guarded_ec2.create_tags(Resources=[vol_id], Tags=tags)

    described = plain_ec2.describe_volumes(VolumeIds=[vol_id])
    assert described["Volumes"][0].get("Tags", []) == []


def test_tag_mode_allows_candidate_and_reason(aws_env: None) -> None:
    """AC-12: tag モードで CleanupCandidate と CleanupReason は正常にタグ付けできる。"""
    plain_ec2 = boto3.client("ec2", region_name="ap-northeast-1")  # noqa: TID251
    vol = plain_ec2.create_volume(AvailabilityZone="ap-northeast-1a", Size=1)
    vol_id = vol["VolumeId"]

    factory = ClientFactory("tag")
    guarded_ec2 = factory.client("ec2", "ap-northeast-1")
    guarded_ec2.create_tags(
        Resources=[vol_id],
        Tags=[
            {"Key": "CleanupCandidate", "Value": "true"},
            {"Key": "CleanupReason", "Value": "unattached"},
        ],
    )

    described = plain_ec2.describe_volumes(VolumeIds=[vol_id])
    actual_tags = {
        t["Key"]: t["Value"] for t in described["Volumes"][0].get("Tags", [])
    }
    assert actual_tags == {
        "CleanupCandidate": "true",
        "CleanupReason": "unattached",
    }


def test_tag_mode_blocks_terminate_instances(aws_env: None) -> None:
    """AC-13: tag モードで terminate_instances を呼ぶと
    DisallowedApiCallError になる。
    """
    factory = ClientFactory("tag")
    guarded_ec2 = factory.client("ec2", "ap-northeast-1")
    with pytest.raises(DisallowedApiCallError):
        guarded_ec2.terminate_instances(InstanceIds=["i-0123456789abcdef0"])


def test_execute_mode_rejects_candidate_key(aws_env: None) -> None:
    """AC-14: execute モードで CleanupCandidate を付けると
    DisallowedApiCallError になる。
    """
    plain_ec2 = boto3.client("ec2", region_name="ap-northeast-1")  # noqa: TID251
    vol = plain_ec2.create_volume(AvailabilityZone="ap-northeast-1a", Size=1)
    vol_id = vol["VolumeId"]

    factory = ClientFactory("execute")
    guarded_ec2 = factory.client("ec2", "ap-northeast-1")
    with pytest.raises(DisallowedApiCallError):
        guarded_ec2.create_tags(
            Resources=[vol_id],
            Tags=[{"Key": "CleanupCandidate", "Value": "true"}],
        )


def test_execute_mode_allows_stopped_at(aws_env: None) -> None:
    """AC-14: execute モードで CleanupStoppedAt のみなら正常にタグ付けできる。"""
    plain_ec2 = boto3.client("ec2", region_name="ap-northeast-1")  # noqa: TID251
    vol = plain_ec2.create_volume(AvailabilityZone="ap-northeast-1a", Size=1)
    vol_id = vol["VolumeId"]

    factory = ClientFactory("execute")
    guarded_ec2 = factory.client("ec2", "ap-northeast-1")
    guarded_ec2.create_tags(
        Resources=[vol_id],
        Tags=[{"Key": "CleanupStoppedAt", "Value": "2026-01-01T00:00:00Z"}],
    )

    described = plain_ec2.describe_volumes(VolumeIds=[vol_id])
    actual_tags = {
        t["Key"]: t["Value"] for t in described["Volumes"][0].get("Tags", [])
    }
    assert actual_tags == {"CleanupStoppedAt": "2026-01-01T00:00:00Z"}


def test_tag_mode_rds_and_elbv2_reject_keep(aws_env: None) -> None:
    """AC-15: tag モードの rds と elbv2 で Keep を指定すると
    DisallowedApiCallError になる。
    """
    factory = ClientFactory("tag")
    rds = factory.client("rds", "ap-northeast-1")
    with pytest.raises(DisallowedApiCallError):
        rds.add_tags_to_resource(
            ResourceName="arn:aws:rds:ap-northeast-1:123456789012:db:example",
            Tags=[{"Key": "Keep", "Value": "true"}],
        )

    elbv2 = factory.client("elbv2", "ap-northeast-1")
    with pytest.raises(DisallowedApiCallError):
        elbv2.add_tags(
            ResourceArns=[
                "arn:aws:elasticloadbalancing:ap-northeast-1:123456789012:loadbalancer/app/example/0123456789abcdef"
            ],
            Tags=[{"Key": "Keep", "Value": "true"}],
        )


def test_execute_mode_rds_rejects_candidate_key(aws_env: None) -> None:
    """AC-16: execute モードの rds で CleanupCandidate を指定すると
    DisallowedApiCallError になる。
    """
    factory = ClientFactory("execute")
    rds = factory.client("rds", "ap-northeast-1")
    with pytest.raises(DisallowedApiCallError):
        rds.add_tags_to_resource(
            ResourceName="arn:aws:rds:ap-northeast-1:123456789012:db:example",
            Tags=[{"Key": "CleanupCandidate", "Value": "true"}],
        )


def test_execute_mode_blocks_elbv2_add_tags(aws_env: None) -> None:
    """AC-16: execute モードの elbv2 で add_tags を呼ぶと
    キーに関係なく DisallowedApiCallError になる。
    """
    factory = ClientFactory("execute")
    elbv2 = factory.client("elbv2", "ap-northeast-1")
    with pytest.raises(DisallowedApiCallError):
        elbv2.add_tags(
            ResourceArns=[
                "arn:aws:elasticloadbalancing:ap-northeast-1:123456789012:loadbalancer/app/example/0123456789abcdef"
            ],
            Tags=[{"Key": "CleanupStoppedAt", "Value": "2026-01-01T00:00:00Z"}],
        )


def test_install_guard_on_plain_client(aws_env: None) -> None:
    """AC-18: 素の boto3 クライアントに install_guard すると許可外操作が拒否される。"""
    client = boto3.client("ec2", region_name="ap-northeast-1")  # noqa: TID251
    install_guard(client, "read")
    with pytest.raises(DisallowedApiCallError):
        client.run_instances(ImageId="ami-12345678", MinCount=1, MaxCount=1)
