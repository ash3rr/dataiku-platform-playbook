# Retiring or replacing an EKS node group

Applies to the legacy `gpu-workers` group (replaced by the GPU groups in `infra/eks-gpu-nodegroups/`) and to any CLI-created group. Done without kubectl.

## Things that bite

* Scaling desiredSize down kills pods. AWS terminates the instances straight away and does not honour Pod Disruption Budgets. Only node group version updates drain gracefully. We left the T4 group at desired 1 for this reason while a job was on it.
* Desired is not actual. After failed launches the ASG drops back to 0 while the console may still show the old desired size. Check the ASG:

  ```powershell
  aws autoscaling describe-auto-scaling-groups --auto-scaling-group-name "<ASG_NAME>" --profile prod --query "AutoScalingGroups[0].{Desired:DesiredCapacity,InService:Instances[?LifecycleState=='InService']}"
  ```
* The CLI-created groups reuse eksctl-created node roles. `ml-workers` uses the role created for `gpu-workers`, and `standard-workers` and `workshop-workers` use the `nongpu-workers` role. Deleting a node group through eksctl, or deleting its CloudFormation stack, deletes its role and breaks every group using it. Deleting with `aws eks delete-nodegroup` on a group that eksctl did not create does not touch roles, but check before deleting an eksctl group:

  ```powershell
  aws cloudformation list-stack-resources --stack-name eksctl-<EKS_CLUSTER_NAME>-nodegroup-<NG> --region eu-west-1 --profile prod --query "StackResourceSummaries[?ResourceType=='AWS::IAM::Role'].{LogicalId:LogicalResourceId,PhysicalId:PhysicalResourceId}"
  aws eks list-nodegroups --cluster-name <EKS_CLUSTER_NAME> --profile prod
  foreach ($ng in (aws eks list-nodegroups --cluster-name <EKS_CLUSTER_NAME> --profile prod --query "nodegroups[]" --output text) -split "\s+") { aws eks describe-nodegroup --cluster-name <EKS_CLUSTER_NAME> --nodegroup-name $ng --profile prod --query "[nodegroup.nodegroupName,nodegroup.nodeRole]" --output text }
  ```

  (PowerShell loops pasted at the prompt work even where `.ps1` scripts are blocked.)
* DSS profiles may still point at the group. Check every containerized execution profile's node selector and toleration before removing the only group that matches them. Otherwise pods stay `Pending` with `didn't match Pod's node affinity/selector` or `had untolerated taint(s)`.
* Old groups can run instances from an older launch template version than the current default (the legacy GPU node was several versions behind). That drift is not a reason to keep a group, but it explains odd differences between nodes.

## Sequence

1. Make sure the replacement groups exist, are `ACTIVE`, and have run a job. A successful DSS test is not proof, so check which node the job landed on in the scheduler log or ASG activity (see the GPU runbook's testing section).
2. Point the DSS profile(s) at the shared label of the new groups.
3. Wait until nothing runs on the old group. Without kubectl the practical signals are: no DSS jobs or Code Studios using that profile, and CloudWatch scheduler logs showing no placements onto it. With kubectl it would be `kubectl cordon <node>` then `kubectl drain <node> --ignore-daemonsets --delete-emptydir-data`.
4. Delete the group and wait:

   ```powershell
   aws eks delete-nodegroup --cluster-name <EKS_CLUSTER_NAME> --nodegroup-name gpu-workers --region eu-west-1 --profile prod
   aws eks wait nodegroup-deleted --cluster-name <EKS_CLUSTER_NAME> --nodegroup-name gpu-workers --region eu-west-1 --profile prod
   ```

   Or poll `describe-nodegroup ... --query "nodegroup.status"` until it returns `ResourceNotFoundException`.
5. Delete its launch template if nothing else uses it:

   ```powershell
   aws ec2 delete-launch-template --launch-template-id <LT_ID> --region eu-west-1 --profile prod
   ```
6. Remove its CloudWatch alarms (`gpu-nodegroup-*` for GPU groups).

## Recreating a group with a different template

Same pattern was used for `ml-workers`: delete, poll until gone, create with the new launch template version. Expect a short gap with no capacity for that label.

## Status

* Legacy `gpu-workers`: still present at last check. Retirement pending.
