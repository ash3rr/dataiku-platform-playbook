# Infrastructure

Configuration and commands for the AWS side of the platform. Nothing here is applied automatically. It is the record of what was built, with placeholders for account-specific IDs. Terraform for the base infrastructure (VPC, Fleet Manager EC2, ALB, IAM) belongs to the cloud team's repository and is not copied here. The modules under `terraform/` are our own, written for the target design.

| Path | What |
|---|---|
| `ecr/build_and_push_base_image.sh` | Build the DSS container-exec base image after an upgrade and push it to ECR, waiting for DSS's own code studio pushes to drain first |
| `eks-cluster/eksctl-cluster-config.yaml` | The eksctl ClusterConfig DSS uses for the prod cluster, as agreed when the cluster was recreated, with the reasons for each change |
| `eks-nodegroups/` | CPU node groups created with the CLI after the rebuild (ml, standard, workshop) and the shared `eks-gp3-500gb` launch template |
| `eks-gpu-nodegroups/` | Four GPU node groups across g5 and g4dn, their launch templates, the DSS GPU container profile and the CloudWatch alarms |
| `terraform/` | Modules for per-group S3 storage (bucket, bucket policy, IAM role, Intelligent-Tiering) and a Snowflake PrivateLink endpoint per VPC, plus a DEV example. Written for the target design in `docs/architecture/multi-vpc-target-design.md`, not applied |
| `snowflake/network-policies.sql` | Account- and user-level Snowflake network policies using `AWSVPCEID` rules, with checks |
| `lambda/lambda_trigger_scenario.py` | Lambda that starts a DSS scenario when files land in S3. Not run against a live instance |
| `fleet-manager/ist-custom-ansible.yaml` | Custom Ansible from the Fleet Manager instance settings templates (Miniconda, Datadog agent), after the pgdg13 fix |

Conventions used throughout: region eu-west-1, AWS CLI from PowerShell with SSO profile `prod` (prod account) or `dev2` (sandbox account), no kubectl (private cluster endpoint). Commands are given in the form that worked.
