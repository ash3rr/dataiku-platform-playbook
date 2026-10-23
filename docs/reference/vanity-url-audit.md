# Vanity URL audit

Scripts: [`scripts/monitoring/vanity_url_audit/`](../../scripts/monitoring/vanity_url_audit/)

| File | Role |
|---|---|
| `00_schema.sql` | Snowflake table, ownership grant, notes on the managed access schema |
| `01_collect.py` | Step 1, Python recipe: every vanity URL entry plus webapp details into `VANITY_URL_AUDIT` |
| `02_check_and_alert.py` | Step 2, scenario Python step: latest snapshot only, HTML alert into scenario variables, `sys.exit(1)` on duplicates |
| `03_cleanup.sql` | Step 3, SQL step (run always): delete rows older than six months |
| `notebook_vanity_report.py` | The notebook cells used to find and triage the duplicates by hand |

Scenario "Vanity URL check" in the platform maintenance project, daily time trigger, one mail reporter (settings under Reporter).

## Incident

Right after the 14.2.1 to 14.5.1 upgrade, users reported Code Studio webapps returning `404 Not Found` from nginx. Two webapps in one project, published from the same Code Studio and template with identical container config, behaved differently: one worked, the other returned 404.

What we went through before finding the cause (the host-level and pod-level detail is in the runbooks, not here):

* The original error on the first webapp was `IllegalStateException: The code studio template must be rebuilt`, which started the template rebuild work in [code-studio-template-rebuild.md](code-studio-template-rebuild.md).
* An earlier Dataiku support case had the pod's nginx failing with `invalid port in upstream "127.0.0.1:8888http://localhost:8888/" in /home/dataiku/nginx.conf:115`. Dataiku's workaround was to set the nginx block's "Exposed location" field to something other than `/`, for example `/?foo=bar`. They described it as a known issue with no proper fix. It works, but means editing the nginx block in every affected template. Our template used the built-in Streamlit block, which has no Exposed location field, so the workaround could not be applied as described.
* A permission alert on `/var/log/nginx/error.log` inside the new base image appeared in both the working and the broken webapp logs, so it was not the difference.
* The broken webapp's exposition log showed DSS port-forwarding to 8182 (the pod nginx port for the Streamlit entrypoint). The working one looked the same.

Then Dataiku support came back with the cause: duplicate vanity URL configurations. Two webapps in one project (an original and its `_v2` replacement) were both mapped to the same slug and both running. After the `_v2` entry's slug was renamed, all webapps worked again, including the original pair. The project that started the investigation had no vanity URL at all. One slug mapped to two running webapps was enough to break routing for the whole instance.

At that point the audit also found other duplicate slugs where at most one webapp was running. Those were left for their owners and became the first content of the daily alert.

## Where the mapping lives

```python
settings = client.get_general_settings().get_raw()
ws = settings["webAppSecuritySettings"]
ws["vanityURLs"]                       # [{"secret": False, "key": "PROJECTKEY.webappId", "value": "<slug>"}, ...]
ws["requiredAuthenticationMode"]       # "ALL_BUT_ADMIN_WHITELIST" here
ws["requiredAuthenticationWhitelist"]  # ["PROJECTKEY.webappId", ...] allowed without login
```

In the UI it is Administration > Settings > Security & Audit > Other security settings (`https://<DSS_HOST>/admin/general/security-other/`), next to the public webapp whitelist. An instance admin types both the `PROJECTKEY.webappId` and the slug. DSS does not check that the slug is unique when saving.

How to read an entry in the UI: open the webapp. The browser URL is `/projects/PROJECTKEY/webapps/webappId_<name>/view`, and the characters before the underscore are the webapp ID.

Vanity URL: `https://<DSS_HOST>/public-webapps/<slug>`. Most webapps have none. The webapp name is a display label and is in no URL, so the same name in several projects is not a conflict. A duplicate is only the same slug mapped to more than one key.

### How we found it (dead ends included)

1. Search top-level keys of `get_general_settings().get_raw()` for "vanity": nothing. (The match is a nested key.)
2. Search the JSON value of each top-level key: `webAppSecuritySettings` matched, but only the whitelist part was printed (truncated at 500 characters), so it looked like it held webapp IDs only.
3. `webappsSettings`: empty.
4. Every project's `get_settings().get_raw()` searched for "vanity": nothing.
5. Every webapp via `project.list_web_apps()` and `get_settings()`: nothing, inside a bare `except: pass`.
6. A shell command for the SSM session reading `config/general-settings.json`: rejected, the work had to stay in a notebook.
7. `os.walk` over the DSS config directory from the notebook, grepping for two of the slugs: nothing.
8. `requests.get(f"{client.host}/admin/vanity-urls", headers={"Authorization": f"Bearer {client.api_key}"})` and `/public/api/admin/general-settings/vanity-urls`: rejected before running. We have the API client, so guessed REST endpoints are out.
9. `dir(client)` filtered for "vanity" or "url".
10. `raw = json.dumps(settings); raw.find(keyword)` for each slug and for `vanityURL`: found `webAppSecuritySettings.vanityURLs`.

## Triage (notebook)

The cells are in `notebook_vanity_report.py`.

* Grouping entries by `value` gives the duplicates. The first output was plain text with `print`, too wide and wrapped. Then came `to_string`, and then a DataFrame rendered as the cell's last expression, with `reset_index(drop=True)` after the sort (the index numbers were out of order) and `pd.set_option('display.max_colwidth', 35)` / `display.width` so the duplicate flag was visible without scrolling.
* Enriching via `project.list_web_apps()` built a lookup that never matched: every field came out `?`. Calling `project.get_webapp(webapp_id)` directly, with the ID split from the key, worked and gave name, `creationTag` (created, creator), `versionTag` (last modified, by whom) and `get_state().running`.
* Which entry is the stale one: the criteria discussed were running state, last modified, v1 versus v2, same project versus different projects. A `recommend()` function was drafted (if exactly one is running, keep it, otherwise keep the newest created and mark the rest REMOVE). It was not adopted, because only the webapp owner knows which one should keep the slug, so the report gives owners the facts instead of a verdict.
* Project display names: `project.get_metadata()["label"]` returns the UI name for a key (an upper-case key versus its mixed-case label). Adding a `PROJECT_NAME` column needed a schema change, so instead we construct a direct link to the webapp, which is more useful anyway. `/projects/{KEY}/webapps/{ID}/view` does not work, but `/projects/{KEY}/webapps/{ID}_{re.sub(r'[^a-z0-9]', '', name.lower())}/view` does (tested on several webapps).

## Pipeline

### Step 1: collect

Python recipe writing `VANITY_URL_AUDIT`, one row per vanity entry per run: `CHECK_TIMESTAMP`, `VANITY_SLUG`, `WEBAPP_KEY`, `PROJECT_KEY`, `WEBAPP_ID`, `WEBAPP_NAME`, `CREATED_AT`, `LAST_MODIFIED_AT`, `CREATOR`, `LAST_MODIFIED_BY`, `IS_RUNNING`, `IS_DUPLICATE`. Collection and alerting were split on purpose: the table keeps the history of the mapping, useful when debugging the next routing problem. An earlier single-step version raised an exception straight from the check. This split replaced it.

Getting it to write:

* The first draft wrote ISO strings, and the DDL draft used `TIMESTAMP_TZ` with tz-aware `pd.Timestamp(..., tz='UTC')`. Both rejected: timezone-aware types were unnecessary complexity (everything is UTC) and string timestamps had already broken the downgrade log (see [inactive-user-downgrade.md](inactive-user-downgrade.md)). Final: `TIMESTAMP_NTZ` and naive `pd.Timestamp(ms, unit='ms')`, `CHECK_TIMESTAMP = pd.Timestamp.utcnow()`, `None` for missing values.
* The table was created by hand in `<DATABASE>.<SCHEMA>` next to the other platform maintenance tables. The first run failed:

  ```
  SnowflakeSQLException: SQL access control error: Insufficient privileges to operate on table 'PLATFORM_MAINTENANCE_VANITY_URL_AUDIT'. Your primary role <DSS_SNOWFLAKE_ROLE> must have OWNERSHIP granted on TABLE <DATABASE>.<SCHEMA>.PLATFORM_MAINTENANCE_VANITY_URL_AUDIT.
  ```

* `GRANT OWNERSHIP ... TO ROLE <DSS_SNOWFLAKE_ROLE> COPY CURRENT GRANTS` failed with `SQL execution error: Not authorized in a managed access schema. Use the schema owner role or a role with the MANAGE GRANTS privilege.`
* Suggested next: run the grant as SYSADMIN or ACCOUNTADMIN, or drop the table and let DSS create it. The table was dropped, and DSS did not recreate it (the dataset's Advanced settings show no "create if missing" option, and rebuild behaviour is Normal / Explicit / Write-protected, plus "Prevent data drop in append mode"). Clearing the dataset was also suggested. The last suggestions were `GRANT CREATE TABLE ON SCHEMA ... TO ROLE <DSS_SNOWFLAKE_ROLE>` or creating the table while using the DSS role (`USE ROLE <DSS_SNOWFLAKE_ROLE>; CREATE TABLE ...`).
* Not recorded which of these finally worked. The pipeline ran afterwards, so one of them did.

The dataset is append (each run adds a snapshot). That caused the first alert bug in step 2.

### Step 2: check and alert

Reads `VANITY_URL_AUDIT`, keeps only `CHECK_TIMESTAMP == max`, and if any `IS_DUPLICATE`:

* builds an HTML report:
  * a red CRITICAL header if any duplicate is running, amber WARNING otherwise
  * one table with slug, project, webapp ID, webapp name linked to the webapp, running, creator
  * one "How to fix" list naming the slugs
  * a footer note and links to the settings page and the scenario
* collects recipients from `CREATOR` and `LAST_MODIFIED_BY` where the value has an `@` and a dot in the domain (this drops the `admin` login, which made sends fail)
* stores both with `Scenario().set_scenario_variables(duplicate_alert_html=..., duplicate_recipients=...)`
* prints one short line and calls `sys.exit(1)`

At the top it first sets fallback values for both variables (a short "the check failed before it could run, see the scenario logs" message and the platform team address), so the mail is not empty when the scenario fails for another reason, for example step 1 failing because Snowflake is down. If step 1 fails, step 2 never runs. A separate always-run first step with the same fallback was suggested for that case.

How the alert evolved (details of the reporter problems in [scenario-reporters.md](scenario-reporters.md)):

1. `raise Exception(...)` with the list of slugs, mail reporter "on failure" with `${allEventsSummary}` in the body. It worked, but every duplicate appeared many times: the check read the whole appended history. Fixed by filtering to the latest `CHECK_TIMESTAMP`.
2. The mail was one unreadable block (line breaks stripped). Switched to HTML built in Python and passed through `${duplicate_alert_html}`, To `${duplicate_recipients}`.
3. The HTML showed as raw tags until "Send as HTML" was ticked.
4. The body still had the traceback, the escaped HTML from `print(message)` and step logs around it. `print(message)` removed and `raise` replaced with `sys.exit(1)` (fails the step, no traceback).
5. Still noisy: the reporter template had `${allEventsSummary}`, `${duplicate_recipients}` and hand-written notes around the variable. Template reduced to the single line `${duplicate_alert_html}`, and the notes moved into the Python HTML.
6. FreeMarker wrapped the value in quotes and showed literal `\n` sequences. `${duplicate_alert_html?no_esc}` failed with `?no_esc can't be used here, as the current output format isn't a markup (escaping) format`. Switching Template type to Simple fixed it.
7. Readers did not know what to change, so a short explanation of what a vanity URL is and explicit per-slug instructions (remove the stale entry, or rename its slug with `-old` or `-v2`) were added. That became repetitive and was consolidated to one table and one instruction list.
8. Project key column, then the constructed webapp link, then a separate webapp ID column (the code people match against the settings page).
9. Inline imports moved to the top of the script.

### Step 3: cleanup

SQL step on the Snowflake connection, run condition "Run always" so it runs after step 2 has failed on purpose:

```sql
DELETE FROM <DATABASE>.<SCHEMA>.PLATFORM_MAINTENANCE_VANITY_URL_AUDIT
WHERE CHECK_TIMESTAMP < DATEADD(MONTH, -6, CURRENT_TIMESTAMP())
```

A Python version with `dataiku.sql.SQLExecutor2(...).exec_recipe_fragment(...)` and a row count via `query_to_df` was drafted first. The SQL step is simpler.

### Reporter

| Setting | Value |
|---|---|
| Channel | the SMTP relay mail channel |
| Run condition | `outcome != 'SUCCESS'` |
| Send as HTML | ticked |
| Template type | Simple |
| To | `${duplicate_recipients}` |
| Cc | platform team address |
| Subject | `ALERT: Duplicate Vanity URLs Detected - ${scenarioName}: ${outcome}` |
| Message | `${duplicate_alert_html}` only |

Some recipients still failed until their mail domains were added to the mail channel's authorised domains (see [scenario-reporters.md](scenario-reporters.md)). The "notification email" field in user settings is the address DSS uses for platform notifications to that user. It is unrelated to the reporter.

Dynamic recipients mean the webapp owners get the mail directly. Before that the alert went only to the platform team, who forwarded it.

## Open questions

* Which ownership fix made the table writable (above).
* The remaining duplicates were left for the owners. Whether all were fixed is not recorded.
* A separate housekeeping check for the same webapp name across projects was proposed and not built.
