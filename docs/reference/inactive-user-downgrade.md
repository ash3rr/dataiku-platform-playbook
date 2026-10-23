# Inactive user downgrade

Script: [`scripts/licensing/inactive_user_downgrade.py`](../../scripts/licensing/inactive_user_downgrade.py). Python recipe in the platform maintenance project, run daily by a scenario. Reclaims paid licences from people who stopped using them and logs each change to `inactive_designers` (Snowflake), which feeds the Sankey chart in [licence-reporting.md](licence-reporting.md).

## Current rules

| Profile | Account age | Inactivity (no login AND no session activity) | New profile |
|---|---|---|---|
| FULL_DESIGNER | older than 90 days | 90 days | READER |
| AI_CONSUMER or READER | older than 180 days | 180 days | NONE |

Skipped before any rule runs:

* logins in the exception dataset `snowflake_dataiku_license_exception_list` (column `login`)
* logins ending in an excluded mail domain, `EXCLUDED_EMAIL_SUFFIX` in the script (users covered by a separate licence agreement)
* service-like logins: prefix `svc_`, `service_`, `robot_`, `tech_`, or containing `monitor`, `monitoring`, `ci_cd`, `automation`, `batch` (case-insensitive substring)

`NONE` is deliberate. Setting the profile to NONE takes the licence away, which is how we disable the user. We do not flip `enabled`.

Last login and last activity come from `client.get_user(login).get_activity()`, attributes `last_successful_login` and `last_session_activity`. Both are timezone-aware datetimes or `None`. `safe_dt()` maps `None` to 1970-01-01 UTC so "never logged in" counts as inactive.

Production ran with `DRY_RUN = False`. The repo copy defaults to `True`.

## History

### First draft (rejected)

The first attempt was a standalone script written from the request "count full designers, flag no login for 90 days, ignore accounts created in the last 30 days, keep an exemption list, use only correct API calls". It got several things wrong and was never used:

* used `dataikuapi.DSSClient(DSS_URL, API_KEY)` from outside DSS instead of the internal client
* filtered on `userProfile == 'DESIGNER'` (the real value is `FULL_DESIGNER`)
* read `lastSuccessfulLogin` and `createdOn` from `user.get_definition()`, where neither field exists. Last login comes from `get_activity()`, creation from `creationDate` in `list_users()`
* disabled users through `definition['enabled'] = False` and `set_definition()`
* hard-coded the exemption list (`admin`, `service_account`, `integration_user`) and exported a JSON report to the local filesystem

### The working v1 (`UserList_lastlogin.py`)

Transcribed from screenshots of the recipe that was in use, together with the threshold script (see [licence-count-snapshot.md](licence-count-snapshot.md)). It only handled FULL_DESIGNER:

* 90 days of no login and no session activity, account created more than 30 days ago
* exception list read from the dataset `dataiku_license_exception_list` (column `login`)
* downgrade to READER via `get_settings().get_raw()["userProfile"] = "READER"; save()`
* logged to `inactive_designers` by reading the existing dataset, converting the date columns with `pd.to_datetime(..., utc=True)`, `pd.concat` with the new rows, and `write_with_schema`

Two bugs in the transcribed copy:

```python
from datetime import import datetime, timedelta, timezone     # SyntaxError
print(f"User: {user['login']}, last_login, type(last_login), last_session_activity, type(last_session_activity))
```

The f-string had no braces around the variables (and in the first copy no closing quote), so it printed the literal names. The fix was only the braces:

```python
print(f"User: {user['login']}, {last_login}, {type(last_login)}, {last_session_activity}, {type(last_session_activity)}")
```

A "fully corrected" version with a DRY_RUN flag, INFO lines, emoji progress output and int-to-datetime conversion for the activity fields was rejected as noise. The person maintaining it wanted the minimal fix. The int conversion was not needed in any case: `get_activity()` already returns datetimes. A review also suggested narrowing `except Exception` on the history read to `(FileNotFoundError, KeyError)`. That was not adopted.

### v2 with AI Consumer and the Snowflake problems

By then the recipe had grown the AI_CONSUMER/READER 180-day rule, the service account filter, `safe_dt()`, the 90-day creation age for designers (previously 30) and a DRY_RUN flag. It wrote to a Snowflake-backed `inactive_designers`. A review for correctness found, in order:

1. `raise SystemExit()` in the dry-run branch. In a DSS recipe that marks the job FAILED, so every dry run showed red in the job history. Replaced with `if DRY_RUN: print(...) else: ...` with the apply and log blocks inside `else`, so dry runs never touch the dataset.
2. Silent `except Exception` on the history read. If the read failed for a permission or schema reason, the code silently rewrote the dataset with only the new rows and destroyed the history. Moot after the next fix, which removed the read.
3. A stray indented blank line after the service account `continue` (cosmetic).

Then the real symptom: nothing was written to Snowflake, and the recipe failed on the first run and succeeded on the second. The output dataset already had "Append instead of overwrite" switched on. The read + concat logic was fighting that setting: on a first run `get_dataframe()` failed because the table did not exist, and the write in that error state failed too. With append mode on, DSS keeps the history and the recipe only writes the new rows. (`get_writer().write_dataframe()` was briefly considered and dropped, because it replaces rather than appends.)

The replacement write block initially converted the date columns to ISO strings:

```python
s = pd.to_datetime(df_new[c], utc=True, errors="coerce")
df_new[c] = s.dt.strftime("%Y-%m-%dT%H:%M:%SZ").fillna("")
```

That failed against the existing table:

```
Connection OK. Table exists, but schema does not match : Type mismatch for column 3 (creation_date) : 'VARCHAR' in dataset, 'TIMESTAMP'(93:TIMESTAMPNTZ) in table Type mismatch for column 4 (last_login) : 'VARCHAR' in dataset, 'TIMESTAMP'(93:TIMESTAMPNTZ) in table Type mismatch for column 5 (last_session_activity) : 'VARCHAR' in dataset, 'TIMESTAMP'(93:TIMESTAMPNTZ) in table Type mismatch for column 6 (run_timestamp) : 'VARCHAR' in dataset, 'TIMESTAMP'(93:TIMESTAMPNTZ) in table
```

Removing the `strftime` (keeping `pd.to_datetime(..., utc=True, errors="coerce")`) did not clear it on its own: the DSS dataset's stored schema still said string. Fix in the UI: `inactive_designers` > Settings > Schema, set `creation_date`, `last_login`, `last_session_activity`, `run_timestamp` to Date/time without timezone (matches TIMESTAMP_NTZ, and the values are already normalised to UTC in code). After that the recipe succeeded.

It then reported:

```
FULL DESIGNERS to downgrade: 0
AI_CONSUMERS to downgrade: 0
```

A per-user debug line went in to see why:

```python
print(f"{login} | profile={profile} | created={created.date()} | last_login={last_login.date()} | last_session={last_session.date()} | inactive_90={inactive_90} | inactive_180={inactive_180}")
```

The output showed the logic was correct and nobody qualified: every FULL_DESIGNER had been active within 90 days, and the inactive AI Consumer and Reader accounts had last logged in between 90 and 180 days ago. Two options were put forward:

* A: lower `AI_INACTIVITY` to 90 days
* B: keep 180 days but drop the `created < NOW - AI_INACTIVITY` condition

Left open. No decision was made, and the later code still has 180 days with the creation condition. The debug line is still in the script.

### Exception list moved to Snowflake, domain rule

The exception list had been an uploaded CSV. It was copied into Snowflake with a Sync recipe (in the flow, re-runnable) rather than the dataset's "Export to dataset > create new dataset > store into Snowflake" action, which also works but is a one-off side action outside the flow. The new dataset is `snowflake_dataiku_license_exception_list`. Swapping the recipe input needs both:

* the string in `dataiku.Dataset("snowflake_dataiku_license_exception_list")`
* the recipe's Inputs/Outputs tab (DSS needs the two to match, and in a containerised recipe an undeclared input fails)

Then a rule to skip every user of one mail domain (users on a separate licence agreement), right after the exception check:

```python
if login.lower().endswith(EXCLUDED_EMAIL_SUFFIX):   # e.g. "@excluded-domain.example"
    print(f"Skipping excluded-domain email: {login}")
    continue
```

This assumes the login is the email address, which holds on this instance (logins are full emails).

## Open questions

* Option A or B for the AI Consumer threshold (above). Not decided.
* Whether `NONE` behaves identically across DSS versions was raised in review and not tested beyond our 14.x instance, where it works as intended.
