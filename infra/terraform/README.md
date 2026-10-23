# Terraform

Two modules and one example that wires them together for a DEV environment.

| Path | Creates |
|------|---------|
| `modules/dss-group-storage` | Bucket, bucket policy, IAM role and archive tiering for one DSS group in one environment |
| `modules/snowflake-privatelink` | Interface endpoint to Snowflake, its security group, and a private hosted zone with the PrivateLink hostnames |
| `examples/dev-environment` | The DEV node role (trust anchor), storage for each group, and the Snowflake endpoint |

The VPCs, Transit Gateway and Fleet Manager are not here. In the environment this came from, a central cloud team owned VPC creation, and the DSS code worked inside VPCs it was given.

## Things to know before applying

### The bucket policy denies everyone not on the list

`admin_role_arns` must include the role Terraform runs as, or the next plan fails with access denied on the bucket. The account root user can still delete the bucket policy to recover.

### Archive tiers delay reads

Objects in the Archive Access or Deep Archive Access tiers need a restore before DSS can read them. Set `archive_access_days` and `deep_archive_access_days` to `null` to keep only the automatic tiers, which don't need a restore.

### Group name length

Group names are constrained to 30 lowercase characters so bucket names (63 max) and role names (64 max) stay valid with the prefix, environment and account-ID suffix added. Map DSS group names to these slugs in whatever feeds `dss_groups`.

### The node role can assume every group role in its environment

That's the design, and it's why user code must not be able to reach the instance profile. See the metadata section of [S3 isolation per group](../../docs/architecture/s3-group-isolation.md#the-gap-you-have-to-close-instance-metadata).

## Order of work for a new environment

1. Apply the environment configuration. Record `snowflake_vpce_id` and `dss_node_instance_profile_arn`.
2. Add the endpoint ID to the Snowflake account's network rules ([`snowflake/network-policies.sql`](../snowflake/network-policies.sql)).
3. Set the instance profile in the Fleet Manager instance template and give the FM role `iam:PassRole` on it.
4. Create each group's S3 connection with [`scripts/clone_connection_for_group.py`](../../scripts/connections/clone_connection_for_group.py), using the role ARNs from `group_role_arns`.
