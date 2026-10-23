# EKS GPU node groups for Dataiku containerized execution

Built on the prod cluster. Commands are PowerShell from a Windows laptop with an SSO profile called `prod` (role `AWSReservedSSO_<PLATFORM_ADMIN_ROLE>_<HASH>`). There is no kubectl: the cluster API endpoint is private only and there is no bastion, so everything here goes through the AWS CLI and CloudWatch Logs.

Related: `docs/runbooks/eks-scaling-investigation.md` (why a group scaled), `docs/runbooks/eks-node-group-retirement.md` (removing the legacy group), `infra/eks-nodegroups/RUNBOOK.md` (the CPU node groups and the shared 500 GB launch template).

## Background: how we got here

The starting point was a single managed node group, `gpu-workers`, on g5.2xlarge, with label `workerType=gpu` and taint `nvidia.com/gpu`. The DSS containerized execution profile for GPU had:

* node selector `workerType: gpu`
* toleration `key: nvidia.com/gpu`, `operator: Exists`, `effect: NoSchedule`
* custom limit `nvidia.com/gpu: 1`
* memory request 500 MB, memory limit 30720 MB (scheduling uses the request, and the limit does not affect placement)

GPU jobs were not scheduling and we went through the profile line by line before finding the real cause.

### The taint casing detour

The EKS console showed the taint value as lowercase `exists`. That raised the question of whether casing was the problem. Findings, in order:

* `aws eks describe-nodegroup ... --query "nodegroup.taints"` returned `"value": "exists"`. A later full `describe-nodegroup` without `--query` showed `"Exists"`. We never explained the difference. It does not matter for matching (next point), but do not trust the console or a single filtered query as ground truth.
* With `operator: Exists`, a toleration matches on key and effect only. The taint value is ignored. So `exists` vs `Exists` in the taint value is irrelevant.
* The operator field in the pod spec is case sensitive and only accepts `Equal` or `Exists`. At one point DSS rejected a test pod with:

  ```
  spec.tolerations[0].operator: Unsupported value: "exists": supported values: "Equal", "Exists"
  ```

  That was the profile's operator field, which must be `Exists` with a capital E.
* The ASG's running instance was on an older launch template version than the template's current one. Comparing them (`aws ec2 describe-launch-template-versions --launch-template-id <LT_ID> --versions <OLD_VERSION> <CURRENT_VERSION>`) showed the only difference was the UserData taint flag: `--register-with-taints=nvidia.com/gpu=exists:NoSchedule` in the old version and `...=Exists:NoSchedule` in the current one. Someone had "fixed" the value. Old nodes keep the version they launched with, so a group can carry mixed configuration until nodes are cycled. This is one reason the new templates have no UserData at all.

### The real cause

The kube-scheduler log in CloudWatch said:

```
0/<N> nodes are available: 1 Insufficient nvidia.com/gpu, 1 node(s) were unschedulable,
<M> node(s) had untolerated taint(s), 2 node(s) didn't match Pod's node affinity/selector
```

Reading it: the one GPU node existed but its single GPU was already allocated to another pod. The nodes with untolerated taints carried taints the pod did not tolerate (normal, those are the CPU groups). "Unschedulable" was a node that never came up.

The ASG had `DesiredCapacity: 2` but one instance. Its scaling activities showed `InsufficientInstanceCapacity` for g5.2xlarge in eu-west-1a, 1b and 1c. AWS had no g5.2xlarge capacity in the region. The profile and node group configuration were correct.

## Options considered

| Option | Outcome |
|---|---|
| Wait for AWS capacity | No SLA. Rejected as the only answer. |
| Reduce desired to 1 and wait for the busy GPU | Does not help a second concurrent job. |
| Multiple instance types on one managed node group | We worked on the basis that this cannot be combined with our custom launch template. Not tested. AWS does allow several instance types with a launch template as long as the template itself sets no instance type, so this is worth re-checking before building more groups. |
| Drop the custom launch template | Rejected. We would lose the 200 GB gp3 root volume (default is 20 GB, which GPU container images fill quickly), the SSH key, the three security groups and the cost allocation tags. |
| One node group per instance type, same label and taint | Chosen. The scheduler places pods on whichever group has a node with capacity. |

GPU families in eu-west-1, from `describe-instance-type-offerings` (offerings, not live capacity):

```powershell
aws ec2 describe-instance-type-offerings --location-type availability-zone --filters "Name=instance-type,Values=g5.*,g4dn.*,p3.*,p4d.*" --region eu-west-1 --profile prod --query "InstanceTypeOfferings[*].[InstanceType,Location]" --output table
```

* g5 (A10G 24 GB), xlarge to 48xlarge: offered in all three AZs.
* g4dn (T4 16 GB): offered in all three AZs. Different capacity pool from g5, which is why it is the useful fallback.
* p4d.24xlarge (8x A100): eu-west-1b only, far too expensive for this.
* p3: not in the output (p3 had already been retired from our configs earlier).
* g6 (L4): not offered in eu-west-1 at the time (Frankfurt, London, Paris, Spain, Stockholm only).

Approximate on-demand prices used for the decision. These were estimates (us-east-1 list prices plus 10 to 15 percent), not confirmed eu-west-1 prices:

| Instance | GPU | vCPU / RAM | ~USD/h |
|---|---|---|---|
| g4dn.2xlarge | 1x T4 16 GB | 8 / 32 GB | 0.84 |
| g5.2xlarge | 1x A10G 24 GB | 8 / 32 GB | 1.37 |
| g5.4xlarge | 1x A10G 24 GB | 16 / 64 GB | 1.84 |
| g5.8xlarge | 1x A10G 24 GB | 32 / 128 GB | 2.77 |

T4 is an NVIDIA (Turing) GPU, so the same `nvidia.com/gpu` resource, taint and device plugin apply.

## Naming

The existing CPU groups are named by shape (for example `eks-8vcpu-128gb-500gb`). The GPU groups follow the same idea with GPU model and VRAM:

| Node group | Launch template | Instance | GPU | vCPU / RAM | Disk |
|---|---|---|---|---|---|
| `eks-gpu-a10g-24gb-8vcpu-200gb` | `lt-gpu-a10g-24gb-8vcpu-200gb` | g5.2xlarge | A10G 24 GB | 8 / 32 GB | 200 GB gp3 |
| `eks-gpu-a10g-24gb-16vcpu-200gb` | `lt-gpu-a10g-24gb-16vcpu-200gb` | g5.4xlarge | A10G 24 GB | 16 / 64 GB | 200 GB gp3 |
| `eks-gpu-a10g-24gb-32vcpu-200gb` | `lt-gpu-a10g-24gb-32vcpu-200gb` | g5.8xlarge | A10G 24 GB | 32 / 128 GB | 200 GB gp3 |
| `eks-gpu-t4-16gb-8vcpu-200gb` | `lt-gpu-t4-16gb-8vcpu-200gb` | g4dn.2xlarge | T4 16 GB | 8 / 32 GB | 200 GB gp3 |

We first planned one launch template for all four. That works technically (the template holds disk, key, SGs and tags, and the instance type is set on the node group), but the tags would say the same thing for every group and break cost allocation. We went with one template per group.

## 0. Variables

```powershell
$AWS_REGION  = "eu-west-1"
$AWS_PROFILE = "prod"
$CLUSTER     = "<EKS_CLUSTER_NAME>"
$NODE_ROLE   = "arn:aws:iam::<AWS_ACCOUNT_ID>:role/<GPU_NODE_INSTANCE_ROLE>"
```

### Security groups: take them from a running node

The old runbook we started from listed security group IDs that no longer existed (two of the three were recreated when the cluster was rebuilt, see `docs/runbooks/eks-cluster-stop-and-recreate.md`). Get the real ones from a node that is running now:

```powershell
aws ec2 describe-instances --instance-ids <RUNNING_GPU_NODE_ID> --region eu-west-1 --profile prod --query "Reservations[0].Instances[0].SecurityGroups"
```

That returned three:

| Purpose | Name pattern |
|---|---|
| EKS cluster security group (required, since nodes cannot talk to the control plane without it) | `eks-cluster-sg-<cluster>-<n>` |
| Shared workers security group | `<company>-workers-sg` |
| SSH remote access group created by eksctl | `eksctl-<cluster>-nodegroup-gpu-workers-remoteAccess` |

The old runbook also had `HttpTokens: optional` (IMDSv1 allowed). The live template had `required`. The new templates use `required` with hop limit 2.

## 1. Launch templates

The four JSON files are in `launch-templates/`. Each sets a 200 GB gp3 root volume (3000 IOPS, 125 MB/s), the key pair, the three security groups, IMDSv2 required, and instance, volume and network-interface tags (`Name`, `Application`, `Environment`, `Project`, plus the `alpha.eksctl.io/nodegroup-*` tags).

They contain no UserData and no AMI. EKS bootstraps the node from `--ami-type`. Taints and labels are passed on `create-nodegroup` where they are visible and cannot drift between template versions.

Gotcha: when the JSON was first saved from a markdown document, the file began with a stray ```` ```json ```` line and the editor showed it red. The file must start with `{`.

```powershell
foreach ($lt in "lt-gpu-a10g-24gb-8vcpu-200gb","lt-gpu-a10g-24gb-16vcpu-200gb",
                "lt-gpu-a10g-24gb-32vcpu-200gb","lt-gpu-t4-16gb-8vcpu-200gb") {
  aws ec2 create-launch-template `
    --launch-template-name $lt `
    --version-description "v1" `
    --launch-template-data file://launch-templates/$lt.json `
    --region $AWS_REGION --profile $AWS_PROFILE
}
```

## 2. Node groups

Final working form for one group. Repeat with the other names, templates and instance types.

```powershell
aws eks create-nodegroup `
    --cluster-name $CLUSTER `
    --nodegroup-name "eks-gpu-a10g-24gb-8vcpu-200gb" `
    --node-role $NODE_ROLE `
    --subnets "<SUBNET_A>" "<SUBNET_B>" "<SUBNET_C>" `
    --scaling-config minSize=0,maxSize=5,desiredSize=0 `
    --launch-template name=lt-gpu-a10g-24gb-8vcpu-200gb,version=1 `
    --instance-types g5.2xlarge `
    --ami-type AL2023_x86_64_NVIDIA `
    --taints key=nvidia.com/gpu,value=Exists,effect=NO_SCHEDULE `
    --labels workerType=gpu `
    --region $AWS_REGION --profile $AWS_PROFILE
```

Errors on the way:

* Backslash line continuation from a bash example fails in PowerShell with `Missing expression after unary operator '--'`. Use backticks, or put the command on one line.
* Subnets as one joined string fail, both space joined and comma joined:

  ```
  An error occurred (InvalidParameterException) when calling the CreateNodegroup operation:
  The subnet ID '<SUBNET_A> <SUBNET_B> <SUBNET_C>' does not exist (Service: AmazonEC2;
  Status Code: 400; Error Code: InvalidSubnetID.NotFound; ...)
  ```

  The CLI wants separate quoted arguments, as above. A `$SUBNETS -join " "` variable does not work.
* A trailing space after a backtick silently ends the command at that line. Nothing may follow the backtick.

Check all four:

```powershell
foreach ($ng in "eks-gpu-a10g-24gb-8vcpu-200gb","eks-gpu-a10g-24gb-16vcpu-200gb",
                "eks-gpu-a10g-24gb-32vcpu-200gb","eks-gpu-t4-16gb-8vcpu-200gb") {
  aws eks describe-nodegroup --cluster-name $CLUSTER --nodegroup-name $ng `
    --query "{Name:nodegroup.nodegroupName,Status:nodegroup.status,InstanceTypes:nodegroup.instanceTypes,Scaling:nodegroup.scalingConfig,Taints:nodegroup.taints}" `
    --region $AWS_REGION --profile $AWS_PROFILE
}
```

All four came back `ACTIVE`, min 0, max 5, desired 0, taint `nvidia.com/gpu` / `Exists` / `NO_SCHEDULE`. The new groups launched on AMI release `1.35.5-20260527`, while the legacy `gpu-workers` was on `1.35.3-20260415`.

## 3. DSS container execution profile

See `dss-gpu-container-profile.yaml`.

All four groups share the label and taint, so one DSS profile covers them. We first created four profiles named after the node groups and then realised they were identical: with the same node selector they are four names for the same scheduling rule, and Kubernetes will put the pod wherever there is capacity. Separate profiles only make sense if each group gets its own label (for example `nodeGroup=eks-gpu-a10g-24gb-32vcpu-200gb`) and each profile selects on it, so a user can pin a heavy job to the 32 vCPU group.

Size the memory limit to the smallest node the profile can land on, minus OS and system pods: about 30 GB on a 32 GB node. A 68 GB limit on a 32 GB node can never be satisfied.

## 4. Testing, and what happened

1. Clicked Test on the GPU profile with all new groups at desired 0. Scheduler log:

   ```
   0/<N> nodes are available: 1 Insufficient nvidia.com/gpu, 2 node(s) didn't match Pod's node affinity/selector
   ```

   No new node launched. The new ASGs did carry `k8s.io/cluster-autoscaler/enabled: true`:

   ```powershell
   aws autoscaling describe-auto-scaling-groups --filters "Name=tag:eks:cluster-name,Values=$CLUSTER" --region eu-west-1 --profile prod --query "AutoScalingGroups[*].{Name:AutoScalingGroupName,Tags:Tags[?Key=='k8s.io/cluster-autoscaler/enabled'].Value}"
   ```

   The test pod went into a per-user namespace (`dss-ns-gpu-s-<user-derived>`), which made us suspect an old profile, but the profile's selector and toleration were correct.

2. There is no `cluster-autoscaler` log stream in `/aws/eks/<cluster>/cluster`. Cluster logging has `api`, `audit`, `controllerManager`, `scheduler` enabled and `authenticator` disabled. The streams present are kube-scheduler, kube-apiserver, kube-controller-manager and cloud-controller-manager. The autoscaler runs as a pod, so its logs only reach CloudWatch through Container Insights / Fluent Bit. The `amazon-cloudwatch-observability` add-on (v5.3.1-eksbuild.1) is installed but has neither Pod Identity nor IRSA configured, and `/aws/containerinsights` has no log groups. So the add-on is running without permission to ship anything. The autoscaler itself has worked before, so "not deployed" was the wrong conclusion. Its logs are invisible to us.

3. Scaled `eks-gpu-a10g-24gb-16vcpu-200gb` to desired 1 by hand. The DSS test passed. We took that as proof the new group worked. It was not: every scaling activity on that ASG was `InsufficientInstanceCapacity` in every AZ, and the test had landed on the old `gpu-workers` node after its GPU freed up. The console still showed desired 1 while the ASG had already gone back to desired 0 with no instances:

   ```powershell
   aws autoscaling describe-auto-scaling-groups --auto-scaling-group-name "<ASG_NAME>" --profile prod --query "AutoScalingGroups[0].{Desired:DesiredCapacity,InService:Instances[?LifecycleState=='InService']}"
   ```

   Look at scaling activities without a short `--query` slice. The first five entries can all be old failures and hide the one you care about.

4. Scaled all four to desired 1. The g4dn (T4) group launched. All three g5 groups failed with capacity errors. The whole g5 family was exhausted in eu-west-1 that day, not just g5.2xlarge.

5. Scaled the three g5 groups back to 0 and left the T4 group at 1 because a workload had landed on it.

**Scaling a group's desiredSize down terminates nodes immediately and does not honour Pod Disruption Budgets** (AWS documentation). A node group *version update* drains gracefully, but a *scaling config* change does not. Never set desired to 0 on a group with running jobs.

## 5. Monitoring

The gap this exposed: an ASG can retry, fail on capacity and quietly drop back to 0 while the console shows stale values, and nobody is told.

What the SSO role could and could not do (tested, not assumed):

| Action | Result |
|---|---|
| `sns:CreateTopic` | Denied |
| `events:PutRule` | `AccessDeniedException ... is not authorized to perform: events:PutRule ... because no identity-based policy allows the events:PutRule action` |
| `lambda list-functions` | Allowed (list only, create not tested) |
| SES | No verified identities, so not usable |
| `cloudwatch:PutMetricAlarm` | Allowed |
| `iam simulate-principal-policy` with the `arn:aws:sts::...:assumed-role/...` ARN | `InvalidInput: Invalid ARN`. Use the IAM role ARN form `arn:aws:iam::<AWS_ACCOUNT_ID>:role/aws-reserved/sso.amazonaws.com/AWSReservedSSO_<PLATFORM_ADMIN_ROLE>_<HASH>` instead. |

There were no SNS topics in the account. There is no Datadog AWS integration role (the only monitoring-like role is `AWSServiceRoleForObservabilityAdmin`, which is AWS's own). A local scheduled PowerShell job sending SMTP mail was suggested and rejected, because the monitoring has to live in AWS.

So we built CloudWatch alarms without actions. They show state in the console. Email needs SNS later.

Test that alarm creation works, then remove the test alarm:

```powershell
aws cloudwatch put-metric-alarm --alarm-name "test-permission-check" --metric-name GroupDesiredCapacity --namespace AWS/AutoScaling --statistic Average --period 300 --threshold 1 --comparison-operator GreaterThanOrEqualToThreshold --evaluation-periods 1 --region eu-west-1 --profile prod
aws cloudwatch describe-alarms --alarm-names "test-permission-check" --region eu-west-1 --profile prod
aws cloudwatch delete-alarms --alarm-names "test-permission-check" --region eu-west-1 --profile prod
```

Get the ASG name behind each group:

```powershell
aws autoscaling describe-auto-scaling-groups --filters "Name=tag:eks:nodegroup-name,Values=eks-gpu-a10g-24gb-8vcpu-200gb,eks-gpu-a10g-24gb-16vcpu-200gb,eks-gpu-a10g-24gb-32vcpu-200gb,eks-gpu-t4-16gb-8vcpu-200gb" --region eu-west-1 --profile prod --query "AutoScalingGroups[*].{NodeGroup:Tags[?Key=='eks:nodegroup-name'].Value|[0],ASG:AutoScalingGroupName}"
```

Two alarms per group, verbatim as created:

```powershell
$asgs = @{
    "eks-gpu-a10g-24gb-8vcpu-200gb"  = "<ASG_A10G_8VCPU>"
    "eks-gpu-a10g-24gb-16vcpu-200gb" = "<ASG_A10G_16VCPU>"
    "eks-gpu-a10g-24gb-32vcpu-200gb" = "<ASG_A10G_32VCPU>"
    "eks-gpu-t4-16gb-8vcpu-200gb"    = "<ASG_T4_8VCPU>"
}

foreach ($ng in $asgs.Keys) {
    $asg = $asgs[$ng]

    aws cloudwatch put-metric-alarm `
        --alarm-name "gpu-nodegroup-no-instances-$ng" `
        --alarm-description "GPU node group $ng has 0 instances in service" `
        --metric-name GroupInServiceInstances `
        --namespace AWS/AutoScaling `
        --statistic Average `
        --period 300 `
        --threshold 1 `
        --comparison-operator LessThanThreshold `
        --evaluation-periods 2 `
        --dimensions Name=AutoScalingGroupName,Value=$asg `
        --treat-missing-data notBreaching `
        --region eu-west-1 `
        --profile prod

    aws cloudwatch put-metric-alarm `
        --alarm-name "gpu-nodegroup-capacity-failure-$ng" `
        --alarm-description "GPU node group $ng desired capacity not met - possible InsufficientInstanceCapacity" `
        --metric-name GroupPendingInstances `
        --namespace AWS/AutoScaling `
        --statistic Average `
        --period 300 `
        --threshold 1 `
        --comparison-operator GreaterThanOrEqualToThreshold `
        --evaluation-periods 3 `
        --dimensions Name=AutoScalingGroupName,Value=$asg `
        --treat-missing-data notBreaching `
        --region eu-west-1 `
        --profile prod
}

aws cloudwatch describe-alarms --alarm-name-prefix "gpu-nodegroup" --region eu-west-1 --profile prod --query "MetricAlarms[*].{Name:AlarmName,State:StateValue,Reason:StateReason}"
```

State an hour later: the three g5 `no-instances` alarms in `ALARM` (2 datapoints of 0.0), the T4 one `OK` (1 instance), all four `capacity-failure` alarms `OK` (pending 0, because the ASGs had already given up and gone back to 0). Right after creation most showed `INSUFFICIENT_DATA`, which clears once datapoints arrive.

Known weaknesses:

* `no-instances` fires on any group that is legitimately at 0. It is a visibility aid, not a page.
* `capacity-failure` will rarely fire because a failed ASG returns to desired 0 instead of sitting in pending.
* If group metrics are not enabled on an ASG the alarms get no data: `aws autoscaling enable-metrics-collection --auto-scaling-group-name <ASG_NAME> --granularity "1Minute"`.

What the proper setup looks like once permissions exist (designed, not deployed). EventBridge rule on failed launches, target an SNS topic with an email subscription:

```json
{
  "source": ["aws.autoscaling"],
  "detail-type": ["EC2 Instance Launch Unsuccessful"],
  "detail": {
    "AutoScalingGroupName": [
      "eks-eks-gpu-a10g-24gb-8vcpu-200gb-*",
      "eks-eks-gpu-a10g-24gb-16vcpu-200gb-*",
      "eks-eks-gpu-a10g-24gb-32vcpu-200gb-*",
      "eks-eks-gpu-t4-16gb-8vcpu-200gb-*"
    ]
  }
}
```

EventBridge patterns do not support a trailing `*` as written here. Use `{"prefix": "eks-eks-gpu-"}` when this is built. Once SNS exists, re-run the alarm loop with `--alarm-actions arn:aws:sns:eu-west-1:<AWS_ACCOUNT_ID>:<TOPIC>` added.

Pending pods (the other thing worth alerting on) need Container Insights metrics, which need the IAM fix on the observability add-on.

## 6. Diagnosing scheduling without kubectl

```powershell
aws logs describe-log-groups --log-group-name-prefix "/aws/eks/$CLUSTER" --profile prod

aws logs filter-log-events `
  --log-group-name "/aws/eks/$CLUSTER/cluster" `
  --log-stream-name-prefix "kube-scheduler" `
  --start-time ([DateTimeOffset]::UtcNow.AddMinutes(-5).ToUnixTimeMilliseconds()) `
  --profile prod

aws logs describe-log-streams --log-group-name "/aws/eks/$CLUSTER/cluster" --region eu-west-1 --profile prod --query "logStreams[*].logStreamName"

aws eks describe-cluster --name $CLUSTER --region eu-west-1 --profile prod --query "cluster.logging"

aws autoscaling describe-scaling-activities --auto-scaling-group-name "<ASG_NAME>" --profile prod --query "Activities[0:10]"
```

The CloudWatch "control plane scaling" dashboard (API concurrency, scheduling rate, etcd size) does not help with node group capacity questions.

## 7. Retiring the legacy `gpu-workers` group

Optional and not done yet. Only after the new groups are proven and the profile is pointed at the shared label. Cordon and drain need kubectl, which we do not have, so the practical sequence is: wait until nothing runs on the old node, then delete the group. See `docs/runbooks/eks-node-group-retirement.md`.

## Open items

* Ask the cloud team for `sns:CreateTopic`, `sns:Subscribe`, `events:PutRule`, `events:PutTargets` on the <PLATFORM_ADMIN_ROLE> role, then add alarm actions and the EventBridge rule.
* Ask the cloud team to attach an IAM role with `CloudWatchAgentServerPolicy` to the `amazon-cloudwatch-observability` add-on (Pod Identity or IRSA) so pod logs, including the cluster autoscaler, and pending-pod metrics reach CloudWatch.
* Re-check whether multiple instance types on one group with our launch template is possible. It would collapse the four g5/g4dn groups.
* Retire `gpu-workers` and its launch template.
* Confirm the autoscaler scales the new groups up from 0 on its own (only manual scale-ups were observed).
