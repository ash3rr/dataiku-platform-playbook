# Workshop infrastructure

The platform hosted hands-on workshops: a large hands-on workshop with one project per attendee, and a planned short workshop on AI and LLM workflows for a few dozen participants. This page covers the compute side. Creating the per-attendee projects, users and permissions is in [`scripts/provisioning/workshop_projects.py`](../../scripts/provisioning/workshop_projects.py).

## Node group for the LLM workflow workshop

Request: a new EKS node group, separate from the existing ML node group (r6i.4xlarge) and the existing GPU node group (g5.2xlarge), for a workshop lasting a few days.

The workload as described: supply data to a model, then build an inference endpoint and predict answers based on that data. Whether a GPU is needed depends on one question: where does the model run?

| Where the model runs | What the node does | GPU |
|---|---|---|
| External LLM behind a DSS LLM connection (Azure OpenAI, Bedrock, OpenAI and so on) | Orchestrates API calls and visual pipelines | No |
| Self-hosted model loaded on the cluster (Hugging Face and similar) | Runs the model | Yes |

Dataiku does not build or host LLMs. It connects to them. On this platform the LLMs are behind Azure OpenAI connections, so the node group should not need a GPU. The answer changes the cost by roughly three to five times, so confirm with whoever runs the workshop before provisioning.

Options:

| Instance | vCPU / RAM | When |
|---|---|---|
| m5.4xlarge | 16 / 64 GB | Starting point for a few dozen users doing orchestration, RAG and embeddings via API |
| m5.8xlarge | 32 / 128 GB | Heavier concurrent load |
| g4dn.2xlarge | 8 / 32 GB, T4 16 GB | Only if a model must be loaded locally. Cheap GPU, widely available in eu-west-1 |
| g5.2xlarge | 8 / 32 GB, A10G 24 GB | Local fine-tuning or inference of 7B-class models, about $1.20/hr. A single node bottlenecks quickly with a room full of people |

The existing g5.2xlarge was mentioned only as context for what the platform already ran. At one point it was wrongly taken as the workshop choice, but it was never decided for the workshop. The direction at the end was m5.4xlarge, not confirmed. The node group provisioning commands were going to be tidied separately. The GPU node group runbook in [`infra/eks-gpu-nodegroups/`](../../infra/eks-gpu-nodegroups/) shows the pattern we use (launch template per node group, labels and taints as `create-nodegroup` flags).

For a burst of a few days: autoscale (for example minimum 1, maximum 3 to 4 for a GPU group) and make sure it scales down when idle. A g5 left running for days by accident adds up.

## The large hands-on workshop

### Spark by default

Prepare recipes in the workshop projects ran on Spark on Kubernetes by default, on small datasets (under 100 MB, under 200k rows), with dozens of users in parallel and autoscaling r6i.2xlarge Spark nodes. Each run took about 2 minutes 23 seconds, mostly Spark overhead. That default was pre-existing. For workshops with small data, set visual recipes to the DSS or in-database engine. See [spark-on-kubernetes.md](spark-on-kubernetes.md).

### The `workshop-workers` node group

* Status `ACTIVE`, with autoscaling minimum, desired and maximum sizes set.
* Scaled by hand to a larger desired size before a session:

  ```powershell
  aws eks update-nodegroup-config `
    --cluster-name <EKS_CLUSTER_NAME> `
    --nodegroup-name workshop-workers `
    --scaling-config desiredSize=<N> `
    --region eu-west-1 `
    --profile prod; `
  aws eks describe-nodegroup `
    --cluster-name <EKS_CLUSTER_NAME> `
    --nodegroup-name workshop-workers `
    --region eu-west-1 `
    --profile prod `
    --query 'nodegroup.{Desired:scalingConfig.desiredSize,Current:scalingConfig.currentSize,Ready:scalingConfig.desiredSize}' `
    --output table
  ```

  (The `Ready` column in that query repeats `desiredSize` and is not a ready count. Use `kubectl get nodes` or the ASG for that.) If the Cluster Autoscaler manages the group, a manual desired size can be changed back by it.
* A later scale-up at 10:02 UTC was traced in the cluster's CloudWatch log group to workshop participants running visual ML training jobs (`ANALYSIS_ML_TRAIN` activities in DSS) creating CPU pressure, which made the Cluster Autoscaler add a node. Not a manual change. How we read the logs is in [eks-scaling-investigation.md](../runbooks/eks-scaling-investigation.md).

Access note: our AWS access was an SSO assumed role (`AWSReservedSSO_<role>`), so `aws iam get-user` fails with `Must specify userName when calling with non-User credentials`. Use `aws sts get-caller-identity`. Adding another person to that SSO role is not done in the account (IAM Identity Center showed only an "Enable" page there because identity is managed upstream). It goes through whoever manages the organisation's identity provider.

### Per-user Kubernetes namespaces in the container profile

We tried a containerized execution config whose namespace was `dss-wk-${dssUserLogin}`, so each workshop user's pods would land in their own namespace. DSS logins on this instance are email addresses, and `@` and `.` are not valid in Kubernetes names (RFC 1123: lowercase alphanumerics and hyphens).

What we tried:

| Attempt | Result |
|---|---|
| `${dssUserLogin.replaceAll('[^a-zA-Z0-9]', '-')}` | `Unknown DSS variable: dssUserLogin.replaceAll('[^a-zA-Z0-9]', '-')`. No inline expressions |
| `dss-wk-${projectKey}` | `Configuration test failed: Unknown DSS variable: projectKey`. Not available in this admin-level field |
| `${dssInstanceId}` | suggested, not tested |

From the documentation: DSS variables support only these filters: `tolower`, `toupper`, `default`, `getornull`, `tojson`, `fromjson`, `formatdate`, `indent`. None replaces characters, so the email cannot be sanitised in the field.

Options left:

1. A static namespace for all workshop pods, for example `dss-wk-workshop` (simplest).
2. Short alphanumeric DSS logins instead of emails, so `${dssUserLogin}` is valid. A large change for an SSO instance.
3. A custom variable per user (admin variables, for example `"k8s_user": "<short id>"`) referenced as `${k8s_user}`. Does not scale.

Note that DSS's own Spark integration does sanitise logins into namespaces (`dss-spark-ns-<login with dashes>-<number>`), so the limitation is specific to the container profile field. Which option was chosen is not recorded. Per-project isolation was raised as a possible alternative to per-user.

## Checklist for the next workshop

* Ask whether any model runs locally. If not, use a CPU node group.
* Check engine defaults for visual recipes in the template project, and turn off Spark for small data.
* Pre-scale the workshop node group shortly before the session, and scale it back after.
* Decide the namespace approach before the day.
* Run the provisioning script well in advance and re-run it to confirm it is idempotent (it must re-apply permissions on existing projects, not skip them).
