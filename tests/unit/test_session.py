"""Unit tests for ClientFactory (session.py)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from moto import mock_aws

from aws_cost_cleaner.aws.session import ClientFactory


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


def test_client_is_cached_per_service_and_region(aws_env: None) -> None:
    """AC-17: 同一サービス・リージョンでは同一クライアントを返し、
    リージョンが違えば別オブジェクトを返す。
    """
    factory = ClientFactory("read")
    c1 = factory.client("ec2", "ap-northeast-1")
    c2 = factory.client("ec2", "ap-northeast-1")
    assert c1 is c2

    c3 = factory.client("ec2", "us-east-1")
    assert c1 is not c3


def test_client_different_service(aws_env: None) -> None:
    """異なるサービスでは別のクライアントオブジェクトを返す。"""
    factory = ClientFactory("read")
    ec2 = factory.client("ec2", "ap-northeast-1")
    sts = factory.client("sts", "ap-northeast-1")
    assert ec2 is not sts
