# Dataset inventory

Script: [`scripts/governance/dataset_inventory.py`](../../scripts/governance/dataset_inventory.py). One notebook cell, admin, scans every project on the design node.

## Goal

Clean up the instance. Plan in three phases:

1. Inventory: every dataset with type, connection, schema size, tags, managed flag, timestamps, producers, consumers, usages outside the flow.
2. Categorise: active, stale, orphan, source, shared/external.
3. Act: tag candidates (`cleanup_candidate`), have owners review, move confirmed junk to an archive project or tag it `deprecated`, delete after a grace period.

Only phase 1 and 2 were done. DSS does not track "last read" per dataset. That would need the audit log (see the host diagnostics runbook). The inventory uses flow connectivity and last-modified time instead.

## Categories

From the flow graph plus `get_usages()`:

| Category | Rule |
|---|---|
| orphan | nothing produces it, nothing consumes it, no usages |
| external_dependency | usages only (dashboards, other projects), no producer or consumer in its own flow |
| source | nothing produces it, recipes consume it |
| terminal | produced, not consumed, no usages |
| active_flow | everything else |

Freshness: `versionTag.lastModifiedOn` older than `STALE_DAYS` (90) is stale. A stale orphan is the safest cleanup target.

## Final run

Shape of the result: `active_flow` was the large majority, `source` and `orphan` came next in similar numbers, and `external_dependency` was a small handful. Stale orphans were about a tenth of all datasets. Well over a third of datasets had no build metric (never built, as far as metrics can tell).

What to look for in the per-project breakdown: environment copies of one project (base, UAT, PRD) where only PRD is live and the copies hold most of the orphans, single projects where most orphans are also stale, projects named TEST full of never-built datasets, and "copy of" projects that were never cleaned up. Projects like those are candidates for archiving whole rather than dataset by dataset.

Suggested next steps, not done: group stale orphans by `last_modified_by` and send each owner their list, with a 30-day grace period before tagging `deprecated`. A script to tag stale orphans `cleanup_candidate` in the UI. Snowflake table sizes for the orphans so cleanup can be prioritised by storage cost.

## How the script got there

1. First version used `client.get_default_project()` and only scanned the notebook's own project, so only a handful of datasets appeared. It also used `get_definition()` and placeholder calls (`get_object_discussions()` as a timestamp fallback, which makes no sense). Replaced with a loop over `client.list_project_keys()` and progress output every 100 projects.
2. `dataset.get_definition()` raised `DeprecationWarning: Dataset.get_definition is deprecated, please use get_settings`. Switched to `get_settings().get_raw()`.
3. First full run: the scan covered every project, but the categories were only `external_dependency` and `orphan`, and freshness only `unknown` and `never_built`. No source, terminal or active_flow at all meant the flow graph parsing found no recipes.
4. A diagnostic printed the first graph nodes and the metric list of one dataset. Recipe nodes are `type == "RUNNABLE_RECIPE"` (we tested for `RECIPE`), the recipe name is in `ref` (we read `name`), inputs are `predecessors` and outputs `successors` (we read `inputs` / `outputs`). The external_dependency rule also changed: a dataset with usages *and* flow links is active, not external. After the fix the categories came out as above.
5. Freshness from metrics: `get_last_metric_values().get_global_value("reporting:BUILD_START_DATE")` returned nothing. Walking `get_raw()["metrics"]` for `metricType == "BUILD_START_DATE"` / `COUNT_RECORDS` and reading `lastValues[0]["value"]` works, but those metrics only exist when a build computed them ("compute metrics" in the build, a manual Compute, or a scenario step). Most of our datasets had none, hence `unknown` / `never_built`. We did not start a metrics job for every dataset on the instance.
6. `dataset.get_status()` does not exist (`AttributeError`).
7. `versionTag` and `creationTag` are on every dataset: `{'versionNumber': 6, 'lastModifiedBy': {'login': ...}, 'lastModifiedOn': <ms>, 'extendedProperties': {}}`. Freshness uses `versionTag.lastModifiedOn`. `created`, `last_modified` and `last_modified_by` columns were added. Build date and record count from metrics stay as extra columns where present.
8. The freshness breakdown printed one line per day count (`stale (412d)`, ...), which is noise in the printout. Filter the dataset instead.
9. A pasted cell arrived cut off (`SyntaxError: unexpected EOF while parsing` at `metrics = ds_handle.get_last_met`). Re-pasting fixed it.

## Getting the output out of a notebook

* The notebook only showed the first and last five rows of the (large) DataFrame. `pd.set_option("display.max_rows", 200)` helps inline, but the summary prints are what matter.
* `df.to_csv("/tmp/...")` wrote to `/tmp` on the DSS host, where nobody can download it from the UI.
* `dataiku.Folder("YOUR_FOLDER_ID")` (a placeholder left in the script) crashes. A managed folder upload was suggested, but a notebook has no recipe and no managed folder attached through the flow.
* An `IPython.display.FileLink` to the `/tmp` file was proposed as a quick option and not used.
* What works: create three empty datasets in the project first (+Dataset, any writable connection such as filesystem_managed or Snowflake) named `dataset_inventory`, `orphan_datasets`, `stale_orphan_datasets`, write to them with `write_with_schema`, then Explore > Export.
