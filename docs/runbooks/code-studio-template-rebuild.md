# Rebuilding Code Studio templates after an upgrade

Prod design node, after the move to 14.5.1. Some of the templates had never been built.

Script internals and how the rebuild script evolved are in [reference/code-studio-template-rebuild.md](../reference/code-studio-template-rebuild.md).

## Symptom that started it

A Streamlit webapp published from a Code Studio failed to start:

```
IllegalStateException: The code studio template must be rebuilt
```

After a DSS upgrade every template image is built on the old base and has to be rebuilt. Some templates were rebuilt by hand in the UI on upgrade day, and we wanted the rest done by API.

## API facts (14.5.1)

* `client.list_code_studio_templates()` returns `DSSCodeStudioTemplateListItem` objects, not dicts. `t["id"]` fails with `TypeError: 'DSSCodeStudioTemplateListItem' object is not subscriptable`. Attributes: `id`, `label`, `last_built`, `build_for_configs`, `client`, `to_code_studio_template`.
* `client.get_code_studio_template(id).build()` starts a build and returns a future immediately. `build(disable_docker_cache=True)` forces `--no-cache`.
* Builds run for whatever targets the template's container configs say (local docker daemon, EKS via ECR, or both). An image built for EKS lives in ECR. Local execution uses the local docker cache. You do not choose at build time.
* Needs an admin API key (a personal key of an admin, or a key with an admin user to impersonate). In a DSS notebook `dataiku.api_client()` is enough.
* Template settings (`handle.get_settings().get_raw()`) keys: `id`, `type`, `label`, `tags`, `versionTag`, `creationTag`, `isEditor`, `reattachOnRestart`, `owner`, `defaultPermission`, `permissions`, `defaultContainerConf`, `allowContainerConfOverride`, `allContainerConfs`, `containerConfs`, `inheritGlobalCodeEnvsExtraSettings`, `codeEnvsExtraSettings`, `params`. `versionTag.lastModifiedOn` / `lastModifiedBy` is the last definition edit, not the last build. There is no build timestamp in settings.
* On disk, template definitions are in `config/code_studio_templates/<template>.json`. Code Studio instances are in `config/projects/<PROJECT_KEY>/code_studios/<id>.json` and only reference `templateId`. Build contexts go under `tmp/code-studio-build/<template>/build-<timestamp>/docker-build/` (often cleaned up).

## What went wrong, in order

1. Sequential loop. Worked but slow, one `wait_for_result()` at a time.
2. Skip by date. Listing `last_built` showed many templates built years earlier and some with `None`. A filter of "skip `None`, skip `last_built >= <UPGRADE_DATE>`, build the rest in parallel" skipped the never-built ones and those already rebuilt through the UI, and built the remainder. All of them printed "Done".
3. `last_built` did not move. Re-running the date check right after showed the same templates with their old dates. Neither the list item nor the settings expose a build time that updates after an API-triggered build. Comparing a UI-rebuilt template with an API-rebuilt one showed identical structure. Looks like a DSS bug. Not raised yet. A hard-coded skip list was suggested and rejected: if `last_built` cannot be trusted, nothing can be automated on it.
4. "Done" did not mean success. Users still reported broken templates. Calling `build()` on one and printing the result:

   ```
   {'messages': {'messages': [{'severity': 'ERROR', 'isFatal': False,
      'code': 'ERR_CODE_STUDIO_CONTAINER_IMAGE_BUILD_FAILED',
      'title': 'Failed to build Code Studio image',
      'details': "Failed to build images for <template> on docker host: localhost : Command failed: 'docker' (exit code: 1)", ...}],
    'maxSeverity': 'ERROR', 'success': False, 'error': True, ...},
    'builds': [], 'futureLog': {'totalLines': 803, 'lines': [...]}}
   ```

   `wait_for_result()` does **not** raise when a build fails. The earlier loops caught exceptions only, so every failure printed "Done". The templates rebuilt through the UI may have failed silently too.

   The log tail for that template:

   ```
   RUN source $CODE_ENV_PATH/bin/activate && mim install mmcv-full==1.5.0 && pip install -e git+https://github.com/...
   UserWarning: pkg_resources is deprecated as an API ... slated for removal as early as 2025-11-30. Refrain from using this package or pin to Setuptools<81.
   ModuleNotFoundError: No module named 'pkg_resources'
   ERROR: Failed to build 'mmcv-full' when getting requirements to build wheel
   ```

   The new base (AlmaLinux 9.7 packages) builds with a setuptools that no longer ships `pkg_resources`. Packages whose setup scripts import it fail. The template owner has to fix it (pin `setuptools<81` in the build step or move to a maintained package).

5. All at once. The next script kicked off every buildable template in parallel. On top of the code studio pushes this host had already shown it cannot take (`ecr-base-image-push.md`), that was too much for one docker daemon. A kernel restart mid-run produced `InterruptedException` for the build in flight and `Command failed: 'docker' (exit code: 143)` (SIGTERM) on another. Builds keep running server-side after the notebook kernel dies, but the futures are lost.
6. No progress output. Jupyter buffered all `print` output until the cell finished. Fix: `print = functools.partial(print, flush=True)` at the top. A new cell queues behind a running one, because DSS notebooks run one cell at a time per kernel.
7. Stopped. After the batched version was written, we decided not to run a full rebuild at all: it is too heavy for the shared design node in working hours. Troubleshoot the templates users actually report first.

## The batched audit and rebuild (final form, not run in full)

Five at a time, records owner and last editor so failures can be sent to the right person, shows a DataFrame (notebooks here cannot write CSVs to a useful place).

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
            b["build_log_tail"] = "\n".join(result.get("futureLog", {}).get("lines", [])[-30:])
            if success:
                b["status"], b["error_code"], b["error_detail"] = "OK", "", ""
                print(f"  OK: {b['label']}")
            else:
                b["status"] = "FAILED"
                b["error_code"] = msgs[0].get("code", "") if msgs else ""
                b["error_detail"] = msgs[0].get("details", "") if msgs else ""
                print(f"  FAILED: {b['label']} - {b['error_detail'][:120]}")
        except Exception as e:
            b["status"], b["error_code"], b["error_detail"], b["build_log_tail"] = "EXCEPTION", type(e).__name__, str(e), ""
            print(f"  EXCEPTION: {b['label']} - {e}")
        results.append(b)

df = pd.DataFrame(results)[["label", "id", "status", "owner", "last_editor", "last_edited_on",
                            "last_built", "container_confs", "error_code", "error_detail", "build_log_tail"]]
print(f"\nTotal: {len(df)}  OK: {len(df[df['status'] == 'OK'])}  Failed: {len(df[df['status'] != 'OK'])}")
df.sort_values("status", ascending=False)
```

Builds in a batch run concurrently on the backend. Collecting results in order does not serialise them, and each future returns its own messages and log.

## Procedure for the next upgrade

1. Expect DSS to rebuild and push every template image when the new base image is built. Do the base build at a quiet time and let that finish (`ps aux | grep "docker push" | grep -v grep | wc -l` reaches 0).
2. Run the batched audit script for the remaining templates, outside working hours, five at a time.
3. Use `success` in the result as the only signal. Never rely on "no exception" or on `last_built`.
4. Send each failure to the template's owner or last editor with the error detail and log tail.
5. Never-built templates (on prod these were test and personal templates) are candidates for deletion.

## Related problems found during the same work

* The webapp 404s that looked like template problems were caused by duplicate vanity URLs, see `webapp-404-after-upgrade.md`.
* The system nginx IPv6 problem was found while doing this, see `nginx-ipv6-upstream-after-upgrade.md`.

## Open items

* Raise the `last_built` not updating after API builds with Dataiku support.
* Template owners to fix `pkg_resources` failures.
* Full audit of which templates actually build on 14.5.1 has not been run.
