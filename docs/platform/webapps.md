# Webapps: operational notes

Running webapps on this platform (Dash and Streamlit mostly). Whether a given use case should be a Dataiku webapp at all is in [webapp-technology-choice.md](../architecture/webapp-technology-choice.md). Code Studio webapps are covered in [code-studios.md](code-studios.md).

## Where a webapp backend runs

A standard (Dash, Bokeh, Flask) webapp backend can run on the DSS host or in a container. Check the backend log's infra block: `"containerMode": "NONE"` with `"serveLocally": true` means it runs on the DSS host and EKS is irrelevant, even if a `"containerConf"` such as `eks-highmemory` is also listed. Code Studio webapps always run as pods (`"containerMode": "EXPLICIT_CONTAINER"`).

URL pattern inside DSS, confirmed by testing: `/projects/<PROJECT_KEY>/webapps/<WEBAPP_ID>_<name lowercased, alphanumeric only>/view`.

## "Webapp backend start timeout"

A scenario step that restarts a Dash webapp failed:

```
java.io.IOException: Webapp backend start timeout
```

at 20:48:54. DSS gives the backend about 60 seconds to report ready. The backend did come up, about four minutes later: `Started Dash on port 37595` at 20:52:08. RSS grew from about 40 MB to 129 MB during that time, consistent with heavy imports and initialisation at module load (pandas, Snowflake connector, project libraries, a `SQLExecutor2` created at import time).

We tested the theory that the EKS node group was cold-starting. The `standard-workers` node group's ASG scaling activities showed a scale-down at 12:33 and the next scale-up at 05:01 the following day, nothing near 20:47. And the log said `containerMode: NONE`, `serveLocally: true`: the webapp runs on the DSS host. Theory rejected.

Commands used, in PowerShell. The JMESPath time filter in `--query` returned nothing, so we listed recent activities and read them instead:

```powershell
aws eks describe-nodegroup --cluster-name <EKS_CLUSTER_NAME> --nodegroup-name standard-workers `
  --query 'nodegroup.resources.autoScalingGroups' --profile prod

aws autoscaling describe-scaling-activities --auto-scaling-group-name <ASG_NAME> `
  --max-items 20 --output json --profile prod |
  ConvertFrom-Json | Select-Object -ExpandProperty Activities |
  Select-Object StartTime, EndTime, StatusCode, Cause | Format-List
```

The fix options:

1. Make startup fast: move heavy imports and connection setup out of module level. For example, instead of creating `SQLExecutor2(connection="<SNOWFLAKE_CONNECTION>")` at import, create it on first use inside a callback.
2. Raise the webapp backend startup timeout. We did not find where that setting lives (admin UI or a properties file) and did not want to guess. Check the documentation or ask support.

Which was done is not recorded.

## 502 Bad Gateway on a Dash webapp

Startup logs were clean: Dash started on its port, nginx was reloaded, the `__ping` health check passed. So the 502 was happening after startup. Candidates:

* a callback crashing the process (for example a Snowflake query failing or timing out),
* the proxy timing out on a slow callback,
* the Snowflake connection the app uses failing during a request. Lines in the app code that switched between two Snowflake connections had been commented out, which was worth checking.

We asked for tracebacks after the clean startup lines. None came back, so the cause is open.

## Duplicate vanity URLs break routing

After the 14.5.1 upgrade, webapps returned 404s across the instance. The cause was two running webapps sharing one vanity URL slug. Renaming the duplicate fixed every outage. Further duplicate slugs were found and left to their owners. Vanity URLs are stored in instance settings under `webAppSecuritySettings.vanityURLs` as `{"key": "<PROJECT_KEY>.<webappId>", "value": "<slug>"}` entries. A daily scenario now detects duplicates and emails owners: [`scripts/monitoring/vanity_url_audit/`](../../scripts/monitoring/vanity_url_audit/).

## Public webapps and licences

A webapp can be published from DSS with a public URL that needs no DSS login, so viewers need no Reader licence. Queries run server-side through the DSS connection's service account. API Deployer is not involved. Details and caveats in [webapp-technology-choice.md](../architecture/webapp-technology-choice.md).
