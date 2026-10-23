# Compute placement and cost

Where Dataiku work runs on this platform, how to tell, and what it costs. The platform is a DSS design node and automation nodes on EC2 (Fleet Manager), containerized execution on one EKS cluster in eu-west-1, and Snowflake as the main store. Detailed write-ups live in `docs/platform/`. This page is the summary and the place for decisions that cut across them.

| Topic | Detail page |
|---|---|
| Spark on Kubernetes, Spark-by-default for visual recipes, Spark job failures | [spark-on-kubernetes.md](../platform/spark-on-kubernetes.md) |
| Billion-row processing on EKS instead of Snowflake, work queue design | [eks-vs-snowflake-compute.md](../platform/eks-vs-snowflake-compute.md) |
| LLM recipes, LLM connections, GPU or not | [llm-mesh-and-llm-workloads.md](../platform/llm-mesh-and-llm-workloads.md) |
| Code Studios and their image builds | [code-studios.md](../platform/code-studios.md) |
| Webapps (backends, timeouts, Code Studio webapps) | [webapps.md](../platform/webapps.md) |
| Workshop node groups and per-user namespaces | [workshop-infrastructure.md](../platform/workshop-infrastructure.md) |
| Temporary 64 vCPU / 150 GB simulation box | [simulation-infrastructure.md](../platform/simulation-infrastructure.md) |
| Recipe patterns that behave differently in containers | [recipe-patterns.md](../platform/recipe-patterns.md) |
| GPU node groups (instances, launch templates, scheduling) | [infra/eks-gpu-nodegroups/RUNBOOK.md](../../infra/eks-gpu-nodegroups/RUNBOOK.md) |

## Local versus containerized execution

DSS separates where a recipe runs from what it runs. Moving a project from local DSS compute to EKS needs no recipe code changes when the code is clean. It needs:

1. A container execution config chosen as the project default (Project settings > Code env & containerization) or per recipe (recipe Advanced tab, execution/runtime section). We already had many container configs (`eks-default`, `eks-gpu`, `eks-gpu-big`, `eks-highmemory` and others). The one project in question used local compute.
2. The project's code env built as a container image for that config, in addition to its normal local build (Administration > Code Envs > env > containerized execution, pick the configs, build). If someone adds a package to the env later, both the local env and the container images must be rebuilt. It is not automatic.

Things that break when a recipe moves into a container, all seen on this platform:

* Local paths. Anything that reads the DSS host filesystem, for example `folder.get_path()` on a managed folder. The path does not exist in the pod. Use the streaming API. See [`scripts/recipes/managed_folder_streaming.py`](../../scripts/recipes/managed_folder_streaming.py).
* Undeclared inputs and outputs. A containerized recipe only gets datasets that are declared in its I/O. DSS injects connection details for those at pod launch. Locally the same code often works because the process has broader access. Error text:

  ```
  Error in python process: At line 141: <class 'Exception'>: Dataset <dataset_name> cannot be used : declare it as input or output of your recipe
  ```

  We hit this twice in one project on the same day: once for an input, once for an output after a GPU embedding step had already processed its whole input. Fix: add the dataset to the recipe's Inputs/Outputs.
* Local system tools or network resources only reachable from the DSS host.

### Silent fallback to local

A recipe can run on the DSS host even when the project default says EKS. DSS resolves execution roughly as recipe setting, then code env, then project default, then global default. Two causes we found:

1. A recipe explicitly overridden to local.
2. The code env lists container configs but no image was ever built. Its definition then shows `dockerImageResources: INIT` and DSS quietly runs locally, no error.

How to tell from a job log that it ran locally: the log says `Running a DSS Python recipe locally`, the Python binary is `/data/dataiku/dss_data/code-envs/python/<env>/bin/python`, the cgroup is `/sys/fs/cgroup/DSS/workloads/<user>/pythonRRecipes`, job files are under `/data/dataiku/dss_data/jobs/...`, and the process runs as the impersonated unix user through `sudo` and the security wrapper. In one case the local cgroup limit was 90 GB memory and 2 CPUs. The post-activity hook still mentions the EKS cluster because the project default points at it, which is misleading. On EKS you instead see pod names (`dataiku-exec-python-<id>`), Kubernetes secrets created and deleted, and `executeKubernetesCodeRecipe` in stack traces.

Checking hundreds of recipes for explicit overrides (works across recipe types, whereas `get_raw()` does not exist on `PrepareRecipeSettings`):

```python
import dataiku

client = dataiku.api_client()
project = client.get_project("<PROJECT_KEY>")

for recipe in project.list_recipes():
    r = project.get_recipe(recipe["name"])
    settings = r.get_settings()
    raw = settings.recipe_settings if hasattr(settings, "recipe_settings") else {}
    exec_env = raw.get("params", {}).get("executionParams", {})
    if exec_env:
        print(recipe["name"], exec_env)
```

No output means nothing overrides and the code env is the next suspect:

```python
env = client.get_code_env("python", "<env_name>")
print(env.get_definition().get("desc", {}).get("dockerImageResources"))
```

`client.get_code_env("python", "<PROJECT_KEY>")` fails with `java.io.IOException: Not a file: /python/<PROJECT_KEY>/desc.json`, because the project key is not the env name. List envs with `client.list_code_envs()`.

Building the images without blocking the notebook is in [`scripts/code_envs/rebuild_container_images.py`](../../scripts/code_envs/rebuild_container_images.py). On one LLM project env (torch 2.8.0+cu126, CUDA libraries, sentence-transformers, bertopic) the build produced three images, one per container config, about 16 minutes each and about 32 minutes in total on the second run. An interrupted first run had pushed images to ECR but left the env at `INIT`.

### Package pinning

Rebuilds pull whatever the spec allows. On the forecasting env we audited, most packages had tight ranges (`lightgbm>=4.6,<4.7`, `scipy>=1.13,<1.14`), two were exact (`seaborn==0.13.2`, `matplotlib==3.9.4`), and several had no upper bound or no version at all (`openpyxl`, `streamlit`, `rapidfuzz>=3.13`, `linearmodels>=6.0`, `nbformat>=4.2.0`). Those are rebuild risks, `streamlit` especially. The same failure mode hit Code Studio templates after the 14.5.1 upgrade, where a newer setuptools without `pkg_resources` broke a build (see [code-studios.md](../platform/code-studios.md)).

### Model results that changed after the 14.2 to 14.5 upgrade

Users of a forecasting project reported that retraining the same model several times, with identical configuration, settings and inputs, gave different validation results from run to run, and one run failed outright. It started after the platform went from DSS 14.2 to 14.5. The model code and execution logic were reviewed on the data science side, and the platform side looked at what the upgrade could have changed underneath.

Code envs have to be rebuilt for a new DSS version, and that was the main hypothesis: the rebuild re-resolved every unpinned or loosely pinned dependency from PyPI and pulled newer versions, one of which changed the model's behaviour. Secondary candidates were parallelism and thread-count differences between pods (EKS execution), and the dependency graph resolving differently at rebuild time even where the top-level pins held.

How to test it:

1. Compare `pip freeze` (or the conda export) from job logs of a run before the upgrade with one after.
2. Check the env's spec for loose pins (the audit above found several in this project's env).
3. Check whether the env's "last built" timestamp lines up with when the results started to diverge.

The first platform-side reaction was that the input data had probably changed between runs. That was never checked against the data and was set aside once the rebuild came up. Other layers listed as able to inject non-determinism below unchanged code: a Python runtime change (hash randomisation, floating point handling), a different base image in the rebuilt containers, a change of instance type or CPU count on the node group (thread count changes the order of floating point operations in tree models), and memory pressure pushing numerical libraries down different code paths. Two things we did not know at the time and could not answer: whether LightGBM was the library involved, and whether the runs landed on the same node each time. The failed run was read as more likely a resource or environment problem than a code one.

The outcome was not recorded. If it comes back: pin the env fully (or freeze it to the pre-upgrade versions from an old job log), fix the model's random seeds and thread counts, and rebuild once.

### A training job that stopped without finishing

Around the same time, a job training several LightGBM models with early stopping showed a grey stopped icon (not the green tick) and a duration of `0s`. Read on its own, the log looked like a success. The icon was what showed it had not completed. The log stopped at 07:58:46 with CPU still near 100% and memory climbing steadily (about 35 MB a minute). The job ran in a container (the log had the kubectl calls), and its name ended in `_NP`. We wondered whether that meant a special run type or a manual kill. The same suffix appears on ordinary DSS job names elsewhere (for example `compute_platform_maintenance_license_trending_NP` in [licence-count-snapshot.md](../reference/licence-count-snapshot.md)), so it says nothing about how the job ended. A `0s` duration is what DSS tends to show when it cannot work out the real runtime, which fits an abnormal end. Earlier, at 07:55:10, LightGBM logged two `[Fatal]` warnings about `min_data_in_leaf` with `feature_pre_filter=true`. That is a parameter conflict worth fixing in the model settings, but it did not look like the reason the job stopped. Candidates were a manual abort, an OOM kill or timeout imposed by the cluster, or pod eviction under memory pressure. Not resolved. The next step was to look for log content after 07:58:46 and at the pod's termination reason.

## Choosing a container profile

We considered auditing every project's code env package list to infer the right profile (GPU, high memory, plain CPU). It is not reliable on its own:

* an env with `torch` does not mean the recipe uses a GPU,
* the profile is chosen per recipe, not per env, so recipes sharing an env can need different profiles,
* some recipes use the built-in env,
* package lists can include things nobody uses.

A better signal combines the package list (what could run), the recipe-level container config (what is configured) and job history (what ran, duration, failures). Parked for later.

## Does it need a GPU?

Usually not for LLM work on this platform. Dataiku orchestrates and the model runs behind an LLM connection (Azure OpenAI through private link in our case). A recipe that labels documents by calling the LLM through DSS is an HTTP client. The one we looked at made sequential POSTs to the DSS backend's `/llms/completions` endpoint, stayed at about 162 to 164 MB RSS for over two hours, and needed CPU and a little memory. Details in [llm-mesh-and-llm-workloads.md](../platform/llm-mesh-and-llm-workloads.md).

A GPU makes sense when the recipe itself runs a model: local embeddings with sentence-transformers, torch training, fine-tuning a local model. Our embedding step ran on a GPU profile.

For big multi-GPU work (the platform previously used p3, and g6 and g6e were not available in our zone), we compared options with at least 4 GPUs, at least 200 GB RAM and more than 32 vCPUs. Prices were approximate eu-west-1 on-demand figures found at the time and should be rechecked:

| Instance | GPUs | GPU | vCPU | RAM | NVLink | Approx. $/hr |
|---|---|---|---|---|---|---|
| p4d.24xlarge | 8 | A100 40 GB | 96 | 1152 GB | yes | ~22 |
| p5.48xlarge | 8 | H100 80 GB | 192 | 2048 GB | yes | ~55 |
| g5.12xlarge | 4 | A10G 24 GB | 48 | 192 GB | no | ~5.70 |
| g5.48xlarge | 8 | A10G 24 GB | 192 | 768 GB | no | ~16 |

g5.12xlarge falls just short of the 200 GB RAM requirement. NVLink matters only when one model is spread across GPUs (model parallelism). It does not hurt workloads that do not need it. As the platform team we did not know which the users needed, so we chose p4d.24xlarge for the big GPU group (NVLink, available in the region) and g5.4xlarge for smaller GPU work. An earlier estimate put p4d at about $32/hr. The ~$22 figure reflects the mid-2025 price cuts. The resulting node groups are in the GPU runbook.

## Visual recipes on Spark by default

Prepare recipes in a hands-on workshop project were running on Spark on Kubernetes by default, on small datasets (under 100 MB, under 200k rows). A job took about 2 minutes 23 seconds, most of it Spark overhead (executor pod scheduling, JAR distribution, Snowflake staging). The Spark node group was r6i.2xlarge (8 vCPU, 64 GB), autoscaling. With dozens of users each triggering executor pods, that is the expensive way to process sub-100 MB data. We did not set that default. It was pre-existing. Recommendation: default visual recipes to the in-database (SQL) or DSS engine and keep Spark for large data. Whether the default was changed is not recorded. Full notes in [spark-on-kubernetes.md](../platform/spark-on-kubernetes.md).

## EKS instead of Snowflake compute

A team pulled billion-row-per-unit data out of Snowflake into EKS pods because Snowflake warehouse time was the expensive part. They had already raised nodes to 128 GB RAM and 500 GB disk, up to 12 nodes, and parallel pods were slower than serial because they collided on reads and writes. The trade is valid, but it needs a cheap manifest query, a work queue so pods never claim the same unit, streaming through pods instead of materialising, staging to S3 as Parquet and bulk-loading with `COPY INTO`, and separate small warehouses for reads and writes. Native Dataiku partitioning by unit hit DSS's partition limit. Full design and the code sketch: [eks-vs-snowflake-compute.md](../platform/eks-vs-snowflake-compute.md).

## Partition build concurrency

Partitioned builds run one activity per partition, capped by DSS concurrency settings. A user hit the cap of 7 and asked for more. From the documentation:

* global instance-wide limit on concurrent activities, default 5 for self-managed installs (ours had been raised to 7), set in Administration > Settings > Resources control > Concurrent jobs and activities, admin only
* per-project and per-user limits in the same place, key/value syntax such as `project/<PROJECT_KEY>` and `user/<login>`
* per-recipe limit, recipe > Advanced tab, concurrent activities, settable by the user but bounded by the global limit.

Raising it only helps if the cluster has the capacity for that many activities at once. Whether the limit was raised is not recorded.

## Short-lived simulation infrastructure

For a temporary "simulate ML model production behaviour" request (at least 64 cores, 150 GB RAM, CPU only): one m7i.16xlarge (64 vCPU, 256 GB) as an EKS node, scale to zero, Karpenter suggested over Cluster Autoscaler. Fixed cost is the EKS control plane, about $73/month. The node costs about $3.80/hr on demand or roughly $1.50 to $1.60/hr spot, only while running. If production does not run on Kubernetes, a plain EC2 instance with Docker is simpler. Full notes and corrected monthly figures: [simulation-infrastructure.md](../platform/simulation-infrastructure.md).

## Node groups seen on the cluster

For orientation, the managed node groups present at one point were `api-workers`, `gpu-big-workers-v2`, `gpu-small-workers`, `nongpu-workers`, `standard-workers`, `system-nodes`, `<USECASE>-big-workers` and `workshop-workers`. The Spark group used r6i.2xlarge with a gp3 500 GB launch template on AMI release `1.33.8-20260304`. The general ML node group used r6i.4xlarge and the original GPU group g5.2xlarge. When the cluster had to be stopped, every group was set to `minSize=0,desiredSize=0` with `aws eks update-nodegroup-config`. Note each group's current `maxSize` first so it can be restored.

Code Studios need the cluster: with the cluster down they fail with `ERR_CODE_STUDIO_START ... The cluster <EKS_CLUSTER_NAME> used by this Code Studio isn't running`. Notebooks and recipes can still run locally on the DSS node if the project does not force containers.
