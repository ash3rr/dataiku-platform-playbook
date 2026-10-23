# -*- coding: utf-8 -*-
"""
Append one run's rows to a Snowflake-backed log dataset from a Python
recipe, without the problems we hit doing it the obvious way.

Where this came from
--------------------
An admin recipe that changes things (it downgraded inactive users) also
wrote an audit row per change to a Snowflake dataset. It had four
problems, all generic to recipes that keep a history table:

1. Dry-run mode ended with `raise SystemExit()`. In a DSS recipe that
   does not "skip cleanly": the recipe is marked FAILED, so every dry
   run showed red in job history. Use if/else instead.

2. History was kept by reading the existing dataset, concatenating the
   new rows and writing everything back:

       try:
           df_old = ds.get_dataframe()
           df_write = pd.concat([df_old, df_new])
       except Exception:
           df_write = df_new          # silently swallows the reason
       ds.write_with_schema(df_write)

   On the first run the Snowflake table did not exist, get_dataframe()
   raised, and the write in that state failed too, so the recipe failed
   on run one and worked on run two. The bare except also means a
   permission or schema error on read would overwrite the whole history
   with only today's rows. The output dataset already had
   "Append instead of overwrite" ticked (dataset settings), so the
   read-and-concat block was fighting DSS. With append mode on, only
   write the new rows.

3. Timestamps were formatted to strings ("%Y-%m-%dT%H:%M:%SZ") before
   writing. The table had TIMESTAMP_NTZ columns, giving:

       Table exists, but schema does not match : Type mismatch for
       column 3 (creation_date) : 'VARCHAR' in dataset,
       'TIMESTAMP'(93:TIMESTAMPNTZ) in table

   Keep them as pandas datetimes. Removing the strftime was not enough
   on its own: the dataset's stored schema in DSS still said string.
   Fix it in the dataset's Settings > Schema: set those columns to
   date/time *without* timezone (TIMESTAMP_NTZ has no zone, and values are
   normalised to UTC in code before writing).

4. On a later run, "nothing written" was not a bug:
   the job log said 0 rows qualified, and the write block is guarded by
   `if rows:`. Print counts so an empty run is obvious.

Also seen on this dataset, harmless: DSS warns
    Failed to synchronize table description ... Insufficient privileges
when the dataset or its columns have descriptions in DSS. DSS tries to
push them as Snowflake COMMENTs, which needs OWNERSHIP of the table, a
privilege a writing service role normally does not have. The data
write still succeeds. We looked for a connection setting to turn the
sync off and could not find one documented. Removing the descriptions
from the dataset stops the attempt. Scenario outcome stays SUCCESS
(warnings in a step do not change it unless the step errors), so set
the failure reporter conditions to outcome == "FAILED" and, as a
second reporter, outcome == "ABORTED" (we were not sure the condition
field accepts OR).

Setup
-----
* Output dataset on the Snowflake connection, declared as the recipe's
  output (required in containers), "Append instead of overwrite" ticked.
* Schema in DSS matching the table: strings for text, date/time without
  timezone for the timestamp columns.
"""
from datetime import datetime, timezone

import dataiku
import pandas as pd

OUTPUT_DATASET = "<log_dataset>"
DRY_RUN = True

COLUMNS = [
    "login",
    "old_value",
    "new_value",
    "event_time",
    "run_timestamp",
]
TIMESTAMP_COLUMNS = ["event_time", "run_timestamp"]

NOW = datetime.now(timezone.utc)


def collect_rows():
    """Replace with the real logic. Return a list of dicts keyed by COLUMNS."""
    return []


def apply_changes(rows):
    """Replace with the real side effects. Never called in dry run."""
    for row in rows:
        print("Would apply:", row["login"], row["old_value"], "->", row["new_value"])


rows = collect_rows()
print("Rows to process:", len(rows))

if DRY_RUN:
    print("DRY RUN: no changes applied, nothing written.")
else:
    apply_changes(rows)

    if rows:
        df_new = pd.DataFrame(rows)
        for col in COLUMNS:
            if col not in df_new.columns:
                df_new[col] = None
        df_new = df_new[COLUMNS]

        # Keep real datetimes. Snowflake TIMESTAMP_NTZ wants timestamps, not strings.
        for col in TIMESTAMP_COLUMNS:
            df_new[col] = pd.to_datetime(df_new[col], utc=True, errors="coerce")

        # Append mode on the dataset does the history. No read, no concat.
        dataiku.Dataset(OUTPUT_DATASET).write_with_schema(df_new)
        print("Appended", len(df_new), "row(s) to", OUTPUT_DATASET)
    else:
        print("No rows to log.")

print("Done.")
