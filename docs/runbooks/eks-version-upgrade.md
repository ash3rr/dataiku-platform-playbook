# EKS version upgrades and node AMI updates

The prod cluster was recreated on 1.33 (`eks-cluster-stop-and-recreate.md`). Later it was on 1.35: add-ons `kube-proxy v1.35.3-eksbuild.2`, `vpc-cni v1.21.1-eksbuild.7`, `metrics-server v0.8.1-eksbuild.6`, `amazon-cloudwatch-observability v5.3.1-eksbuild.1`, `aws-network-flow-monitoring-agent v1.1.3-eksbuild.4` among others, node AMI releases `1.35.3-20260415` on the older GPU group and `1.35.5-20260527` on groups created later. Node AMI updates and cluster observability were part of the DSS 14.5.1 upgrade work. The individual upgrade steps were not written down at the time. This page has the procedure as worked out beforehand, plus what we know about the state before and after.

## What to know before upgrading

* Order: control plane first, then add-ons, then node groups. One minor version at a time.
* The control plane upgrade does not take workloads down. Reports for 1.33 to 1.34 put it at around 6 minutes.
* Look at the cluster's Upgrade insights tab first. EKS scans for known blockers for the target version.
* 1.33 to 1.34 specifics: VolumeAttributesClass is GA in 1.34 and only the GA API is served, so the EBS CSI driver must be 1.50 or later or volume modifications fail. AppArmor is deprecated in 1.34 (move to seccomp or Pod Security Standards if used).
* Staying behind is not free: once a version leaves standard support it costs extra per cluster hour, and when extended support ends AWS upgrades the control plane on its own schedule.

## Node groups

For managed node groups, "upgrading" the group means rolling it to the AMI release for the new Kubernetes version. AWS launches new nodes, cordons and drains old ones (honouring Pod Disruption Budgets) and terminates them one at a time. This is different from changing desired size, which terminates nodes immediately without draining.

The console shows "New AMI release version is available for this node group" when there is a newer AMI for the group's current version. Same mechanism.

```powershell
aws eks update-nodegroup-version --cluster-name <EKS_CLUSTER_NAME> --nodegroup-name <NG> --region eu-west-1 --profile prod
aws eks describe-nodegroup --cluster-name <EKS_CLUSTER_NAME> --nodegroup-name <NG> --region eu-west-1 --profile prod --query "nodegroup.{Status:status,Version:version,Release:releaseVersion}"
```

With a custom launch template that does not pin an AMI, the group follows `--ami-type` and the update picks the new release. If a template pins `ImageId`, create a new template version with the new AMI (look it up through SSM) and point the group at it.

```powershell
aws ssm get-parameter --name /aws/service/eks/optimized-ami/<K8S_VERSION>/amazon-linux-2023/x86_64/standard/recommended/image_id --region eu-west-1 --profile prod --query "Parameter.Value" --output text
aws ssm get-parameter --name /aws/service/eks/optimized-ami/<K8S_VERSION>/amazon-linux-2023/x86_64/nvidia/recommended/image_id --region eu-west-1 --profile prod --query "Parameter.Value" --output text
```

Self-managed groups (we have none) need new nodes, cordon, drain, decommission by hand. Fargate pods pick up the version on redeploy.

## AMI families

* AL2 EKS AMIs stopped being published on 2025-11-26. 1.32 is the last Kubernetes version with AL2 images. From 1.33 only AL2023 and Bottlerocket.
* `AL2_x86_64_GPU` became `AL2023_x86_64_NVIDIA` (and `AL2023_x86_64_NEURON` for Inferentia). The NVIDIA AMI has the driver and kernel modules. CUDA user space must be in the container image.
* Moving hosts AL2 to AL2023 did not require rebuilding container images on this platform (cgroup v2 and dnf were already in use). Test one job per major code env and rebuild only what fails.
* Official EKS AMIs are owned by `602401143452`.

## Without kubectl

We cannot run `kubectl get nodes` to watch a roll. Watch it through the node group status, the ASG activity history and the kube-scheduler log stream (`eks-scaling-investigation.md`).

## Open items

* Record the actual steps of the 1.33 to 1.35 upgrades and the add-on versions before and after. They were not written down.
* Confirm the EBS CSI driver add-on version (it was not in the add-on list we captured).
* The CloudWatch observability add-on has no IAM role (`infra/eks-gpu-nodegroups/RUNBOOK.md`).
