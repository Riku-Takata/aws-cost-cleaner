"""Unit tests for collectors (EBS, EIP, NAT)."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from typing import Any

import boto3  # type: ignore[import-not-found]  # noqa: TID251
import pytest
from botocore.stub import Stubber  # type: ignore[import-not-found]
from moto import mock_aws  # type: ignore[import-not-found]

from aws_cost_cleaner.aws.session import ClientFactory
from aws_cost_cleaner.collectors import (
    COLLECTORS,
    CollectResult,
    ebs,
    eip,
    nat,
)


class FakeClientFactory:
    """Fake ClientFactory routing client requests to a specific client."""

    def __init__(self, ec2_client: Any) -> None:
        self.ec2_client = ec2_client

    def client(self, service: str, region: str) -> Any:
        if service == "ec2":
            return self.ec2_client
        raise ValueError(f"Unexpected service: {service}")


# --- AC-1: CollectResult import and immutability ---


def test_ac1_collect_result_import_and_frozen() -> None:
    assert dataclasses.is_dataclass(CollectResult)
    fields = {f.name for f in dataclasses.fields(CollectResult)}
    assert fields == {"resources", "skipped"}

    res = CollectResult(resources=(), skipped=())
    with pytest.raises(dataclasses.FrozenInstanceError):
        res.resources = ()  # type: ignore[misc]

    with pytest.raises(dataclasses.FrozenInstanceError):
        res.skipped = ()  # type: ignore[misc]


# --- AC-2: COLLECTORS keys and function mappings ---


def test_ac2_collectors_keys_and_values() -> None:
    assert "ebs_volume" in COLLECTORS
    assert "elastic_ip" in COLLECTORS
    assert "nat_gateway" in COLLECTORS

    assert COLLECTORS["ebs_volume"] is ebs.collect
    assert COLLECTORS["elastic_ip"] is eip.collect
    assert COLLECTORS["nat_gateway"] is nat.collect


# --- AC-3: ebs.collect with available volume ---


def test_ac3_ebs_collect_available_volume() -> None:
    with mock_aws():
        region = "ap-northeast-1"
        raw_ec2 = boto3.client("ec2", region_name=region)  # noqa: TID251
        vol = raw_ec2.create_volume(
            AvailabilityZone=f"{region}a",
            Size=12,
            VolumeType="gp3",
            Iops=3000,
            Throughput=125,
        )
        volume_id = vol["VolumeId"]
        create_time = vol["CreateTime"]

        factory = ClientFactory("read")
        result = ebs.collect(factory, region)

        assert len(result.resources) == 1
        r = result.resources[0]
        assert r.resource_type == "ebs_volume"
        assert r.region == region
        assert r.resource_id == volume_id
        assert r.arn is None
        assert r.state == "available"
        assert r.created_at == create_time

        assert "Size" in r.attrs
        assert "VolumeType" in r.attrs
        assert "Iops" in r.attrs
        assert "Throughput" in r.attrs
        assert r.attrs["Size"] == 12


# --- AC-4: ebs.collect ignores in-use volumes ---


def test_ac4_ebs_collect_ignores_in_use_volume() -> None:
    with mock_aws():
        region = "ap-northeast-1"
        raw_ec2 = boto3.client("ec2", region_name=region)  # noqa: TID251

        # available volume
        avail_vol = raw_ec2.create_volume(
            AvailabilityZone=f"{region}a",
            Size=8,
        )
        avail_id = avail_vol["VolumeId"]

        # attached (in-use) volume
        in_use_vol = raw_ec2.create_volume(
            AvailabilityZone=f"{region}a",
            Size=16,
        )
        in_use_id = in_use_vol["VolumeId"]

        instance = raw_ec2.run_instances(
            ImageId="ami-12345678",
            MinCount=1,
            MaxCount=1,
            InstanceType="t2.micro",
        )
        instance_id = instance["Instances"][0]["InstanceId"]
        raw_ec2.attach_volume(
            VolumeId=in_use_id,
            InstanceId=instance_id,
            Device="/dev/sdf",
        )

        factory = ClientFactory("read")
        result = ebs.collect(factory, region)

        collected_ids = [res.resource_id for res in result.resources]
        assert avail_id in collected_ids
        assert in_use_id not in collected_ids


# --- AC-5: eip.collect with unassociated EIP ---


def test_ac5_eip_collect_unassociated() -> None:
    with mock_aws():
        region = "ap-northeast-1"
        raw_ec2 = boto3.client("ec2", region_name=region)  # noqa: TID251

        # Allocate 2 EIPs
        eip1 = raw_ec2.allocate_address(Domain="vpc")
        eip2 = raw_ec2.allocate_address(Domain="vpc")

        # Associate eip2 to an instance
        instance = raw_ec2.run_instances(
            ImageId="ami-12345678",
            MinCount=1,
            MaxCount=1,
            InstanceType="t2.micro",
        )
        instance_id = instance["Instances"][0]["InstanceId"]
        raw_ec2.associate_address(
            AllocationId=eip2["AllocationId"],
            InstanceId=instance_id,
        )

        factory = ClientFactory("read")
        result = eip.collect(factory, region)

        assert len(result.resources) == 1
        r = result.resources[0]
        assert r.resource_type == "elastic_ip"
        assert r.resource_id == eip1["AllocationId"]
        assert r.name == eip1["PublicIp"]
        assert r.arn is None
        assert r.state == "unassociated"
        assert r.created_at is None
        assert r.attrs.get("PublicIp") == eip1["PublicIp"]

        collected_ids = [res.resource_id for res in result.resources]
        assert eip2["AllocationId"] not in collected_ids


# --- AC-6: nat.collect with available NAT Gateway ---


def test_ac6_nat_collect_available() -> None:
    with mock_aws():
        region = "ap-northeast-1"
        raw_ec2 = boto3.client("ec2", region_name=region)  # noqa: TID251

        vpc = raw_ec2.create_vpc(CidrBlock="10.0.0.0/16")
        subnet = raw_ec2.create_subnet(
            VpcId=vpc["Vpc"]["VpcId"],
            CidrBlock="10.0.1.0/24",
            AvailabilityZone=f"{region}a",
        )
        eip_alloc = raw_ec2.allocate_address(Domain="vpc")

        nat_gw = raw_ec2.create_nat_gateway(
            SubnetId=subnet["Subnet"]["SubnetId"],
            AllocationId=eip_alloc["AllocationId"],
        )
        nat_id = nat_gw["NatGateway"]["NatGatewayId"]
        create_time = nat_gw["NatGateway"]["CreateTime"]

        factory = ClientFactory("read")
        result = nat.collect(factory, region)

        assert len(result.resources) == 1
        r = result.resources[0]
        assert r.resource_type == "nat_gateway"
        assert r.resource_id == nat_id
        assert r.arn is None
        assert r.state == "available"
        assert r.created_at == create_time
        assert "ConnectivityType" in r.attrs


# --- AC-7: nat.collect ignores deleted NAT Gateway ---


def test_ac7_nat_collect_ignores_deleted() -> None:
    with mock_aws():
        region = "ap-northeast-1"
        raw_ec2 = boto3.client("ec2", region_name=region)  # noqa: TID251

        vpc = raw_ec2.create_vpc(CidrBlock="10.0.0.0/16")
        subnet = raw_ec2.create_subnet(
            VpcId=vpc["Vpc"]["VpcId"],
            CidrBlock="10.0.1.0/24",
            AvailabilityZone=f"{region}a",
        )
        eip_alloc = raw_ec2.allocate_address(Domain="vpc")

        nat_gw = raw_ec2.create_nat_gateway(
            SubnetId=subnet["Subnet"]["SubnetId"],
            AllocationId=eip_alloc["AllocationId"],
        )
        nat_id = nat_gw["NatGateway"]["NatGatewayId"]

        raw_ec2.delete_nat_gateway(NatGatewayId=nat_id)

        factory = ClientFactory("read")
        result = nat.collect(factory, region)

        collected_ids = [res.resource_id for res in result.resources]
        assert nat_id not in collected_ids


# --- AC-8: Name and Keep=true tags on EBS volume ---


def test_ac8_ebs_collect_keep_tag() -> None:
    with mock_aws():
        region = "ap-northeast-1"
        raw_ec2 = boto3.client("ec2", region_name=region)  # noqa: TID251

        vol = raw_ec2.create_volume(
            AvailabilityZone=f"{region}a",
            Size=20,
            TagSpecifications=[
                {
                    "ResourceType": "volume",
                    "Tags": [
                        {"Key": "Name", "Value": "protected-vol"},
                        {"Key": "Keep", "Value": "true"},
                    ],
                }
            ],
        )
        volume_id = vol["VolumeId"]

        factory = ClientFactory("read")
        result = ebs.collect(factory, region)

        assert len(result.resources) == 1
        r = result.resources[0]
        assert r.resource_id == volume_id
        assert dict(r.tags) == {"Name": "protected-vol", "Keep": "true"}
        assert r.name == "protected-vol"
        assert result.skipped == ()


# --- AC-9: Untagged resources have name is None and tags == {} ---


def test_ac9_untagged_ebs_and_nat() -> None:
    with mock_aws():
        region = "ap-northeast-1"
        raw_ec2 = boto3.client("ec2", region_name=region)  # noqa: TID251

        # EBS without tags
        raw_ec2.create_volume(
            AvailabilityZone=f"{region}a",
            Size=10,
        )

        # NAT without tags
        vpc = raw_ec2.create_vpc(CidrBlock="10.0.0.0/16")
        subnet = raw_ec2.create_subnet(
            VpcId=vpc["Vpc"]["VpcId"],
            CidrBlock="10.0.1.0/24",
            AvailabilityZone=f"{region}a",
        )
        eip_alloc = raw_ec2.allocate_address(Domain="vpc")
        raw_ec2.create_nat_gateway(
            SubnetId=subnet["Subnet"]["SubnetId"],
            AllocationId=eip_alloc["AllocationId"],
        )

        factory = ClientFactory("read")

        ebs_res = ebs.collect(factory, region)
        assert len(ebs_res.resources) == 1
        assert ebs_res.resources[0].name is None
        assert dict(ebs_res.resources[0].tags) == {}

        nat_res = nat.collect(factory, region)
        assert len(nat_res.resources) == 1
        assert nat_res.resources[0].name is None
        assert dict(nat_res.resources[0].tags) == {}


# --- AC-10: skipped is always empty tuple () ---


def test_ac10_skipped_is_empty_tuple() -> None:
    with mock_aws():
        region = "ap-northeast-1"
        factory = ClientFactory("read")

        assert ebs.collect(factory, region).skipped == ()
        assert eip.collect(factory, region).skipped == ()
        assert nat.collect(factory, region).skipped == ()


# --- AC-11: ebs.collect paginates DescribeVolumes with Stubber ---


def test_ac11_ebs_collect_pagination_stubber() -> None:
    ec2_client = boto3.client(  # noqa: TID251
        "ec2",
        region_name="ap-northeast-1",
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )
    stubber = Stubber(ec2_client)
    dt = datetime(2026, 10, 1, 12, 0, 0, tzinfo=UTC)

    page1: dict[str, Any] = {
        "Volumes": [
            {
                "VolumeId": "vol-page1",
                "State": "available",
                "CreateTime": dt,
                "Size": 10,
                "VolumeType": "gp3",
                "Iops": 3000,
                "Throughput": 125,
            }
        ],
        "NextToken": "next-token-1",
    }
    page2: dict[str, Any] = {
        "Volumes": [
            {
                "VolumeId": "vol-page2",
                "State": "available",
                "CreateTime": dt,
                "Size": 20,
                "VolumeType": "gp2",
            }
        ],
    }

    stubber.add_response("describe_volumes", page1, expected_params={})
    stubber.add_response(
        "describe_volumes", page2, expected_params={"NextToken": "next-token-1"}
    )
    stubber.activate()

    factory = FakeClientFactory(ec2_client)
    result = ebs.collect(factory, "ap-northeast-1")  # type: ignore[arg-type]

    assert len(result.resources) == 2
    resource_ids = {r.resource_id for r in result.resources}
    assert resource_ids == {"vol-page1", "vol-page2"}


# --- AC-12: nat.collect paginates DescribeNatGateways with Stubber ---


def test_ac12_nat_collect_pagination_stubber() -> None:
    ec2_client = boto3.client(  # noqa: TID251
        "ec2",
        region_name="ap-northeast-1",
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )
    stubber = Stubber(ec2_client)
    dt = datetime(2026, 10, 1, 12, 0, 0, tzinfo=UTC)

    page1: dict[str, Any] = {
        "NatGateways": [
            {
                "NatGatewayId": "nat-page1",
                "State": "available",
                "CreateTime": dt,
                "ConnectivityType": "public",
            }
        ],
        "NextToken": "token-nat-1",
    }
    page2: dict[str, Any] = {
        "NatGateways": [
            {
                "NatGatewayId": "nat-page2",
                "State": "available",
                "CreateTime": dt,
                "ConnectivityType": "private",
            }
        ],
    }

    stubber.add_response("describe_nat_gateways", page1, expected_params={})
    stubber.add_response(
        "describe_nat_gateways",
        page2,
        expected_params={"NextToken": "token-nat-1"},
    )
    stubber.activate()

    factory = FakeClientFactory(ec2_client)
    result = nat.collect(factory, "ap-northeast-1")  # type: ignore[arg-type]

    assert len(result.resources) == 2
    resource_ids = {r.resource_id for r in result.resources}
    assert resource_ids == {"nat-page1", "nat-page2"}


# --- AC-13: to_resource specification examples ---


def test_ac13_to_resource_module_functions() -> None:
    dt = datetime(2026, 5, 1, 10, 0, 0, tzinfo=UTC)

    # EBS
    ebs_item: dict[str, Any] = {
        "VolumeId": "vol-1",
        "State": "available",
        "CreateTime": dt,
        "Size": 8,
        "VolumeType": "gp3",
        "Iops": 3000,
        "Throughput": 125,
        "Tags": [{"Key": "Name", "Value": "n"}],
    }
    ebs_res = ebs.to_resource(ebs_item, "us-east-1")
    assert ebs_res.resource_type == "ebs_volume"
    assert ebs_res.region == "us-east-1"
    assert ebs_res.resource_id == "vol-1"
    assert ebs_res.arn is None
    assert ebs_res.name == "n"
    assert dict(ebs_res.tags) == {"Name": "n"}
    assert ebs_res.state == "available"
    assert ebs_res.created_at == dt
    assert ebs_res.attrs["Size"] == 8
    assert ebs_res.attrs["VolumeType"] == "gp3"
    assert ebs_res.attrs["Iops"] == 3000
    assert ebs_res.attrs["Throughput"] == 125

    # EIP
    eip_item: dict[str, Any] = {
        "AllocationId": "eipalloc-1",
        "PublicIp": "203.0.113.1",
        "Domain": "vpc",
    }
    eip_res = eip.to_resource(eip_item, "us-east-1")
    assert eip_res.resource_type == "elastic_ip"
    assert eip_res.region == "us-east-1"
    assert eip_res.resource_id == "eipalloc-1"
    assert eip_res.name == "203.0.113.1"
    assert eip_res.state == "unassociated"
    assert eip_res.created_at is None
    assert eip_res.arn is None
    assert dict(eip_res.tags) == {}
    assert eip_res.attrs["PublicIp"] == "203.0.113.1"

    # NAT
    nat_item: dict[str, Any] = {
        "NatGatewayId": "nat-1",
        "State": "available",
        "CreateTime": dt,
        "ConnectivityType": "public",
    }
    nat_res = nat.to_resource(nat_item, "us-east-1")
    assert nat_res.resource_type == "nat_gateway"
    assert nat_res.region == "us-east-1"
    assert nat_res.resource_id == "nat-1"
    assert nat_res.state == "available"
    assert nat_res.created_at == dt
    assert nat_res.name is None
    assert dict(nat_res.tags) == {}
    assert nat_res.arn is None
    assert nat_res.attrs["ConnectivityType"] == "public"


# --- AC-14: to_resource handles missing attrs keys with None ---


def test_ac14_to_resource_missing_attrs_keys_set_to_none() -> None:
    # EBS without Iops, Throughput, Size, VolumeType
    ebs_res = ebs.to_resource({"VolumeId": "vol-min"}, "ap-northeast-1")
    assert "Size" in ebs_res.attrs and ebs_res.attrs["Size"] is None
    assert "VolumeType" in ebs_res.attrs and ebs_res.attrs["VolumeType"] is None
    assert "Iops" in ebs_res.attrs and ebs_res.attrs["Iops"] is None
    assert "Throughput" in ebs_res.attrs and ebs_res.attrs["Throughput"] is None

    # EIP without PublicIp
    eip_res = eip.to_resource({"AllocationId": "eip-min"}, "ap-northeast-1")
    assert "PublicIp" in eip_res.attrs and eip_res.attrs["PublicIp"] is None

    # NAT without ConnectivityType
    nat_res = nat.to_resource({"NatGatewayId": "nat-min"}, "ap-northeast-1")
    assert (
        "ConnectivityType" in nat_res.attrs
        and nat_res.attrs["ConnectivityType"] is None
    )


# --- AC-15: ClientFactory("read") does not raise DisallowedApiCallError ---


def test_ac15_read_guard_allows_collector_apis() -> None:
    with mock_aws():
        factory = ClientFactory("read")
        region = "ap-northeast-1"

        # Should not raise DisallowedApiCallError
        res_ebs = ebs.collect(factory, region)
        assert isinstance(res_ebs, CollectResult)

        res_eip = eip.collect(factory, region)
        assert isinstance(res_eip, CollectResult)

        res_nat = nat.collect(factory, region)
        assert isinstance(res_nat, CollectResult)
