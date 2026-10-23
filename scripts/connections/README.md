# Connection scripts

Python against the DSS API for inventorying and creating connections. Unlike the other scripts in this repo, these also run from outside DSS: `dss_client.get_client()` uses the internal client inside a DSS notebook or recipe, and falls back to `dataikuapi.DSSClient` with an API key everywhere else.

```bash
pip install -r requirements.txt
export DSS_HOST=https://<DSS_HOST>
export DSS_API_KEY=...
python list_connections.py
```

| Script | What it does | Writes to DSS |
|--------|--------------|---------------|
| `list_connections.py` | Lists every connection with host, bucket or account, and auth method. Secrets are redacted | no |
| `clone_connection_for_group.py` | Copies a template S3 connection for one group, swapping bucket and role, restricted to that group | yes (`--dry-run` available) |
| `dss_client.py` | Returns the internal client inside DSS, or an API-key client outside it | no |

The S3-triggered scenario starter that used to sit alongside these is in [`infra/lambda/lambda_trigger_scenario.py`](../../infra/lambda/lambda_trigger_scenario.py), with the design notes in [`docs/platform/event-driven-scenario-runs.md`](../../docs/platform/event-driven-scenario-runs.md).

Most of these need an admin API key. Don't commit keys. Use environment variables locally and Secrets Manager for the Lambda.

`list_connections.py` is a cleaned-up version of a script used on DSS 14. `clone_connection_for_group.py` and the Lambda were written later and have not been run against a live instance, so start with `--dry-run` and a test group.

Connection parameter names differ by connection type and DSS version, which is why `clone_connection_for_group.py` takes them as arguments instead of hard-coding them.

The per-group connection model these scripts support is described in [`docs/architecture/s3-group-isolation.md`](../../docs/architecture/s3-group-isolation.md).
