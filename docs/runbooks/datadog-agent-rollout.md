# Datadog agent on Fleet Manager instances

The working tasks are in [`infra/fleet-manager/ist-custom-ansible.yaml`](../../infra/fleet-manager/ist-custom-ansible.yaml). This page is how we got there. It first worked on the template of a non-prod instance. That same session was the first time we hit the dead PostgreSQL 13 repo, which later took down the prod design node (`fleet-manager-reprovision-and-volume-loop.md`).

## What Dataiku said

Asked how to install Datadog on FM-managed nodes, Dataiku support answered that the Datadog agent is not supported by Dataiku and DSS has no monitoring infrastructure of its own. We may integrate it as long as it does not interfere with DSS operation (their example: do not change the ownership or permissions of the DATA_DIR). They had no documentation or guidelines to offer. So this is ours to own.

## Where the tasks go

The instance settings template (IST) has two custom Ansible fields: one that runs before DSS is installed and one that runs after. There is no earlier hook. FM's own provisioning tasks (including `TASK [install packages]`) run before either of them, so nothing in our YAML can fix a problem in FM's own steps.

## Attempts, in order

### 1. The Datadog Ansible role

The first templates used the official role, either as `ansible.builtin.include_role: name: datadog.dd.agent` in the after-DSS field with `datadog_api_key`, `datadog_site: datadoghq.eu` and `datadog_config` tags, or as a full playbook (`hosts: your_linux_servers`, `roles: - role: datadog.dd.agent`) pasted from Datadog's docs. Provisioning stopped progressing. The FM logs showed `ansible-galaxy` not found: the role comes from the `datadog.dd` collection, and FM's Ansible runner has no way to install collections. Pre-baking the collection into the image was the only other way to use the role, and we do not build the images. Both role-based snippets were dropped.

The same logs showed a 404 for `DATADOG_RPM_KEY_B0103B7C.public`. Datadog has retired that key. The templates now list only `DATADOG_RPM_KEY_CURRENT.public` and `DATADOG_RPM_KEY_FD4BF915.public`.

### 2. Inline tasks

The replacement is plain tasks: write `/etc/yum.repos.d/datadog.repo`, import the RPM keys with `rpm_key`, `dnf clean all`, refresh metadata, install `datadog-agent` (before-DSS field), then write `/etc/datadog-agent/datadog.yaml` and start the service with `systemd` (after-DSS field). The RPM creates the `dd-agent` user and an empty `datadog.yaml`, so the `copy` with `owner: dd-agent` works when it runs after the install. None of it touches the DATA_DIR. Each failure along the way:

| Run | Error | Change |
|---|---|---|
| a | `dnf -y makecache --refresh` failed: `Errors during downloading metadata for repository 'pgdg13': Status code: 404 for https://download.postgresql.org/pub/repos/yum/13/...`. The Datadog repo itself had refreshed fine (`Datadog 18 kB/s \| 905 B`) | `dnf makecache --refresh --repo datadog`, so only the Datadog repo is refreshed |
| b | The Datadog tasks had no `become` | `become: True` on every Datadog task, and the `retries: 3`, `delay: 10`, `until: makecache_result.rc == 0` loop kept on makecache as a guard against a flaky network at boot |
| c | `repomd.xml GPG signature verification error: Bad GPG signature`, and an interactive `Is this ok [y/N]:` prompt in a non-interactive run | `repo_gpgcheck = 0` in the repo file. `gpgcheck = 1` stays, so packages are still verified. Only the signature check on the repository metadata is off |
| d | makecache passed, but the `dnf` install task refreshed every repo again and hit pgdg13 | `disablerepo: "pgdg13"` on the install task (this line was removed later, see below) |

Instead of disabling the repo we considered marking it skippable, on the reasoning that it might be there for a reason:

```yaml
- name: Set pgdg13 repo to skip if unavailable
  become: True
  ini_file:
    path: /etc/yum.repos.d/pgdg-redhat-all.repo
    section: pgdg13
    option: skip_if_unavailable
    value: "1"
```

On the next attempt that task did not appear in the output at all (either the module is not available in FM's runner or it failed silently), and pgdg13 still blocked the install. A `sed` that sets `enabled=0` only inside the `[pgdg13]` section was drafted (`sed -i '/^\[pgdg13\]/,/^\[/{s/^enabled=1/enabled=0/}' /etc/yum.repos.d/pgdg-redhat-all.repo`). It went nowhere because of the next finding.

### 3. The real cause: PostgreSQL 13 is end of life

A second instance on the same template failed at FM's own `TASK [install packages]`, with only one task completed, before any of our YAML ran. So the broken repo was not something our template could work around.

The instance had provisioned cleanly some months earlier (an earlier physical instance reached `pi-initialization-complete`), so something outside had changed. PostgreSQL 13 reached end of life on 13 November 2025, and the PGDG RHEL 9 repository for 13 was removed afterwards: `https://download.postgresql.org/pub/repos/yum/13/redhat/rhel-9-x86_64/repodata/repomd.xml` returns 404, and the PGDG repo now starts at 14 and goes to 18. The `dss-14.2.1-default` image still has a pgdg13 entry in its repo file (alongside 14 to 18), and dnf fails if any enabled repo cannot be read.

Our first reading was that Dataiku had to fix its image. The better reading: 14.2.1 predates the EOL, so we were pinned to an image built around a dead dependency, and the fix is a newer DSS version. Dataiku published no bulletin about it that we could find. The AMIs are private and shared to the account, so the list of versions is the DSS version dropdown in FM, which FM refreshes itself when it has internet access. Releases after 14.2.1, for reference: 14.2.2 (released the day after the PostgreSQL 13 EOL, so it may not include the fix), 14.2.3, 14.3.0 and 14.4.4.

We set the test instance to 14.4 (`dss-14.4.4-default`) and reprovisioned. It reached `pi-initialization-complete`. The failures still visible in the log belonged to the older physical instance on 14.2.1.

All nodes managed by one Fleet Manager should run the same DSS version. Mixing versions between design, automation and deployer nodes is not supported. The test instance on 14.4 was an exception for that test, and the prod fleet later moved to 14.5.1 together (`dss-upgrade-14.5.1.md`).

Later the same dead repo broke a reprovision of the prod design node on 14.2.1, and the fix there was 14.5.1, which no longer needs pgdg13. At that point `disablerepo: "pgdg13"` was removed from both templates. dnf reads metadata for every configured repo before it applies `disablerepo`, so that line had never protected anything.

## Verifying

FM's Events tab only shows high-level events, not task output from the after-DSS field. Check on the host:

```bash
aws ssm start-session --target <INSTANCE_ID> --profile prod
systemctl status datadog-agent
# Active: active (running) since <DATE> UTC ... /opt/datadog-agent/bin/agent/agent run ...
```

Then in Datadog (EU site, `app.datadoghq.eu`) under Infrastructure > Hosts the instance appears within a few minutes with the tags from `datadog.yaml` (`company:<COMPANY_TAG>`, `service:dataiku`, `env:prd`). The test instance used `env:prd` too. Whether a non-prod instance should have its own environment tag is an open question.

## Coverage

Filter on `service:dataiku` in Datadog to see which hosts report, and compare against the list of FM instances and EKS node groups. Nodes not yet reporting are the remaining scope. EKS workers are not covered by the instance templates at all.

For nodes that already run DSS, the after-DSS block alone is enough only if the agent package is already installed. Otherwise they need the before-DSS block as well, which means a reprovision.

## Secrets

Treat any API key that has ever been typed into a template as exposed and rotate it. The key belongs in a secret store (SSM Parameter Store or Secrets Manager) read at provision time. The committed file uses `<DATADOG_API_KEY>`. Wiring the template to a secret store is not done yet.

## Related

* While setting up CloudWatch alarms for the GPU node groups, we looked for a Datadog AWS integration role to forward CloudWatch alarms instead of SNS. `aws iam list-roles` filtered on datadog, monitor, observ, grafana, newrelic and splunk found only `AWSServiceRoleForObservabilityAdmin`, an AWS service role. So the Datadog side covers the agents on hosts, not CloudWatch (`infra/eks-gpu-nodegroups/RUNBOOK.md`).
