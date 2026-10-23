# Prod design node down: volume resize loop, dead pgdg13 repo, reprovision onto 14.5.1

The prod design node went into error in Fleet Manager and stayed down until it was reprovisioned on a new DSS version. The Govern node failed at the same time for a different reason (`govern-node-password-corruption.md`).

## Sequence

The FM UI shows event times in the browser's local time, while files and logs on the instance are UTC. Keep that in mind when lining them up.

| Step | What |
|---|---|
| 1 | Design node "In Error" in FM. FM backend returning HTTP 500 on every agent heartbeat. Diagnosis: FM trying to shrink the data volume. |
| 2 | A reprovision on the existing version (14.2.1) failed: `initialization-failed`, Ansible `[install packages]`, pgdg13 metadata. |
| 3 | About ten minutes later, reprovision on 14.5.1: new EC2 instance, event `pi-agent-alive`. FM shows "Starting" for a long time. |
| 4 | A few minutes in, the pre-migration backup folder `_pre_migration_backup_<YYYYMMDD-HHMMSS>` is written in the data dir. |
| 5 | About twenty minutes after that, `run/install.log` shows the 14.5.1 data migration completed cleanly. |
| 6 | Some fifteen minutes later the FM agent is still running provisioning steps (impersonation setup, user home symlinks, cgroups). `[install packages]` passed: `ok=2 failed=0`. |
| 7 | DSS found not running and started manually. Data volume confirmed intact. |
| 8 | Govern fixed with a manual workaround. |
| 9 | Rest of the fleet moved to 14.5.1 to avoid version mismatch, plus container images and code studios. See `dss-upgrade-14.5.1.md`. |

## Part 1: the volume resize loop

The FM agent log on the node showed every heartbeat failing with a 500 from the FM backend. The backend was calling EC2 `ModifyVolume` to make the data volume smaller, which EBS never allows (volumes only grow), so every reconcile failed.

* The real data volume (device size and `bytesUsed` in the agent log, `df -h /data` on the host) was far larger than the template's data volume size.
* Instance settings in FM: Data volume size and Data volume maximum size. The field could not be edited at instance level. It is inherited from the instance settings template ("IST for design/automation nodes").
* The tooltip says the volume starts at the data volume size and grows automatically up to the maximum. That is how this instance legitimately grew well past the starting size.

So FM compared the template's starting size with the much larger disk and tried to "correct" it. Our working theory was that the template value had been lowered at some point (or a code change made FM start enforcing it). This was not confirmed. The obvious fix was to raise the template's data volume size to the real size, but the template is shared by several instances, so changing it needs a check that no other instance is smaller than the new value. In the event, the reprovision on 14.5.1 cleared the loop and the template was not changed then.

Before touching a shared template: list the instances using it and their real volume sizes. FM only ever grows volumes, so raising the template value is safe for instances already larger and will grow smaller ones up to it.

## Part 2: pgdg13 kills provisioning

The first recovery attempt reprovisioned on the same version (14.2.1) and failed:

```
initialization-failed - <DATE> <TIME>
FmAgentException: Ansible Playbook execution failed (returncode=2, ...)
TASK [install packages] ****
fatal: [localhost]: FAILED! => { "changed": false, "rc": 1, "results": [] }
MSG:
Failed to download metadata for repo 'pgdg13': Cannot download repomd.xml: Cannot download repodata/repomd.xml: All mirrors were tried
```

The PostgreSQL 13 yum repository had gone end of life upstream and the 14.2.1 provisioning still referenced it in FM's own `[install packages]` task, which runs before any custom Ansible. Nothing in the template could skip it. 14.5.1 no longer depends on pgdg13 (it uses PostgreSQL 17 for Govern).

Our custom Ansible in the IST also had `disablerepo: "pgdg13"` in the Datadog install task. That line was removed from the design and Govern templates (`infra/fleet-manager/ist-custom-ansible.yaml`). dnf loads metadata for all repos before applying `disablerepo`, so it never helped.

## Part 3: reprovision onto 14.5.1

Reprovisioning is risky on a prod design node (new EC2 instance, downtime). The system was already down, so the risk was acceptable. Downgrades are blocked in FM, so moving forward was the only direction anyway.

How reprovision treats data: FM tracks the data volume against the logical instance (`li-...`), not the EC2 instance. Reprovision = new EC2 instance (new physical instance `pi-...`) + the same data volume reattached + DSS installed + data migrated. Snapshots (`fm-snap-*` per logical instance, plus older pre-upgrade snapshots) were the fallback.

### "Starting" for a long time

After `pi-agent-alive` nothing appeared in the FM Events or Agent logs tabs. Opening the DSS URL showed Dataiku's "preparing" page. On the instance:

```bash
aws ssm start-session --target <INSTANCE_ID> --region eu-west-1 --profile prod
sudo su - dataiku
ls                          # home of dataiku: dss_data, diag, update_*_build_code_env_images_*.txt, ...
~/dss_data/bin/dss status   # "dss: DSS supervisor is not running"
df -h                       # /dev/nvme0n1 mounted on /data, the data volume (root is a separate, smaller disk)
ls -la ~/dss_data           # -> /data/dataiku/dss_data, projects etc. all present
ls -la ~/dss_data/run/      # no dss.log yet; install.log large; stale supervisord.pid
tail -100 ~/dss_data/run/install.log
```

`run/dss.log` does not exist on this layout. The useful files are `install.log` (migration), `backend.log` (after start) and `supervisord.log`. The FM agent log is `/var/log/dataiku-fm-agent/dataiku-fm-agent.log` (found with `find / -name "*.log" -path "*fm-agent*"`). `journalctl -u dataiku-fm-agent` needs sudo, and the dataiku user's own sudo asks for a password, so run journalctl or sudo from the ssm-user shell.

Migration had finished cleanly, and the supervisor was not running. We started it by hand:

```
$ ~/dss_data/bin/dss start
Unlinking stale socket /data/dataiku/dss_data/run/svd.sock
Waiting for DSS supervisor to start ...
backend                          STARTING
ipython                          STARTING
nginx                            STARTING
DSS started, pid=28313
Waiting for DSS backend to start ...............
```

FM should issue the start itself at the end of provisioning (on the Govern node it did: `fm.lifecycle.install ... Executing: ['/data/dataiku/dss_data/bin/dss', 'start']`). The agent log showed it was still mid-provisioning when we started DSS manually, so starting by hand can collide with FM's own start. If DSS ends up in an odd state, `dss restart` as dataiku fixes it. Next time, watch the agent log until provisioning finishes before starting anything.

Checking PostgreSQL on a node: `psql --version` and `systemctl list-units | grep postgres`.

## Fleet Manager concepts that mattered

| Term | Meaning here |
|---|---|
| Logical instance `li-...` | The DSS node as FM knows it. Data volume and snapshots attach to this. |
| Physical instance `pi-...` | The current EC2 instance. Changes on every reprovision. |
| Instance settings template (IST) | Shared settings: volume sizes, key pair, custom Ansible, IAM profiles. Instance-level overrides are limited (data volume size could not be overridden). |
| Events / Agent logs tabs | First place to look. Can go quiet during long steps. |

## Lessons

* An EOL'd third-party package repo can take a node down at the next reprovision even when nothing on our side changed. Keep FM and DSS versions current enough that provisioning does not depend on dead repos.
* A shared template's "starting size" is enforced against existing instances. Treat template volume values as a floor that every instance must already exceed.
* Reprovisioning does not lose the data volume, but it does throw away anything changed on the root disk (relevant for the Govern workaround).
