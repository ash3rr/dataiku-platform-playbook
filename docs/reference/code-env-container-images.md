# Code env container images

The incident on the prod design node and the operational procedure are in [runbooks/code-env-container-images.md](../runbooks/code-env-container-images.md). This page covers the scripts and the API behaviour behind them.

Scripts in [`scripts/code_envs/`](../../scripts/code_envs/):

| File | Use |
|---|---|
| `find_recipe_execution_overrides.py` | List recipes in a project that override the container config |
| `check_code_env_container_status.py` | Show a code env's container configs, `dockerImageResources`, logs and package pins |
| `rebuild_container_images.py` | Build a code env's container images with `update_images(wait=False)` and poll the future without blocking |

## Symptom

Jobs in one project ran for hours. The project's settings said containerised execution on EKS (`<EKS_CLUSTER_NAME>` showed in the post-activity hook), but the job log said:

```
Running a DSS Python recipe locally
```

with a local code env binary under `$DIP_HOME/code-envs/python/<CODE_ENV_NAME>/bin/python`, a local cgroup `DSS/workloads/<user>/pythonRRecipes` (90 GB memory, 2 CPU limit), job files under `$DIP_HOME/jobs/...`, and the process running as the impersonated `dssuser_...` account. Data was read from and written to Snowflake over PrivateLink in eu-west-1. Only the compute was local. One recipe was making a long series of sequential LLM calls on that single local process.

## Cause

DSS resolves the execution context roughly as recipe setting, then code env, then project default, then global default. We checked each level:

1. Recipe overrides. Hundreds of recipes, so via the API. `recipe.get_settings().get_raw()` failed (`'PrepareRecipeSettings' object has no attribute 'get_raw'`, the settings class depends on the recipe type). `settings.recipe_settings` was proposed as a fallback. `recipe.get_definition_and_payload().get_recipe()["params"]["executionParams"]` works for every type. It printed nothing: no recipe overrides the default. (No positive control was run, so it is not proven that `executionParams` is where an override would appear.)
2. Code env. `client.get_code_env("python", name).get_definition()` showed `desc.containerConfs: ['eks-default', 'eks-gpu', 'eks-gpu-big']`, `allContainerConfs: False`, so it is allowed to run on those three configs, and `desc.dockerImageResources: 'INIT'`.

`INIT` means the container image was never built. Listing container configs only says the env *may* use them. With no image, DSS silently runs the recipe locally, whatever the project says. The env logs had a `Dockerfile.dump` (a Dockerfile had been generated once) but no image build.

The env was large (torch 2.8.0 with CUDA 12.6, sentence-transformers, bertopic, hdbscan), shared with other projects, and owned by someone outside the platform team. Worth telling the owner before a build.

## What we tried, in order

| Call | Result |
|---|---|
| `env.build_for_container()` / `build_for_containers()` | Do not exist (`AttributeError: 'DSSCodeEnv' object has no attribute 'build_for_containers'`) |
| `env.get_status()` | Does not exist |
| `env.get_definition()["status"]` | `KeyError: 'status'` |
| `print([m for m in dir(env) if not m.startswith('_')])` | `client, delete, env_lang, env_name, get_definition, get_log, get_settings, get_version_for_project, list_logs, list_usages, set_definition, set_jupyter_support, update_images, update_packages` |
| `help(env.update_images)` | `update_images(env_version=None, wait=True)`: "Rebuild the docker image of the code env", needs admin, `wait=False` returns a future |
| `env.list_logs()` | `createPythonEnv.log`, `updateEnvAccordingToSpec.log`, `Dockerfile.dump` (dicts with `name`, `totalSize`, `lastModified`, `linesShown`, `tailSize`, `truncated`) |
| `env.get_log("image-build")` | Wrong name (guessed). The real one after a build is `rebuildImage.log`. |

`update_images()` builds one image per container config the env is scoped to, and the call takes no conf argument. It is per env, not instance-wide (the concern was not to trigger every env on the instance).

### First build (blocking)

`env.update_images(wait=True)` with the result printed afterwards. The session slept and the call was interrupted. The partial printout code then failed (`AttributeError: 'str' object has no attribute 'get'`): the return is not the documented flat message dict. What it returned:

```python
{'messages': {'messages': [], 'anyMessage': False, 'success': False, 'warning': False,
              'error': False, 'fatal': False},
 'logFileName': 'rebuildImage.log',
 'imagesBuilt': ['dataiku-containerexec-base-cuda-dss-14.5.1-pyenv-<CODE_ENV_NAME>:r-<timestamp>',
                 'dku-exec-base-<hash>-dss-14.5.1-pyenv-<CODE_ENV_NAME>:r-<timestamp>',
                 'dataiku-containerexec-base-cuda-dss-14.2.1-pyenv-<CODE_ENV_NAME>:r-<timestamp>']}
```

So the messages are nested one level down, and `success` is False with no messages even though all three images were built and pushed (the `rebuildImage.log` ended with the three digests). One image name still says 14.2.1: that container config's base image tag predates the upgrade, which is harmless.

`dockerImageResources` still read `INIT` after re-fetching the env. The explanation at the time was that the interrupted call never let DSS record the result, so the build was run again.

### Second build (non-blocking)

```python
future = env.update_images(wait=False)   # dataikuapi.dss.future.DSSFuture
```

Future methods: `abort`, `client`, `from_resp`, `get_result`, `get_result_wait_if_needed`, `get_state`, `has_result`, `job_id`, `peek_state`, `result_wrapper`, `state`, `state_is_peek`, `wait_for_result`.

Polled with `peek_state()` every 30 seconds in a separate cell (break when `state.get('futureStatus')` is DONE, FAILED or ABORTED, then `future.get_result()`). `peek_state()` returns a dict with `alive`, `hasResult`, `runningTime` (ms) and the log tail. Progress was followed in the log lines (hdbscan wheel, package install, layer export, `Pushed`, digests). Finished state: `alive: False, hasResult: True`. Three images, about 32 minutes in total (roughly 10 to 16 minutes per image), pushed to ECR in `<AWS_ACCOUNT_ID>`.

Then `env.get_settings().get_raw()` / `get_definition()["desc"]["dockerImageResources"]` was checked again. **The final value was not recorded.** (A snippet using `dataikuapi.DSSClient(url, key)` for that check was rejected, because the notebook uses the internal client.)

### Second project

Another project had the same problem shortly after: its env had `containerConfs: ['eks-gpu', 'eks-highmemory']`, a built local env (`actualPackageList` populated) and `dockerImageResources: 'INIT'`. The polling loop first used here was:

```python
while True:
    state = future.get_state()
    print(state)
    if state in ['DONE', 'FAILED', 'ABORTED']:
        break
    time.sleep(10)
```

`get_state()` returns a dict, so that comparison is never true. The loop kept going after the build had finished, until the server had dropped the future and the call raised:

```
DataikuException: java.lang.IllegalArgumentException: No future by that id
```

The response just before showed `'alive': False, 'hasResult': True`: the build had succeeded (two images, built and pushed). The exception means "finished and garbage-collected", not failure. The repo script polls `peek_state()`, stops on `alive == False` or a terminal `futureStatus`, and catches that exception.

Package pins in that env (`specPackageList`): `seaborn==0.13.2`, `matplotlib==3.9.4`, most others ranged (`lightgbm>=4.6,<4.7`, `scipy>=1.13,<1.14`), and loose ones that are a rebuild risk: `openpyxl` and `streamlit` unpinned, `rapidfuzz>=3.13`, `linearmodels>=6.0`, `nbformat>=4.2.0` with no upper bound. Pinning them before the next rebuild was recommended but is not recorded as done.

## Containerised recipes and undeclared datasets

Once the first project's recipes ran on EKS, two failed although the code was unchanged: a recipe read or wrote a dataset that was not declared in its Inputs/Outputs (one wrote an embeddings dataset at the end of its GPU embedding step). Locally this works because the process can reach any dataset, but in a container only declared datasets are available. Fix: add the dataset to the recipe's Inputs/Outputs and rerun.

## Moving a project from local to containers

From an earlier question: no recipe code changes are needed. Set the project default (Project settings > Code env & containerization) or override per recipe (Advanced), and make sure every code env the project uses is built for the container configs. Adding a package to a code env means rebuilding both the local env and the container images. It is not automatic. Code that depends on local paths, host tools or network routes only the DSS host has will break in a container.

## Not done

* Inferring the right container profile (GPU, high memory, CPU) for every project from its code env's packages. Judged unreliable on its own: an env can contain torch without using a GPU, profiles are set per recipe, some recipes use the built-in env, and packages can be unused. It would need recipe-level container config and job history as well. Parked.
* Confirming `dockerImageResources` changed from `INIT` after a completed build (above).
