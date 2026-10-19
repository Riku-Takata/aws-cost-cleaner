"""Resource lookup and re-fetch for approved items."""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING

from botocore.exceptions import ClientError

from aws_cost_cleaner.collectors import ebs, ec2, eip, elb, nat, rds
from aws_cost_cleaner.collectors.elb import _count_registered_targets, _fetch_tags
from aws_cost_cleaner.errors import AwsOperationError, StateMismatchError
from aws_cost_cleaner.models import ApprovalItem, Resource

if TYPE_CHECKING:
    from aws_cost_cleaner.aws.session import ClientFactory

NOT_FOUND_CODES: frozenset[str] = frozenset(
    {
        "InvalidVolume.NotFound",
        "InvalidAllocationID.NotFound",
        "NatGatewayNotFound",
        "LoadBalancerNotFound",
        "InvalidInstanceID.NotFound",
        "DBInstanceNotFound",
    }
)


def _not_found(item: ApprovalItem, detail: str) -> StateMismatchError:
    return StateMismatchError(
        f"{item.resource_type} {item.resource_id} in {item.region}: {detail}"
    )


def _fetch_ebs(factory: ClientFactory, item: ApprovalItem) -> Resource:
    client = factory.client("ec2", item.region)
    resp = client.describe_volumes(VolumeIds=[item.resource_id])
    vols = resp.get("Volumes", [])
    if not vols:
        raise _not_found(item, "not found")
    return ebs.to_resource(vols[0], item.region)


def _fetch_eip(factory: ClientFactory, item: ApprovalItem) -> Resource:
    client = factory.client("ec2", item.region)
    resp = client.describe_addresses(AllocationIds=[item.resource_id])
    addrs = resp.get("Addresses", [])
    if not addrs:
        raise _not_found(item, "not found")
    addr = addrs[0]
    assoc = addr.get("AssociationId") or None
    base = eip.to_resource(addr, item.region)
    return dataclasses.replace(
        base,
        state="associated" if assoc else "unassociated",
        attrs={**base.attrs, "AssociationId": assoc},
    )


def _fetch_nat(factory: ClientFactory, item: ApprovalItem) -> Resource:
    client = factory.client("ec2", item.region)
    resp = client.describe_nat_gateways(NatGatewayIds=[item.resource_id])
    gws = resp.get("NatGateways", [])
    if not gws:
        raise _not_found(item, "not found")
    gw = gws[0]
    if gw.get("State") == "deleted":
        raise _not_found(item, "state is deleted")
    return nat.to_resource(gw, item.region)


def _fetch_elb(factory: ClientFactory, item: ApprovalItem) -> Resource:
    client = factory.client("elbv2", item.region)
    resp = client.describe_load_balancers(LoadBalancerArns=[item.arn])
    lbs = resp.get("LoadBalancers", [])
    if not lbs:
        raise _not_found(item, "not found")
    lb = lbs[0]
    arn = str(lb["LoadBalancerArn"])
    tags = _fetch_tags(client, [arn]).get(arn, {})
    count = _count_registered_targets(client, arn)
    return elb.to_resource(lb, item.region, tags, count)


def _fetch_ec2(factory: ClientFactory, item: ApprovalItem) -> Resource:
    client = factory.client("ec2", item.region)
    resp = client.describe_instances(InstanceIds=[item.resource_id])
    instances = [
        inst
        for reservation in resp.get("Reservations", [])
        for inst in reservation.get("Instances", [])
    ]
    if not instances:
        raise _not_found(item, "not found")
    inst = instances[0]
    if (inst.get("State") or {}).get("Name") == "terminated":
        raise _not_found(item, "state is terminated")
    return ec2.to_resource(inst, item.region)


def _fetch_rds(factory: ClientFactory, item: ApprovalItem) -> Resource:
    client = factory.client("rds", item.region)
    resp = client.describe_db_instances(DBInstanceIdentifier=item.resource_id)
    dbs = resp.get("DBInstances", [])
    if not dbs:
        raise _not_found(item, "not found")
    return rds.to_resource(dbs[0], item.region)


_FETCHERS: Mapping[str, Callable[[ClientFactory, ApprovalItem], Resource]] = {
    "ebs_volume": _fetch_ebs,
    "elastic_ip": _fetch_eip,
    "nat_gateway": _fetch_nat,
    "load_balancer": _fetch_elb,
    "ec2_instance": _fetch_ec2,
    "rds_instance": _fetch_rds,
}


def fetch(factory: ClientFactory, item: ApprovalItem) -> Resource:
    """Re-describe one approved resource by ID in item.region."""
    try:
        return _FETCHERS[item.resource_type](factory, item)
    except ClientError as exc:
        code = str(exc.response.get("Error", {}).get("Code", ""))
        if code in NOT_FOUND_CODES:
            raise _not_found(item, f"not found ({code})") from exc
        raise AwsOperationError(
            f"{item.resource_type} {item.resource_id} in {item.region}: {code}",
            aws_error_code=code or None,
        ) from exc
