# S3 isolation per group

## Why the project-key prefix isn't enough

The default pattern is one bucket per environment, with each project's managed data under a `${projectKey}/` prefix set in the DSS connection. DSS enforces that prefix when it reads and writes datasets.

It does nothing for code. All projects share the connection's IAM identity, so a Python recipe can do this:

```python
import boto3
boto3.client("s3").list_objects_v2(Bucket="dss-dev", Prefix="OTHER_PROJECT/")
```

and AWS will allow it. On a platform where data scientists write their own code, the prefix is only a convention. It does not stop code reading another project's data.

## What scopes access

In DSS, access is scoped by group: a group owns one or more projects, and its members see all of them. The mapping between groups and projects isn't one to one. So the AWS boundary should follow the group, not the project. Per-project buckets would also be unmanageable once a platform has thousands of projects.

The design is one bucket and one IAM role per group per environment:

| Environment | Bucket                 | Role                     |
|-------------|------------------------|--------------------------|
| DEV         | `dss-dev-<group>`      | `dss-grp-<group>-dev`    |
| UAT         | `dss-uat-<group>`      | `dss-grp-<group>-uat`    |
| PROD        | `dss-prod-<group>`     | `dss-grp-<group>-prod`   |

With, say, 300 groups across three environments, that's about 900 buckets and 900 roles. S3's default quota is 10,000 general purpose buckets per account (it was 100 until late 2024, which you'll still see quoted). The real cost is provisioning, so bucket, role and connection creation has to be a pipeline from day one. The Terraform module for one group is [`terraform/modules/dss-group-storage`](../../infra/terraform/modules/dss-group-storage).

## Two layers of IAM

The instance profile on each node is per environment (`dss-node-dev`, `dss-node-uat`, `dss-node-prod`). It has no S3 permissions. Its only job is `sts:AssumeRole` on the group roles of its own environment, `arn:aws:iam::<account>:role/dss-grp-*-dev` for the DEV node. A DEV node can't assume a PROD group role because its policy doesn't allow it and the PROD role's trust policy doesn't name it.

The group role is what touches S3. Its policy allows read and write on its own bucket only. The bucket policy denies every principal except that role and the automation role that manages the bucket, so a mistake in an IAM policy elsewhere still doesn't open the bucket.

Each group gets its own DSS S3 connection, configured with "STS with AssumeRole" and the group's role ARN, and restricted to that group ("Usable by: selected groups"). [`scripts/clone_connection_for_group.py`](../../scripts/connections/clone_connection_for_group.py) creates these from a template connection.

### Why not one connection with a per-user admin property

An earlier version used a single S3 connection whose role ARN came from `${adminProperty:iamRoleToAssume}`, set on each user. It looks tidier, but a user property holds one value, and users belong to several groups. A user in two groups could only ever assume one of the two roles. Per-group connections avoid that and make the connection list itself show who can reach which bucket.

## The gap you have to close: instance metadata

The group roles' trust policies trust the environment's instance profile. Code running on the node can usually reach the EC2 instance metadata endpoint (`169.254.169.254`) and pick up that instance profile's credentials. From there it can assume *any* group role in the environment, including other teams'. The same applies to containerised execution on EKS: unless pods use IRSA or EKS Pod Identity and the node's metadata hop limit is 1, a pod can read the worker node's role.

So the per-group boundary holds only if user code can't reach the instance profile. Things to check, in order:

1. User Isolation Framework (UIF) is on, so recipes and notebooks run as impersonated Unix users rather than as the DSS service account. On Cloud Stacks it is managed for you, but confirm it's enabled.
2. Impersonated users can't reach the metadata endpoint. Test it from a Python recipe: `requests.get("http://169.254.169.254/latest/meta-data/iam/security-credentials/", timeout=2)`. If that returns a role name, the boundary is open.
3. EKS: IMDSv2 required with hop limit 1 on worker nodes, and the containerised execution configs use a service account with its own scoped role.
4. Connection "details readable by" is limited, so users can't read another group's connection credentials through the DSS API.
5. CloudTrail alarms on `AssumeRole` calls for `dss-grp-*` roles whose source isn't the DSS service. A cross-group assumption should never happen, so any alarm here needs investigating.

This analysis came after the design was presented, and it wasn't validated with the vendor. Test it before anything else.

## Lifecycle and archiving

The requirement was "archive a group's data if nobody has used it". Standard S3 lifecycle rules act on object age (time since creation), not on last access, so a dataset read every day but written once would be archived. That's the wrong behaviour.

S3 Intelligent-Tiering tracks access per object. The module transitions objects into Intelligent-Tiering immediately and enables the optional archive tiers after a configurable period (90 and 180 days by default). Two caveats:

- Objects smaller than 128 KB are never moved between tiers.
- Objects in the Archive Access and Deep Archive Access tiers have to be restored before they can be read. A DSS dataset backed by archived files will fail to build until someone restores them. That's acceptable for abandoned groups and painful for seasonal ones. Set the archive threshold with that in mind, or leave the archive tiers off and settle for the automatic infrequent-access tiers, which need no restore.

Retiring a group is then simple: transition or delete its bucket after the retention period, delete its role and its connection. Nothing has to be picked out of a shared bucket.

## Snowflake fast-write staging

DSS loads into Snowflake through an S3 stage when fast-write is enabled. Give each environment its own staging bucket and connection, so the DEV/UAT Snowflake account never has a storage integration pointing at a PROD bucket.
