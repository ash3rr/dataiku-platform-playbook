# -*- coding: utf-8 -*-
"""
Build one code env's container images without blocking the notebook, and
poll until done.

Why: a code env can list container configs (eks-default, eks-gpu, ...) and
still never have had an image built. Its definition then shows
desc.dockerImageResources == 'INIT' and DSS silently runs the recipes on
local DSS compute ("Running a DSS Python recipe locally" in the job log)
even when the project default is an EKS container config. No error.

Confirmed behaviour (DSS 14.5.1):
* DSSCodeEnv methods: delete, get_definition, get_log, get_settings,
  get_version_for_project, list_logs, list_usages, set_definition,
  set_jupyter_support, update_images, update_packages. There is no
  get_status() and no build_for_containers().
* update_images(env_version=None, wait=True) builds one image per container
  config the env is scoped to (containerConfs, allContainerConfs False).
  One env scoped to three configs produced three images, about 10 to 16
  minutes each with torch/CUDA (about 32 minutes total).
* wait=True blocks the cell. When the laptop screen slept the update never
  came back. wait=False returns a dataikuapi.dss.future.DSSFuture with
  abort, get_result, get_result_wait_if_needed, get_state, has_result,
  job_id, peek_state, state, state_is_peek, wait_for_result.
* peek_state() does not block and returns a dict (alive, hasResult,
  runningTime in ms, log progress). Once finished: alive False, hasResult
  True. Polling after that can raise
      DataikuException: java.lang.IllegalArgumentException: No future by that id
  because the server already dropped the future. That is completion, not
  failure.
* A loop written as `if future.get_state() in ['DONE', 'FAILED', 'ABORTED']`
  never exits: get_state() returns a dict, not a status string. It ran until
  the "No future by that id" exception.
* The result dict looks like
      {'messages': {'messages': [], 'anyMessage': False, 'success': False,
                    'warning': False, 'error': False, 'fatal': False},
       'logFileName': 'rebuildImage.log',
       'imagesBuilt': ['<image>:r-<timestamp>', ...]}
  Note success False with no messages even when all images were pushed.
* After an interrupted wait=True run AND after a completed one, the
  definition still showed INIT when we checked. Whether it changed after the
  third (async) run was never checked. Check it, and
  check that the next recipe run goes to EKS.
* The build log is env.get_log("rebuildImage.log"), and env.list_logs() also
  showed createPythonEnv.log, updateEnvAccordingToSpec.log, Dockerfile.dump.

History: docs/reference/code-env-container-images.md
"""
import time

import dataiku

ENV_LANG = "python"
ENV_NAME = "<CODE_ENV_NAME>"
POLL_SECONDS = 30

client = dataiku.api_client()
env = client.get_code_env(ENV_LANG, ENV_NAME)


def image_status():
    return env.get_definition().get("desc", {}).get("dockerImageResources")


defn = env.get_definition()
print("Container confs :", defn.get("desc", {}).get("containerConfs"),
      "| all confs:", defn.get("desc", {}).get("allContainerConfs"))
print("Image status    :", image_status())   # INIT = never built

# Kick it off async
future = env.update_images(wait=False)
print(type(future))

while True:
    try:
        state = future.peek_state()
    except Exception as e:
        # "No future by that id" after completion
        print("Future no longer available (usually means it finished):", e)
        break
    print(state)
    if state.get("futureStatus") in ("DONE", "FAILED", "ABORTED"):
        break
    if state.get("alive") is False:
        break
    time.sleep(POLL_SECONDS)

try:
    result = future.get_result()
    print(result)
    for img in result.get("imagesBuilt", []):
        print("  built:", img)
except Exception as e:
    print("Could not read result:", e)

# Re-fetch the definition. Do not trust an object fetched before the build.
env = client.get_code_env(ENV_LANG, ENV_NAME)
print("Image status now:", image_status())
# print(env.get_log("rebuildImage.log"))
