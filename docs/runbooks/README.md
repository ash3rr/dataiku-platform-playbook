# Runbooks

Incident write-ups and procedures for the self-hosted DSS 14.x platform (Fleet Manager on EC2, containerized execution on EKS). Most are written as the history of a real incident followed by the procedure that came out of it.

## Upgrades

| Runbook | Covers |
|---|---|
| [dss-upgrade-14.5.1.md](dss-upgrade-14.5.1.md) | The 14.2.1 to 14.5.1 move across the fleet, the sequence of events, and the checklist for next time |
| [fleet-manager-reprovision-and-volume-loop.md](fleet-manager-reprovision-and-volume-loop.md) | Prod design node down: FM volume shrink loop, dead pgdg13 repo, reprovision onto 14.5.1 |
| [govern-node-password-corruption.md](govern-node-password-corruption.md) | Govern on 14.5.1: FM writes log4j output into the DB password, and the `dkugovern` wrapper patch |
| [ecr-base-image-push.md](ecr-base-image-push.md) | Base image built but never pushed, then the push hung behind the code studio pushes DSS started |
| [code-studio-template-rebuild.md](code-studio-template-rebuild.md) | Rebuilding every template by API: silent failures, `pkg_resources`, `last_built` not updating, batching |
| [code-env-container-images.md](code-env-container-images.md) | Recipes running locally, `dockerImageResources: INIT`, `update_images` and futures |
| [nginx-ipv6-upstream-after-upgrade.md](nginx-ipv6-upstream-after-upgrade.md) | System nginx proxying to `[::1]:10000` |
| [webapp-404-after-upgrade.md](webapp-404-after-upgrade.md) | Webapps returning 404: duplicate vanity URLs, and the red herrings |
| [webapp-nginx-exposed-location.md](webapp-nginx-exposed-location.md) | Code Studio webapp pod fails with nginx "invalid port in upstream": the Exposed location workaround and how to test it on a copy of the template |
| [eks-version-upgrade.md](eks-version-upgrade.md) | Control plane, add-ons and node AMI updates, plus the AL2 to AL2023 move |

## EKS

| Runbook | Covers |
|---|---|
| [eks-cluster-stop-and-recreate.md](eks-cluster-stop-and-recreate.md) | "Stop cluster" deleted the prod cluster. Orphaned eksctl stack, reconnecting DSS nodes |
| [eks-scaling-investigation.md](eks-scaling-investigation.md) | Why a node group scaled or did not, and how to read scheduler messages |
| [eks-node-group-retirement.md](eks-node-group-retirement.md) | Removing or replacing a node group safely without kubectl |
| `../../infra/eks-gpu-nodegroups/RUNBOOK.md` | GPU node groups across g5 and g4dn, capacity shortage, CloudWatch alarms |
| `../../infra/eks-nodegroups/RUNBOOK.md` | CPU node groups, the shared 500 GB launch template, and port 10001 |

## Fleet Manager and hosts

| Runbook | Covers |
|---|---|
| [fleet-manager-provisioning-stuck.md](fleet-manager-provisioning-stuck.md) | Provision hangs: `fmmain.log`, missing key pair, FM's database |
| [host-level-diagnostics.md](host-level-diagnostics.md) | Paths, versions, ports, logs, local admin and password resets, audit log |
| [datadog-agent-rollout.md](datadog-agent-rollout.md) | Installing the Datadog agent through the instance templates: the role that cannot run in FM, the GPG and repo errors, and the first encounter with the dead pgdg13 repo |

