# EKS CPU node groups and the shared 500 GB launch template

Built the day after the prod EKS cluster was deleted and recreated (`docs/runbooks/eks-cluster-stop-and-recreate.md`). The workshop group was added later. Commands are AWS CLI from PowerShell with the `prod` SSO profile. PowerShell script execution was disabled on the laptop, so everything is one line or backtick continued, pasted by hand.

## Node groups on the cluster after the rebuild

| Node group | Created by | Instance | AMI type | Labels | Taint | min/max/desired |
|---|---|---|---|---|---|---|
| `system-nodes` | eksctl (DSS) | t3.large | AL2023 standard | `workerType=system` | none | 2/2/2 |
| `nongpu-workers` | eksctl (DSS) | m6i.large | AL2023 standard | `workerType=nongpu` | `non-gpu=true:NoSchedule` | 1/2/1 |
| `api-workers` | eksctl (DSS) | m6i.large | AL2023 standard | `workerType=api` | `api-workers=true:NoSchedule` | 0/2/0 |
| `gpu-workers` | eksctl (DSS) | g5.2xlarge | AL2023 NVIDIA | `workerType=gpu` | GPU taint | 0/1/0 at creation |
| `ml-workers` | CLI, this runbook | r6i.4xlarge | `AL2023_x86_64_STANDARD` | `workerType=ml-workers`, `workload=training` | `workload=training:NoSchedule` | 0/6/1 at creation |
| `standard-workers` | CLI, this runbook | r6i.2xlarge | `AL2023_x86_64_STANDARD` | `workerType=standard` | `standard=true:NoSchedule` | 0/6/1 at creation |
| `workshop-workers` | CLI, this runbook | m5.4xlarge | `AL2023_x86_64_STANDARD` | `workerType=workshop` | `workshop=true:NoSchedule` | 0/6/0 at creation, later raised (see Scaling the workshop group) |

The eksctl-created groups come from the cluster config in `infra/eks-cluster/eksctl-cluster-config.yaml`. The CLI groups all use the shared launch template `eks-gp3-500gb`. The GPU groups added later are in `infra/eks-gpu-nodegroups/`.

Before the rebuild the cluster also had `gpu-big-workers-v2`, `gpu-small-workers` and another GPU group, which were not recreated in this form.

## Instance choices

The memory-heavy ML group used to be r5.4xlarge. r6i.4xlarge has the same 16 vCPU / 128 GB and the same list price (about USD 736/month on-demand in us-east-1, with eu-west-1 usually 10 to 15 percent higher, which we could not confirm from a primary source at the time) with a newer CPU, so r6i replaced r5. r6i.2xlarge (8 vCPU / 64 GB, about USD 368/month us-east-1) became the general "standard" worker. Both are widely available in eu-west-1.

For an ML workload needing pods of 2 vCPU and up to 80 GB, several at once, the options compared were:

| Instance | vCPU / RAM | Pods at 80 GB | ~USD/month us-east-1 |
|---|---|---|---|
| r6i.4xlarge | 16 / 128 GB | 1 | 736 |
| r6i.8xlarge | 32 / 256 GB | 3 | 1,472 |
| r6i.12xlarge | 48 / 384 GB | 4 | 2,208 |

Families looked at and rejected:

* Graviton (C7g, C6gn, C6gd): ARM. DSS base and code env images are x86, NVIDIA GPUs are x86 only, and mixing architectures needs extra taint discipline. Graviton is roughly 20 percent cheaper but not worth the compatibility risk on a platform being rebuilt under pressure.
* C6in, C6id, C7a: compute-optimised (2 GB per vCPU). The `n` and `d` variants pay for network and local NVMe we do not need. DSS workers are memory-hungry, so M and R families fit better. C7a (AMD Genoa) is fine technically. m7a/r7a would be the AMD options with the right RAM ratio, with a small availability risk for newer families in eu-west-1.
* m6a/r6a/c6a (AMD, slightly cheaper than Intel) are acceptable alternatives to m6i/r6i.

## The shared launch template `eks-gp3-500gb`

Named for what it does, not for a node group, because it is reused. A `eks-gp3-200gb` would be the sibling for smaller disks.

Why a launch template at all: `create-nodegroup --disk-size` only sets size and gives gp2. gp3 needs a launch template.

Final content (version 4) is in `launch-templates/eks-gp3-500gb.json`: the EKS cluster security group, the Fleet Manager virtual network default security group, key pair, 500 GB gp3 at 3000 IOPS / 125 MB/s on `/dev/xvda` (the root EBS volume, separate from G5 local NVMe ephemeral storage). No AMI, no UserData, no instance type.

How it got there, because each version fixed a real error:

| Version | Change | Why |
|---|---|---|
| 1 | Disk only | First attempt, with SGs and key passed on the CLI |
| 2 | + `SecurityGroupIds: [<EKS_CLUSTER_SG_ID>]` | The cluster SG belongs on the node ENI, not in `--remote-access` |
| 3 | + `KeyName` | `create-nodegroup` failed with `InvalidParameterException ... Remote access configuration cannot be specified with a launch template.` With a template, the key goes in the template and SSH source restriction is just SG rules |
| 4 | + `<FM_VN_DEFAULT_SG_ID>` | Container jobs could not reach the DSS backend on port 10001 (see the port 10001 incident) |

Create and version it. Inline JSON on the command line is mangled by PowerShell (`Unknown options: BlockDeviceMappings\:[{\DeviceName\:...`), so always use a file:

```powershell
aws ec2 create-launch-template --launch-template-name eks-gp3-500gb --launch-template-data file://lt-data.json --region eu-west-1 --profile prod

aws ec2 create-launch-template-version --launch-template-id <LT_ID> --source-version 3 --launch-template-data file://lt-data.json --region eu-west-1 --profile prod
```

`--source-version` copies everything from that version and overrides only what is in the file.

### The port 10001 incident

The first group built on version 3 accepted pods, but every containerized job died with:

```
[ERROR] [root] Could not reach DSS: HTTPConnectionPool(host='ip-<DESIGN_NODE_IP>.eu-west-1.compute.internal', port=10001):
Max retries exceeded with url: /dip/api/tintercom/containers/get-execution
(Caused by NewConnectionError('<urllib3.connection.HTTPConnection object at 0x...>:
Failed to establish a new connection: [Errno 110] Connection timed out'))
```

The pod phones home to the DSS backend on 10001. The DSS host's security groups are the two Fleet Manager virtual network groups, `fm-vn-Virtual_Network_for_fleet_<FLEET>-instances-external-connectivity-<SUFFIX>` and `fm-vn-Virtual_Network_for_fleet_<FLEET>-default-<SUFFIX>`. The default one allows all traffic from members of itself (plus two other groups), tcp 10000 and tcp 443 from the ALB group, and nothing on 10001 from elsewhere:

```powershell
aws ec2 describe-instances --filters "Name=private-ip-address,Values=<DESIGN_NODE_IP>" --region eu-west-1 --profile prod --query "Reservations[0].Instances[0].SecurityGroups"
aws ec2 describe-security-groups --group-ids <FM_VN_DEFAULT_SG_ID> --region eu-west-1 --profile prod --query "SecurityGroups[0].IpPermissions[].{From:FromPort,To:ToPort,Proto:IpProtocol}" --output table
```

The old cluster had a dedicated SG granting this, but it was deleted with the cluster. Options:

* `aws ec2 authorize-security-group-ingress --group-id <FM_VN_DEFAULT_SG_ID> --protocol tcp --port 10001 --source-group <EKS_CLUSTER_SG_ID>`: explicit, but it edits a Fleet Manager managed SG, which FM may overwrite.
* Put the nodes in the FM default SG via the launch template so the existing "all traffic from members" rule covers 10001. Chosen (version 4). Every node group on version 4 gets it.

A JMESPath filter with backticks (``IpPermissions[?FromPort==`10001`]``) fails in PowerShell with `Bad jmespath expression: Unknown token \:`. Use `--output table` and read it.

## Node role

eksctl creates one IAM role per node group (`eksctl-<cluster>-nodegroup--NodeInstanceRole-<SUFFIX>`). All had the same managed policies: `AmazonEKSWorkerNodePolicy`, `AmazonEKS_CNI_Policy`, `AmazonEC2ContainerRegistryPullOnly`, `AmazonSSMManagedInstanceCore`.

We wanted one shared role. Creating it failed:

```
An error occurred (AccessDenied) when calling the CreateRole operation: User: arn:aws:sts::<AWS_ACCOUNT_ID>:assumed-role/AWSReservedSSO_<PLATFORM_ADMIN_ROLE>_<HASH>/<user>
is not authorized to perform: iam:CreateRole on resource: arn:aws:iam::<AWS_ACCOUNT_ID>:role/<NEW_ROLE>
with an explicit deny in a permissions boundary: arn:aws:iam::<AWS_ACCOUNT_ID>:policy/AdminPermissionsBoundary
```

So the CLI groups reuse eksctl roles: `ml-workers` uses the gpu-workers role, `standard-workers` and `workshop-workers` use the nongpu-workers role. A role is just a reference and can be shared. The risk: if eksctl ever deletes the owning node group's stack, it deletes the role and the other groups break. See open items.

Get a role ARN:

```powershell
aws eks describe-nodegroup --cluster-name <EKS_CLUSTER_NAME> --nodegroup-name nongpu-workers --region eu-west-1 --profile prod --query "nodegroup.nodeRole" --output text
aws iam list-roles --profile prod --query "Roles[?contains(RoleName, 'NodeInstance')].{Name:RoleName,ARN:Arn}" --output table
```

## Commands as run

```powershell
aws eks create-nodegroup --cluster-name <EKS_CLUSTER_NAME> --nodegroup-name ml-workers --node-role arn:aws:iam::<AWS_ACCOUNT_ID>:role/<GPU_WORKERS_NODE_ROLE> --subnets <SUBNET_A> <SUBNET_B> <SUBNET_C> --instance-types r6i.4xlarge --ami-type AL2023_x86_64_STANDARD --scaling-config minSize=0,maxSize=6,desiredSize=1 --launch-template id=<LT_ID>,version=4 --labels workerType=ml-workers,workload=training --taints key=workload,value=training,effect=NO_SCHEDULE --region eu-west-1 --profile prod

aws eks create-nodegroup --cluster-name <EKS_CLUSTER_NAME> --nodegroup-name standard-workers --node-role arn:aws:iam::<AWS_ACCOUNT_ID>:role/<NONGPU_WORKERS_NODE_ROLE> --subnets <SUBNET_A> <SUBNET_B> <SUBNET_C> --instance-types r6i.2xlarge --ami-type AL2023_x86_64_STANDARD --scaling-config minSize=0,maxSize=6,desiredSize=1 --launch-template id=<LT_ID>,version=4 --labels workerType=standard --taints key=standard,value=true,effect=NO_SCHEDULE --region eu-west-1 --profile prod

aws eks create-nodegroup --cluster-name <EKS_CLUSTER_NAME> --nodegroup-name workshop-workers --node-role arn:aws:iam::<AWS_ACCOUNT_ID>:role/<NONGPU_WORKERS_NODE_ROLE> --subnets <SUBNET_A> <SUBNET_B> <SUBNET_C> --instance-types m5.4xlarge --ami-type AL2023_x86_64_STANDARD --scaling-config minSize=0,maxSize=6,desiredSize=0 --launch-template id=<LT_ID>,version=4 --labels workerType=workshop --taints key=workshop,value=true,effect=NO_SCHEDULE --region eu-west-1 --profile prod
```

The matching DSS container profiles use the same pair, for example for standard workers:

```yaml
nodeSelector:
  workerType: standard
tolerations:
  - key: standard
    value: "true"
    effect: NoSchedule
```

and for ML training, `workerType: ml-workers` plus `workload: training` with toleration `key: workload, operator: Equal, value: training, effect: NoSchedule`. The label in the profile must be exactly what the node group has. An early draft used `ml-big` in one place and `ml-workers` in the other.

Before creating `workshop-workers` we checked that the launch template still matched what the existing groups use. The console page for `ml-workers` (Kubernetes 1.33, AMI release `1.33.8-20260304`, launch template `eks-gp3-500gb` version 4) showed remote access "on" with key pair `<EC2_KEY_PAIR_NAME>` and "Allow remote access from" listing the EKS cluster SG and the FM virtual network default SG, which are the two SGs in version 4. It also showed node auto repair disabled and no warm pool. The same page showed `ml-workers` at min 0, max 5, desired 1, while the command above has `maxSize=6`. The console reading is the later one (it was taken after creation), so the max was either created as 5 or changed afterwards. Not checked.

The workshop command was drafted with `desiredSize=0` so no node starts until it is needed, and with the nongpu-workers node role because m5 is a non-GPU instance.

### The ml-workers mix-up

The ML group was first drafted as `gpu-workers-v3` on g5.4xlarge with `AL2023_x86_64_NVIDIA`, because the request arrived in the middle of GPU work. It is a memory/CPU group (previously r5.4xlarge), so it was deleted and recreated as above. A g5 is a GPU instance (the G means GPU).

```powershell
aws eks delete-nodegroup --cluster-name <EKS_CLUSTER_NAME> --nodegroup-name ml-workers --region eu-west-1 --profile prod
aws eks describe-nodegroup --cluster-name <EKS_CLUSTER_NAME> --nodegroup-name ml-workers --region eu-west-1 --profile prod --query "nodegroup.status" --output text
```

Repeat the describe until it returns `ResourceNotFoundException`, then create.

## Behaviour worth knowing

* `minSize=0, desiredSize=1` keeps one node running permanently. Scale to zero needs `desiredSize=0` and the cluster autoscaler to bring nodes back. Cold start is roughly 2 to 3 minutes for CPU nodes and longer for GPU.
* Two node groups with the same taint and label: the scheduler places each pod on a node that can satisfy its resource requests. A pod requesting more memory than the small group's nodes have lands on the big group automatically. This only works because the DSS container configs set requests.
* Moving hosts from AL2 to AL2023 did not require rebuilding images. Containers do not depend on the host OS for user space. This platform already used cgroup v2 and dnf on AL2, which removed the usual AL2023 surprises. Rule: do not rebuild proactively. Run a test job per major code env and rebuild only what fails.
* The console shows "New AMI release version is available for this node group" when AWS publishes a newer AMI for the group's Kubernetes version. Updating is a node group version update, which drains. See `docs/runbooks/eks-version-upgrade.md`.
* The console also shows "Your current IAM principal doesn't have access to Kubernetes objects on this cluster": the SSO role has no access entry or aws-auth mapping, which is the same reason kubectl is not available.

## Scaling the workshop group

For busy sessions the workshop group's scaling was raised, for example:

```powershell
aws eks update-nodegroup-config --cluster-name <EKS_CLUSTER_NAME> --nodegroup-name workshop-workers --scaling-config desiredSize=10 --profile prod; `
aws eks describe-nodegroup --cluster-name <EKS_CLUSTER_NAME> --nodegroup-name workshop-workers --query "nodegroup.scalingConfig" --profile prod
```

Later it sat at min 3, max 15. For why it scaled on a given day, see `docs/runbooks/eks-scaling-investigation.md`.

## The pre-rebuild scripts and the tidy-up

Some weeks after the rebuild we gathered every node group script and launch template dump we could find (mostly from before the rebuild) and turned them into one ordered, commented PowerShell runbook with placeholder variables. That runbook is the "old runbook" the later GPU work started from (`infra/eks-gpu-nodegroups/RUNBOOK.md`, Security groups). What was in the pile, and what we decided:

Commands as found (all PowerShell, prod profile, the three cluster subnets, one eksctl node role):

| Command | Notes |
|---|---|
| `create-launch-template --launch-template-name gpu-small-template --launch-template-data file://gpu-big-lt.json` | Name and file disagree |
| `create-nodegroup gpu-big-workers`, g5.4xlarge, `AL2_x86_64_GPU`, 1/4/1, template `gpu-small-template` v1 | A "big" group on a small template |
| `create-nodegroup gpu-big-workers-v2`, p4d.24xlarge, `AL2_x86_64_GPU`, 1/4/1, template `gpu-big-template` v1, taint `nvidia.com/gpu=true:NoSchedule`, labels `workerType=gpubig` and `alpha.eksctl.io/nodegroup-name=gpu-big-workers` | The eksctl label points at the v1 group's name |
| `create-launch-template gpu-workers-v3` from `gpu-workers-lt.json`, then `create-nodegroup gpu-workers-v3`, g5.4xlarge, `AL2_x86_64_GPU`, 0/6/1 | The node group referenced template `gpu-workers-v2`, not the one just created |

Plus two variable blocks (one in bash syntax, one in PowerShell) naming the old templates: the eksctl-created `eksctl-<cluster>-nodegroup-gpu-workers` and an EKS-generated `eks-<uuid>` template for the big GPU group, then at a much later version. Inspection commands:

```powershell
aws ec2 describe-launch-template-versions --launch-template-id <LT_ID> --versions <VERSION> --query "LaunchTemplateVersions[0].LaunchTemplateData" --region eu-west-1 --profile prod
aws eks describe-nodegroup --cluster-name <EKS_CLUSTER_NAME> --nodegroup-name <NG> --region eu-west-1 --profile prod --query "{LT:nodegroup.launchTemplate, AmiType:nodegroup.amiType, InstanceTypes:nodegroup.instanceTypes}"
```

Launch template data as found:

| Template | Contents |
|---|---|
| eksctl gpu-workers | 200 GB gp3 (3000 IOPS, 125 MB/s) on `/dev/xvda`, key `<EC2_KEY_PAIR_NAME>`, instance/volume/ENI tags (`Name`, `Application`, `Environment`, `Project`, `alpha.eksctl.io/nodegroup-name`, `alpha.eksctl.io/nodegroup-type: managed`), three security groups from the old cluster, `HttpTokens: optional`, hop limit 2 |
| big GPU (EKS-generated, two dumps) | as above plus base64 UserData with a full bootstrap script |
| ml-training | r5.4xlarge pinned as `InstanceType`, a pinned `ImageId`, 500 GB gp3, key `<EC2_KEY_PAIR_NAME>`, tags `Project: ml-training`, `Owner: data-platform`, `Workload: eks-nodes`, a different third security group, `HttpTokens: required` |
| one unnamed block | the two current security groups (EKS cluster SG and FM virtual network default SG), key `<EC2_KEY_PAIR_NAME>`, 500 GB gp3 with `DeleteOnTermination` |

Decoding the UserData showed `/etc/eks/bootstrap.sh <cluster>` with the cluster CA, the old cluster's API endpoint (the deleted `gr7` endpoint), cluster DNS IP `<CLUSTER_DNS_IP>`, `--use-max-pods false`, and `--kubelet-extra-args` carrying `--node-labels=workerType=gpu-big` (or `gpubig` in the other dump), the eksctl and EKS labels including the AMI ID and source launch template ID, `--max-pods=234`, and `--register-with-taints`. The taint differed between the two dumps: `gpu=true:NoSchedule` in one, `nvidia.com/gpu=true:NoSchedule` in the other. One dump also had a `Name` tag saying gpu-workers while the nodegroup-name tag said gpu-big-workers.

Decisions in the tidied runbook:

* Order: variables, launch template JSON files, `create-launch-template`, `create-nodegroup`, verification, teardown. Every ID moved into a variable at the top.
* UserData removed from all templates. With `--ami-type`, EKS generates the bootstrap itself and merges any template UserData as MIME multipart, which is a common cause of boot failures, and the baked-in AMI IDs and endpoints go stale (as this endpoint had). Taints and labels go on `create-nodegroup`.
* Standardised on the `nvidia.com/gpu` taint key, which is what the NVIDIA device plugin uses.
* Tags made consistent with the group each template belongs to, and templates given readable names (`gpu-workers-lt`, `gpu-big-workers-lt`, `ml-training-lt`).
* The ml-training node group was left commented out, since its template pins `ImageId` and `InstanceType` (with a pinned image, omit `--ami-type`).
* Teardown: `delete-nodegroup`, `aws eks wait nodegroup-deleted`, and `delete-launch-template` only once nothing uses it.
* Left for later: moving the GPU templates to IMDSv2 (`HttpTokens: required`) after testing, and whether `gpu-workers` should have min 0 (the v3 draft) or min 1 (the older scripts).
* Also excluded: a list of EBS snapshots (Fleet Manager instance backups, pre-upgrade snapshots, and DLM policy snapshots of unrelated volumes) that had been pasted in with the scripts. Nothing in it related to node groups.

What the tidy-up got wrong, and was corrected later. It treated the unnamed block with the two current security groups as a scratch draft and dropped those SGs, keeping the three old ones. That block matches the data of `eks-gp3-500gb` version 4 (above), the template the post-rebuild groups actually use, and the old SGs no longer existed. The later GPU work had to take the real SGs from a running node. It also read Kubernetes 1.24 from a CloudFormation stack that described the original cluster, not the rebuilt 1.33 one, and the old `AL2_x86_64_GPU` AMI type in the commands is not available for 1.33 (use `AL2023_x86_64_NVIDIA`). The current state is the tables at the top of this file and the GPU runbook, not the tidied document.

## Open items

* Create a node instance role that no eksctl stack owns (needs the cloud team because of the permissions boundary), or at least confirm which CloudFormation stack owns the reused roles:

  ```powershell
  aws cloudformation list-stack-resources --stack-name eksctl-<EKS_CLUSTER_NAME>-nodegroup-gpu-workers --region eu-west-1 --profile prod --query "StackResourceSummaries[?ResourceType=='AWS::IAM::Role'].{LogicalId:LogicalResourceId,PhysicalId:PhysicalResourceId}"
  ```
* Put the shared launch template and these node groups into Terraform so the next rebuild does not depend on this page.
* The 10001 access depends on the FM default SG membership. If Fleet Manager ever changes that group, container jobs break the same way.
