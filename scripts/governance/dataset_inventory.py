# -*- coding: utf-8 -*-
"""
Instance-wide dataset inventory: categorise every dataset and find stale
orphans. Run in one notebook cell (DSS 14.5.1, admin).

Categories (from the flow graph plus get_usages()):
  orphan              no recipe produces it, nothing consumes it, no usages
  external_dependency used only outside the flow (dashboards, other projects)
  source              nothing produces it, recipes consume it
  terminal            produced, nothing consumes it, no usages
  active_flow         everything else
Freshness: versionTag.lastModifiedOn older than STALE_DAYS -> stale.

Stale orphans (orphan AND untouched for STALE_DAYS+) came to about a tenth
of all datasets on the last run. These are the safest cleanup targets.

What had to be fixed along the way (docs/reference/dataset-inventory.md):
* get_default_project() only scans the notebook's own project. Loop over
  client.list_project_keys() for the instance.
* Flow graph recipe nodes are type RUNNABLE_RECIPE (not RECIPE), with
  inputs in 'predecessors', outputs in 'successors', recipe name in 'ref'.
  With the wrong names every dataset came out orphan/external_dependency.
* dataset.get_definition() is deprecated in 14.5.1. Use
  get_settings().get_raw().
* metrics.get_global_value("reporting:BUILD_START_DATE") gave nothing.
  Walking get_last_metric_values().get_raw()['metrics'] works, but
  BUILD_START_DATE / COUNT_RECORDS only exist where metrics were computed
  (most datasets were unknown or never_built on the first pass). versionTag
  timestamps exist on every dataset, so freshness uses those.
* DSSDataset has no get_status().
* A notebook has no managed folder and /tmp on the DSS host is not
  reachable from the UI, so results go to three datasets you create first
  (+Dataset, e.g. filesystem_managed or Snowflake): dataset_inventory,
  orphan_datasets, stale_orphan_datasets. Then Explore > Export.
"""
import dataiku
import pandas as pd
from datetime import datetime

client = dataiku.api_client()
STALE_DAYS = 90

rows = []
project_keys = client.list_project_keys()
total_projects = len(project_keys)

for i, project_key in enumerate(project_keys):
    if i % 100 == 0:
        print(f"Scanning project {i}/{total_projects}...")
    try:
        proj = client.get_project(project_key)
    except:
        continue

    produced_by = {}
    consumed_by = {}
    try:
        flow = proj.get_flow()
        graph = flow.get_graph()
        nodes = graph.nodes if hasattr(graph, "nodes") else graph.data.get("nodes", {})
        if isinstance(nodes, dict):
            node_items = nodes.items()
        else:
            node_items = [(n.get("id", ""), n) for n in nodes]

        for node_id, node in node_items:
            node_data = node if isinstance(node, dict) else node.__dict__
            if node_data.get("type") in ("RUNNABLE_RECIPE", "RECIPE"):
                recipe_name = node_data.get("ref", node_id)
                # predecessors = datasets this recipe reads from
                for inp in node_data.get("predecessors", []):
                    consumed_by.setdefault(inp, []).append(recipe_name)
                # successors = datasets this recipe writes to
                for out in node_data.get("successors", []):
                    produced_by.setdefault(out, []).append(recipe_name)
    except:
        pass

    try:
        datasets = proj.list_datasets()
    except:
        continue

    for ds in datasets:
        ds_name = ds["name"]
        try:
            ds_handle = proj.get_dataset(ds_name)
            ds_settings = ds_handle.get_settings().get_raw()
        except:
            continue

        # Timestamps from version/creation tags (always present)
        version_tag = ds_settings.get("versionTag", {})
        creation_tag = ds_settings.get("creationTag", {})
        last_modified_ms = version_tag.get("lastModifiedOn")
        created_ms = creation_tag.get("lastModifiedOn")
        last_modified_by = version_tag.get("lastModifiedBy", {}).get("login", "")

        last_modified = None
        created = None
        if last_modified_ms:
            last_modified = datetime.fromtimestamp(last_modified_ms / 1000).strftime("%Y-%m-%d")
        if created_ms:
            created = datetime.fromtimestamp(created_ms / 1000).strftime("%Y-%m-%d")

        # Build date from metrics (if available)
        last_built = None
        record_count = None
        try:
            metrics = ds_handle.get_last_metric_values()
            raw_metrics = metrics.get_raw().get("metrics", [])
            for m in raw_metrics:
                mt = m.get("metric", {}).get("metricType")
                last_values = m.get("lastValues", [])
                if mt == "BUILD_START_DATE" and last_values:
                    last_built = last_values[0].get("value")
                if mt == "COUNT_RECORDS" and last_values:
                    record_count = last_values[0].get("value")
        except:
            pass

        usages = []
        try:
            usage_list = ds_handle.get_usages()
            if usage_list:
                usages = [str(u) for u in usage_list]
        except:
            pass

        rows.append({
            "project": project_key,
            "name": ds_name,
            "type": ds_settings.get("type", ""),
            "connection": ds_settings.get("params", {}).get("connection", ""),
            "num_columns": len(ds_settings.get("schema", {}).get("columns", [])),
            "tags": ", ".join(ds_settings.get("tags", [])),
            "managed": ds_settings.get("managed", False),
            "created": created,
            "last_modified": last_modified,
            "last_modified_by": last_modified_by,
            "last_built": last_built,
            "record_count": record_count,
            "external_usages": "; ".join(usages) if usages else "",
            "produced_by": ", ".join(produced_by.get(ds_name, [])),
            "consumed_by": ", ".join(consumed_by.get(ds_name, [])),
        })

df_datasets = pd.DataFrame(rows)


def categorize(row):
    has_producer = bool(row["produced_by"])
    has_consumer = bool(row["consumed_by"])
    has_external = bool(row["external_usages"])
    if not has_producer and not has_consumer and not has_external:
        return "orphan"
    if has_external and not has_producer and not has_consumer:
        return "external_dependency"
    if not has_producer and has_consumer:
        return "source"
    if has_producer and not has_consumer and not has_external:
        return "terminal"
    return "active_flow"


df_datasets["category"] = df_datasets.apply(categorize, axis=1)


def flag_stale(row):
    mod = row["last_modified"]
    if mod is None:
        return "unknown"
    try:
        mod_dt = pd.to_datetime(mod)
        age = (datetime.now() - mod_dt).days
        if age > STALE_DAYS:
            return f"stale ({age}d)"
        return f"fresh ({age}d)"
    except:
        return "unknown"


df_datasets["freshness"] = df_datasets.apply(flag_stale, axis=1)

sort_order = {"orphan": 0, "terminal": 1, "source": 2, "external_dependency": 3, "active_flow": 4}
df_datasets["_sort"] = df_datasets["category"].map(sort_order)
df_datasets = df_datasets.sort_values(["_sort", "project", "freshness"]).drop(columns="_sort")

print(f"\nScanned {df_datasets['project'].nunique()} projects, {len(df_datasets)} total datasets\n")

print("=== CATEGORY BREAKDOWN ===")
print(df_datasets["category"].value_counts())
print()

# One value per day count, so this list is long. Filter the dataset instead.
print("=== FRESHNESS BREAKDOWN ===")
print(df_datasets["freshness"].value_counts())
print()

orphans = df_datasets[df_datasets["category"] == "orphan"]
print(f"=== TOP 20 PROJECTS BY ORPHAN COUNT ({len(orphans)} total orphans) ===")
print(orphans.groupby("project").size().sort_values(ascending=False).head(20))
print()

stale_orphans = orphans[orphans["freshness"].str.startswith("stale", na=False)]
print(f"=== STALE ORPHANS: {len(stale_orphans)} (not modified in {STALE_DAYS}+ days) ===")
print(stale_orphans.groupby("project").size().sort_values(ascending=False).head(20))
print()

never_built = df_datasets[df_datasets["last_built"].isna()]
print(f"=== NEVER-BUILT DATASETS: {len(never_built)} total ===")
print(never_built.groupby("project").size().sort_values(ascending=False).head(20))
print()

# Write results to Dataiku datasets you can browse/export from the UI.
# Create the three datasets in the project first.
output_ds = dataiku.Dataset("dataset_inventory")
output_ds.write_with_schema(df_datasets)
print("Written to dataset_inventory")

output_orphans = dataiku.Dataset("orphan_datasets")
output_orphans.write_with_schema(orphans)
print("Written to orphan_datasets")

output_stale = dataiku.Dataset("stale_orphan_datasets")
output_stale.write_with_schema(stale_orphans)
print("Written to stale_orphan_datasets")
