# Code Studio template rebuild

Scripts: [`scripts/code_envs/rebuild_code_studio_templates.py`](../../scripts/code_envs/rebuild_code_studio_templates.py) (final batched version, never run) and [`scripts/code_envs/code_studio_template_inspect.py`](../../scripts/code_envs/code_studio_template_inspect.py) (the inspection cells).

The incident timeline and the operational procedure are in [runbooks/code-studio-template-rebuild.md](../runbooks/code-studio-template-rebuild.md). This page covers the scripts and how they evolved.

## Why

Code Studio templates carry a built image per target. After the DSS upgrade from 14.2.1 to 14.5.1, a published Code Studio webapp failed to start with:

```
IllegalStateException: The code studio template must be rebuilt
```

Every template needs rebuilding after an upgrade. Some templates had never been built at all.

## What the API gives you

* `client.list_code_studio_templates()` returns `DSSCodeStudioTemplateListItem` objects. `t["id"]` raised `TypeError: 'DSSCodeStudioTemplateListItem' object is not subscriptable`. Attributes: `build_for_configs`, `client`, `id`, `label`, `last_built`, `to_code_studio_template`.
* `last_built` is a timezone-aware UTC datetime or `None` (the first guess was milliseconds, which is wrong).
* `client.get_code_studio_template(id).build()` returns a future. `build(disable_docker_cache=True)` forces `--no-cache` (from the docs, not used).
* A build targets every container config in the template's "Build for" setting. A local build lands in the DSS host's Docker and an EKS build is pushed to ECR. An image built for EKS cannot be used by a local execution and the other way round, so a template used both ways must list both targets. `build()` does what the template says, with nothing extra to pass.
* Template settings keys: `id`, `type`, `label`, `tags`, `versionTag`, `creationTag`, `isEditor`, `reattachOnRestart`, `owner`, `defaultPermission`, `permissions`, `defaultContainerConf`, `allowContainerConfOverride`, `allContainerConfs`, `containerConfs`, `inheritGlobalCodeEnvsExtraSettings`, `codeEnvsExtraSettings`, `params` (`params.blocks`: `dss_base_image` with synced zones, `simple_deployment`, VS Code, Streamlit, code env blocks). There is no build timestamp anywhere in the settings. `versionTag.lastModifiedOn` is the last edit of the definition.
* `wait_for_result()` does **not** raise when the build fails. It returns:

  ```
  {'messages': {'messages': [{'severity': 'ERROR', 'isFatal': False,
                              'code': 'ERR_CODE_STUDIO_CONTAINER_IMAGE_BUILD_FAILED',
                              'title': 'Failed to build Code Studio image',
                              'details': "Failed to build images for <template> on docker host: localhost : Command failed: 'docker' (exit code: 1)",
                              'message': '...'}],
                'maxSeverity': 'ERROR', 'anyMessage': True, 'success': False,
                'warning': False, 'error': True, 'fatal': False},
   'builds': [],
   'futureLog': {'totalLines': 803, 'lines': [...], 'status': [...], 'maxLevel': 3, 'maxLevelFirstLine': 33}}
  ```

  Check `result["messages"]["success"]` and read `futureLog.lines` for the cause.

## History

### Sequential, then parallel

The first script looped over the templates and called `build().wait_for_result()` one at a time, printing "Done". It worked through the list slowly. The next version started every build first and then waited on each future in turn, so the builds run concurrently on the server while the results are collected in order (each future holds its own result, so nothing gets mixed up).

### Only rebuild what is stale

Stopping and restarting the script rebuilds everything again, because `build()` does not skip current images. A filter on `last_built` was added: skip `None` (never built, probably abandoned) and anything built on or after the upgrade date. The run reported a few skipped as never built, most already rebuilt that day (by the earlier runs or in the UI) and the rest started, all "Done".

One run failed immediately with `SyntaxError: unexpected EOF while parsing` because the pasted cell had been cut off mid-line.

### last_built did not move

After the run, the same templates still had their old `last_built` dates and would have been rebuilt again. We went looking for another timestamp (settings keys, `versionTag`, `creationTag`, `params`) and compared a template built in the UI with one built by the script: same structure, nothing build-related. The working theory became "API-triggered builds do not update `last_built`", and a Dataiku bug report was being drafted, along with a hard-coded skip list for those templates as a stopgap.

Users were reporting problems with those templates, so we built one again and printed the future's result. The build had failed. Its log showed `mim install mmcv-full==1.5.0` failing with `ModuleNotFoundError: No module named 'pkg_resources'`: the newer setuptools in the rebuilt environment no longer ships `pkg_resources` (pip also warned it is slated for removal), so the Dockerfile step for that template's custom package install exits 1.

So `last_built` was correct: the builds had failed, and our script printed "Done" because `wait_for_result()` returned normally. The "API builds do not update last_built" theory was wrong, and the skip list was never used. (Whether `last_built` updates after a *successful* API build was not checked afterwards, but nothing suggests it does not.)

### A report for template owners

Next requirement: rebuild everything, record the result per template, and know whom to contact. The script grew:

* per template: label, id, `last_built`, `owner`, last editor and edit time (from `versionTag`), container configs
* per build: status (OK / FAILED / EXCEPTION), error code, error detail, and the last 30 lines of `futureLog`

Writing a CSV to `/home/dataiku/...` was rejected (a notebook cannot hand you that file). The result is a DataFrame shown as the cell's last expression, failures first.

Problems running it:

* Nothing printed while it ran: Jupyter buffered `print` until the cell finished. Fix: `print = functools.partial(print, flush=True)` at the top. (A `sys.stdout.flush = lambda: None` line was also suggested. It is wrong and should be ignored.)
* A new cell stayed "queued" for more than 10 minutes: the kernel runs one cell at a time and the previous cell was still waiting on all the builds it had started. Restarting the kernel frees it. Builds already started keep running on the server, but their results are lost to the notebook.
* After the restart one template showed two errors: `InterruptedException` (the restart killed the wait) and `Command failed: 'docker' (exit code: 143)` (SIGTERM). Every template building concurrently on the host's single Docker daemon was too many.

The final version builds in batches of 5 (`BATCH_SIZE = 5`): start five, wait for all five, record, next batch. Wait for leftover builds from earlier runs to finish before starting it.

### Not run

At that point the decision was to not run the full rebuild: too heavy on the server. The focus moved to troubleshooting the specific webapps users reported, which led to the nginx and vanity URL findings (see [vanity-url-audit.md](vanity-url-audit.md), and the runbooks for host-level nginx). The batched script is in the repo as the version to use next time.

## Related: the template Exposed location workaround

For Code Studio webapps whose pod nginx fails with `invalid port in upstream "127.0.0.1:8888http://localhost:8888/"`, Dataiku's workaround is to set the template's nginx block "Exposed location" to something other than `/`, for example `/?foo=bar`, then rebuild the template and restart the webapp. It has to be done per template. Templates using the built-in Streamlit block have no such field in the Definition tab. The alternative discussed was replacing the built-in block with a custom entrypoint block, which was not done.

## Open questions

* How many templates fail to build on 14.5.1: unknown, the batched report was never run.
* Whether the failing templates were fixed by their owners: not recorded.
