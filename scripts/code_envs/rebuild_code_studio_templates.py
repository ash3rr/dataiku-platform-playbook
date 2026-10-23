# -*- coding: utf-8 -*-
"""
Rebuild every Code Studio template after a DSS upgrade, in small batches,
and report which builds failed and who to contact.

Context: after the 14.2.1 -> 14.5.1 upgrade, Code Studio webapps failed
to start with
    IllegalStateException: The code studio template must be rebuilt
Some templates had never been built (last_built None).

IMPORTANT: this final batched version was written but NOT run. The earlier
"start every build at once" run overloaded the DSS host's Docker daemon
and we decided to troubleshoot individual templates instead. Expect long
runtimes, and pick a quiet window.

Confirmed API behaviour (DSS 14.5.1):
* client.list_code_studio_templates() returns DSSCodeStudioTemplateListItem
  objects, not dicts. t["id"] fails with
      TypeError: 'DSSCodeStudioTemplateListItem' object is not subscriptable
  Attributes: build_for_configs, client, id, label, last_built,
  to_code_studio_template. last_built is a tz-aware UTC datetime or None.
* client.get_code_studio_template(id).build() returns a future and builds
  for whatever the template's "Build for" config lists (local Docker and/or
  EKS). An image built for EKS (pushed to ECR) is not usable by local
  Docker execution and vice versa.
* future.wait_for_result() does NOT raise when the build fails. It returns
  {'messages': {'messages': [{'severity': 'ERROR',
      'code': 'ERR_CODE_STUDIO_CONTAINER_IMAGE_BUILD_FAILED',
      'details': "Failed to build images for <id> on docker host: localhost :
                  Command failed: 'docker' (exit code: 1)", ...}],
      'success': False, 'error': True, ...},
   'builds': [], 'futureLog': {'totalLines': 803, 'lines': [...], ...}}
  so check result['messages']['success'] yourself. Our first bulk run
  printed "Done" for templates that had all failed.
* Settings keys (get_settings().get_raw()): id, type, label, tags,
  versionTag, creationTag, isEditor, reattachOnRestart, owner,
  defaultPermission, permissions, defaultContainerConf,
  allowContainerConfOverride, allContainerConfs, containerConfs,
  inheritGlobalCodeEnvsExtraSettings, codeEnvsExtraSettings, params.
  versionTag.lastModifiedOn is the last DEFINITION edit, not the last build.
* print() output in a DSS notebook is buffered until the cell ends. The
  functools.partial line below makes progress visible.
* The notebook kernel runs one cell at a time: a new cell sits "queued"
  until the previous one finishes. Restarting the kernel does not stop
  builds already running server side, and interrupted builds showed
  InterruptedException and docker exit code 143 (SIGTERM).
* build(disable_docker_cache=True) exists per the docs (forces --no-cache),
  but is not used here.

History: docs/reference/code-studio-template-rebuild.md
"""
import functools
print = functools.partial(print, flush=True)  # noqa: A001

import dataiku  # noqa: E402
import pandas as pd  # noqa: E402

client = dataiku.api_client()
templates = client.list_code_studio_templates()

BATCH_SIZE = 5

# Collect templates to build
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
    batch_num = (i // BATCH_SIZE) + 1
    print(f"--- Batch {batch_num} ---")

    # Start batch
    futures = []
    for b in batch:
        print(f"  Starting: {b['label']}")
        handle = client.get_code_studio_template(b["id"])
        future = handle.build()
        futures.append((b, future))

    # Wait for batch to complete
    for b, future in futures:
        try:
            result = future.wait_for_result()
            success = result.get("messages", {}).get("success", False)
            msgs = result.get("messages", {}).get("messages", [])
            log_lines = result.get("futureLog", {}).get("lines", [])
            b["build_log_tail"] = "\n".join(log_lines[-30:])
            if success:
                b["status"] = "OK"
                b["error_code"] = ""
                b["error_detail"] = ""
                print(f"  OK: {b['label']}")
            else:
                b["status"] = "FAILED"
                b["error_code"] = msgs[0].get("code", "") if msgs else ""
                b["error_detail"] = msgs[0].get("details", "") if msgs else ""
                print(f"  FAILED: {b['label']} - {b['error_detail'][:120]}")
        except Exception as e:
            b["status"] = "EXCEPTION"
            b["error_code"] = type(e).__name__
            b["error_detail"] = str(e)
            b["build_log_tail"] = ""
            print(f"  EXCEPTION: {b['label']} - {e}")
        results.append(b)

# Display as DataFrame (a notebook cannot usefully write a CSV to local disk)
df = pd.DataFrame(results)
df = df[["label", "id", "status", "owner", "last_editor", "last_edited_on", "last_built",
         "container_confs", "error_code", "error_detail", "build_log_tail"]]

print("\n=== SUMMARY ===")
print(f"Total: {len(df)}")
print(f"OK: {len(df[df['status'] == 'OK'])}")
print(f"Failed: {len(df[df['status'] != 'OK'])}")

df.sort_values("status", ascending=False)
