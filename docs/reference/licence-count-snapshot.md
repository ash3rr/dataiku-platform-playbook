# Licence count snapshot

Script: [`scripts/licensing/licence_count_snapshot.py`](../../scripts/licensing/licence_count_snapshot.py), with [`licence_trending_table.sql`](../../scripts/licensing/licence_trending_table.sql) and the Teams pieces in [`scripts/licensing/teams/`](../../scripts/licensing/teams/). Diagnostics used along the way are in [`user_profile_diagnostics.py`](../../scripts/licensing/user_profile_diagnostics.py).

It runs as step 1 of the scenario `PLATFORM_MAINTENANCE.CHECK_NUMBER_OF_LICENCE_HOLDERS`. It counts enabled users per licence profile, writes the counts into project variables (read by the Teams message step), and appends one row per day to `platform_maintenance_license_trending` (Snowflake).

## Current behaviour

* Non-trial seat: `enabled` and `trialStatus.exists == False`.
* Valid trial: `enabled` and `trialStatus.valid == True`.
* Expired trial (`exists` True, `valid` False): in neither bucket. The licence page does not count them, so neither do we.
* `READER` and `AI_CONSUMER` are summed into `ai_consumer_and_reader_count` (and the trial equivalent). READER is the legacy API name. The licence page shows both under AI Consumer.
* `TECHNICAL_ACCOUNT` is tracked on its own (it has its own quota).
* `threshold_reached = full_designer_count > CONTRACTED_FULL_DESIGNERS`. That constant is the alert threshold agreed for FULL_DESIGNER, which can be lower than the number of seats on the licence. Set it at the top of the script. `status_message` is "Threshold exceeded!" or "Within limit".
* Variables set: `full_designer_count`, `technical_account_count`, `trial_full_designer_count`, `trial_technical_account_count`, `total_non_trial_users`, `total_trial_users`, `threshold_reached`, `status_message`, `ai_consumer_and_reader_count`, `trial_ai_consumer_and_reader_count`.
* Trending row: `date` (ISO string from `date.today().isoformat()`, chosen to avoid timezone handling in schema inference), then the counts in the same order as the Snowflake table. Existing rows are read in a `try/except` (the dataset does not exist on the first run), concatenated, deduplicated on `date` with `keep="last"` so a second run on the same day replaces the first, and written with `write_with_schema`.

This dataset is not append-mode. The recipe rewrites the whole history each day. That is the opposite of [inactive-user-downgrade.md](inactive-user-downgrade.md), where append mode is on and read + concat broke things. Both work, but do not mix the two on one dataset.

## History

### Original threshold check

The first version (`threshold_check.py`) only had the valid flag:

```python
non_trial_profiles = [user.get('userProfile', 'UNKNOWN') for user in users
                      if user.get('enabled') and not user.get('trialStatus', {}).get('valid', False)]
trial_profiles     = [user.get('userProfile', 'UNKNOWN') for user in users
                      if user.get('enabled') and user.get('trialStatus', {}).get('valid', False)]
```

It set `full_designer_count`, `trial_full_designer_count`, `total_non_trial_users`, `total_trial_users`, `threshold_reached` (above the threshold) and `status_message` ("Threshold exceeded!" / "within limit", lower case at the time), and called `dataiku.api_client()` a second time for `get_project`. A review called it valid as written and suggested only optional extras (create `variables["standard"]` if missing, print the counts). It was valid Python but not correct: expired trials landed in the non-trial bucket, which is what the next round of work found.

### The count did not match the licence page

The scenario log showed:

```
Updated variables: {'full_designer_count': <n>, 'trial_full_designer_count': <n>, 'total_non_trial_users': <n>, 'total_trial_users': <n>, 'threshold_reached': True, 'status_message': 'Threshold exceeded!'}
```

The licence page showed far fewer FULL_DESIGNER seats than the script, and a different trial count. What we tried, in order:

1. Group override theory. The idea was that `userProfile` is the stored profile and a group could lower the effective one. We printed each FULL_DESIGNER's groups. Not the cause (and later we confirmed no group carries a profile at all). The same step suggested calling `/api/admin/licensing/status`, which was never called.
2. Backend log. The DSS backend log was full of `has a trial token, but expired` warnings. That pointed at expired trials.
3. A `token` field. First fix attempt: `and not user.get('trialStatus', {}).get('token')`. A diagnostic printed `Expired trial FULL_DESIGNERs excluded: 0`. There is no `token` key in `trialStatus`.
4. `not user.get('trialStatus')`. Second attempt, assuming non-trial users have an empty or missing `trialStatus`. `full_designer_count` went to 0, and the diagnostic "users with any trialStatus" printed the number of enabled users, i.e. every one of them. The same diagnostic block also had a typo (`user.get` instead of `u.get` in a generator), which raised on the last line.
5. Print the real structure. Printing `trialStatus` for ten enabled FULL_DESIGNERs showed it is always populated:
   * normal user `{'exists': False, 'expired': False, 'valid': False, ...}`
   * valid trial `{'exists': True, 'expired': False, 'valid': True, ...}`
   * expired trial `{'exists': True, 'expired': True, 'valid': False, ...}`

The fix: non-trial is `not u.get('trialStatus', {}).get('exists', False)`, valid trial stays `valid`. Result: `full_designer_count` matched the page, and the `FULL_DESIGNER (expired trial, excluded)` count was exactly the gap. That diagnostic line stayed in the script.

The page showed two more trials than the script. That difference was not chased further.

### Trending output

The recipe was given an output dataset, `platform_maintenance_license_trending`, but the generated code left the dataframe as a placeholder (`platform_maintenance_license_trending_df = ...`), which fails at the write. The intent was a dated row per day for trending. Changes:

* `today_row` with `date`, `full_designer_count`, `trial_full_designer_count`, `total_non_trial_users`, `total_trial_users`, `threshold_reached`
* read existing in `try/except`, concat, `drop_duplicates(subset=["date"], keep="last")`, `write_with_schema`
* removed the unused `numpy` and `dataiku.pandasutils` imports and reused `client` for `get_project`

It then did nothing on the scheduled runs. The job log said:

```
compute_platform_maintenance_license_trending_NP: not required, pruning
```

A smart build compares the recipe and its inputs with the last run. This recipe's real input is "today", which DSS cannot see, so it is pruned every day until the code changes. The scenario build step was switched to a forced build.

### AI Consumer, Reader and Technical Account

The trending only had FULL_DESIGNER. Adding the other paid profiles took several rounds.

First pass: separate `ai_consumer_count`, `reader_count` and their trial equivalents. The licence page's AI Consumer figure was well above our `ai_consumer_count`. Things we checked:

| Check | Result |
|---|---|
| Disabled AI_CONSUMER users | Only one disabled. Not the gap. |
| Profile distribution (all users) | `TECHNICAL_ACCOUNT`, `NONE`, `FULL_DESIGNER`, `AI_CONSUMER`, `READER`. The counts summed to the number of users `list_users()` returned |
| Expired trials per profile | Many for FULL_DESIGNER and READER, only a couple for AI_CONSUMER |
| `resultingUserProfile` counts (enabled) | Same five profiles. `AI_CONSUMER` equalled our `ai_consumer_count` |
| Full JSON of one AI_CONSUMER user | an expired trial user with `userProfile AI_CONSUMER` and `resultingUserProfile NONE`, and no other licence-related fields |
| Group-level profiles (`list_groups()`) | no group has a `userProfile` field |

Before those checks two other theories were raised and ruled out. One was that `list_users()` does not return everyone (LDAP users not yet materialised, or pagination). The number of users the API returned equals the sum of the profile distribution above, and the Full Designer figure matched the page on the same day, so the API was returning everyone. The other was that the UI counts expired trials. The expired-trial counts in the table (only a couple for AI_CONSUMER) ruled that out for AI Consumer, even though it was briefly presented as the explanation.

At one point "the API and the UI just count differently, raise a ticket with Dataiku" was put forward as the answer. That was rejected: management needed the figure explained. Theories that went nowhere: AI Consumer as an additive entitlement on top of other profiles, and group-inherited entitlements.

The explanation came from the licence page itself, which lists only Full Designer, AI Consumer, Technical Account and four profiles with no seats (Advanced Analytics Designer, Data Designer, Governance Manager, AI Access User). READER has users but no quota line. READER is the legacy name for AI Consumer: enabled non-trial AI_CONSUMER plus READER equals the UI figure exactly. The script's buckets (non-trial by `exists`, which is what `resultingUserProfile` reflects) were right all along. The two profiles need adding together.

Final changes:

* merged into one count and renamed from `ai_consumer_count` to `ai_consumer_and_reader_count` so nobody wonders whether Reader is missing, and moved to the end of the column list and the variables dict
* added `technical_account_count` and `trial_technical_account_count`
* kept a comment in the code explaining the merge
* historical rows have NaN/NULL for the new columns (`write_with_schema` adds them)

An option to track expired trials as their own column, for licence hygiene, was raised and not added.

### Snowflake schema

The table behind the dataset is `PLATFORM_MAINTENANCE_PLATFORM_MAINTENANCE_LICENSE_TRENDING` (DSS prefixes the project key). The schema before the merge:

```sql
CREATE TABLE "PLATFORM_MAINTENANCE_PLATFORM_MAINTENANCE_LICENSE_TRENDING" (
    "date" varchar(16777216),
    "full_designer_count" bigint,
    "trial_full_designer_count" bigint,
    "trial_ai_consumer_count" bigint,
    "trial_reader_count" bigint,
    "total_non_trial_users" bigint,
    "total_trial_users" bigint,
    "ai_consumer_count" bigint,
    "reader_count" bigint
)
```

A history-preserving migration was written as two `ALTER TABLE` statements (Snowflake does not allow ADD and DROP in one ALTER): drop the four split columns, add the four new ones. We went with `CREATE OR REPLACE` instead, which wipes history. The first version of that DDL put `trial_full_designer_count` third, and the recipe failed:

```
Error in Python process: At line 83: <class 'Exception'>: An error occurred during dataset write (...): SQLException: Cannot write to table, table already exists but with an incompatible schema: Name mismatch for column 3 : 'technical_account_count' in dataset, 'trial_full_designer_count' in table Name mismatch for column 4 : 'trial_full_designer_count' in dataset, 'technical_account_count' in table
```

The table's column order has to match `today_row`. The committed DDL does.

### Trial seat allocations (not done)

The licence page has a "Fetch trial" button that returns the trial credit Dataiku gave us:

```json
{
  "canUseFlexUsers": true,
  "allocations": [
    {"userProfile": "DESIGNER", "mode": "TOTAL_CREDIT", "totalCredit": <n>, "remainingCredit": <n>, "alreadyGrantedForLogins": []},
    {"userProfile": "FULL_DESIGNER", "mode": "TOTAL_CREDIT", "totalCredit": <n>, "remainingCredit": <n>, "alreadyGrantedForLogins": []}
  ]
}
```

We wanted it in the trending table. The attempts:

* `client.get_licensing_status()` and `client._session.get(client._base_url + "/api/admin/licensing/trial")`: guesses, rejected before running. The developer docs for `DSSClient` have no licensing or trial method.
* Browser dev tools showed the button calls `GET https://<DSS_HOST>/dip/api/admin/get-trial-status`.
* `client._session.get(client._base_url + "/dip/api/admin/get-trial-status")`: `AttributeError: 'DSSClient' object has no attribute '_base_url'`.
* Same with `client.host`: the response was `Not logged-in`. The client session does not carry the browser session's auth for `/dip/api/` endpoints.
* `requests.get(..., auth=(client.api_key, ""))` was suggested and not pursued.

A DDL with four extra columns (`designer_trial_total_credit`, `designer_trial_remaining_credit`, `full_designer_trial_total_credit`, `full_designer_trial_remaining_credit`) was drafted and held back until the call worked. Left open: there is no supported way to read trial allocations from the Python client.

## Teams notification

The scenario has two steps: this Python recipe, then a Teams message step. The Teams side is a Power Automate flow that renders an Adaptive Card. Six pieces had to change together when the new counts went in:

1. the step 1 Python recipe (this script)
2. the trending part of the same script (new columns)
3. the Snowflake table (recreated)
4. the scenario variable initialisation JSON, [`scenario_variables_init.json`](../../scripts/licensing/teams/scenario_variables_init.json): an `attachments` list with one object holding every field with a 0/false/"" default
5. the message body sent to Teams, [`teams_message_body.json`](../../scripts/licensing/teams/teams_message_body.json): each field as `${variables.<name>}`, with `status_message` quoted
6. the Adaptive Card in the flow, [`adaptive_card.json`](../../scripts/licensing/teams/adaptive_card.json): version 1.4, a FactSet with one fact per field read via `@{triggerBody()?['<name>']}`, the status line coloured with `@{if(equals(triggerBody()?['threshold_reached'], true), 'attention', 'good')}`, and a "Manage Licences" `Action.OpenUrl` to `https://<DSS_HOST>/admin/`

After deploying, Teams still showed the old card (old fields only, with values that matched our numbers). The new variables do not exist until the scenario has run end to end once after the change. A manual run fixed it. The other thing to check in that situation is that the Power Automate flow was saved after pasting the new card JSON.

At the time, the scenario's step 1 Python code and the flow recipe that writes the trending dataset were separate copies of the same counting logic. The step 1 copy only sets project variables (no pandas, no dataset write). Both copies had to get the merged AI Consumer and Reader count and the Technical Account count, so check both when the counting rules change.

On the Teams side (looked at after the move from the old incoming webhooks to Workflows), messages posted as Flow bot show as "Flow bot via Workflows" and the sender name cannot be customised per flow, so several systems posting to one channel all look the same. The only way to get a meaningful sender name ("Dataiku Alerts") is a service account with a Teams licence that is a member of the channel, owns the Power Automate connection, and posts as User. Which option was finally used is not recorded.

More detail from that look. The trigger was that workflow posts had started showing the name of the person who published the workflow, which is wrong for a channel fed by an automated Dataiku script (it misattributes the alert and breaks when that person leaves). In the "Post card in a chat or channel" action the "Post as" choices are:

| Post as | Shows as | Problem |
|---|---|---|
| User | "<connection owner> via Workflows" | Ties the alert to a person |
| Flow bot | "Flow bot via Workflows" (sometimes just "Workflows") | Not customisable, identical for every system posting to the channel. Not available for private channels, which require User |
| Custom value (a dynamic value or expression) | | According to Microsoft's forum answers it does not let you set the channel's GroupId and ChannelId, so it does not solve this |

Microsoft's answer on its Q&A forum was "this is by design" and "currently there is no such way available" for posting without tying the workflow to a personal account. Background: the old Office 365 connectors and incoming webhooks, which could post anonymously with a custom name, are being retired in favour of Power Automate workflows, whose connections belong to user accounts. Reports at the time said the user name had been removed from the desktop client but still showed on mobile. Flow bot is acceptable where only one system posts to a channel. With several systems in one channel the service account is the way to tell them apart, at the cost of a Teams licence for that account.

## Open questions

* Trial allocation numbers: no supported API (above).
* Valid trial count from the script two lower than the page: not investigated.
* Whether to add expired-trial columns: raised, not done.
