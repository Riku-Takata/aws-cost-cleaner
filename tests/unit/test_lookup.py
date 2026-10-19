"""Unit tests for lookup.fetch."""

from __future__ import annotations

from typing import Any

import boto3  # type: ignore[import-not-found]  # noqa: TID251
import pytest
from botocore.stub import Stubber  # type: ignore[import-not-found]
from moto import mock_aws  # type: ignore[import-not-found]

from aws_cost_cleaner.aws.session import ClientFactory
from aws_cost_cleaner.errors import AwsOperationError, StateMismatchError
from aws_cost_cleaner.lookup import NOT_FOUND_CODES, fetch
from aws_cost_cleaner.models import Action, ApprovalItem, ResourceType

REGION = "ap-northeast-1"

_ALLOWED_DESCRIBE_OPS = frozenset(
    {
        "ec2:DescribeVolumes",
        "ec2:DescribeAddresses",
        "ec2:DescribeNatGateways",
        "ec2:DescribeInstances",
        "elbv2:DescribeLoadBalancers",
        "elbv2:DescribeTags",
        "elbv2:DescribeTargetGroups",
        "elbv2:DescribeTargetHealth",
        "rds:DescribeDBInstances",
    }
)


def _item(
    resource_type: ResourceType,
    resource_id: str,
    arn: str | None = None,
    action: Action = "Delete",
) -> ApprovalItem:
    return ApprovalItem(
        resource_type=resource_type,
        region=REGION,
        resource_id=resource_id,
        arn=arn,
        name=None,
        recommended_action=action,
        reason_code="test",
        estimated_monthly_usd=10.0,
        approved=True,
    )


def _vpc() -> tuple[Any, str, list[str]]:
    ec2c = boto3.client("ec2", region_name=REGION)  # noqa: TID251
    vpc = ec2c.create_vpc(CidrBlock="10.0.0.0/16")["Vpc"]["VpcId"]
    subnets = [
        ec2c.create_subnet(
            VpcId=vpc,
            CidrBlock=f"10.0.{i}.0/24",
            AvailabilityZone=f"{REGION}{az}",
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


def _setup_ebs() -> tuple[str, ApprovalItem]:
    c = boto3.client("ec2", region_name=REGION)  # noqa: TID251
    vol = c.create_volume(
        AvailabilityZone=f"{REGION}a",
        Size=10,
        TagSpecifications=[
            {
                "ResourceType": "volume",
                "Tags": [
                    {"Key": "Env", "Value": "prod"},
                    {"Key": "Name", "Value": "my-vol"},
                ],
            }
        ],
    )
    vol_id = str(vol["VolumeId"])
    return vol_id, _item("ebs_volume", vol_id)


def _setup_eip_unassociated() -> tuple[str, ApprovalItem]:
    c = boto3.client("ec2", region_name=REGION)  # noqa: TID251
    alloc = c.allocate_address(Domain="vpc")
    alloc_id = str(alloc["AllocationId"])
    return alloc_id, _item("elastic_ip", alloc_id)


def _setup_eip_associated() -> tuple[str, str, ApprovalItem]:
    c = boto3.client("ec2", region_name=REGION)  # noqa: TID251
    inst = c.run_instances(ImageId="ami-12345678", MinCount=1, MaxCount=1)["Instances"][
        0
    ]
    alloc = c.allocate_address(Domain="vpc")
    alloc_id = str(alloc["AllocationId"])
    assoc = c.associate_address(AllocationId=alloc_id, InstanceId=inst["InstanceId"])
    assoc_id = str(assoc["AssociationId"])
    return alloc_id, assoc_id, _item("elastic_ip", alloc_id)


def _setup_nat() -> tuple[str, ApprovalItem]:
    ec2c, _vpc_id, subnets = _vpc()
    alloc = ec2c.allocate_address(Domain="vpc")
    nat_gw = ec2c.create_nat_gateway(
        SubnetId=subnets[0],
        AllocationId=alloc["AllocationId"],
    )["NatGateway"]
    nat_id = str(nat_gw["NatGatewayId"])
    return nat_id, _item("nat_gateway", nat_id)


def _setup_alb() -> tuple[str, str, ApprovalItem]:
    ec2c, vpc_id, subnets = _vpc()
    elb_c = _elbv2()
    lb_name = "test-alb"
    lb_arn = _lb(elb_c, subnets, name=lb_name)
    elb_c.add_tags(
        ResourceArns=[lb_arn],
        Tags=[
            {"Key": "Env", "Value": "staging"},
            {"Key": "Name", "Value": "app-lb"},
        ],
    )
    _tg_with_targets(ec2c, elb_c, vpc_id, lb_arn, "tg1", 1, 8080)
    return lb_name, lb_arn, _item("load_balancer", lb_name, arn=lb_arn)


def _setup_ec2_running() -> tuple[str, ApprovalItem]:
    c = boto3.client("ec2", region_name=REGION)  # noqa: TID251
    inst = c.run_instances(
        ImageId="ami-12345678",
        MinCount=1,
        MaxCount=1,
        InstanceType="t3.micro",
        TagSpecifications=[
            {
                "ResourceType": "instance",
                "Tags": [
                    {"Key": "Role", "Value": "web"},
                    {"Key": "Name", "Value": "web-1"},
                ],
            }
        ],
    )["Instances"][0]
    inst_id = str(inst["InstanceId"])
    return inst_id, _item("ec2_instance", inst_id)


def _setup_ec2_stopped() -> tuple[str, ApprovalItem]:
    c = boto3.client("ec2", region_name=REGION)  # noqa: TID251
    inst = c.run_instances(
        ImageId="ami-12345678",
        MinCount=1,
        MaxCount=1,
        InstanceType="t3.micro",
    )["Instances"][0]
    inst_id = str(inst["InstanceId"])
    c.stop_instances(InstanceIds=[inst_id])
    return inst_id, _item("ec2_instance", inst_id)


def _setup_rds_available() -> tuple[str, ApprovalItem]:
    rdsc = boto3.client("rds", region_name=REGION)  # noqa: TID251
    db_id = "test-db-1"
    rdsc.create_db_instance(
        DBInstanceIdentifier=db_id,
        DBInstanceClass="db.t3.micro",
        Engine="postgres",
        Tags=[
            {"Key": "Project", "Value": "cost"},
            {"Key": "Name", "Value": "db-1"},
        ],
    )
    return db_id, _item("rds_instance", db_id)


def _setup_rds_stopped() -> tuple[str, ApprovalItem]:
    rdsc = boto3.client("rds", region_name=REGION)  # noqa: TID251
    db_id = "test-db-2"
    rdsc.create_db_instance(
        DBInstanceIdentifier=db_id,
        DBInstanceClass="db.t3.micro",
        Engine="postgres",
    )
    rdsc.stop_db_instance(DBInstanceIdentifier=db_id)
    return db_id, _item("rds_instance", db_id)


def test_ac1_constants() -> None:
    expected_codes = {
        "InvalidVolume.NotFound",
        "InvalidAllocationID.NotFound",
        "NatGatewayNotFound",
        "LoadBalancerNotFound",
        "InvalidInstanceID.NotFound",
        "DBInstanceNotFound",
    }
    assert set(NOT_FOUND_CODES) == expected_codes
    assert callable(fetch)


@mock_aws
def test_ac2_ebs() -> None:
    vol_id, item = _setup_ebs()
    res = fetch(ClientFactory("tag"), item)
    assert res.resource_type == "ebs_volume"
    assert res.resource_id == vol_id
    assert res.state == "available"
    assert res.region == item.region
    assert res.tags == {"Env": "prod", "Name": "my-vol"}


@mock_aws
def test_ac3_eip_unassociated() -> None:
    alloc_id, item = _setup_eip_unassociated()
    res = fetch(ClientFactory("tag"), item)
    assert res.resource_type == "elastic_ip"
    assert res.resource_id == alloc_id
    assert res.state == "unassociated"
    assert "AssociationId" in res.attrs
    assert res.attrs["AssociationId"] is None


@mock_aws
def test_ac4_eip_associated() -> None:
    alloc_id, assoc_id, item = _setup_eip_associated()
    res = fetch(ClientFactory("tag"), item)
    assert res.resource_type == "elastic_ip"
    assert res.resource_id == alloc_id
    assert res.state == "associated"
    assert res.attrs["AssociationId"] is not None
    assert res.attrs["AssociationId"] == assoc_id


@mock_aws
def test_ac5_nat() -> None:
    nat_id, item = _setup_nat()
    c = boto3.client("ec2", region_name=REGION)  # noqa: TID251
    expected_state = c.describe_nat_gateways(NatGatewayIds=[nat_id])["NatGateways"][0][
        "State"
    ]
    res = fetch(ClientFactory("tag"), item)
    assert res.resource_type == "nat_gateway"
    assert res.resource_id == nat_id
    assert res.state == expected_state
    assert res.state == "available"


@mock_aws
def test_ac6_elb() -> None:
    lb_name, lb_arn, item = _setup_alb()
    elb_c = _elbv2()
    expected_state = elb_c.describe_load_balancers(LoadBalancerArns=[lb_arn])[
        "LoadBalancers"
    ][0]["State"]["Code"]
    res = fetch(ClientFactory("tag"), item)
    assert res.resource_type == "load_balancer"
    assert res.resource_id == lb_name
    assert res.arn == lb_arn
    assert res.state == expected_state
    assert res.tags == {"Env": "staging", "Name": "app-lb"}
    assert res.attrs["registered_target_count"] == 1


@mock_aws
def test_ac7_ec2_running() -> None:
    inst_id, item = _setup_ec2_running()
    res = fetch(ClientFactory("tag"), item)
    assert res.resource_type == "ec2_instance"
    assert res.resource_id == inst_id
    assert res.state == "running"
    assert res.tags == {"Role": "web", "Name": "web-1"}


@mock_aws
def test_ac8_ec2_stopped() -> None:
    inst_id, item = _setup_ec2_stopped()
    res = fetch(ClientFactory("tag"), item)
    assert res.resource_type == "ec2_instance"
    assert res.resource_id == inst_id
    assert res.state == "stopped"


@mock_aws
def test_ac9_rds_available() -> None:
    db_id, item = _setup_rds_available()
    res = fetch(ClientFactory("tag"), item)
    assert res.resource_type == "rds_instance"
    assert res.resource_id == db_id
    assert res.state == "available"
    assert res.tags == {"Project": "cost", "Name": "db-1"}


@mock_aws
def test_ac10_rds_stopped() -> None:
    db_id, item = _setup_rds_stopped()
    res = fetch(ClientFactory("tag"), item)
    assert res.resource_type == "rds_instance"
    assert res.resource_id == db_id
    assert res.state == "stopped"


@mock_aws
@pytest.mark.parametrize(
    ("res_type", "res_id", "arn"),
    [
        ("ebs_volume", "vol-0123456789abcdef0", None),
        ("elastic_ip", "eipalloc-0123456789abcdef0", None),
        (
            "load_balancer",
            "nope",
            "arn:aws:elasticloadbalancing:ap-northeast-1:123456789012:loadbalancer/app/nope/0123456789abcdef",
        ),
        ("ec2_instance", "i-0123456789abcdef0", None),
        ("rds_instance", "nope", None),
    ],
)
def test_ac11_missing_ids(res_type: ResourceType, res_id: str, arn: str | None) -> None:
    item = _item(res_type, res_id, arn=arn)
    with pytest.raises(StateMismatchError):
        fetch(ClientFactory("tag"), item)


@mock_aws
def test_ac12_missing_nat() -> None:
    item = _item("nat_gateway", "nat-0123456789abcdef0")
    with pytest.raises(StateMismatchError):
        fetch(ClientFactory("tag"), item)


_STUBBER_PARAMS: list[
    tuple[
        ResourceType,
        str,
        str,
        dict[str, Any],
        dict[str, Any],
        str,
        str,
        str | None,
    ]
] = [
    (
        "ebs_volume",
        "ec2",
        "describe_volumes",
        {"VolumeIds": ["vol-1234567890abcdef0"]},
        {"Volumes": []},
        "InvalidVolume.NotFound",
        "vol-1234567890abcdef0",
        None,
    ),
    (
        "elastic_ip",
        "ec2",
        "describe_addresses",
        {"AllocationIds": ["eipalloc-1234567890abcdef0"]},
        {"Addresses": []},
        "InvalidAllocationID.NotFound",
        "eipalloc-1234567890abcdef0",
        None,
    ),
    (
        "nat_gateway",
        "ec2",
        "describe_nat_gateways",
        {"NatGatewayIds": ["nat-1234567890abcdef0"]},
        {"NatGateways": []},
        "NatGatewayNotFound",
        "nat-1234567890abcdef0",
        None,
    ),
    (
        "load_balancer",
        "elbv2",
        "describe_load_balancers",
        {
            "LoadBalancerArns": [
                "arn:aws:elasticloadbalancing:ap-northeast-1:123456789012:loadbalancer/app/lb/1234567890abcdef"
            ]
        },
        {"LoadBalancers": []},
        "LoadBalancerNotFound",
        "lb",
        "arn:aws:elasticloadbalancing:ap-northeast-1:123456789012:loadbalancer/app/lb/1234567890abcdef",
    ),
    (
        "ec2_instance",
        "ec2",
        "describe_instances",
        {"InstanceIds": ["i-1234567890abcdef0"]},
        {"Reservations": []},
        "InvalidInstanceID.NotFound",
        "i-1234567890abcdef0",
        None,
    ),
    (
        "rds_instance",
        "rds",
        "describe_db_instances",
        {"DBInstanceIdentifier": "db-1234567890abcdef0"},
        {"DBInstances": []},
        "DBInstanceNotFound",
        "db-1234567890abcdef0",
        None,
    ),
]


@mock_aws
@pytest.mark.parametrize(
    (
        "resource_type",
        "service",
        "operation",
        "expected_params",
        "empty_response",
        "not_found_code",
        "res_id",
        "arn",
    ),
    _STUBBER_PARAMS,
)
def test_ac13_not_found_codes(
    resource_type: ResourceType,
    service: str,
    operation: str,
    expected_params: dict[str, Any],
    empty_response: dict[str, Any],
    not_found_code: str,
    res_id: str,
    arn: str | None,
) -> None:
    factory = ClientFactory("tag")
    client = factory.client(service, REGION)
    stubber = Stubber(client)
    stubber.add_client_error(
        operation,
        service_error_code=not_found_code,
        expected_params=expected_params,
    )
    with stubber:
        item = _item(resource_type, res_id, arn=arn)
        with pytest.raises(StateMismatchError):
            fetch(factory, item)
    stubber.assert_no_pending_responses()


@mock_aws
@pytest.mark.parametrize(
    (
        "resource_type",
        "service",
        "operation",
        "expected_params",
        "empty_response",
        "not_found_code",
        "res_id",
        "arn",
    ),
    _STUBBER_PARAMS,
)
def test_ac14_empty_list(
    resource_type: ResourceType,
    service: str,
    operation: str,
    expected_params: dict[str, Any],
    empty_response: dict[str, Any],
    not_found_code: str,
    res_id: str,
    arn: str | None,
) -> None:
    factory = ClientFactory("tag")
    client = factory.client(service, REGION)
    stubber = Stubber(client)
    stubber.add_response(operation, empty_response, expected_params=expected_params)
    with stubber:
        item = _item(resource_type, res_id, arn=arn)
        with pytest.raises(StateMismatchError):
            fetch(factory, item)
    stubber.assert_no_pending_responses()


@mock_aws
def test_ac15_nat_deleted() -> None:
    factory = ClientFactory("tag")
    client = factory.client("ec2", REGION)
    stubber = Stubber(client)
    stubber.add_response(
        "describe_nat_gateways",
        {
            "NatGateways": [
                {"NatGatewayId": "nat-1234567890abcdef0", "State": "deleted"}
            ]
        },
        expected_params={"NatGatewayIds": ["nat-1234567890abcdef0"]},
    )
    with stubber:
        item = _item("nat_gateway", "nat-1234567890abcdef0")
        with pytest.raises(StateMismatchError):
            fetch(factory, item)
    stubber.assert_no_pending_responses()


@mock_aws
def test_ac16_ec2_terminated() -> None:
    c = boto3.client("ec2", region_name=REGION)  # noqa: TID251
    inst = c.run_instances(ImageId="ami-12345678", MinCount=1, MaxCount=1)["Instances"][
        0
    ]
    inst_id = str(inst["InstanceId"])
    c.terminate_instances(InstanceIds=[inst_id])
    item = _item("ec2_instance", inst_id)
    with pytest.raises(StateMismatchError):
        fetch(ClientFactory("tag"), item)


@mock_aws
@pytest.mark.parametrize(
    (
        "resource_type",
        "service",
        "operation",
        "expected_params",
        "empty_response",
        "not_found_code",
        "res_id",
        "arn",
    ),
    _STUBBER_PARAMS,
)
def test_ac17_other_client_error(
    resource_type: ResourceType,
    service: str,
    operation: str,
    expected_params: dict[str, Any],
    empty_response: dict[str, Any],
    not_found_code: str,
    res_id: str,
    arn: str | None,
) -> None:
    factory = ClientFactory("tag")
    client = factory.client(service, REGION)
    stubber = Stubber(client)
    stubber.add_client_error(
        operation,
        service_error_code="UnauthorizedOperation",
        expected_params=expected_params,
    )
    with stubber:
        item = _item(resource_type, res_id, arn=arn)
        with pytest.raises(AwsOperationError) as exc_info:
            fetch(factory, item)
        assert not isinstance(exc_info.value, StateMismatchError)
        assert exc_info.value.aws_error_code == "UnauthorizedOperation"
    stubber.assert_no_pending_responses()


@mock_aws
@pytest.mark.parametrize(
    (
        "resource_type",
        "service",
        "operation",
        "expected_params",
        "empty_response",
        "not_found_code",
        "res_id",
        "arn",
    ),
    _STUBBER_PARAMS,
)
def test_ac19_params(
    resource_type: ResourceType,
    service: str,
    operation: str,
    expected_params: dict[str, Any],
    empty_response: dict[str, Any],
    not_found_code: str,
    res_id: str,
    arn: str | None,
) -> None:
    factory = ClientFactory("tag")
    client = factory.client(service, REGION)
    stubber = Stubber(client)
    stubber.add_response(operation, empty_response, expected_params=expected_params)
    with stubber:
        item = _item(resource_type, res_id, arn=arn)
        with pytest.raises(StateMismatchError):
            fetch(factory, item)
    stubber.assert_no_pending_responses()


@mock_aws
def test_ac20_only_describe_ops() -> None:
    _vol_id, item_ebs = _setup_ebs()
    _alloc_id1, item_eip1 = _setup_eip_unassociated()
    _alloc_id2, _assoc_id, item_eip2 = _setup_eip_associated()
    _nat_id, item_nat = _setup_nat()
    _lb_name, _lb_arn, item_alb = _setup_alb()
    _inst_id1, item_ec2_run = _setup_ec2_running()
    _inst_id2, item_ec2_stop = _setup_ec2_stopped()
    _db_id1, item_rds_avail = _setup_rds_available()
    _db_id2, item_rds_stop = _setup_rds_stopped()

    items = [
        item_ebs,
        item_eip1,
        item_eip2,
        item_nat,
        item_alb,
        item_ec2_run,
        item_ec2_stop,
        item_rds_avail,
        item_rds_stop,
    ]

    recorded_ops: list[str] = []
    requested_services: list[str] = []
    registered_clients: set[int] = set()

    class _RecordingFactory:
        def __init__(self, inner: ClientFactory) -> None:
            self._inner = inner

        def client(self, service: str, region: str) -> Any:
            requested_services.append(service)
            cl = self._inner.client(service, region)
            if id(cl) not in registered_clients:

                def _hook(model: Any, **kw: Any) -> None:
                    recorded_ops.append(
                        f"{model.service_model.service_name}:{model.name}"
                    )

                cl.meta.events.register("before-call.*.*", _hook)
                registered_clients.add(id(cl))
            return cl

    rf = _RecordingFactory(ClientFactory("tag"))
    for item in items:
        fetch(rf, item)  # type: ignore[arg-type]

    assert len(recorded_ops) > 0
    for op in recorded_ops:
        assert op in _ALLOWED_DESCRIBE_OPS, f"Unexpected operation: {op}"
    assert "resource-explorer-2:Search" not in recorded_ops
    assert "resource-explorer-2" not in requested_services


@mock_aws
def test_ac21_modes() -> None:
    _vol_id, item_ebs = _setup_ebs()
    _alloc_id1, item_eip1 = _setup_eip_unassociated()
    _alloc_id2, _assoc_id, item_eip2 = _setup_eip_associated()
    _nat_id, item_nat = _setup_nat()
    _lb_name, _lb_arn, item_alb = _setup_alb()
    _inst_id1, item_ec2_run = _setup_ec2_running()
    _inst_id2, item_ec2_stop = _setup_ec2_stopped()
    _db_id1, item_rds_avail = _setup_rds_available()
    _db_id2, item_rds_stop = _setup_rds_stopped()

    items = [
        item_ebs,
        item_eip1,
        item_eip2,
        item_nat,
        item_alb,
        item_ec2_run,
        item_ec2_stop,
        item_rds_avail,
        item_rds_stop,
    ]

    tag_factory = ClientFactory("tag")
    exec_factory = ClientFactory("execute")

    for item in items:
        res_tag = fetch(tag_factory, item)
        res_exec = fetch(exec_factory, item)
        assert res_tag == res_exec
