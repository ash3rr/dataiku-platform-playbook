# DSS upgrade to 14.5.1 across the fleet

How the move from 14.2.1 to 14.5.1 went, and the procedure to follow next time. It was not planned as an upgrade: the prod design node went down and moving to 14.5.1 was the way out. The other nodes followed so that versions matched. Everything in a fleet should be on the same DSS version (design, automation, deployer, Govern), otherwise bundles and Govern sync can misbehave.

## Sequence of events

| Step | Node / area | Event | Detail |
|---|---|---|---|
| 1 | Prod design | Down. FM volume resize loop. Reprovision on 14.2.1 fails on dead pgdg13 repo, reprovision on 14.5.1 succeeds. DSS started by hand | `fleet-manager-reprovision-and-volume-loop.md` |
| 2 | Prod Govern | Will not start: FM writes log4j noise into the DB password. Wrapper patch, FM agent stopped | `govern-node-password-corruption.md` |
| 3 | Instance templates | `disablerepo: "pgdg13"` removed from design and Govern custom Ansible | `infra/fleet-manager/ist-custom-ansible.yaml` |
| 4 | Container base image | Built on the host, never pushed. GPU jobs fail with `...:dss-14.5.1: not found` | `ecr-base-image-push.md` |
| 5 | Prod automation | Upgraded by FM. FM UI showed 14.5.1 before the node was on it | "Checking what version a node is running" |
| 6 | Code Studio templates | Rebuild by API with silent failures and `pkg_resources` errors. Full rebuild stopped as too heavy | `code-studio-template-rebuild.md` |
| 7 | ECR | Base image push hangs behind the code studio pushes started by DSS | `ecr-base-image-push.md` |
| 4 to 7, in parallel | EKS | Node AMI versions updated, observability work on the cluster | `eks-version-upgrade.md` |
| 8 | Prod design | System nginx proxying to `[::1]:10000` | `nginx-ipv6-upstream-after-upgrade.md` |
| 9 | Webapps | 404s across webapps, caused by duplicate vanity URLs | `webapp-404-after-upgrade.md` |
| 10, some weeks later | Code envs | Recipes running locally. Code env images rebuilt with `update_images` | `code-env-container-images.md` |

## Checking what version a node is running

`dss` has no version argument (`./dss version` prints `Usage: dss (start | stop | status | restart | reopenlogs)`), and neither does `dssadmin`. The install directory in `bin/env-default.sh` is the answer:

```bash
grep DKUINSTALLDIR $DIP_HOME/bin/env-default.sh
# export DKUINSTALLDIR="/opt/dataiku-dss-14.5.1"
grep DKU_NODE_TYPE $DIP_HOME/bin/env-default.sh
# export DKU_NODE_TYPE="automation"
ls -la $DIP_HOME/bin
```

On the automation node, the first look showed 14.2.1 while FM already displayed 14.5.1. The FM agent log only showed heartbeats every ~30 s, `UPDATE_NODES_DIRECTORY` and `SEND_LOGS`. A second look some minutes later showed every `bin/` symlink (`dss`, `dssadmin`, `dsscli`, `fek`, `hproxy`, `jek`) re-pointed to `/opt/dataiku-dss-14.5.1/scripts/linked/`, and `env-default.sh` rewritten a minute after the symlinks. The upgrade landed between the two looks. FM shows the target version, not necessarily the running one.

## Procedure for the next upgrade

Run it per environment: dev, then UAT, then prod. Each environment has its own ECR and its own nodes.

### Before

1. Read the release notes for removed dependencies (pgdg13 caught us). Check that every yum repo the provisioning uses still exists.
2. Check the instance templates: custom Ansible, data volume size vs the real size of every instance using the template (`fleet-manager-reprovision-and-volume-loop.md`).
3. Confirm recent FM snapshots exist for every logical instance (`aws ec2 describe-snapshots --owner-ids self ...`, look for `fm-snap-*`).
4. Raise the Govern password bug with the vendor and get the fixed version, or plan to patch `dkugovern` before FM starts Govern.
5. Agree a quiet window, because code studio and code env rebuilds load the design node's docker daemon heavily.

### Upgrade the nodes

6. Upgrade or reprovision through FM. Downgrade is blocked, so there is no going back except snapshots.
7. On each node, check `env-default.sh` and `dss status`. Watch the FM agent log (`/var/log/dataiku-fm-agent/dataiku-fm-agent.log`) until provisioning has finished before starting anything by hand.
8. Govern: check `run/governserver.log` and the `psql.jdbc.password` line in `config/dip.properties`. If it contains log text, follow the Govern runbook.

### Containers

9. Build the container-exec base image on the design node and push it to ECR (`infra/ecr/build_and_push_base_image.sh`). Wait for DSS's own code studio pushes to finish first.
10. Re-run the containerized execution test in Administration > Settings > Containerized execution.
11. Rebuild code studio templates in batches of five, checking `success` on every result (`code-studio-template-rebuild.md`).
12. Rebuild code env images for envs with container configs (`code-env-container-images.md`). Check the base tag each image used.

### Front end

13. Check `/etc/nginx/conf.d/dataiku-reverse-proxy.snippet` uses `127.0.0.1:10000`, not `localhost`, and test `curl -sk https://127.0.0.1/`.
14. Run the vanity URL duplicate check.
15. Start a sample of Code Studio webapps and confirm they serve.

### After

16. Keep the previous version's local images until the new one is proven, then prune old versions.
17. Restore anything patched as a workaround once the vendor fix is in (Govern `dkugovern`).

## Open items from this upgrade

* Dev and UAT: confirm the base image and code studio rebuilds were done there too.
* Govern: vendor fix outstanding, patched wrapper in place.
* `last_built` not updating after API builds, raised internally, not yet with the vendor.
* One container config still building on the 14.2.1 base.
