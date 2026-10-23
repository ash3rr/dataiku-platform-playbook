# Recipe patterns that bit us

Recipe-level lessons from this platform, mostly about code that worked on local DSS compute and broke in containers or on Snowflake. Runnable examples are in [`scripts/recipes/`](../../scripts/recipes/).

| Script | Purpose |
|---|---|
| [`managed_folder_streaming.py`](../../scripts/recipes/managed_folder_streaming.py) | Read and write managed-folder files (pickles, joblib models) through the API so it works in containers |
| [`append_run_log_to_snowflake.py`](../../scripts/recipes/append_run_log_to_snowflake.py) | Append audit rows to a Snowflake dataset with append mode, native timestamps and a dry run that does not fail the recipe |

## Containers enforce declared inputs and outputs

In containerized execution a recipe only receives connection details for datasets declared in its Inputs/Outputs. Referencing anything else fails:

```
Error in python process: At line 141: <class 'Exception'>: Dataset <name> cannot be used : declare it as input or output of your recipe
```

Locally the same code often works, so the problem appears when a project moves to EKS or to an automation node. Fix in the recipe's Inputs/Outputs tab (or in the Flow, right-click the recipe, Change inputs). Same for outputs: a recipe that computed GPU embeddings for its whole input failed at the very end because its output dataset was not declared.

When the code reads a dataset by name, the name in `dataiku.Dataset("<name>")` and the declared input must match. Watch for typos that have propagated through a flow (we had a dataset whose name was misspelled at source and every reference had to use the misspelling).

## Managed folders: stream, do not use `get_path()`

`folder.get_path()` returns a path on the DSS host, which means nothing inside a pod. Use `get_download_stream()` and `get_upload_stream()`. Fix the writing recipe as well as the reading one. Full write-up and code: [`managed_folder_streaming.py`](../../scripts/recipes/managed_folder_streaming.py).

Related: DSS notebooks do not get managed folders through the Flow the way recipes do. For an instance-wide inventory notebook we wrote results to named DSS datasets with `dataiku.Dataset("<name>").write_with_schema(df)` so they could be viewed and exported from the UI.

## Moving an uploaded CSV into Snowflake

To replace an uploaded CSV dataset with a Snowflake table as a recipe input:

* Sync recipe from the CSV dataset to the Snowflake connection. It appears in the Flow, is re-runnable, and DSS handles schema mapping and the write. Check the output schema before running: columns DSS typed as strings that should be dates or numbers are easier to fix before the table exists.
* "Export to dataset" (create new dataset, store into the Snowflake connection) also works for a one-off copy, but it is a side action that does not live in the Flow.

Then change both places in the consuming recipe: the Inputs/Outputs tab, and the dataset name string in the code. DSS needs both to match.

## History tables: let append mode do it

The "read the table, concat new rows, write it all back" pattern caused a recipe to fail on its first run (the Snowflake table did not exist yet, `get_dataframe()` raised, and the write failed in that state) and work on the second. Its bare `except` would also have overwritten the whole history with one run's rows if the read had failed for a permissions or schema reason. The dataset already had "Append instead of overwrite" ticked. With append on, write only the new rows.

## A dry run must not `raise SystemExit()`

In a DSS recipe `SystemExit` marks the recipe failed. Every dry run showed red in job history. Use `if DRY_RUN: ... else: ...`.

## Timestamps into Snowflake

Formatting datetimes as strings before `write_with_schema` against a table with `TIMESTAMP_NTZ` columns gives:

```
Table exists, but schema does not match : Type mismatch for column 3 (creation_date) :
'VARCHAR' in dataset, 'TIMESTAMP'(93:TIMESTAMPNTZ) in table
```

Keep pandas datetimes (`pd.to_datetime(col, utc=True)`), and fix the dataset's stored schema in DSS (Settings > Schema) to date/time without timezone. Changing the code alone left the error in place because DSS still held the old string schema.

## "Failed to synchronize table description" warnings

```
WARNING : Failed to synchronize table description: Cannot synchronize dataset description to SQL database.
SQL access control error: Insufficient privileges to operate on table '<TABLE>'.
```

DSS pushes dataset and column descriptions to Snowflake as `COMMENT`s. That needs ownership of the table, which a writing service role does not have (it has INSERT and SELECT). The data write succeeds and the warning is noise. We could not find a documented connection setting to disable the sync. We had at one point believed one exists under the connection's advanced settings, but that did not hold up. Options: ignore it, or remove the descriptions from the dataset. Granting OWNERSHIP to the service role is not worth it.

## Scenario reporters: alert on real failures only

Scenario outcomes are `SUCCESS`, `WARNING`, `FAILED` and `ABORTED`. A step that ends with warnings does not make the scenario outcome `WARNING` unless something errors, so warnings like the one above still give `SUCCESS`. For failure alerts use two reporters to the same channel, one with condition `outcome == "FAILED"` and one with `outcome == "ABORTED"`. The documentation shows `==` and `!=`, and we did not confirm that `OR` works in one condition.

## Large inputs in Python recipes

Do not `get_dataframe()` something that does not fit in the pod. Read in chunks and keep one writer open:

```python
import dataiku

src = dataiku.Dataset("<input>")
out = dataiku.Dataset("<output>")
with out.get_writer() as writer:
    for chunk in src.iter_dataframes(chunksize=5_000_000, columns=["<col1>", "<col2>"]):
        writer.write_dataframe(transform(chunk))
```

`write_with_schema` sets the schema. With a writer, set the output schema first (for example from the first chunk) if the dataset has none. For very large Snowflake-to-Snowflake processing, see [eks-vs-snowflake-compute.md](eks-vs-snowflake-compute.md).

## Copying recipes between projects

Grep the code for the source project key. Recipes that call the API with a hard-coded key keep pointing at the old project after a copy.

## Filtering users in admin recipes

A recipe that changes user accounts should skip exception lists and service accounts first, and can skip whole account populations by email domain, for example:

```python
if login in exceptions:
    continue
if login.lower().endswith("@other-org.example"):
    print(f"Skipping other-org account: {login}")
    continue
```

The licence recipes that use this are in [`scripts/licensing/`](../../scripts/licensing/).
