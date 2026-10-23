# Reviewing an existing deployment

Before proposing changes, find out what's actually there. These are the things that turned up when reviewing a Cloud Stacks deployment's Terraform and DSS configuration, and they're common enough to check for anywhere.

## In the Terraform

### Where the VPC comes from

If the VPC ID and CIDRs are read from SSM parameters (`/baseline/<env>/vpc_id` or similar), a central cloud team owns the VPC and your code only creates resources inside it. You probably can't create new VPCs yourself, so a multi-VPC design starts as a request to them, not a pull request.

### How many IAM roles the DSS nodes use

A single role and instance profile shared by every node, Design and Automation alike, means every node has the same reach. Check what's attached to it. Broad grants such as `eks:*`, `secretsmanager:*` or the `AmazonEC2FullAccess` managed policy on the node role mean any code that reaches the instance profile has them too.

### What Fleet Manager can pass

If the FM role has `iam:PassRole` on exactly one role, it can only ever launch nodes with that role. Per-environment instance profiles need this widened to each of them.

### Security groups

One security group for all DSS nodes means you can't write a rule that treats DEV and PROD differently. Ingress of 443 from `0.0.0.0/0` and unrestricted egress are common and worth tightening regardless of anything else.

### Whether Snowflake connectivity appears at all

If there's no VPC endpoint for Snowflake anywhere in the code, the traffic either goes out over the internet or through someone else's network account. Find out which before designing network rules. See [Snowflake network isolation](snowflake-network-isolation.md).

### Load balancer routing

A single ALB with host-header rules per DSS instance (one target group per node) is a reasonable pattern and doesn't need to change. It does mean all environments share one listener and certificate, so check who can edit the listener rules.

## In DSS

### Connections

Run [`scripts/list_connections.py`](../../scripts/connections/list_connections.py) to get every connection's type, host, bucket and authentication method. Look for:

- the same Snowflake account or S3 bucket reachable from more than one environment's nodes;
- connections usable by all users instead of selected groups;
- S3 connections using the instance role directly instead of an assumed role;
- password authentication where key pair or OAuth is available.

### UIF

Whether User Isolation is on, and what impersonation rules are configured. Without it, all user code runs as the DSS service account.

### Code environments and containerised execution configs

Which service account and IAM role pods run under on EKS.

### Groups and projects

How many groups there are, how many projects each owns, and whether any project has permissions granted to individual users rather than groups. Per-group isolation assumes the group is the unit of access, and individual grants break that assumption.
