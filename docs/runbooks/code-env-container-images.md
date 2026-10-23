# Code env container images: recipes silently running locally, INIT state, rebuilding

Prod design node, some weeks after the 14.5.1 base image was in ECR. The reusable script is `scripts/code_envs/rebuild_container_images.py`. This page has the operational procedure and what we saw.

Script internals and API behaviour are in [reference/code-env-container-images.md](../reference/code-env-container-images.md).

## Symptom

A long-running Python recipe was slow. Its job log said:

```
Running a DSS Python recipe locally
```

with the Python binary under `/data/dataiku/dss_data/code-envs/python/<CODE_ENV>/bin/python`, a local cgroup `DSS/workloads/<user>/pythonRRecipes` (with its memory and CPU limits), impersonated user `dssuser_<...>`, job files in `/data/dataiku/dss_data/jobs/...`. The post-activity hook mentioned the EKS cluster only because the project's default container config pointed at it. Input and output datasets were on Snowflake.

On EKS you would instead see a container configuration, a pod, image pulls.

## How DSS decides

Recipe setting, then code env, then project default, then global default. A recipe that inherits all the way up can still end up local if its code env has no usable container image.

Checking every recipe in the project for an explicit override (`PrepareRecipeSettings` has no `get_raw`, so go through the definition):

```python
import dataiku
client = dataiku.api_client()
project = client.get_project("<PROJECT_KEY>")

for recipe in project.list_recipes():
    r = project.get_recipe(recipe["name"])
    raw = r.get_definition_and_payload()
    exec_env = raw.get_recipe().get("params", {}).get("executionParams", {})
    if exec_env:
        print(recipe["name"], exec_env)
```

Nothing printed, so no recipe overrode the default.

The code env definition:

```python
env = client.get_code_env("python", "<CODE_ENV>")
d = env.get_definition()
```

showed `containerConfs: ['eks-default', 'eks-gpu', 'eks-gpu-big']`, `allContainerConfs: False`, and `desc.dockerImageResources: 'INIT'`. `containerConfs` only says which configs the env is allowed to use. `INIT` looked like "image never built". The env is `DESIGN_MANAGED` with torch 2.8.0, other heavy ML packages and CUDA libraries, so images are large. Only this one env was to be rebuilt.

## The API on 14.5.1

`DSSCodeEnv` methods: `delete`, `get_definition`, `get_log`, `get_settings`, `get_version_for_project`, `list_logs`, `list_usages`, `set_definition`, `set_jupyter_support`, `update_images`, `update_packages`. There is no `get_status` and no `build_for_container`.

```
update_images(env_version=None, wait=True)
    Rebuild the docker image of the code env
    This call requires an API key with admin rights
    :return: dict ... anyMessage, success, warning, error, fatal, messages
```

`env.list_logs()` before the build: `createPythonEnv.log`, `updateEnvAccordingToSpec.log`, `Dockerfile.dump`. After: `rebuildImage.log`.

## What happened

1. `update_images(wait=True)` was interrupted by hand. The result was still a dict, but `messages` is itself a dict, so iterating over it as a list failed with `AttributeError: 'str' object has no attribute 'get'`:

   ```
   {'messages': {'messages': [], 'anyMessage': False, 'success': False, 'warning': False, 'error': False, 'fatal': False},
    'logFileName': 'rebuildImage.log',
    'imagesBuilt': ['dataiku-containerexec-base-cuda-dss-14.5.1-pyenv-<CODE_ENV>:r-<YYYY-MM-DD-HH-MM-SS-mmm>',
                    'dku-exec-base-<hash>-dss-14.5.1-pyenv-<CODE_ENV>:r-<YYYY-MM-DD-HH-MM-SS-mmm>',
                    'dataiku-containerexec-base-cuda-dss-14.2.1-pyenv-<CODE_ENV>:r-<YYYY-MM-DD-HH-MM-SS-mmm>']}
   ```

   Three images, one per container config. One of them is built on the 14.2.1 CUDA base: at least one container config still referenced the old base image tag. It was treated at the time as a leftover from before the upgrade, but it means that config was not updated.

2. `rebuildImage.log` showed all three built and pushed to ECR with digests. `dockerImageResources` was still `INIT` after re-fetching the definition.

3. A second full run with `wait=True` died when the laptop slept and the notebook connection dropped. Use `wait=False`, which returns a `DSSFuture` (`abort`, `get_result`, `get_state`, `has_result`, `job_id`, `peek_state`, `wait_for_result`, ...), and poll:

   ```python
   import time
   future = env.update_images(wait=False)
   while True:
       state = future.peek_state()
       print(state)
       if state.get("futureStatus") in ("DONE", "FAILED", "ABORTED"):
           break
       time.sleep(30)
   print(future.get_result())
   ```

   Progress seen: a wheel build from source, package install, pushes, the third image downloading CUDA libraries again, `#17 exporting layers`. About 32 minutes in total for three images. It ended with `hasResult: True`, `alive: False`.

4. Final check of `dockerImageResources` after the clean run: not recorded. The suggested check was `env.get_settings().get_raw()` and look for the field. Whether `INIT` means "never built" is therefore unconfirmed. The images were confirmed in ECR.

5. A day later, polling another rebuild's future after it had finished gave `DataikuException: java.lang.IllegalArgumentException: No future by that id`. The backend drops finished futures, and `alive: False` with `hasResult: True` from the last poll already meant success. Verify in the registry instead of re-polling.

## Procedure

1. Confirm the job runs locally (the `Running a DSS Python recipe locally` log line) and that no recipe override explains it.
2. Inspect the env definition: container configs and `dockerImageResources`.
3. Rebuild with `update_images(wait=False)` and poll, or use the script. One env at a time on the shared host.
4. Check `rebuildImage.log` and ECR for the new tags (`r-<timestamp>`), and check which base each image used. An image on an old base points at a container config still referencing the old tag.
5. Re-run the recipe and confirm the log no longer says "locally".

## Open items

* Find and fix the container config that still builds on `dss-14.2.1`.
* Confirm what `dockerImageResources` shows after a clean build.
