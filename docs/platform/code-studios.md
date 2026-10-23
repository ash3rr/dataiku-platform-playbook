# Code Studios

Code Studios are VS Code, JupyterLab or Streamlit environments that DSS runs as pods on the EKS cluster, built from Code Studio templates. A template is a stack of blocks (base image, deployment, VS Code, Streamlit, extra code envs and so on) that DSS turns into a container image. Code Studios can also be published as webapps ("Code Studio as webapp").

## Containers only

Code Studios cannot run on local DSS compute. With the EKS cluster down (while a cluster stack was stuck and had to be recreated) they failed with:

```
ERR_CODE_STUDIO_START
Failed to start Code Studio
Unable to start run : The cluster <EKS_CLUSTER_NAME> used by this Code Studio isn't running
```

Workarounds while the cluster is down: DSS notebooks and recipes on local compute (if the project does not force containers), or VS Code on a laptop using the Dataiku Python client.

## Where things live on the DSS host

| Path | What |
|---|---|
| `/data/dataiku/dss_data/config/projects/<PROJECT_KEY>/code_studios/<CODE_STUDIO_ID>.json` | Code Studio definition |
| `/data/dataiku/dss_data/config/projects/<PROJECT_KEY>/code_studios/<CODE_STUDIO_ID>/vscode-machine-settings/` | `coder.json`, `settings.json` |
| `/data/dataiku/dss_data/install-support/backends.d/<PROJECT_KEY>/<CODE_STUDIO_ID>-<port>.conf` | nginx location blocks DSS generates per exposed port (8080 VS Code, 8181 Streamlit in our case) |
| `/data/dataiku/dss_data/config/projects/<PROJECT_KEY>/lib/streamlit/.streamlit/config.toml` | Streamlit settings synced from the project library |
| `/data/dataiku/dss_data/config/projects/<PROJECT_KEY>/web_apps/<WEBAPP_ID>.json` | Webapp definitions, including Code Studio webapps |
| `/data/dataiku/dss_data/code_studios/exec/<PROJECT_KEY>/<run_id>/state.json` | Running state of a session, including who submitted it, the container config and limits |

An nginx backend block for a Streamlit port looks like this:

```
# expose Streamlit of Code Studio
location ^~ /code-studios/<PROJECT_KEY>/<CODE_STUDIO_ID>/8181/ {
  resolver 127.0.0.1;
  proxy_pass http://127.0.0.1:<local port>$uri$is_args$args;
  proxy_next_upstream off; # Don't retry
  proxy_read_timeout 3600; # We have long queries
  error_page 502 /code-studio-error-502.html;
}
```

`state.json` is where to look when you need to know who owns each active session, for example during a security investigation. It also shows the container config and memory limit each session runs with.

Inside the pod, synced zones are mounted under `/home/dataiku/workspace/`: `code_studio-versioned`, `code_studio-resources`, `project-lib-versioned`, `project-lib-resources`, `recipes`, `notebooks`, `user-versioned`, `user-resources`. The Code Studio and a webapp published from it are separate pods with separate file syncs. In the Code Studio UI, Logs > Container files shows the Code Studio pod. There is no equivalent browser for the webapp pod, which only has View, Edit, Logs and History tabs.

## Rebuilding templates after an upgrade

After the upgrade from 14.2.1 to 14.5.1, Code Studio webapps failed to start with:

```
IllegalStateException: The code studio template must be rebuilt
```

Every template has to be rebuilt after an upgrade. Templates that had never been built (test and abandoned ones) were skipped.

### API facts we confirmed

* `client.list_code_studio_templates()` returns `DSSCodeStudioTemplateListItem` objects, not dicts. `t["id"]` raises `TypeError: 'DSSCodeStudioTemplateListItem' object is not subscriptable`. Attributes: `build_for_configs`, `client`, `id`, `label`, `last_built`, `to_code_studio_template`.
* `client.get_code_studio_template(id).build()` returns a future. `build()` builds for every target the template is configured for (its container configs), and you do not choose.
* An image built for EKS is pushed to the registry (ECR), while a local Docker execution looks in the local daemon. They are separate. If users need both, the template must list both.
* `future.wait_for_result()` does **not** raise when the build fails. It returns a dict, so check `result["messages"]["success"]`. Our first bulk script printed "Done" for builds that had failed.
* `last_built` on the list item did **not** change after builds triggered through the API, even successful ones. Templates rebuilt through the UI did show a new date. The template settings (`get_settings().get_raw()`) have no build timestamp at all, and `versionTag.lastModifiedOn` is the last edit of the definition, not the image. Raw settings keys: `id`, `type`, `label`, `tags`, `versionTag`, `creationTag`, `isEditor`, `reattachOnRestart`, `owner`, `defaultPermission`, `permissions`, `defaultContainerConf`, `allowContainerConfOverride`, `allContainerConfs`, `containerConfs`, `inheritGlobalCodeEnvsExtraSettings`, `codeEnvsExtraSettings`, `params`. So `last_built` cannot be used to make a rerun skip already-rebuilt templates. We treated it as a probable DSS bug worth a support ticket. We did not hear back on it.
* The build result carries `futureLog.lines` (the Docker build log). Keep the last 30 lines for the report.
* Builds need an admin (or an API key with admin rights). `build()` also accepts `disable_docker_cache=True` for a `--no-cache` rebuild.

### What went wrong on the way

1. First pass compared `last_built` to the upgrade date: most looked current, and only the remainder were rebuilt. All of those printed "Done". Users still reported broken templates, because the script never checked `success`.
2. Rebuilding one of them and printing the result showed the real failure:

   ```
   ERR_CODE_STUDIO_CONTAINER_IMAGE_BUILD_FAILED
   Failed to build images for <template> on docker host: localhost : Command failed: 'docker' (exit code: 1)
   ...
   RUN source $CODE_ENV_PATH/bin/activate && mim install mmcv-full==1.5.0 && pip install -e git+https://github.com/Sense-X/Co-DETR.git@<sha>#egg=mmdet
   ModuleNotFoundError: No module named 'pkg_resources'
   ERROR: Failed to build 'mmcv-full' when getting requirements to build wheel
   ```

   The template's Dockerfile fragment built `mmcv-full` from source, and the newer setuptools in the rebuilt image no longer ships `pkg_resources` (the warning in the log says it is slated for removal and suggests pinning `setuptools<81`). That is the template owner's dependency to fix.
3. We then started builds for every previously built template in parallel from one notebook cell. Output did not appear while it ran (Jupyter buffers `print` until the cell ends, fixed with `print = functools.partial(print, flush=True)` at the top). A second cell queued behind the first for over ten minutes because a DSS notebook kernel runs one cell at a time. Restarting the kernel killed the collection loop but not the server-side builds. Results included `InterruptedException` from the restart and `Command failed: 'docker' (exit code: 143)` (SIGTERM), with all those builds competing for the single local Docker daemon.
4. Shortly after, users got 502s and the system nginx error log (`/var/log/nginx/error.log`, owned `nginx:root`, mode 640, so read it with `sudo`) was full of `connect() failed (111: Connection refused) while connecting to upstream: http://[::1]:10000/`. `dss status` showed backend, ipython and nginx all RUNNING with 5 days uptime. The backend listened on `0.0.0.0:10000` only (`ss -tlnp | grep 10000`), `getent hosts localhost` returned `::1`, `curl http://127.0.0.1:10000/` gave 200 and `curl http://[::1]:10000/` gave 000. The system reverse proxy snippet `/etc/nginx/conf.d/dataiku-reverse-proxy.snippet` had `proxy_pass http://localhost:10000;`, which we changed to `127.0.0.1:10000` and reloaded (`nginx -t` passed). Immediately afterwards `curl http://127.0.0.1/` on the host still returned 000 and the investigation moved on to what the system nginx was listening on. That thread is host-level and belongs with the host runbooks. Whether the parallel builds contributed was never established. The image-push side of an overloaded Docker daemon is handled in [`infra/ecr/build_and_push_base_image.sh`](../../infra/ecr/build_and_push_base_image.sh).

### Final script: batched rebuild with a report

Run in a DSS notebook as an admin. Builds in batches of 5 so the Docker daemon is not swamped, records owner, last editor and the error for each template so owners can be contacted, and shows a table with failures first. The notebook could not write files to a path we could collect, so it displays a DataFrame. Export it from the notebook UI.

```python
import functools
print = functools.partial(print, flush=True)

import dataiku
import pandas as pd

client = dataiku.api_client()
templates = client.list_code_studio_templates()

BATCH_SIZE = 5

to_build = []
for t in templates:
    if t.last_built is None:
        print(f"Skipping (never built): {t.label}")
        continue
    handle = client.get_code_studio_template(t.id)
    settings = handle.get_settings().get_raw()
    to_build.append({
        "label": t.label,
        "id": t.id,
        "last_built": str(t.last_built),
        "owner": settings.get("owner", "unknown"),
        "last_editor": settings.get("versionTag", {}).get("lastModifiedBy", {}).get("login", "unknown"),
        "last_edited_on": settings.get("versionTag", {}).get("lastModifiedOn", ""),
        "container_confs": ", ".join(settings.get("containerConfs", [])),
    })

print(f"\n{len(to_build)} templates to build in batches of {BATCH_SIZE}\n")

results = []
for i in range(0, len(to_build), BATCH_SIZE):
    batch = to_build[i:i + BATCH_SIZE]
    print(f"--- Batch {(i // BATCH_SIZE) + 1} ---")

    futures = []
    for b in batch:
        print(f"  Starting: {b['label']}")
        futures.append((b, client.get_code_studio_template(b["id"]).build()))

    for b, future in futures:
        try:
            result = future.wait_for_result()
            success = result.get("messages", {}).get("success", False)
            msgs = result.get("messages", {}).get("messages", [])
            log_lines = result.get("futureLog", {}).get("lines", [])
            b["build_log_tail"] = "\n".join(log_lines[-30:])
            if success:
                b["status"], b["error_code"], b["error_detail"] = "OK", "", ""
                print(f"  OK: {b['label']}")
            else:
                b["status"] = "FAILED"
                b["error_code"] = msgs[0].get("code", "") if msgs else ""
                b["error_detail"] = msgs[0].get("details", "") if msgs else ""
                print(f"  FAILED: {b['label']} - {b['error_detail'][:120]}")
        except Exception as e:
            b["status"], b["error_code"], b["error_detail"] = "EXCEPTION", type(e).__name__, str(e)
            b["build_log_tail"] = ""
            print(f"  EXCEPTION: {b['label']} - {e}")
        results.append(b)

df = pd.DataFrame(results)[[
    "label", "id", "status", "owner", "last_editor", "last_edited_on",
    "last_built", "container_confs", "error_code", "error_detail", "build_log_tail",
]]
print("\n=== SUMMARY ===")
print(f"Total: {len(df)}  OK: {(df['status'] == 'OK').sum()}  Failed: {(df['status'] != 'OK').sum()}")
df.sort_values("status", ascending=False)
```

Wait for any builds still running from an earlier attempt before starting this one. The final counts of OK and failed templates from the batched run were not recorded here. Each template's last editor (`versionTag.lastModifiedBy`) is the person to send the failure to.

To check where a template builds (local Docker on the DSS host, EKS, or both), look at its container configs in the settings. They decide whether the build hammers the DSS host's Docker daemon or not. In our case the failing builds ran on "docker host: localhost", which is the design node.

## Streamlit Code Studio webapps after the upgrade

### Symptom

A Streamlit webapp published from a Code Studio returned 404 on its View tab (inside DSS, not via a public URL) after the template was rebuilt. The Code Studio itself was running.

### What we looked at

* The Code Studio pod's `code_studio-versioned` folder held only VS Code settings, and `code_studio-resources` and `project-lib-versioned` were empty in that pod. "Sync files with DSS" reported nothing changed. The Streamlit entrypoint in the Code Studio showed zero CPU time: Streamlit was started with nothing to serve. The app code lived in the project library and was used by the published webapp pod, not the Code Studio pod.
* The published webapp's own log showed Streamlit running (`You can now view your Streamlit app in your browser`, on `127.0.0.1:8181`).
* `config.toml` from the project library was the default, with the `[server]` and `[browser]` sections marked "configured by Dataiku for Code Studio, do not override". No custom `baseUrlPath`, because DSS manages the path.
* Two webapps in the project used the same Code Studio and template, both exposing Streamlit on 8181 with port-forward exposition on `eks-highmemory`. One worked, one did not. Both logs had `nginx: [alert] could not open error log file: open() "/var/log/nginx/error.log" failed (13: Permission denied)` from the nginx inside the pod. In the failing one, the exposition log showed repeated connection refused on port 8182 inside the pod before eventually connecting. One reading was that 8182 is a dynamically allocated conflict port. Another was that 8182 is the nginx layer in front of Streamlit (port-forward to 8182, nginx to 8181). Neither was confirmed. "One of them got unlucky with nginx startup" was put forward as an explanation and rejected because it explained nothing.
* Dataiku support first described a known 14.5.1 bug with no fix and suggested a workaround: set the "Exposed location" field in the template's nginx block to something other than `/`, such as `/?foo=bar`. This worked for one project but would mean editing the nginx block in every affected template. The built-in Streamlit block (`pycdstdioblk_code-studio-blocks_streamlit`) has no exposed location field at all, because DSS hardcodes `/`. Applying the workaround would mean replacing the built-in block with a custom entrypoint block plus an nginx block where the exposed location can be set. We chose that route and then stopped, because of the next point.

### Root cause

Dataiku support came back with the root cause for this case: duplicate vanity URL configurations. Two webapps were mapped to the same vanity slug, so DSS could only route one of them, and the duplicates broke webapp routing more widely after the upgrade. Removing the duplicate fixed it, and the exposed-location workaround was not needed here. The daily check that now catches duplicate slugs is in [`scripts/monitoring/vanity_url_audit/`](../../scripts/monitoring/vanity_url_audit/), and the API details for vanity URLs are in [python-api-field-notes.md](../reference/python-api-field-notes.md).

Lesson: when one of two identical webapps fails, compare everything outside the webapp definition too, starting with instance-level routing such as vanity URLs.

## Template options seen

From the VS Code block: Install AI Code Assistant (needs an OpenAI connection), Install GitHub Copilot (experimental), settings, extensions and machine-level settings folders (with shortcuts such as `__USER_VERSIONED__`, `__CODE_STUDIO_RESOURCES__`, `__CODE_STUDIO_VERSIONED__`), starting folder, port, start script location, code-server version (minimum 4.92.2, default 4.106.3 on 14.5.1), Python extension default 2025.4.0, Jupyter extension default 2025.7.0, additional extensions (must be on open-vsx.org), ignore TLS errors. From the Streamlit block: launch for webapps, code env mode and code env, starting file, settings folder for `.streamlit/config.toml`, port, start script location, enable XSRF. Code envs added to a template at build time appear under `/opt/dataiku/python-code-envs/<env>`. The deployment block defaults included `useTcpReadiness: true` and `initialSyncTimeoutInMinutes: 10`.
