# Why did an EKS node group scale (or not)?

PowerShell and AWS CLI, no kubectl. Used three times: the `workshop-workers` group scaling up during a busy session, a webapp timeout blamed on a cold start, and GPU groups that would not scale (in `infra/eks-gpu-nodegroups/RUNBOOK.md`).

## Commands

```powershell
$CLUSTER = "<EKS_CLUSTER_NAME>"; $NG = "<nodegroup>"

aws eks list-nodegroups --cluster-name $CLUSTER --profile prod

aws eks describe-nodegroup --cluster-name $CLUSTER --nodegroup-name $NG --profile prod `
  --query "nodegroup.{status:status,desired:scalingConfig.desiredSize,min:scalingConfig.minSize,max:scalingConfig.maxSize}"

aws eks describe-nodegroup --cluster-name $CLUSTER --nodegroup-name $NG --profile prod `
  --query "nodegroup.resources.autoScalingGroups"

aws autoscaling describe-scaling-activities --auto-scaling-group-name <ASG_NAME> --profile prod `
  --query "Activities[*].{time:StartTime,status:StatusCode,desc:Description}"
```

If the activity table is hard to read:

```powershell
aws autoscaling describe-scaling-activities --auto-scaling-group-name <ASG_NAME> --max-items 20 --output json --profile prod | ConvertFrom-Json | Select-Object -ExpandProperty Activities | Select-Object StartTime, EndTime, StatusCode, Cause | Format-List
```

A JMESPath time filter like ``Activities[?StartTime>=`YYYY-MM-DDThh:mm:ssZ`]`` does not survive PowerShell quoting. Use `--max-items` and read the times.

The ASG history says what happened, not why. For the why, CloudWatch Logs. The cluster log group has the scheduler and other control plane streams. Output can be enormous, so write it to a file and filter locally:

```powershell
$startTime = [DateTimeOffset]::UtcNow.AddHours(-3).ToUnixTimeMilliseconds()

aws logs filter-log-events --log-group-name "/aws/eks/$CLUSTER/cluster" `
  --filter-pattern $NG --start-time $startTime --region eu-west-1 --profile prod `
  | Out-File .\nodegroup-logs.json

Select-String -Path .\nodegroup-logs.json -Pattern "scale|Insufficient|pending" | Select-Object -First 50
```

Other filters that were useful: `--filter-pattern "scale-up"`, `--filter-pattern "ERROR"`, `--log-stream-name-prefix "kube-scheduler"`. In PowerShell replace bash `$(date -d '1 hour ago' +%s000)` with `[DateTimeOffset]::UtcNow.AddHours(-1).ToUnixTimeMilliseconds()` and `\` continuations with backticks.

The cluster autoscaler's own logs are not in CloudWatch (it runs as a pod and the observability add-on has no IAM role), so the scheduler log and the ASG history are what we have.

## Case 0: how actively is the cluster used?

The first time we looked, the question was simply whether the node groups scale up and down at all and how busy they are. The groups then were `api-workers`, `gpu-workers`, `ml-workers`, `nongpu-workers`, `standard-workers` and `system-nodes`. A bash `for` loop does not run in PowerShell. The PowerShell version:

```powershell
foreach ($ng in @("api-workers", "gpu-workers", "ml-workers", "nongpu-workers", "standard-workers", "system-nodes")) {
    Write-Host "=== $ng ==="
    aws eks describe-nodegroup `
        --cluster-name <EKS_CLUSTER_NAME> `
        --nodegroup-name $ng `
        --profile prod `
        --query "nodegroup.{status: status, desiredSize: scalingConfig.desiredSize, minSize: scalingConfig.minSize, maxSize: scalingConfig.maxSize, instanceType: instanceTypes[0], asgName: resources.autoScalingGroups[0].name}" `
        --output table
}
```

What it showed that day:

| Group | Instance | State |
|---|---|---|
| `system-nodes` | t3.large | fixed at 2, never scales |
| `api-workers` | m6i.large | at least 1, max 5 |
| `gpu-workers` | g5.2xlarge | 1 running, min 0 |
| `ml-workers` | r6i.4xlarge | 2 running, min 0, so these were active jobs |
| `standard-workers` | r6i.2xlarge | 2 running, min 0, also in use |
| `nongpu-workers` | m6i.large | idle at 0, max 15, a burst group |

These limits differ from the creation values in `infra/eks-nodegroups/RUNBOOK.md` (`api-workers` 0/2/0 and `nongpu-workers` 1/2/1 at creation after the rebuild). The figures above are the later ones, so the limits were changed some time after the rebuild.

Scaling history for every worker ASG since the rebuild, written to a file because the tables are long:

```powershell
foreach ($asg in @("<ASG_API_WORKERS>", "<ASG_GPU_WORKERS>", "<ASG_ML_WORKERS>", "<ASG_NONGPU_WORKERS>", "<ASG_STANDARD_WORKERS>")) {
    "=== $asg ===" | Out-File -Append -FilePath "scaling_activity.txt"
    aws autoscaling describe-scaling-activities `
        --auto-scaling-group-name $asg `
        --profile prod `
        --query "Activities[?StartTime>='<REBUILD_DATE>'].{Time: StartTime, Status: StatusCode, Description: Description}" `
        --output table | Out-File -Append -FilePath "scaling_activity.txt"
}
```

The file lands in the current PowerShell directory. This string comparison on `StartTime` (single quotes, no backticks) did work in PowerShell, unlike the backtick literal noted under Commands. A `cloudwatch get-metric-statistics` call on namespace `AWS/EKS` with metric `node_count` was also drafted for a 7-day view. It was never run, and we have not confirmed that such a metric exists for this cluster. The contents of `scaling_activity.txt` were not reviewed that day because a security alert took over.

## Case 1: workshop-workers scaling up

Group `ACTIVE`, desired 6, min 3, max 15. ASG history:

* The day before: 08:47 burst to about 10 nodes, all terminated at 08:58. Another small burst 13:12 to 13:22.
* On the day: 07:48 +2, 09:57 +2, 10:02 +1 (3 to 6), all `Successful`.

Gradual steps like this point to load, not to someone setting the size by hand (manual changes land all at once). The filtered cluster log confirmed it: users' `ANALYSIS_ML_TRAIN` jobs in DSS created CPU pressure, and the autoscaler added nodes. Nobody scaled it manually.

How we got from the ASG history to the cause, in order:

1. `describe-nodegroup` for status and scaling config (`ACTIVE`, 6/3/15).
2. `describe-nodegroup --query 'nodegroup.resources.autoScalingGroups'` for the ASG name (managed node group ASGs are named `eks-<nodegroup>-<uuid>`).
3. `describe-scaling-activities` on that ASG for the timeline above.
4. `filter-log-events` on `/aws/eks/<EKS_CLUSTER_NAME>/cluster`. `--filter-pattern "scale-up"` found nothing useful. `--filter-pattern "workshop-workers"` over the last 3 hours did, but the JSON was far too large to read. We wrote it to a file and cut it down locally:

```powershell
aws logs filter-log-events --log-group-name /aws/eks/<EKS_CLUSTER_NAME>/cluster `
  --filter-pattern "workshop-workers" --start-time $startTime --region eu-west-1 --profile prod `
  | Out-File -FilePath ".\workshop-workers-logs.json"

# only lines mentioning scaling
Get-Content .\workshop-workers-logs.json | Select-String "scale" | Out-File .\workshop-workers-scale.txt
(Get-Content .\workshop-workers-scale.txt).Count

# or only the minutes around the scale-up being explained (10:00 to 10:05)
Get-Content .\workshop-workers-logs.json | Select-String "10:0[0-5]" | Out-File .\workshop-workers-1002.txt

# still too big: first 50 matching lines
Get-Content .\workshop-workers-scale.txt | Select-String "workshop-workers" | Select-Object -First 50
```

Twenty to thirty lines around the event were enough. What they showed for the 10:02 scale-up:

| Time (UTC) | Event |
|---|---|
| 10:02:40 | Several users submit `ANALYSIS_ML_TRAIN` jobs at once. DSS creates the pods through the Kubernetes API (`kubectl create`, authenticated as the DSS IAM role `<DSS_INSTANCE_ROLE>`) |
| 10:02:40 | Scheduler marks them `Unschedulable`: `0/<N> nodes available: <M> Insufficient cpu` |
| 10:02:58 | New EC2 instance launched in the workshop ASG, about 18 seconds after the pods went pending |
| 10:03:16 | Two more users' pods hit the same `Insufficient cpu` |
| 10:03:34 | Pods scheduled on the new node, and the 10:03:16 pods followed once it was ready |

So the chain was DSS submits pods, the scheduler cannot place them for CPU, the Cluster Autoscaler sees pending pods and raises the ASG desired count, the node joins, the pods schedule. The whole loop took under a minute.

At the time we were not sure where the autoscaler's own logs went. The usual candidates are a separate log group such as `/aws/eks/<EKS_CLUSTER_NAME>/cluster-autoscaler` or `kubectl logs` on the autoscaler pod. Neither was available to us (see the note under Commands), and the scheduler events in the cluster log group were enough for this question.

Setting a size by hand for a session:

```powershell
aws eks update-nodegroup-config --cluster-name $CLUSTER --nodegroup-name workshop-workers `
  --scaling-config desiredSize=10 --profile prod; `
aws eks describe-nodegroup --cluster-name $CLUSTER --nodegroup-name workshop-workers --query "nodegroup.scalingConfig" --profile prod
```

## Case 2: webapp start timeout at 20:47 UTC

A Dash webapp failed a scenario step with `java.io.IOException: Webapp backend start timeout` (DSS waits 60 s) and came up about 4 minutes later on its own. Theory: EKS cold start on `standard-workers`. ASG history showed a scale-down at 12:33 and the next scale-up at 05:01 the following day, nothing near 20:47. And the webapp log itself said `"containerMode": "NONE"` and `"serveLocally": true` (the `containerConf: eks-highmemory` line next to it was inert): the backend runs on the DSS host, not on EKS. Cold start ruled out. The remaining explanations are slow Python startup (heavy imports, Snowflake connections at module load) or the 60 s timeout being too short. The fix is lazy initialisation or a longer timeout.

Lesson: read the job/webapp log for the execution mode before investigating the cluster.

## Case 3: GPU groups that did not scale

See `infra/eks-gpu-nodegroups/RUNBOOK.md`, sections 4 and 6. Short version: `InsufficientInstanceCapacity` in every AZ for the whole g5 family, and the ASG gave up and returned to 0.

## Reading scheduler messages

| Message | Meaning |
|---|---|
| `Insufficient nvidia.com/gpu` | A matching node exists but its GPU is taken |
| `had untolerated taint(s)` | Nodes from other groups, normal noise |
| `didn't match Pod's node affinity/selector` | Label mismatch. Normal for other groups, a problem if it is the group you expect |
| `node(s) were unschedulable` | A node not ready or cordoned, often one that never came up |
| `max node group size reached` | The group is at max. Raise max or add a group |
