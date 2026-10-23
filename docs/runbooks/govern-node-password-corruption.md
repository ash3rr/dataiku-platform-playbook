# Govern node won't start after upgrade: database password corrupted by log4j output

Seen on the prod Govern node, DSS 14.5.1, managed by Fleet Manager. This was the second half of the outage described in `fleet-manager-reprovision-and-volume-loop.md` (the design node half). Logs on the instance are UTC while the FM UI shows local time, which caused confusion more than once when reading logs.

## Symptom

In Fleet Manager the Govern node never reached Running. On the instance (SSM session, see `host-level-diagnostics.md`):

```
$ sudo su - dataiku -c "~/dss_data/bin/dss status"
governserver                     FATAL     Exited too quickly (process log may have details)
nginx                            RUNNING   pid 103453, uptime 0:01:24
```

Govern's log is `run/governserver.log`, not `backend.log` (the design node file). It rotates at 100 MB and keeps ten files. At the bottom of the stack trace, under a higher level "cannot reach the Govern database" message:

```
FATAL: password authentication failed for user "governdbuser"
```

So PostgreSQL was reachable and rejecting the password. It had failed this way since the first start after provisioning.

Fleet Manager's agent log showed it starting DSS and then giving up:

```
dss start execution failed (returncode=1, output=b'Waiting for DSS supervisor to start ...
governserver STARTING  nginx STARTING  DSS started, pid=8043
Waiting for Dataiku Govern server to start ......', err=b'*** dss : Warning: Dataiku Govern server
still not started after two minutes. It might still be initializing, and taking longer than expected,
because the server is heavily loaded, or it might be a permanent failure. ...')
```

## Layout on the Govern node

| What | Where |
|---|---|
| DSS data dir | `/data/dataiku/dss_data` (`~/dss_data` for the dataiku user) |
| DB connection settings | `config/dip.properties`: `psql.jdbc.url=jdbc:postgresql://localhost:15432/governdb`, `psql.jdbc.user=governdbuser`, `psql.jdbc.password=e:AES:...` |
| PostgreSQL | version 17, `/usr/pgsql-17/bin/postgres -D /data/pgsql-govern/data`, port 15432 (found with `ps aux \| grep postgres`. `find` for `pg_hba.conf` under /data and /var/lib returned nothing as ssm-user) |
| pg_hba.conf | `/data/pgsql-govern/data/pg_hba.conf`, md5 on localhost, fine. Read it as postgres: `sudo su - postgres -c "cat /data/pgsql-govern/data/pg_hba.conf"` |
| FM agent log | `/var/log/dataiku-fm-agent/dataiku-fm-agent.log` (journalctl needs sudo) |
| `run/fm_settings.json` | Only a list of FM setup steps (`SETUP_GRAPHICS_EXPORT`, `SETUP_RUNTIME_DATABASE`, `SETUP_MEMORY_SETTINGS`, `SETUP_MUS_AUTOCREATE`, `SETUP_UI_CUSTOMIZATION`, `SETUP_INSTANCE_NAME`, `SETUP_SECURE_COOKIES`, `SETUP_K8S_AND_SPARK`). No password. |

There is no `dsscli` on Govern. Its admin CLI wrapper is `bin/dkugovern` (documented as `govern-admin` in places), with `encrypt-password`, `init-db`, `regenerate-config` and `run-diagnosis`.

## Cause

Fleet Manager owns this password, and you are not meant to set it yourself. During provisioning the agent's `govern_database` step creates the user and database if they do not exist ("Create the user governdbuser", "Create the database governdb"), generates a password, runs `dkugovern encrypt-password <pw>` and writes whatever that prints into `dip.properties` with `sed -n w`.

On 14.5.1, `dkugovern encrypt-password` prints log4j DEBUG lines to stdout before the `e:AES:` value. They come from a `log4j2.properties` bundled inside the Athena JDBC driver jar on Govern's classpath:

```
... main DEBUG Reconfiguration complete for context[name=...] at URI
jar:file:/data/dataiku/dss_data/lib/jdbc/AthenaJDBC42-2.1.1.1000.jar!/log4j2.properties ...
```

`sed -n w` captures all of it, so the stored "password" becomes the first debug line:

```
psql.jdbc.password=<YYYY-MM-DD hh:mm:ss,SSS> main DEBUG null null initializing configuration org.apache.logging.log4j.core.config.properties.PropertiesConfiguration@4dd02341
```

The trace that proved it, taken while the agent ran a start cycle:

```bash
sudo strace -e trace=execve -f -p $(pgrep -f fm-agent) 2>&1 | grep sed
```

showed `execve("/usr/bin/sudo", ["sudo", "-i", "-u", "dataiku", "sed", "-n", "w /data/dataiku/dss_data/config/"...`. (`inotifywait -m` on the file is an alternative.)

This is not Log4Shell or any other vulnerability. The cause is log output on the wrong stream.

## What we tried that did not work

In order, so nobody repeats them:

1. Reset the password in PostgreSQL and `sed` a plaintext password into `dip.properties`:

   ```bash
   sudo su - postgres -c "psql -p 15432 -c \"ALTER USER governdbuser WITH PASSWORD '<NEW_PASSWORD>';\""
   ```

   Govern needs the `e:AES:` encrypted form. It came up once (HikariPool showed 50 connections), then failed again.
2. Python rewrite of the line as the dataiku user: no effect, the file was rewritten again on the next start.
3. `sed -i` as ssm-user: `sed: couldn't open temporary file /data/dataiku/dss_data/config/sedXXXXXX: Permission denied` (ssm-user is not root, and `whoami` says `ssm-user`). Working pattern: `sudo cp` to /tmp, `sudo sed` there, `sudo cp` back.
4. Encrypted value written correctly while DSS was stopped. Govern started and answered `/api/ping`, then crashed three minutes later when the FM agent ran its start sequence and rewrote the file again. The PostgreSQL password also appeared to "revert" between attempts, because FM was resetting it too.
5. Restarting while the previous process was alive: `*** ERROR : server port 10000 is already in use ... server ports busy, cannot start DSS`. Stop first. One start ended with `Waiting for Dataiku Govern server to start ..........Killed`.
6. Reprovision from Fleet Manager. Expected to regenerate everything cleanly. It created a new EC2 instance, the `govern_database` step reported `changed=1`, DSS started, and five minutes later the same corrupted line was back. This is what proved FM itself was writing the garbage.
7. Downgrade in FM settings: blocked, "downgrading isn't supported".
8. Adding `-Dlog4j2.level=OFF` to the Java call in `dkugovern`: the bundled config inside the jar still wins and the output is unchanged.

## Workaround that worked

1. Back up the wrapper and look at it. The last command is `exec "$DKUJAVABIN" ... com.dataiku.gh.cli.DKU "$@"`.

   ```bash
   sudo cp /data/dataiku/dss_data/bin/dkugovern /data/dataiku/dss_data/bin/dkugovern.bak
   ls -la /data/dataiku/dss_data/bin/dkugovern
   # lrwxrwxrwx ... dkugovern -> /opt/dataiku-dss-14.5.1/scripts/linked/dkugovern
   ```

2. It is a symlink into the install directory. Do not edit the shipped file. Replace the link in the data dir with a copy (unlink only removes the link, the install is untouched):

   ```bash
   sudo unlink /data/dataiku/dss_data/bin/dkugovern
   sudo cp /opt/dataiku-dss-14.5.1/scripts/linked/dkugovern /data/dataiku/dss_data/bin/dkugovern
   sudo chown dataiku:dataiku /data/dataiku/dss_data/bin/dkugovern
   sudo chmod +x /data/dataiku/dss_data/bin/dkugovern
   ```

3. Drop `exec` and filter the output so only the useful line reaches stdout. Python avoids sed escaping problems:

   ```bash
   sudo python3 <<'PYEOF'
   p = '/data/dataiku/dss_data/bin/dkugovern'
   s = open(p).read()
   s = s.replace('exec "$DKUJAVABIN"', '"$DKUJAVABIN"', 1)
   s = s.replace('com.dataiku.gh.cli.DKU "$@"',
                 'com.dataiku.gh.cli.DKU "$@" 2>&1 | grep -v "^\\[\\|^2[0-9]\\{3\\}-\\|^$"')
   open(p, 'w').write(s)
   print("Done")
   PYEOF
   ```

   (The version applied during the incident also still had the useless `-Dlog4j2.level=OFF` from step 8 of the failed attempts. It does no harm.) The filter removes lines starting with `[`, lines starting with a four digit year, and blank lines. It applies to every `dkugovern` command, not only `encrypt-password`, which is acceptable for a stopgap.

4. Test. Expect a single line:

   ```bash
   sudo su - dataiku -c "~/dss_data/bin/dkugovern encrypt-password 'TestPass123'"
   # e:AES:X2:...
   ```

5. Do **not** reprovision now: a reprovision builds a new instance and the patched wrapper is gone. Stop the agent so it cannot overwrite anything, then set the password by hand:

   ```bash
   sudo systemctl stop dataiku-fm-agent
   sudo su - dataiku -c "~/dss_data/bin/dss stop"
   sudo su - postgres -c "psql -p 15432 -c \"ALTER USER governdbuser WITH PASSWORD '<NEW_PASSWORD>';\""
   sudo su - postgres -c "PGPASSWORD='<NEW_PASSWORD>' psql -p 15432 -U governdbuser -d governdb -h localhost -c 'SELECT 1;'"
   sudo su - dataiku -c "~/dss_data/bin/dkugovern encrypt-password '<NEW_PASSWORD>'"

   sudo cp /data/dataiku/dss_data/config/dip.properties /tmp/dip.properties
   sudo sed -i 's|^psql\.jdbc\.password=.*|psql.jdbc.password=<E_AES_VALUE>|' /tmp/dip.properties
   sudo grep 'psql.jdbc.password' /tmp/dip.properties
   sudo cp /tmp/dip.properties /data/dataiku/dss_data/config/dip.properties
   sudo su - dataiku -c "~/dss_data/bin/dss start"
   sudo su - dataiku -c "tail -5 /data/dataiku/dss_data/run/governserver.log"
   ```

   Use `|` as the sed delimiter because the AES value contains `/` and `+`. A healthy log ends with lines like `Computed license instance id`, `Computed local hostname` and `Done call: /api/ping`.

   With the agent stopped, Fleet Manager shows the instance as unreachable. That is the price until the vendor fixes the bug. Restart the agent afterwards.

6. Raise it with Dataiku support as P1 with: version 14.5.1, node type Govern, the corrupted `dip.properties` line, the strace output showing `sed -n w` capturing mixed output, the AthenaJDBC jar path, and the fact that reprovisioning reproduces it every time.

Checking which log file is current: `ls -lt /data/dataiku/dss_data/run/governserver.log*`. If nothing new appears in `governserver.log`, check it has not rotated.

## Instance template change made during the incident

The Govern IST's custom Ansible had the same `disablerepo: "pgdg13"` line in the Datadog task as the design template. Removed from both. Final version in `infra/fleet-manager/ist-custom-ansible.yaml`.

## Open items

* Confirm the vendor fix version and remove the patched `dkugovern` copy (restore the symlink) after upgrading.
* Until then, any FM reprovision or agent-driven start of Govern will corrupt the password again. Re-apply the wrapper patch first, or keep the agent stopped.
* Whether the FM agent was restarted after the manual fix, and when, was not recorded.
