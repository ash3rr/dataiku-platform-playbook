# "Stop cluster" deleted the prod EKS cluster: recreating it

The prod EKS cluster used for containerized execution was deleted and recreated. This page covers what happened, the blockers, and everything that had to be reconnected afterwards.

## Starting point

The goal was modest: stop the workloads on the cluster for a while because some pods were hanging. Constraints:

* No kubectl. The cluster endpoint is private only (`publicAccess: false`), and there is no bastion.
* PowerShell scripts are disabled on the laptop, so no `.ps1` loops. One-line commands pasted by hand, or AWS CloudShell (bash in the console, already authenticated) for anything loop-shaped.
* EKS has no pause. Without kubectl the lever is scaling node groups to zero, which terminates the instances and every pod on them.

Node groups at the time included `api-workers`, `gpu-big-workers-v2`, `gpu-small-workers`, `nongpu-workers`, `standard-workers`, `system-nodes` and `workshop-workers`.

```powershell
aws eks list-nodegroups --cluster-name <EKS_CLUSTER_NAME> --region eu-west-1 --profile prod
aws eks describe-nodegroup --cluster-name <EKS_CLUSTER_NAME> --nodegroup-name <NG> --region eu-west-1 --profile prod --query "nodegroup.scalingConfig"
aws eks update-nodegroup-config --cluster-name <EKS_CLUSTER_NAME> --nodegroup-name <NG> --region eu-west-1 --profile prod --scaling-config minSize=0,maxSize=10,desiredSize=0
```

Record each group's current min/max/desired first so you can restore it. `maxSize` in the command must be at least the group's real max or the call errors. `system-nodes` was pinned at min = max = 2 (CoreDNS and friends live there).

### Backups checked before touching anything

```powershell
aws ec2 describe-snapshots --owner-ids self --region eu-west-1 --profile prod --query "Snapshots[*].{ID:SnapshotId,Desc:Description,Date:StartTime,Size:VolumeSize}" --output json > snapshots.json
```

(`--owner-type self` is not a valid flag. Use `--owner-ids self`. Table output for hundreds of snapshots is unreadable, so write JSON to a file.)

We found daily Fleet Manager snapshots (`fm-snap-*`) for each logical instance, including the design node's data volume, current to that day. There were also older pre-upgrade snapshots and a Data Lifecycle Manager policy taking daily snapshots of the system volumes. Node groups are stateless and have no backups, and nothing on them needs one. Other places checked for cluster-level backups: CloudFormation stacks (eksctl), S3 for Velero buckets (`aws s3 ls --profile prod | grep -i velero`), ECR for images.

## What went wrong

The DSS "Stop cluster" action on a DSS-managed EKS cluster does not pause it. It deletes it (DSS drives eksctl). The deletion failed halfway, and the cluster was then deleted by hand in the AWS console. When DSS was asked to start the cluster again, it ran eksctl create and failed:

```
AlreadyExistsException: Stack [eksctl-<EKS_CLUSTER_NAME>-cluster] already exists
```

The CloudFormation stack that eksctl uses for the control plane was orphaned: the stack existed, the cluster under it did not. Nobody had realised the cluster was a CloudFormation stack.

Saving the stack template before anything else:

```powershell
aws cloudformation get-template --stack-name eksctl-<EKS_CLUSTER_NAME>-cluster --region eu-west-1 --profile prod --query TemplateBody > cluster-template.json
aws cloudformation describe-stacks --stack-name eksctl-<EKS_CLUSTER_NAME>-cluster --region eu-west-1 --profile prod --query "Stacks[0].StackStatus"
aws cloudformation list-stacks --region eu-west-1 --profile prod --query "StackSummaries[?contains(StackName,'<EKS_CLUSTER_NAME>')].{Name:StackName,Status:StackStatus}" --output table
```

The template said `"Version": "1.24"`: the stack dated from the cluster's original creation at Kubernetes 1.24, long out of support. It only covers the control plane. Node groups have their own `eksctl-<cluster>-nodegroup-*` stacks.

## Permissions

We needed to delete the orphaned stack. Checking rights without guessing:

```powershell
aws iam get-user --profile prod
# ValidationError: Must specify userName when calling with non-User credentials   (SSO role, not a user)
aws sts get-caller-identity --profile prod
# arn:aws:sts::<AWS_ACCOUNT_ID>:assumed-role/AWSReservedSSO_<PLATFORM_ADMIN_ROLE>_<HASH>/<user>
aws iam simulate-principal-policy --policy-source-arn arn:aws:iam::<AWS_ACCOUNT_ID>:role/aws-reserved/sso.amazonaws.com/AWSReservedSSO_<PLATFORM_ADMIN_ROLE>_<HASH> --action-names cloudformation:UpdateStack cloudformation:DeleteStack cloudformation:CreateStack --resource-arns "*" --profile prod
```

`simulate-principal-policy` rejects the `arn:aws:sts::...:assumed-role/...` form with `InvalidInput: Invalid ARN`. It needs the IAM role ARN under `role/aws-reserved/sso.amazonaws.com/`. Result:

| Action | Decision |
|---|---|
| `cloudformation:CreateStack` | allowed (SSO inline policy) |
| `cloudformation:UpdateStack` | implicitDeny |
| `cloudformation:DeleteStack` | implicitDeny |

Rejected along the way: `update-stack --use-previous-template` as a "permission test" (it would try to update a stack whose cluster no longer exists and could make things worse) and a change-set dry run (too slow for the moment). Deleting the stack needed someone outside our role (the cloud team).

Leftover exports matter: the old stack exported values such as `eksctl-<cluster>-cluster::ClusterSecurityGroupId`. A new stack with the same name and exports fails until the old one is deleted. Delete it cleanly and do not retain resources from it.

While the cluster was gone, Code Studios failed with:

```
ERR_CODE_STUDIO_START Failed to start Code Studio
Unable to start run : The cluster <EKS_CLUSTER_NAME> used by this Code Studio isn't running
```

Code Studios only run in containers. Notebooks and recipes that do not force containers kept working on the DSS host.

## The eksctl config DSS used

DSS holds an eksctl `ClusterConfig` YAML for the cluster. The one in place described only some of the live cluster's node groups and was years out of date. It was reviewed and updated before the recreate. The final version and the reasons for each change are in `infra/eks-cluster/eksctl-cluster-config.yaml`. Summary: p3.2xlarge (decommissioned) to g5.2xlarge, t3.large to m6i.large, api-workers 0/0, system-nodes t3.large, gp3 everywhere, version 1.33.

Version choice: EKS then supported 1.29 to 1.35, with 1.32 to 1.35 in standard support and 1.29 to 1.31 in paid extended support. Before the incident the cluster was on 1.32 (as far as anyone remembered, though the CloudFormation template still said 1.24 from the original creation). It was recreated on 1.33. If a cluster is left until extended support ends, AWS upgrades the control plane on its own schedule. Also noted at the time: the upstream Ingress NGINX project was being retired, worth checking whether DSS relies on it.

DSS shows "Use YAML. If you use advanced configuration, presets will not be used." next to this field. With a full YAML config DSS applies no presets. We did not establish exactly what the presets would have added.

Dependencies of the config: the VPC, subnets, the control plane SG (`vpc.securityGroup`) and the attached workers SG must exist, and eksctl creates the node roles.

The cluster was recreated by DSS once the stack was cleared, with eksctl 0.215.0 (the original was created with 0.133.0) and platform version eks.29.

## Reconnecting everything afterwards

### Security groups changed

Two of the three SGs in the old GPU launch template no longer existed (`InvalidGroup.NotFound`). The recreated cluster has a new EKS cluster SG (`eks-cluster-sg-<cluster>-<n>`, "EKS created security group applied to ENI that is attached to EKS Control Plane master nodes, as well as any managed workloads") and a new remote access SG per eksctl node group. The shared workers SG survived. Every launch template referencing old IDs had to be redone.

### New node groups and the shared launch template

Built the next morning. `infra/eks-nodegroups/RUNBOOK.md` has the full story (gp3 needs a launch template, remote access cannot be combined with a launch template, node roles and the permissions boundary, the port 10001 failure and the FM default SG fix, the ml-workers mix-up).

### AMIs

A specific AMI ID was suggested, but `describe-images` for it returned `null` (not visible to the account). Marketplace and console AMI lists (ECS-optimized AMIs, plain AL2023 kernel 6.1/6.18 AMIs, third-party repackages) are the wrong images for EKS. For managed node groups pass `--ami-type` and let EKS pick, or look up the recommended EKS AMI via SSM:

```powershell
aws ssm get-parameter --name /aws/service/eks/optimized-ami/1.33/amazon-linux-2023/x86_64/nvidia/recommended/image_id --region eu-west-1 --profile prod --query "Parameter.Value" --output text
aws ssm get-parameter --name /aws/service/eks/optimized-ami/1.33/amazon-linux-2023/x86_64/standard/recommended/image_id --region eu-west-1 --profile prod --query "Parameter.Value" --output text
```

Official EKS AMIs are owned by `602401143452`. AWS stopped publishing EKS-optimized AL2 AMIs on 2025-11-26. Kubernetes 1.32 is the last version with AL2 images, and `AL2_x86_64_GPU` is replaced by `AL2023_x86_64_NVIDIA` (driver only, CUDA must be in the container). If a launch template pins an `ImageId`, omit `--ami-type`. Otherwise pass it. An AMI named "EKS Kubernetes Worker AMI with AmazonLinux2 image, (k8s: 1.32.9, containerd: 1.7.29-...)" is the standard (non-GPU) AL2 image.

### DSS nodes still pointing at the old cluster

Containerized jobs from the design and automation nodes failed after the rebuild. Each DSS node keeps its own cluster state under `/data/dataiku/dss_data/clusters/<cluster>/exec/` (on one node the directory was `<cluster>_1`):

* `kube_config`: a normal kubectl config whose user section runs `aws eks get-token` (via `command: aws`).
* a JSON state file holding `kube_config_path` and a copy of `describe-cluster` output: `AccessConfig.AuthenticationMode`, `CertificateAuthority.Data`, `CreatedAt`, `Endpoint`, OIDC issuer, `ServiceIpv4Cidr`, logging, `PlatformVersion`, `ResourcesVpcConfig` (cluster SG, control plane SG, subnets, VPC), `RoleArn`, tags, `Version`.
* the eksctl `ClusterConfig` YAML.

Old vs new values that changed: endpoint and CA, `Version` 1.24 to 1.33, `PlatformVersion` eks.28 to eks.29, `AuthenticationMode` `CONFIG_MAP` to `API_AND_CONFIG_MAP`, cluster SG, cluster service role, CloudFormation stack ID and eksctl version tags. (One node still had a state file from the original cluster with platform version eks.4.)

What did not work:

* Pasting into vi or a heredoc through the browser-based SSM console. Paste is unreliable there.
* `aws configure sso` on the server. The browser callback goes to 127.0.0.1 on the server.
* `aws ssm start-session` from the laptop at first. It needs the Session Manager plugin, and installing executables is blocked on the laptop, so the browser was sent to `127.0.0.1:<port>` and failed.

The Detach button in Administration > Clusters is not documented. We did not use it, to avoid losing the DSS-side cluster configuration. Re-attaching through Add Cluster > Attach EKS is the documented path if ever needed.

What worked, on each DSS node as the dataiku user, using the instance profile (no `--profile`):

```bash
sudo su - dataiku
aws eks update-kubeconfig --name <EKS_CLUSTER_NAME> --region eu-west-1 --kubeconfig /data/dataiku/dss_data/clusters/<EKS_CLUSTER_NAME>/exec/kube_config
/data/dataiku/dss_data/bin/dss restart
```

The resulting file contains both the old `cluster-<name>` entry and the new ARN-named entry, with `current-context` on the new one. Done on the design node, the prod automation node and a third DSS node.

Testing: pick a recipe that runs in containers. The first "test" was a Snowflake SQL recipe (`Selected engine type: SQL`, `SQLSamplingExecutor`), which never touches EKS.

On the prod automation node the cluster configuration test kept failing after `update-kubeconfig` and the restart:

```
Configuration test failed
Failed to create pod, caused by: IOException: kubectl failed with code=1 out= err=E<MMDD> <hh:mm:ss.ffffff> 98375 memcache.go:265]
"Unhandled Error" err="couldn't get current server API group list: Get \"https://<OLD_EKS_ENDPOINT>.gr7.eu-west-1.eks.amazonaws.com/api?timeout=32s\":
dial tcp: lookup <OLD_EKS_ENDPOINT>.gr7.eu-west-1.eks.amazonaws.com on <VPC_DNS_RESOLVER>:53: no such host"
...
Unable to connect to the server: dial tcp: lookup <OLD_EKS_ENDPOINT>... no such host
HTTP code: , type: java.io.IOException
```

`no such host` on the old endpoint means DSS is still using a config that names the deleted cluster (the new endpoint has a different hostname, on `sk1` instead of `gr7`). Checks for that case:

```bash
grep server /data/dataiku/dss_data/clusters/<EKS_CLUSTER_NAME>/exec/kube_config
find /data/dataiku/dss_data/clusters -name "kube_config"
grep -r "<OLD_EKS_ENDPOINT>" /data/dataiku/dss_data/ 2>/dev/null
```

The grep shows exactly which file still holds the old endpoint. In the same window, though, a real scenario run on the automation node logged `Pod was successfully created` and `Pods phase is: Succeeded`, and most activities in the scenario ran successfully, so containerized work was reaching the new cluster. The one recipe still failing published to an on-prem endpoint, and that turned out to be an on-prem routing problem through the Transit Gateway, not a cluster problem.

After the restart on the automation node it "did not pick it up": the JSON state file still described the old cluster. Whether DSS refreshed that file itself or it was edited by hand was not recorded. A replacement JSON with the new values, in exactly the old structure, was prepared for that case.

Instance confusion worth avoiding: confirm which node you are on with `hostname && curl -s http://169.254.169.254/latest/meta-data/instance-id` before changing anything. The prod automation node shows in FM as `execution-<FLEET>_Prod-0 (DSS managed by FM)`.

## Lessons

* Never use DSS "Stop cluster" on a DSS-managed EKS cluster to pause workloads. Scale node groups to zero instead (and remember that kills running pods immediately).
* Before deleting anything eksctl created, look at its CloudFormation stacks.
* Keep the eksctl config, launch templates and node group commands in version control. The rebuild depended on reconstructing them from memory and screenshots.
* After any cluster rebuild, update every DSS node's cluster state and restart it, then test with a containerized recipe.

## Open items

* The orphaned-stack deletion depended on the cloud team, and our role still cannot delete or update CloudFormation stacks.
* Node roles are per-node-group eksctl roles shared by CLI groups (see `infra/eks-nodegroups/RUNBOOK.md`).
* Get kubectl access (access entry for the SSO role plus a bastion or VPN path) so the next incident is not done blind.
