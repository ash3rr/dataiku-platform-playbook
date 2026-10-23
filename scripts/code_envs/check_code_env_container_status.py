# -*- coding: utf-8 -*-
"""
Why is this recipe running locally when the project says EKS?

Run cell by cell in a DSS notebook. This is the sequence that found the
answer for two projects: the recipes inherited the project's
EKS container config, but the code env's container image had never been
built (dockerImageResources == 'INIT'), so DSS fell back to local compute
without an error.

Resolution order DSS uses: recipe setting -> code env -> project default
-> global default.
"""
import dataiku

client = dataiku.api_client()
ENV_NAME = "<CODE_ENV_NAME>"

# 1. Is the env name right? (lists every code env on the instance)
for e in client.list_code_envs():
    print(e['envName'], e['envLang'])

# 2. Full definition. Look at desc.containerConfs, desc.allContainerConfs,
#    desc.dockerImageResources, specPackageList, actualPackageList.
#    get_definition()['status'] raises KeyError: there is no 'status' key.
env = client.get_code_env("python", ENV_NAME)
defn = env.get_definition()
print(defn)
print("containerConfs      :", defn.get('desc', {}).get('containerConfs'))
print("allContainerConfs   :", defn.get('desc', {}).get('allContainerConfs'))
print("dockerImageResources:", defn.get('desc', {}).get('dockerImageResources'))   # INIT = never built

# 3. Package pinning. Unpinned packages (no version, or lower bound only)
#    are what break the next image rebuild. Example review on one env:
#    exact pins seaborn==0.13.2 and matplotlib==3.9.4. Tight ranges like
#    lightgbm>=4.6,<4.7 and scipy>=1.13,<1.14. Risky: openpyxl and streamlit
#    with no version, rapidfuzz>=3.13, linearmodels>=6.0, nbformat>=4.2.0.
print(defn.get('specPackageList', ''))

# 4. What can the env object do? (no get_status, no build_for_containers)
print([m for m in dir(env) if not m.startswith('_')])
help(env.update_images)

# 5. Logs. list_logs() returns dicts (name, totalSize, lastModified,
#    linesShown, tailSize, truncated). A Dockerfile.dump with no image built
#    means a Dockerfile was generated at some point but the build never
#    completed.
print(env.list_logs())
# print(env.get_log("Dockerfile.dump"))
# print(env.get_log("rebuildImage.log"))

# 6. Which projects use it (method exists on 14.5.1, output not recorded)
# print(env.list_usages())
