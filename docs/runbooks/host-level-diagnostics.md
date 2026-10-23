# Host-level diagnostics on DSS, Govern and Fleet Manager nodes

Small things that are slow to rediscover. All assume an SSM session.

## Getting on a node

```powershell
aws ssm start-session --target <INSTANCE_ID> --region eu-west-1 --profile prod
```

The session lands as `ssm-user` in `sh`. That user has sudo, no AWS credentials and no docker access. For DSS work:

```bash
sudo su - dataiku
```

The dataiku user has no passwordless sudo, so anything needing root goes from the ssm-user shell (`exit` back to it). `journalctl` for other users' services also needs sudo ("You are currently not seeing messages from other users and the system").

Check where you are before changing anything. Instance names, IPs and roles get mixed up easily:

```bash
hostname && curl -s http://169.254.169.254/latest/meta-data/instance-id
```

The SSM browser console mangles pasted text. The Session Manager plugin on the laptop is the better route when available.

## Layout

| | DSS design / automation | Govern | Fleet Manager |
|---|---|---|---|
| Data dir | `/data/dataiku/dss_data` (`~/dss_data`) | `/data/dataiku/dss_data` | `/data/dataiku/fmhome` |
| Install dir | `/opt/dataiku-dss-<version>` | same | `/opt/dataiku/dataiku-fm-<version>` (13.0.0 in the sandbox) |
| Main log | `run/backend.log` | `run/governserver.log` | `run/fmmain.log` |
| Also in `run/` | `install.log` (migrations), `supervisord.log`, `nginx/`, `audit/` | `install.log`, `audit/`, `fm_settings.json` | `install.log`, `supervisord.log`, `nginx.log` |
| CLI | `bin/dss`, `bin/dssadmin`, `bin/dsscli` | `bin/dss`, `bin/dkugovern` (no dsscli) | `bin/fm`, `bin/fmadmin`, `bin/internal-fmcli` |
| FM agent log | `/var/log/dataiku-fm-agent/dataiku-fm-agent.log` | same | n/a |

`run/dss.log` does not exist on these nodes. Logs rotate at 100 MB and keep ten files. `ls -lt run/<name>.log*` shows which is current.

The data volume is `/dev/nvme0n1` mounted on `/data`, and root is a separate, smaller disk. Docker's root on the design node is `/data/docker-data`, not `/var/lib/docker` (`docker info | grep "Docker Root Dir"`).

DSS's EKS connection lives in `clusters/<cluster>/exec/` (kubeconfig, a JSON copy of the cluster description, the eksctl config). kubectl itself is not configured for ssm-user or dataiku.

## Which DSS version is installed

```bash
grep DKUINSTALLDIR $DIP_HOME/bin/env-default.sh     # export DKUINSTALLDIR="/opt/dataiku-dss-14.5.1"
grep DKU_NODE_TYPE $DIP_HOME/bin/env-default.sh     # design / automation / govern
ls -la $DIP_HOME/bin                                # symlink timestamps show when an upgrade flipped them
```

`dss version` and `dssadmin version` do not exist. Fleet Manager's UI may show the target version before the node has it.

`env-default.sh` also has the ports and JVM sizes. On prod automation:

| Process | Port | JVM heap |
|---|---|---|
| base/nginx | 10000 | |
| backend | 10001 | `-Xmx8g`, G1 |
| ipython | 10002 | |
| hproxy | 10003 | `-Xmx2g` |
| event server | 10004 | 2g |
| stories | 10006 | |
| fek, jek, cak | | `-Xmx2g` |
| dku | | 4g |

Do not edit `env-default.sh`. Local changes go in `bin/env-site.sh`. Containerized jobs call home to the backend on 10001, which matters for security groups.

## Starting, stopping, status

```bash
~/dss_data/bin/dss status
~/dss_data/bin/dss start
~/dss_data/bin/dss stop
~/dss_data/bin/dss restart
```

`dss start` on a fresh node may print `Unlinking stale socket .../run/svd.sock`, which is harmless. `*** ERROR : server port 10000 is already in use` means a previous instance is still running. Stop it first. On FM-managed nodes, the FM agent may start or restart DSS itself during provisioning.

## dssadmin actions (14.x)

`build-base-image`, `build-cde-plugins-image`, `build-container-exec-code-env-images [--all] [--dry-run]`, `install-monitoring-integration`, `install-event-server`, `regenerate-config`, `install-impersonation`, `run-diagnosis [OPTIONS] OUTPUT_FILE`, `install-graphics-export`, `compact-internal-databases`, `copy-databases-to-external`, `encrypt-password PASSWORD`, `verify-installation-integrity`, `jupyter-nbextensions`.

## Getting admin access back

Forcing the local login form when SSO is on and your directory account is not in the admin group:

```
https://<DSS_HOST>/login?localLogin=true
```

Log in with the local `admin` account. That worked on the sandbox design node.

Resetting passwords from the host (DSS must be running, and passwords need at least 5 characters):

| Node | Command |
|---|---|
| DSS design / automation | `./bin/dsscli user-edit --password <NEW_PASSWORD> admin` from the data dir |
| Fleet Manager | `./bin/fmadmin set-user-password <USER_ID> <NEW_PASSWORD>` from `/data/dataiku/fmhome` (positional arguments) |
| Govern | No CLI reset. `dkugovern` only offers `encrypt-password`, `init-db`, `regenerate-config`, `run-diagnosis`. The fallback is the Govern database, whose connection details are in `config/dip.properties`. Not done |

Older guesses that do not exist: `dssadmin reset-password`, `govern-admin set-user-password`, `dku user-set-password`.

`fmadmin` actions: `initial-setup`, `create-single-tenant`, `create-user`, `create-personal-api-key`, `set-user-password`, `register-aws-vpc`, `register-azure-vnet`, `create-tenant`, `regenerate-config`, `update-image-list`, `create-fm-images-in-gallery`, `run-diagnosis`.

Once in through the local admin, password changes for local users are easier in Administration > Security > Users. Afterwards: put the admin credential in a vault and make sure an AD group has admin rights so the break-glass path is not needed.

## Who created a user

The Python API has no audit trail query. The audit log on disk does:

```bash
cd $DIP_HOME
grep 'security-admin-user-create' run/audit/audit.log* | grep -i "<login>"
```

The event is `"msgType":"security-admin-user-create"` under `"topic":"generic"`.

For orientation, the data dir on that node also held the upgrade leftovers, one set per upgrade: `_pre_migration_backup_<YYYYMMDD-HHMMSS>`, `_pre-migration-cgroup-v1-<YYYYMMDD-HHMMSS>.json`, `_pre-migration-postgresql-10_<YYYYMMDD-HHMMSS>.conf` and `install.ini.bak.<YYYYMMDD>`. The newest set is from the 14.5.1 upgrade and the one before it presumably from the move to 14.2.1. Logs are under `run/`, not a top-level `logs/` directory.

How we found that (DSS 14.5.1, design node). The audit trail has no dedicated user topic, so guessing topic names (`"user-admin"`) returned nothing. Listing what is actually there worked:

```bash
grep -oP '"topic"\s*:\s*"[^"]*"' run/audit/audit.log | sort -u
# "topic":"apicall"  "topic":"authfail"  "topic":"compute-resource-usage"  "topic":"generic"  "topic":"generic-failure"

grep -oP '"msgType"\s*:\s*"[^"]*"' run/audit/audit.log | sort -u
# includes "msgType":"security-admin-user-create"
```

A `generic` entry looks like this (one JSON object per line):

```json
{"severity":"INFO","logger":"dku.audit.generic","topic":"generic","message":{"authVia":["ticket:python_trigger:<PROJECT_KEY>.<SCENARIO>"],"callPath":"/api/tintercom/sql-queries/verify","auditTopic":"generic","msgType":"sql-query-verify","authSource":"USER_FROM_UI","clientIP":"127.0.0.1","authUser":"<user login>","queryId":"<uuid>"},"mdc":{"apiCall":"/api/tintercom/sql-queries/verify","user":"<user login>"},"callTime":0,"timestamp":"<YYYY-MM-DDThh:mm:ss.SSS+0000>"}
```

`authUser` (and `mdc.user`) is who performed the action. Grepping `generic` for "user" mostly finds unrelated events whose login contains the word, so filter on `msgType`. `python3 -m json.tool` fails with `Extra data: line 2 column 1` when given more than one line, and with `Expecting value: line 1 column 1` when grep returned nothing. Pipe `head -1` into it.

Rotation limits how far back this goes. The directory holds `audit.log` plus numbered rotations (`audit.log.1`, `audit.log.2`, ...), each about 100 MB, and on a busy design node the files on disk cover only a few days. A user created earlier will not be found here, and we did not find an archive of older audit logs (the Event Server or S3 would be the place to look if one is set up). Note that an email-style login may contain a dot or space between first and last name, so grep on the surname alone first.

Python approaches tried first, all dead ends:

* `client.get_audit_trail()`: `AttributeError: 'DSSClient' object has no attribute 'get_audit_trail'`.
* Guessed internal endpoints (`client._session.get(client._base_url + "/admin/audit/")`, `/dip/api/admin/audit/list`, `/dip/api/admin/audit/query`, a `requests.Session` with `DKU_BACKEND_PORT` and an `X-DKU-APITicket` header from `DKU_API_TICKET`, `client._perform_json("GET", "/admin/audit/query", ...)`): none of these is documented. They were dropped without a confirmed result. From a DSS notebook `dataiku.api_client()` already carries the admin context, so there is no need for the hand-built session.
* `client.list_logs()` works, but returns dicts (`name`, `totalSize`, `lastModified`, `linesShown`, `tailSize`, `truncated`), so filtering with `l.lower()` failed with `AttributeError: 'dict' object has no attribute 'lower'`. The audit files show up as `audit/audit.log.N`. `client.get_log("audit/audit.log")` on a 100 MB file was not tried. Grep on the host is faster.

After running greps for a named person on a shared host, `history -c && history -w` clears the shell history. `-c` clears the session and `-w` writes the now empty history to `~/.bash_history`, which would otherwise still hold the commands.

Careful when pasting: pasting `ls -la` output back into the shell runs each line as a command (`-bash: -rw-r--r--: command not found`). Harmless, but noisy.

The same files give session activity per user (`compute-resource-usage-start` / `-complete` events with `authIdentifier`), which is how active users were listed during an investigation:

```bash
grep "<YYYY-MM-DD>T07:2[5-9]" run/audit/audit.log.1 | grep -o '"authIdentifier":"[^"]*"' | sort | uniq -c | sort -rn
```

As ssm-user the audit log gives `Permission denied`, so use `sudo -u dataiku`.

## Licence file

`$DIP_HOME/config/license.json`. Updating the licence in Administration > Licensing writes the same file.

## Pasting a file onto the server

```bash
cat > myfile.json   # paste, then Ctrl+D
```

For anything long, prefer generating the file on the server (for example `aws eks update-kubeconfig --kubeconfig <path>`) over pasting through the browser console.

## Editing protected config files

ssm-user cannot `sed -i` in the DSS config dir (`sed: couldn't open temporary file .../sedXXXXXX: Permission denied`). Pattern:

```bash
sudo cp /data/dataiku/dss_data/config/<file> /tmp/<file>
sudo sed -i '...' /tmp/<file>
grep '<key>' /tmp/<file>
sudo cp /tmp/<file> /data/dataiku/dss_data/config/<file>
```

Stop DSS first if DSS or the FM agent rewrites the file on start.

## Code env packages from the host

```bash
$DIP_HOME/code-envs/python/<ENV>/bin/pip --version
$DIP_HOME/code-envs/python/<ENV>/bin/pip list --format=freeze > /tmp/<env>_packages.txt
wc -l /tmp/<env>_packages.txt
```

If the redirect to /tmp appears to produce nothing, check `pip --version` works and that you can write the target file as the current user.

## Two nginx processes on the design node

The system nginx (root, port 443, `/etc/nginx/`, log `/var/log/nginx/error.log`) terminates TLS for the ALB and proxies to DSS's own nginx on 10000 (dataiku, `install-support/nginx.conf`, logs under `run/nginx/`). Test the front door with `curl -sk -o /dev/null -w "%{http_code}" https://127.0.0.1/`. Port 80 is not open. See `nginx-ipv6-upstream-after-upgrade.md`.

## PostgreSQL on a node

```bash
psql --version
systemctl list-units | grep postgres
ps aux | grep postgres     # shows the binary and data dir, e.g. /usr/pgsql-17/bin/postgres -D /data/pgsql-govern/data
```

Fleet Manager's own database: `psql -U dataiku -h 127.0.0.1 -d dataiku` on the FM host.

## Live connections and DNS on a host

```bash
ss -tunp
sudo tcpdump -tttt -i any -n port 53
```

Use `-tttt` so tcpdump prints dates. Without it, captures left running for days only have times of day.
