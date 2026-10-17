"""Unit tests for collectors (ELB, EC2, RDS)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import boto3  # type: ignore[import-not-found]  # noqa: TID251
from botocore.stub import Stubber  # type: ignore[import-not-found]
from moto import mock_aws  # type: ignore[import-not-found]

from aws_cost_cleaner.aws.session import ClientFactory
from aws_cost_cleaner.collectors import COLLECTORS, ebs, ec2, eip, elb, nat, rds
from aws_cost_cleaner.models import Skipped

REGION = "ap-northeast-1"


class _Factory:
    def __init__(self, **clients: Any) -> None:
        self.clients = clients

    def client(self, service: str, region: str) -> Any:
        return self.clients[service]


def _vpc() -> tuple[Any, str, list[str]]:
    ec2c = boto3.client("ec2", region_name=REGION)  # noqa: TID251
    vpc = ec2c.create_vpc(CidrBlock="10.0.0.0/16")["Vpc"]["VpcId"]
    subnets = [
        ec2c.create_subnet(
            VpcId=vpc, CidrBlock=f"10.0.{i}.0/24", AvailabilityZone=f"{REGION}{az}"
        )["Subnet"]["SubnetId"]
        for i, az in enumerate("ac")
    ]
    return ec2c, vpc, subnets


def _elbv2() -> Any:
    return boto3.client("elbv2", region_name=REGION)  # noqa: TID251


def _lb(client: Any, subnets: list[str], name: str = "lb1", **kw: Any) -> str:
    lbs = client.create_load_balancer(Name=name, Subnets=subnets, **kw)
    return str(lbs["LoadBalancers"][0]["LoadBalancerArn"])


def _tg_with_targets(
    ec2c: Any, elb_c: Any, vpc: str, lb_arn: str, name: str, n: int, port: int
) -> None:
    tg = elb_c.create_target_group(
        Name=name, Protocol="HTTP", Port=80, VpcId=vpc, TargetType="instance"
    )["TargetGroups"][0]["TargetGroupArn"]
    elb_c.create_listener(
        LoadBalancerArn=lb_arn,
        Protocol="HTTP",
        Port=port,
        DefaultActions=[{"Type": "forward", "TargetGroupArn": tg}],
    )
    ids = [
        i["InstanceId"]
        for i in ec2c.run_instances(ImageId="ami-12345678", MinCount=n, MaxCount=n)[
            "Instances"
        ]
    ]
    elb_c.register_targets(TargetGroupArn=tg, Targets=[{"Id": i} for i in ids])


def test_ac1_ac2_registry() -> None:
    assert set(COLLECTORS) == {
        "ebs_volume",
        "elastic_ip",
        "nat_gateway",
        "load_balancer",
        "ec2_instance",
        "rds_instance",
    }
    assert COLLECTORS["load_balancer"] is elb.collect
    assert COLLECTORS["ec2_instance"] is ec2.collect
    assert COLLECTORS["rds_instance"] is rds.collect
    assert COLLECTORS["ebs_volume"] is ebs.collect
    assert COLLECTORS["elastic_ip"] is eip.collect
    assert COLLECTORS["nat_gateway"] is nat.collect


@mock_aws
def test_ac3_ac6_ec2_running() -> None:
    c = boto3.client("ec2", region_name=REGION)  # noqa: TID251
    inst = c.run_instances(
        ImageId="ami-12345678", MinCount=1, MaxCount=1, InstanceType="t3.micro"
    )["Instances"][0]
    res = ec2.collect(ClientFactory("read"), REGION)
    assert res.skipped == ()
    assert len(res.resources) == 1
    r = res.resources[0]
    desc = c.describe_instances()["Reservations"][0]["Instances"][0]
    assert r.resource_type == "ec2_instance"
    assert r.region == REGION
    assert r.resource_id == inst["InstanceId"]
    assert r.arn is None
    assert r.state == "running"
    assert r.created_at == desc["LaunchTime"]
    assert r.attrs["InstanceType"] == "t3.micro"
    assert r.attrs["Platform"] is None


@mock_aws
def test_ac4_ac5_ec2_stopped_and_terminated() -> None:
    c = boto3.client("ec2", region_name=REGION)  # noqa: TID251
    plain = c.run_instances(ImageId="ami-12345678", MinCount=1, MaxCount=1)
    cand = c.run_instances(
        ImageId="ami-12345678",
        MinCount=1,
        MaxCount=1,
        TagSpecifications=[
            {
                "ResourceType": "instance",
                "Tags": [{"Key": "CleanupCandidate", "Value": "true"}],
            }
        ],
    )
    gone = c.run_instances(ImageId="ami-12345678", MinCount=1, MaxCount=1)
    pid = plain["Instances"][0]["InstanceId"]
    cid = cand["Instances"][0]["InstanceId"]
    gid = gone["Instances"][0]["InstanceId"]
    c.stop_instances(InstanceIds=[pid, cid])
    c.terminate_instances(InstanceIds=[gid])
    res = ec2.collect(ClientFactory("read"), REGION)
    assert [r.resource_id for r in res.resources] == [cid]
    assert res.resources[0].state == "stopped"
    assert res.resources[0].tags["CleanupCandidate"] == "true"


def test_ac7_ec2_to_resource() -> None:
    dt = datetime(2026, 1, 1, tzinfo=UTC)
    item = {
        "InstanceId": "i-1",
        "State": {"Name": "running"},
        "LaunchTime": dt,
        "InstanceType": "t3.micro",
        "Platform": "windows",
        "Tags": [{"Key": "Name", "Value": "web"}],
    }
    r = ec2.to_resource(item, "us-east-1")
    assert r.resource_id == "i-1"
    assert r.state == "running"
    assert r.created_at == dt
    assert r.attrs == {"InstanceType": "t3.micro", "Platform": "windows"}
    assert r.name == "web"
    assert r.tags == {"Name": "web"}
    assert r.arn is None
    del item["Tags"]
    r2 = ec2.to_resource(item, "us-east-1")
    assert r2.name is None
    assert r2.tags == {}


def _rds_create(c: Any, ident: str, **kw: Any) -> None:
    c.create_db_instance(
        DBInstanceIdentifier=ident,
        Engine="mysql",
        DBInstanceClass="db.t3.micro",
        AllocatedStorage=20,
        MasterUsername="admin",
        MasterUserPassword="password123",
        **kw,
    )


@mock_aws
def test_ac8_rds_available() -> None:
    c = boto3.client("rds", region_name=REGION)  # noqa: TID251
    _rds_create(c, "db1")
    res = rds.collect(ClientFactory("read"), REGION)
    assert len(res.resources) == 1
    r = res.resources[0]
    d = c.describe_db_instances()["DBInstances"][0]
    assert r.resource_type == "rds_instance"
    assert r.resource_id == d["DBInstanceIdentifier"]
    assert r.arn == d["DBInstanceArn"]
    assert r.state == "available"
    assert r.created_at == d["InstanceCreateTime"]
    assert set(r.attrs) == {
        "Engine",
        "DBInstanceClass",
        "MultiAZ",
        "AllocatedStorage",
        "StorageType",
        "Iops",
    }
    for k in (
        "Engine",
        "DBInstanceClass",
        "MultiAZ",
        "AllocatedStorage",
        "StorageType",
    ):
        assert r.attrs[k] == d[k]


@mock_aws
def test_ac9_rds_stopped_candidate() -> None:
    c = boto3.client("rds", region_name=REGION)  # noqa: TID251
    _rds_create(c, "plain")
    _rds_create(c, "cand", Tags=[{"Key": "CleanupCandidate", "Value": "true"}])
    c.stop_db_instance(DBInstanceIdentifier="plain")
    c.stop_db_instance(DBInstanceIdentifier="cand")
    res = rds.collect(ClientFactory("read"), REGION)
    assert [r.resource_id for r in res.resources] == ["cand"]
    assert res.resources[0].state == "stopped"
    assert res.resources[0].tags["CleanupCandidate"] == "true"


def test_ac10_rds_aurora_skipped() -> None:
    c = boto3.client("rds", region_name=REGION)  # noqa: TID251
    items = [
        {"DBInstanceIdentifier": "a", "Engine": "aurora-postgresql"},
        {"DBInstanceIdentifier": "b", "Engine": "mysql", "DBClusterIdentifier": "c1"},
        {"DBInstanceIdentifier": "c", "Engine": "aurora"},
    ]
    with Stubber(c) as stub:
        stub.add_response("describe_db_instances", {"DBInstances": items})
        res = rds.collect(_Factory(rds=c), REGION)  # type: ignore[arg-type]
    assert res.resources == ()
    assert res.skipped == tuple(
        Skipped(
            resource_type="rds_instance",
            region=REGION,
            resource_id=i,
            skip_reason="aurora",
        )
        for i in "abc"
    )


def test_ac11_rds_to_resource() -> None:
    item = {
        "DBInstanceIdentifier": "d",
        "TagList": [{"Key": "Name", "Value": "db"}],
    }
    r = rds.to_resource(item, "us-east-1")
    assert r.attrs["Iops"] is None
    assert r.tags == {"Name": "db"}
    assert r.name == "db"


@mock_aws
def test_ac12_ac16_elb_alb_with_tags() -> None:
    _, _, subnets = _vpc()
    c = _elbv2()
    _lb(
        c,
        subnets,
        Type="application",
        Tags=[{"Key": "Name", "Value": "lb1"}, {"Key": "Keep", "Value": "true"}],
    )
    res = elb.collect(ClientFactory("read"), REGION)
    assert res.skipped == ()
    assert len(res.resources) == 1
    r = res.resources[0]
    d = c.describe_load_balancers()["LoadBalancers"][0]
    assert r.resource_type == "load_balancer"
    assert r.resource_id == d["LoadBalancerName"]
    assert r.arn == d["LoadBalancerArn"]
    assert r.state == d["State"]["Code"]
    assert r.created_at == d["CreatedTime"]
    assert r.attrs["Type"] == "application"
    assert r.attrs["registered_target_count"] == 0
    assert r.tags == {"Name": "lb1", "Keep": "true"}
    assert r.name == "lb1"


@mock_aws
def test_ac13_ac14_elb_target_counts() -> None:
    ec2c, vpc, subnets = _vpc()
    c = _elbv2()
    arn = _lb(c, subnets, Type="application")
    _tg_with_targets(ec2c, c, vpc, arn, "tg1", 1, 80)
    res = elb.collect(ClientFactory("read"), REGION)
    assert res.resources[0].attrs["registered_target_count"] == 1
    _tg_with_targets(ec2c, c, vpc, arn, "tg2", 2, 81)
    res = elb.collect(ClientFactory("read"), REGION)
    assert res.resources[0].attrs["registered_target_count"] == 3


@mock_aws
def test_ac15_ac17_elb_types() -> None:
    _, _, subnets = _vpc()
    c = _elbv2()
    _lb(c, subnets, "nlb", Type="network")
    _lb(c, subnets, "gwlb", Type="gateway")
    res = elb.collect(ClientFactory("read"), REGION)
    assert [r.resource_id for r in res.resources] == ["nlb"]
    assert res.resources[0].attrs["Type"] == "network"
    assert res.skipped == (
        Skipped(
            resource_type="load_balancer",
            region=REGION,
            resource_id="gwlb",
            skip_reason="unsupported_lb_type",
        ),
    )


def test_ac18_elb_to_resource() -> None:
    dt = datetime(2026, 1, 1, tzinfo=UTC)
    item = {
        "LoadBalancerName": "lb1",
        "LoadBalancerArn": "arn:x",
        "Type": "network",
        "State": {"Code": "active"},
        "CreatedTime": dt,
    }
    r = elb.to_resource(item, "us-east-1", {"Name": "n"}, 2)
    assert r.resource_id == "lb1"
    assert r.arn == "arn:x"
    assert r.state == "active"
    assert r.created_at == dt
    assert r.tags == {"Name": "n"}
    assert r.name == "n"
    assert r.attrs == {"Type": "network", "registered_target_count": 2}


@mock_aws
def test_ac19_read_allowlist() -> None:
    ec2c, vpc, subnets = _vpc()
    c = _elbv2()
    arn = _lb(c, subnets, Type="application")
    _tg_with_targets(ec2c, c, vpc, arn, "tg1", 1, 80)
    _rds_create(boto3.client("rds", region_name=REGION), "db1")  # noqa: TID251
    factory = ClientFactory("read")
    elb.collect(factory, REGION)
    ec2.collect(factory, REGION)
    rds.collect(factory, REGION)
