# Project promotion and CI/CD

How projects were meant to move from the design node to UAT and prod automation nodes, the options weighed, what the vendor said about the Deployer, and what changes once dev and prod sit in different AWS accounts. Project promotion was the one area of the environment design where advice was explicitly asked for, and it was never settled.

## What moves and how

* Projects move as bundles: a versioned export of the project (flow, code, config, not the data) created on the design node.
* The Project Deployer pushes a bundle to an automation node. Connections in the bundle are remapped to the target node's connections on deploy.
* Project permissions can travel in the bundle, but only take effect if the users and groups exist on the target node with the same names. With SSO, that means the target node must provision the same group names as the design node. See [automation-node-permissions.md](../reference/automation-node-permissions.md).
* Real-time endpoints go through the API Deployer to API nodes. That path is for APIs and models, not webapps.

## Options

### A. Project Deployer, by hand

1. Developer creates a bundle on design and deploys it to UAT through the Deployer UI.
2. Someone validates in UAT.
3. The same bundle is deployed to prod through the Deployer UI.

For: simple, visual, Dataiku-native. Against: no audit trail beyond the Deployer/FM logs, no approval gate other than "who has Deployer access".

### B. CI/CD wrapping the Dataiku API

1. A merge in the external git repo triggers a pipeline.
2. The pipeline creates the bundle and calls the Dataiku API to deploy it to UAT.
3. After validation, an approval gate in the CI tool (tied to change management) promotes the bundle to prod.

For: proper audit trail, approval gates, can integrate with ServiceNow and change management. Against: something to build and own, and it relies on project content being in an external git repo.

### C. Hybrid

Deployer by hand for design to UAT (fast iteration), CI/CD with an approval step for UAT to prod. This was the leaning, because ServiceNow is already in the picture and governance was wanted for prod. It depended on the company's change-management process for prod deployments, which was asked about and never answered.

### Git

Option B assumes project content lives in an external repo (GitHub/GitLab) rather than Dataiku's internal git: visibility, pull request review on project changes, CI hooks. The users preferred external. The integration work itself is in [git-integration.md](git-integration.md).

## Promotion across the AWS account boundary

Once the cloud team required separate dev and prod accounts, the chain became: design and UAT automation in the dev account, prod automation in the prod account, one Fleet Manager per account at the time.

* Design to UAT stays inside one FM domain and works natively with the Deployer.
* UAT to prod crosses the account boundary. With two FM domains there is no Dataiku-native handoff. A single Deployer would need network reach into the prod account.
* Terraform does not fill this gap. It mirrors infrastructure and has no way to move bundles.

Ways across that were listed:

* A narrowly scoped network path from the deployer to the prod automation node (scoped peering).
* S3-based bundle transfer: export in dev, import in prod.
* A CI/CD pipeline that exports from one side and imports on the other through the APIs.
* Treating the account boundary as the formal release step (CI/CD, Terraform-driven, or Govern), so every prod change crosses it deliberately.

Whether a bundle promotion from UAT to prod was automated at all, or done by hand, was asked and never answered.

The Dataiku solutions engineer also confirmed that Fleet Manager can deploy nodes across accounts, so one FM and one Deployer reaching both accounts is technically possible, though they recommend a single account for simplicity.

## Moving projects between environments (migration, not promotion)

From the Dataiku solutions engineer, when we asked about moving to a new environment layout:

* The Deployer can and should do design to automation deployment. It is not a general tool for migrating things between arbitrary Dataiku nodes.
* For migration, either migrate the whole data disk and then adjust the duplicated nodes as required in the new environment, or use project export and import, manually or through the Python API.

So for standing up a new design node in a new account, the two routes are a data disk move (fast, everything comes across, then fix up node-specific config) or per-project export/import (selective, scriptable).

## Things still to decide

| Question | Status |
|---|---|
| Deployer by hand, CI/CD, or hybrid | Leaning hybrid, not decided |
| Who may promote to prod (Deployer/FM RBAC) | Open |
| The change-management gate for prod (ServiceNow change, CI approval, Govern sign-off) | Open. The company's process was not described |
| How bundles cross the dev/prod account boundary | Open |
| Rollback when a prod deployment breaks (redeploy previous bundle version, or something more) | Open |
| Which git repo layout feeds CI | See [git-integration.md](git-integration.md) |
