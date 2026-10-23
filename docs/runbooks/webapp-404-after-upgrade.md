# Code Studio webapps returning 404 after the upgrade

Prod design node, some days after the upgrade to 14.5.1, with follow-up work afterwards. The detection script and daily check that came out of this are under `scripts/monitoring/vanity_url_audit/`. This page covers the incident.

The nginx `invalid port in upstream` failure mentioned below has its own page: [webapp-nginx-exposed-location.md](webapp-nginx-exposed-location.md).

## Symptom

Streamlit webapps published from Code Studios (`CODE_STUDIO_AS_WEBAPP`) returned a plain nginx `404 Not Found`, both on the published webapp and on the View tab of the Code Studio. It started after the move to 14.5.1 and affected several projects at once. The first one reported had two published webapps on the same Code Studio and template, and one worked while the other did not.

## Red herrings, with why they were dropped

| Lead | Why it looked plausible | Why it was not the cause |
|---|---|---|
| Template needs rebuilding | The first error was `The code studio template must be rebuilt` | Rebuilt, still 404 |
| Code Studio pod has no app files | The Code Studio's `code_studio-versioned` folder only had `vscode-machine-settings` | The Code Studio is the dev environment. The published webapp syncs `project-lib-versioned/streamlit/Home.py` and pages and logs "You can now view your Streamlit app" on 8181 |
| In-pod nginx cannot write its error log | Every webapp log shows `nginx: [alert] could not open error log file: open() "/var/log/nginx/error.log" failed (13: Permission denied)` | Appears in working and failing pods alike. It is an alert, not fatal (the pod config says `error_log stderr`) |
| Known vendor bug with the nginx block | An earlier vendor ticket: pod nginx `[emerg] invalid port in upstream "127.0.0.1:8888http://localhost:8888/" in /home/dataiku/nginx.conf:115`. Vendor workaround "set the Exposed location field in the nginx block to something that is not just `/`, like `/?foo=bar`" | That bug is in the custom nginx entrypoint block. The built-in Streamlit block (`pycdstdioblk_code-studio-blocks_streamlit`) has no Exposed location field, and our pods did not show the emerg error. Replacing the built-in block with a custom entrypoint was considered and not done |
| Port collision (8181 vs 8182) | The failing webapp's exposition log showed repeated `connection refused` to 8182 | 8182 is the in-pod nginx port for the Streamlit entrypoint (8081 is VS Code to 8080). Both webapps use it in separate pods. Not a collision |
| Webapp config differs | | `config/projects/<PROJECT_KEY>/web_apps/<id>.json` for both were identical apart from name and creation date: same `codeStudioId`, `exposedPort` 8181 with `port_forward` exposition, container `eks-highmemory`, 1 replica, no HPA |
| Race on startup ("one got unlucky") | | Rejected as an explanation, since it is not evidence |
| System nginx IPv6 | Real problem on the same box | Fixed separately (`nginx-ipv6-upstream-after-upgrade.md`), not the 404 |

Things that did not work while digging:

* kubectl from the DSS host. There are no contexts for ssm-user or dataiku, and DSS uses its own kubeconfig under `clusters/<cluster>/exec/`.
* Finding a per-Code-Studio nginx config on disk. The Streamlit block's pod nginx.conf is generated inside the image at runtime, and only custom nginx blocks leave `nginx-block.conf.tpl` in `tmp/code-studio-build/`.

On the host side, DSS's own nginx routes `location ^~ /code-studios/<PROJECT_KEY>/<id>/8181/` to a local dynamic port in `install-support/backends.d/<PROJECT_KEY>/<id>-<port>.conf`.

## Cause

Dataiku support identified duplicate vanity URLs. Vanity URLs are stored in general settings under `webAppSecuritySettings.vanityURLs` as a list of `{"key": "<PROJECT_KEY>.<webappId>", "value": "<slug>"}`. DSS allows an admin to save two entries with the same slug without any warning. When two running webapps share a slug, the generated nginx routing breaks, and not only for those two: other webapps on the instance returned 404 as well.

Several slugs were duplicated. One pair had both webapps running (a project had published `..._v2` of a webapp and kept the old one with the same slug). Renaming the duplicate slug made every webapp on the instance work again, including the first one reported.

Finding them from a notebook:

```python
import dataiku
from collections import defaultdict

client = dataiku.api_client()
settings = client.get_general_settings().get_raw()
vanity_urls = settings.get("webAppSecuritySettings", {}).get("vanityURLs", [])

by_slug = defaultdict(list)
for entry in vanity_urls:
    by_slug[entry["value"]].append(entry["key"])

for slug, keys in sorted(by_slug.items()):
    if len(keys) > 1:
        print(slug, keys)
```

Searching for the word "vanity" in other settings keys, project settings or webapp settings finds nothing. Searching the raw general settings JSON for a known slug is what located the field.

## Fix

Administration > Settings > Security & Audit > Other security settings > Vanity URLs. For each duplicate slug, remove or rename the stale entry, save, restart DSS. When a webapp is replaced by a new one that should keep the slug, remove the old entry.

## Follow-up

* The other duplicates (none running) were left for their owners, then fixed by the platform team when nobody else did.
* A daily scenario now detects duplicates and emails the creators and the platform team (`scripts/monitoring/vanity_url_audit/`).
