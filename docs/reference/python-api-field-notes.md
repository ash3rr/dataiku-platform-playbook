# Dataiku Python API: field notes (DSS 14.x)

Behaviour confirmed against our DSS 14.5.1 design node (14.2.1 before the upgrade). Where the public docs were vague, missing or wrong, this file records what happened. Items marked unconfirmed were written or suggested but never verified on the instance.

Per-script history lives in the other files in this folder: [inactive-user-downgrade.md](inactive-user-downgrade.md), [licence-count-snapshot.md](licence-count-snapshot.md), [licence-reporting.md](licence-reporting.md), [vanity-url-audit.md](vanity-url-audit.md), [scenario-reporters.md](scenario-reporters.md), [email-from-python.md](email-from-python.md), [code-env-container-images.md](code-env-container-images.md), [code-studio-template-rebuild.md](code-studio-template-rebuild.md), [dataset-inventory.md](dataset-inventory.md), [offboarding-scan.md](offboarding-scan.md), [workshop-provisioning.md](workshop-provisioning.md).

## The rule: internal client only

Everything runs inside DSS (notebook, Python recipe, scenario "Execute Python code" step):

```python
import dataiku
client = dataiku.api_client()
```

That client acts as the run-as user (the notebook user, or the scenario's run-as user) with their permissions, and needs no host or key. Most of our scripts need instance admin because they read every project or change users.

When a method's existence is in doubt, check the object instead of guessing:

```python
print([m for m in dir(client) if not m.startswith('_')])
print([m for m in dir(client) if 'vanity' in m.lower() or 'url' in m.lower()])
help(env.update_images)
```

This found `update_images`, `MessageSender`, `DSSProjectFolder.get_settings` and the Code Studio list item attributes when guesses had failed.

### Rejected approaches, and why

Every one of these was proposed at some point and turned down. Do not reintroduce them.

| Approach | Where it came up | Why rejected |
|---|---|---|
| `dataikuapi.DSSClient("https://<DSS_HOST>", "<api key>")` | First licence script, offboarding first draft, code env verification | We run inside DSS. The internal client needs no URL or key, and handling an API key in a notebook is a needless secret. |
| `requests.get(f"{client.host}/admin/vanity-urls", headers={"Authorization": f"Bearer {client.api_key}"})` and `/public/api/admin/general-settings/vanity-urls` | Finding vanity URLs | Hand-built HTTP to guessed endpoints. The data was in `get_general_settings()` all along. |
| `client.perform_api_call("POST", "/messaging/send", body={...})` | Sending mail from Python | Calls the public REST API with a guessed endpoint and payload. Rejected as "external API". |
| `client._perform_json("GET", "/admin/messaging-channels")` | Finding the mail channel id | Same: private method hitting a guessed REST path. Rejected. |
| `client._perform_json("GET", "/admin/audit/query", params={...})` | Who created a user | Guessed path, superseded by reading the audit log on the host. |
| `client._session.get(client._base_url + "/dip/api/admin/get-trial-status")` | Trial seat allocation | `DSSClient` has no `_base_url` (AttributeError). With `client.host` instead, the call returned `Not logged-in`: the client session does not carry the browser's auth for `/dip/api/` endpoints. |
| `requests.get(".../dip/api/admin/get-trial-status", auth=(client.api_key, ""))` | Same | Suggested after `Not logged-in` and not pursued (external call with a key). The trial allocation numbers were never pulled programmatically. |
| `requests.Session()` with `X-DKU-APITicket` from `DKU_API_TICKET` against `127.0.0.1:$DKU_BACKEND_PORT/dip/api/admin/audit/list` | Who created a user | Overcomplicated, guessed path. Rejected. |
| `client.get_root().send_mail(...)`, `client.get_messaging_channel(...).send_mail(...)`, `dataiku.api_client().get_internal_client().send_mail(...)` | Sending mail | Invented method names. None exist as far as we found. |
| `smtplib` straight to the relay | Sending mail | The person running it wanted the already configured DSS channel used first. Not tried. |
| `client.get_licensing_status()`, `client._session.get(client._base_url + "/api/admin/licensing/trial")` | Trial seats | Guesses, rejected before running ("I want documented endpoints"). |
| `/api/admin/licensing/status` | Licence counts matching the UI | Suggested, never called. The mismatch turned out to be trial logic. |
| A shell script on the host via SSM reading `config/general-settings.json` | Finding vanity URLs | The work had to stay in a notebook with the API client. |
| `user.get_definition()["lastSuccessfulLogin"]` / `["createdOn"]`, `userProfile == 'DESIGNER'`, `definition['enabled'] = False` + `set_definition()`, JSON report to local disk | First licence draft | Fields do not exist, profile name wrong, disabling is done by profile NONE, and files on the DSS host are unreachable. See [inactive-user-downgrade.md](inactive-user-downgrade.md). |
| Group-level licence profile override | Licence count mismatch | No group carries a `userProfile`. The gap was expired trials and READER being AI Consumer. |
| `trialStatus.get('token')` | Excluding expired trials | There is no `token` key. |
| `not user.get('trialStatus')` | Excluding expired trials | `trialStatus` is always a populated dict, so every user was excluded and the count went to 0. |
| Fuzzy login matching on login, displayName, email | Offboarding scan | Can catch the wrong person and needs user-read rights. Use the exact login. |
| `project.get_metadata()["owner"]` | Offboarding | Always empty. Use `get_permissions()["owner"]` or `list_projects()["ownerLogin"]`. |
| `env.build_for_container()` / `build_for_containers()`, `env.get_status()`, `get_definition()["status"]` | Code env image builds | Do not exist. The method is `update_images()`. |
| `future.get_state() in ['DONE', ...]` | Polling builds | `get_state()` returns a dict, so the loop never stops until "No future by that id". Poll `peek_state()`. |
| `metrics.get_global_value("reporting:BUILD_START_DATE")`, `dataset.get_status()` | Dataset freshness | First returns nothing, second does not exist. Use `versionTag.lastModifiedOn`. |
| CSV to `/tmp` or `/home/dataiku` from a notebook, `dataiku.Folder(...)` from a notebook | Exporting results | Unreachable from the UI / no managed folder in a notebook. Write to datasets. |
| `client.get_audit_trail()` | Who created a user | Does not exist. |
| `client.get_log("audit/audit.log")` on a 100 MB file | Who created a user | Not tried. Grep on the host instead. |

The same reasoning applies to `client._session` in general: it is the internal transport of `dataikuapi`, not a supported way to reach UI-only endpoints.

## Users

* `client.list_users()` returns a list of dicts. Keys seen on 14.5.1: `login`, `displayName`, `email`, `sourceType` (e.g. `LDAP`), `groups` (list of names), `userProfile`, `resultingUserProfile`, `enabled`, `creationDate` (Unix ms), `trialStatus`, `activeWebSocketSesssions` (sic), `npsSurveySettings`, `atSurveySettings`, `objectImgHash`.
* Logins on this instance are full email addresses. The display name can differ from the login (a display name of one spelling and a login of another). Always copy the login from Administration > Security > Users.
* `trialStatus` is ALWAYS a populated dict, never empty or missing:
  * normal user `{'exists': False, 'expired': False, 'valid': False, ...}`
  * valid trial `{'exists': True, 'expired': False, 'valid': True, ...}`
  * expired trial `{'exists': True, 'expired': True, 'illegal': False, 'valid': False, 'grantedOn': <ms>, 'expiresOn': <ms>, 'invalidReason': 'Expired (on <ISO timestamp>)'}`
  * There is no `token` key. `not u.get('trialStatus')` is False for every user.
  * Non-trial seat = `trialStatus.exists == False`. Valid trial = `trialStatus.valid == True`. Expired trials are neither, and the licence page does not count them.
* `resultingUserProfile` is the effective profile. An expired trial user with `userProfile: AI_CONSUMER` showed `resultingUserProfile: NONE`.
* `READER` is the legacy API name for AI Consumer. The licence page shows AI_CONSUMER + READER together under "AI Consumer".
* Profiles with seats on our licence: FULL_DESIGNER, AI Consumer, TECHNICAL_ACCOUNT. Advanced Analytics Designer, Data Designer, Governance Manager and AI Access User had no seats.
* Last login / activity: `client.get_user(login).get_activity()` with attributes `.last_successful_login` and `.last_session_activity`. Both are timezone-aware datetimes or `None`. Treat `None` as "never".
* `user.get_definition()` does not carry `lastSuccessfulLogin` or `createdOn` (those were guesses in the first draft). The profile name is `FULL_DESIGNER`, not `DESIGNER`.
* Change a profile: `s = client.get_user(login).get_settings(); s.get_raw()["userProfile"] = "READER"; s.save()`. Setting `"NONE"` removes the user's licence, and our downgrade does this on purpose.
* Create an LDAP user: `client.create_user(login, password=None, display_name=..., source_type='LDAP', groups=[...], profile=..., email=login)`, then adjust through `get_settings().save()` rather than `set_definition()`. `client.get_user(login).get_info()` raises if the user does not exist, which makes it a usable existence check.

## Groups

* `client.list_groups()` returns plain dicts with at least `name`, `admin`, `description`, `sourceType`. `json.dumps(g, indent=2)` prints them directly.
* No group had a `userProfile` field: licence profiles are not assigned through groups here.
* Unconfirmed: whether group dicts include a `users` list (a running-total example used `len(group.get("users", []))` and was never run).

## Projects, ownership, permissions

* `project.get_permissions()` returns `{"owner": "<login>", "permissions": [{"user": ..., "<flag>": bool}, {"group": ..., ...}]}`. Write back with `project.set_permissions(perms)`.
* `client.list_projects()` entries carry `ownerLogin` (and `projectKey`, `name`). On the project we checked it matched `get_permissions()["owner"]`.
* `project.get_metadata().get("owner")` is empty: metadata does not hold the owner. `project.get_settings().get_raw().get("owner")` was `None`.
* The "+ Add project_owner" pill in a project's About panel is a custom metadata field, not the DSS owner.
* `project.get_metadata()` does hold `label` (display name: an upper-case key such as `PROJECTKEY` versus a mixed-case label) and `shortDesc`, `tags`. `set_metadata()` writes them.
* Permission flags we set: `admin`, `editPermissions` (may change who has access), `readProjectContent`, `writeProjectContent`, `exportDatasetsData`, `readDashboards`, `writeDashboards`, `moderateDashboards`, `publishToDataCollections`, `shareToWorkspaces`, `runScenarios`, `manageDashboardAuthorizations`, `manageExposedElements`, `manageAdditionalDashboardUsers`, `executeApp`, `manageAuthorizedObjects`, `manageSharedObjects`.
* Permissions are additive across the user entry and all of the user's groups.
* User-level entries are shown in the UI under Project > Security, with the groups and the owner. (For a while we believed the UI only showed groups. That was wrong.)
* Each project has one owner login. Deleting that user leaves a dangling owner that needs reassigning (Project > Security > Project Owner). Scenarios have a separate "run as" user that also breaks when the account goes.
* `project.duplicate(target_project_key=..., target_project_name=...)` copies a template, including the template's group permissions (a `data_team` group came along).
* `project.move_to_folder(client.get_project_folder(folder_id))` moves a project into a folder.

## Project folders

* `DSSProjectFolder` has no `get_permissions()` and no `get_definition()` (both raised AttributeError). Methods: `client`, `create_project`, `create_sub_folder`, `delete`, `get_default_folder_for_project_creation`, `get_name`, `get_parent`, `get_path`, `get_settings`, `id`, `list_child_folders`, `list_project_keys`, `list_projects`, `move_project_to`, `move_to`, `name`, `project_folder_id`.
* `folder.get_settings()` returns `dataikuapi.dss.projectfolder.DSSProjectFolderSettings`: `get_name`, `get_owner`, `get_permissions`, `get_raw`, `save`, `set_name`, `set_owner`.
* `settings.get_permissions()` returns a flat list, e.g. `[{'group': 'workshop_demo', 'read': False, 'writeContents': False, 'admin': False}]`. Mutate the list and call `settings.save()`.

## Datasets

* `dataset.get_definition()` is deprecated on 14.5.1 (`DeprecationWarning: Dataset.get_definition is deprecated, please use get_settings`). Use `get_settings().get_raw()`.
* Top-level keys of the raw settings: `type`, `managed`, `featureGroup`, `name`, `smartName`, `projectKey`, `checklists`, `checks`, `customMeta`, `flowOptions`, `readWriteOptions`, `partitioning`, `versionTag`, `creationTag`, `tags`, `params` (incl. `connection`), `schema`, `typeSystemVersion`, `dkuProperties`, `metrics`, `metricsChecks`, `customFields`.
* `versionTag` and `creationTag` exist on every dataset: `{'versionNumber': 6, 'lastModifiedBy': {'login': ...}, 'lastModifiedOn': <ms>, 'extendedProperties': {}}`.
* `get_last_metric_values().get_global_value("reporting:BUILD_START_DATE")` returned nothing. Walk `get_last_metric_values().get_raw()["metrics"]`, matching `m["metric"]["metricType"]` (`BUILD_START_DATE`, `COUNT_RECORDS`) and reading `m["lastValues"][0]["value"]`. These metrics only exist where metrics were computed (build with "compute metrics", a manual Compute, or a scenario step), which was a minority of the datasets on the instance.
* `DSSDataset` has no `get_status()`.
* `dataset.get_usages()` returns usages outside the flow, e.g. `{'type': 'RECIPE_INPUT', 'objectProjectKey': ...}` / `RECIPE_OUTPUT` for cross-project use.
* `dataset.clear()` exists (suggested to empty a dataset). `get_location_info()` returns `info.connectionName` (used in a drafted SQLExecutor2 cleanup).
* Notebooks are not in the flow: no managed folder input/output, and files written to `/tmp` land on the DSS host where nobody can download them. Write results to datasets created beforehand, then Explore > Export.

## Flow graph

`project.get_flow().get_graph()`: use `graph.nodes` (fall back to `graph.data["nodes"]` on other versions). Recipe nodes have `type == "RUNNABLE_RECIPE"` (not `RECIPE`), recipe name in `ref`, inputs in `predecessors`, outputs in `successors`. With `RECIPE` / `inputs` / `outputs` every dataset looked orphaned.

## Recipes

* `recipe.get_settings()` returns a type-specific object: `PrepareRecipeSettings` has no `get_raw()` (AttributeError).
* `recipe.get_definition_and_payload().get_recipe()` works for all recipe types. We read `params.executionParams` for container overrides. It printed nothing on a project where every recipe inherited. Unconfirmed that this is the right key (no positive control).
* Containerised recipes can only touch datasets declared in the recipe's Inputs/Outputs. A dataset read or written in code but not declared works locally and fails in the pod. Swapping an input means changing both the `dataiku.Dataset("...")` string and the Inputs/Outputs tab.
* Moving an uploaded CSV into Snowflake for a recipe: a Sync recipe (in the flow, re-runnable) rather than "Export to dataset" (one-off side action).

## Webapps and vanity URLs

* Vanity URL mapping: `client.get_general_settings().get_raw()["webAppSecuritySettings"]["vanityURLs"]`, a list of `{"secret": False, "key": "PROJECTKEY.webappId", "value": "<slug>"}`.
* The same block holds `requiredAuthenticationMode` (ours `ALL_BUT_ADMIN_WHITELIST`) and `requiredAuthenticationWhitelist` (list of `PROJECTKEY.webappId` allowed without login).
* Search tip that found it: `json.dumps(settings).find("vanityURL")`. Searching only the top-level key names found nothing, and `webappsSettings` was empty.
* `project.get_webapp(webapp_id).get_settings().get_raw()` has `name`, `type`, `creationTag`, `versionTag`, `params`. `.get_state().running` gives the running state. Looking webapps up via `project.list_web_apps()` across all projects gave no matches (blanket `except: pass` hid the failures), while direct `get_webapp(id)` worked.
* Normal webapp URL, confirmed by testing: `/projects/{PROJECT_KEY}/webapps/{WEBAPP_ID}_{re.sub(r'[^a-z0-9]', '', name.lower())}/view`. Without the `_<name>` suffix the link does not work.
* Vanity URL: `/public-webapps/{slug}`. DSS does not validate slug uniqueness. Two running webapps on one slug broke routing instance-wide.
* Code Studio webapp config on disk lives at `config/projects/<KEY>/web_apps/<id>.json` (`type: CODE_STUDIO_AS_WEBAPP`, `params.codeStudioId`, `exposedPort`, `infra.containerSelection`).

## General settings

* `client.get_general_settings().get_raw()` top-level keys on 14.5.1 include `mailSettings`, `notifications`, `webAppSecuritySettings`, `webappsSettings`, `containerSettings`, `codeEnvs`, `licensingSettings`, `ldapSettings`, `ssoSettings`, `security`, `impersonation`, `limits`, `dssPortRange`, `studioExternalUrl`, `welcomeEmailSettings`, `virtualWebAppBackendSettings` and many more.
* `mailSettings` came back `{}` even though an SMTP channel was configured in the UI, and `notifications` only had `notifyOnUserConnection: NOBODY`, `notifyOnTaskStartAndStop: INITIATOR`. Messaging channels are not in general settings.

## Connections

`client.get_connection(name).get_definition()["params"]` gives `db`, `schema`, `account` for a Snowflake connection.

## Code envs

* `client.list_code_envs()` gives dicts with `envName`, `envLang`.
* `client.get_code_env("python", name)`. `get_definition()` has `desc.containerConfs`, `desc.allContainerConfs`, `desc.dockerImageResources` (`INIT` = container image never built), `specPackageList`, `actualPackageList`. There is no `status` key (KeyError).
* `DSSCodeEnv` methods: `delete`, `get_definition`, `get_log`, `get_settings`, `get_version_for_project`, `list_logs`, `list_usages`, `set_definition`, `set_jupyter_support`, `update_images`, `update_packages`. No `get_status`, no `build_for_containers`.
* `update_images(env_version=None, wait=True)` builds one image per container config the env is scoped to. `wait=False` returns a `DSSFuture`. Result dict: `messages` (with `success`, `warning`, `error`, `fatal`, `anyMessage`, `messages`), `logFileName: rebuildImage.log`, `imagesBuilt` (list of `<repo>:r-<timestamp>`). `success` was False with no messages even when all images had been pushed.
* `list_logs()` returns dicts (`name`, `totalSize`, `lastModified`, `linesShown`, `tailSize`, `truncated`) and `get_log(name)` returns the text. Seen: `createPythonEnv.log`, `updateEnvAccordingToSpec.log`, `Dockerfile.dump`, `rebuildImage.log`.
* A code env with container configs but `INIT` image makes recipes run locally without an error, regardless of the project's container setting.

## Futures

* `DSSFuture` methods: `abort`, `client`, `from_resp`, `get_result`, `get_result_wait_if_needed`, `get_state`, `has_result`, `job_id`, `peek_state`, `result_wrapper`, `state`, `state_is_peek`, `wait_for_result`.
* `peek_state()` does not block and returns a dict (`alive`, `hasResult`, `runningTime` in ms, log progress). Finished = `alive: False, hasResult: True`.
* `get_state()` also returns a dict. `if future.get_state() in ['DONE', 'FAILED', 'ABORTED']` never matches, so that loop runs until the server drops the future and the call raises `DataikuException: java.lang.IllegalArgumentException: No future by that id`. That exception after completion means "already finished and garbage-collected", not failure.
* `wait_for_result()` returns the result dict and does NOT raise when the underlying build failed (Code Studio templates). Check the `success` flag.
* Builds started from a notebook keep running server side if the kernel is restarted.

## Code Studio templates

* `client.list_code_studio_templates()` returns `DSSCodeStudioTemplateListItem` objects (not subscriptable). Attributes: `build_for_configs`, `client`, `id`, `label`, `last_built` (tz-aware datetime or None), `to_code_studio_template`.
* `client.get_code_studio_template(id).build()` returns a future, and the docs give `build(disable_docker_cache=True)`. It builds for every target in the template's "Build for" setting. An EKS (ECR) image is not usable by local Docker execution.
* Settings keys: `id`, `type`, `label`, `tags`, `versionTag`, `creationTag`, `isEditor`, `reattachOnRestart`, `owner`, `defaultPermission`, `permissions`, `defaultContainerConf`, `allowContainerConfOverride`, `allContainerConfs`, `containerConfs`, `inheritGlobalCodeEnvsExtraSettings`, `codeEnvsExtraSettings`, `params` (`params.blocks`). `versionTag.lastModifiedOn` is the last definition edit, not the last build.

## Scenarios and variables

* Project variables: `v = project.get_variables(); v["standard"][...] = ...; project.set_variables(v)`. `client.get_project(dataiku.default_project_key())` gets the current project. Reuse the same `client` rather than calling `dataiku.api_client()` twice.
* Scenario variables from a Python step: `from dataiku.scenario import Scenario; Scenario().set_scenario_variables(name=value, ...)`. Read in reporters as `${name}`, in a Teams/webhook message body as `${variables.name}` (project variables).
* `sys.exit(1)` in a scenario Python step fails the step without a traceback in the log. `raise Exception(...)` also fails it, but the traceback ends up in `${allEventsSummary}` and in mails that include it.
* Anything a scenario step prints ends up in the scenario log and in `${allEventsSummary}`. Keep prints short in steps that feed a mail.
* Scenario step Run condition "Run always" makes a cleanup step run after a deliberate failure.
* `Scenario().send_message(sender=..., channel_id=..., subject=..., message=..., recipient=...)` was drafted with a placeholder channel id and never run. Unconfirmed.
* Reporter run condition syntax: bare expression, single quotes, no `${}` and no `if`: `outcome != 'SUCCESS'` (this also catches `ABORTED`, which `outcome == 'FAILED'` would not). Details in [scenario-reporters.md](scenario-reporters.md).
* In a recipe (not a scenario step), `raise SystemExit()` marks the job FAILED. Use if/else for a dry run.

## Build behaviour

A smart build compares the recipe hash with the last run. A recipe whose real input is "today" (daily snapshots, API pulls) never looks changed, so the job log says `compute_<dataset>_NP: not required, pruning` every day forever. Use a forced build step.

## Writing to Snowflake from Python

* Write native pandas datetimes. ISO strings produce `Connection OK. Table exists, but schema does not match : Type mismatch for column 3 (creation_date) : 'VARCHAR' in dataset, 'TIMESTAMP'(93:TIMESTAMPNTZ) in table`. If it persists after fixing the code, the DSS dataset schema is stale: Settings > Schema, set the columns to "Date/time without timezone" (matches TIMESTAMP_NTZ).
* For TIMESTAMP_NTZ, build naive UTC values: `pd.Timestamp(ms, unit='ms')`.
* "Append instead of overwrite" on the output dataset does the history for you. Reading the existing data, concatenating and rewriting against an append dataset failed on the first run (table not there yet) and succeeded on the second. Write only the new rows. `get_writer().write_dataframe()` replaces the data and does not append.
* A table created by hand is owned by a personal role: DSS then fails with `Insufficient privileges to operate on table ... Your primary role <DSS_SNOWFLAKE_ROLE> must have OWNERSHIP granted`. `GRANT OWNERSHIP ... COPY CURRENT GRANTS` fixes it, except in a managed access schema (`Not authorized in a managed access schema. Use the schema owner role or a role with the MANAGE GRANTS privilege`).
* A DSS-created table's column order follows the DataFrame. A hand-made table must match it exactly or the write fails with `Name mismatch for column 3`.
* "Insufficient privileges ... synchronize table description" means DSS can insert but cannot write COMMENTs (does not own the table). The data is still written. Grant ownership or switch off description sync.
* `write_with_schema` adds new columns to a DSS-managed dataset, and historic rows get NaN.

## Notebook practicalities

* `print()` in a DSS Jupyter notebook is buffered until the cell finishes. `print = functools.partial(print, flush=True)` at the top of the cell.
* The kernel runs one cell at a time: a new cell stays "queued" behind a long one.
* Long cells pasted from elsewhere sometimes arrive truncated (`SyntaxError: unexpected EOF while parsing`). Re-paste them.
* `%pylab inline` (default first cell) prints a deprecation warning and is not needed.
* `pd.set_option('display.max_rows', 200)`, `display.max_colwidth`, `display.width` for wide output. Render a DataFrame as the cell's last expression instead of printing it. `.reset_index(drop=True)` after sorting.
* `dataiku.__version__` prints the client version.

## Logs and audit

* `client.list_logs()` returns dicts (`name`, `totalSize`, ...), not strings. The audit trail appears as `audit/audit.log`, `audit/audit.log.1` ... `.20`, about 100 MB each. `client.get_log("audit/audit.log")` was not tried at that size. We grepped `$DIP_HOME/run/audit/audit.log` on the host instead (host-level diagnostics runbook).
* `DSSClient` has no `get_audit_trail()`. The client can write custom entries with `log_custom_audit` but not query the trail.

## Other snippets

* boto3 from a notebook, if the host role allows it:

  ```python
  import boto3
  cw = boto3.client('cloudwatch', region_name='eu-west-1')
  for a in cw.describe_alarms()['MetricAlarms']:
      print(a['AlarmName'], a['StateValue'])
  ```

  Suggested, output not recorded.
* `proj.list_jobs()` was suggested to see recent jobs per project during an investigation. Output not recorded.
