# Dataiku platform playbook

Scripts, runbooks, Terraform and design notes from running Dataiku DSS 14.x as a shared, self-hosted platform on AWS: Fleet Manager (Dataiku Cloud Stacks) managing the nodes, containerized execution on EKS with CPU and GPU node groups, and Snowflake and S3 as the main data stores.

Everything here was used on, or designed for, a real enterprise platform and has been written up generically. Hostnames, account IDs, network IDs, bucket names, group names and table paths are placeholders such as `<DSS_HOST>` and `<AWS_ACCOUNT_ID>`. Where something was never confirmed or never resolved, the page says so.

## Layout

| Folder | Contents |
|---|---|
| [`scripts/`](scripts/) | Python, SQL and template files that run inside DSS with `dataiku.api_client()` |
| [`infra/`](infra/) | AWS configuration and commands: EKS cluster and node groups, launch templates, ECR, Fleet Manager templates, Terraform modules, Snowflake network policies, a Lambda |
| [`docs/runbooks/`](docs/runbooks/) | Incidents and procedures, written as what happened followed by what to do next time |
| [`docs/architecture/`](docs/architecture/) | Environment isolation, Snowflake and S3 isolation, promotion, Git, compute placement, a decision log |
| [`docs/platform/`](docs/platform/) | Running workloads: Spark, Code Studios, LLM workloads, webapps, workshops, event-driven scenarios |
| [`docs/reference/`](docs/reference/) | Python API field notes and one page per script on how it evolved and why it looks the way it does |

## Scripts

| Script | What it does | Notes |
|---|---|---|
| [`licensing/inactive_user_downgrade.py`](scripts/licensing/inactive_user_downgrade.py) | Moves idle designers to Reader and idle AI consumers/readers to None, with grace period, exception list, service-account filter, dry run and change log | [reference](docs/reference/inactive-user-downgrade.md) |
| [`licensing/licence_count_snapshot.py`](scripts/licensing/licence_count_snapshot.py) | Daily seat count per profile (trial and paid), contract threshold flag, trending row | [reference](docs/reference/licence-count-snapshot.md) |
| [`licensing/licence_trending_table.sql`](scripts/licensing/licence_trending_table.sql) | Table behind the trending dataset | |
| [`licensing/monthly_licence_report.ftl`](scripts/licensing/monthly_licence_report.ftl) | FreeMarker body for the monthly licence email | [reference](docs/reference/licence-reporting.md) |
| [`licensing/teams/`](scripts/licensing/teams/) | Teams message body, Adaptive Card and scenario variable init for licence alerts | [reference](docs/reference/scenario-reporters.md) |
| [`licensing/user_profile_diagnostics.py`](scripts/licensing/user_profile_diagnostics.py) | Checks used to make the counts match the DSS licence page | |
| [`monitoring/vanity_url_audit/`](scripts/monitoring/vanity_url_audit/) | Daily scenario that finds webapps sharing a vanity URL slug, logs to Snowflake and emails the owners | [reference](docs/reference/vanity-url-audit.md) |
| [`monitoring/email/message_sender_attempt.py`](scripts/monitoring/email/message_sender_attempt.py) | Sending mail from Python through the DSS mail channel. Not confirmed working | [reference](docs/reference/email-from-python.md) |
| [`governance/dataset_inventory.py`](scripts/governance/dataset_inventory.py) | Classifies every dataset on the instance from the flow graph and flags stale orphans | [reference](docs/reference/dataset-inventory.md) |
| [`governance/offboarding_scan.py`](scripts/governance/offboarding_scan.py) | Projects a departing user owns or has access to, and scenarios that run as them | [reference](docs/reference/offboarding-scan.md) |
| [`governance/list_groups.py`](scripts/governance/list_groups.py) | Prints every group and its metadata | |
| [`provisioning/workshop_projects.py`](scripts/provisioning/workshop_projects.py) | One project per workshop attendee from a template, with users, folder and project permissions. Safe to re-run | [reference](docs/reference/workshop-provisioning.md) |
| [`code_envs/rebuild_container_images.py`](scripts/code_envs/rebuild_container_images.py) | Builds a code env's container images and polls without blocking | [reference](docs/reference/code-env-container-images.md) |
| [`code_envs/check_code_env_container_status.py`](scripts/code_envs/check_code_env_container_status.py) | Works out why a recipe runs locally when the project says EKS | same |
| [`code_envs/find_recipe_execution_overrides.py`](scripts/code_envs/find_recipe_execution_overrides.py) | Lists recipes that override the project's container setting | same |
| [`code_envs/rebuild_code_studio_templates.py`](scripts/code_envs/rebuild_code_studio_templates.py) | Rebuilds Code Studio templates in small batches after an upgrade. Never run | [reference](docs/reference/code-studio-template-rebuild.md) |
| [`code_envs/code_studio_template_inspect.py`](scripts/code_envs/code_studio_template_inspect.py) | Template build dates and the real result of a single build | same |
| [`recipes/managed_folder_streaming.py`](scripts/recipes/managed_folder_streaming.py) | Managed-folder reads and writes that work inside containers | [patterns](docs/platform/recipe-patterns.md) |
| [`connections/list_connections.py`](scripts/connections/list_connections.py) | Every connection with host, bucket or account and auth method, secrets redacted. Runs inside or outside DSS | [readme](scripts/connections/README.md) |
| [`connections/clone_connection_for_group.py`](scripts/connections/clone_connection_for_group.py) | Creates a group's S3 connection from a template connection. Not run live | [design](docs/architecture/s3-group-isolation.md) |
| [`recipes/append_run_log_to_snowflake.py`](scripts/recipes/append_run_log_to_snowflake.py) | Appends one run's rows to a Snowflake-backed log dataset | same |

To run one, paste it into a DSS notebook, a Python recipe or a scenario's "Execute Python code" step. Most need admin rights because they read every project or change users. The ones that change things have a config block at the top, and the downgrade script starts in dry-run mode.

## Runbooks

Start with [`docs/runbooks/README.md`](docs/runbooks/README.md), which indexes all of them. The main ones:

* [DSS upgrade to 14.5.1 across the fleet](docs/runbooks/dss-upgrade-14.5.1.md), with links to each incident from that upgrade
* [Design node down: Fleet Manager volume resize loop and reprovision](docs/runbooks/fleet-manager-reprovision-and-volume-loop.md)
* [Govern node password corrupted by log4j output](docs/runbooks/govern-node-password-corruption.md)
* ["Stop cluster" in Fleet Manager deleted the EKS cluster](docs/runbooks/eks-cluster-stop-and-recreate.md)
* [Datadog agent rollout through the Fleet Manager instance templates](docs/runbooks/datadog-agent-rollout.md)

## Infrastructure

See [`infra/README.md`](infra/README.md). It covers the eksctl cluster config, CPU node groups and the shared 500 GB launch template, the four GPU node groups with their DSS container profile and alarms, the post-upgrade base image build and ECR push, the Fleet Manager instance template's custom Ansible, Terraform modules for per-group S3 storage and Snowflake PrivateLink, the Snowflake network policies, and a Lambda that starts scenarios when files land in S3.

## Architecture

* [Multi-VPC target design](docs/architecture/multi-vpc-target-design.md): keeping DEV away from PROD data with four VPCs, Transit Gateway blackhole routes and per-environment IAM
* [Snowflake network isolation](docs/architecture/snowflake-network-isolation.md): IP allowlisting vs PrivateLink, and the central network account problem
* [S3 isolation per group](docs/architecture/s3-group-isolation.md), including the instance metadata gap
* [Decision log](docs/architecture/decision-log.md)
* [Reviewing an existing deployment](docs/architecture/reviewing-an-existing-deployment.md)
* [Service request catalogue](docs/architecture/service-request-catalogue.md)
* [Project promotion and CI/CD](docs/architecture/promotion-and-cicd.md)
* [Git integration](docs/architecture/git-integration.md)
* [Compute placement and cost](docs/architecture/compute-and-cost.md)
* [Should a search UI be a Dataiku webapp](docs/architecture/webapp-technology-choice.md)

## Platform usage

* [Spark on Kubernetes](docs/platform/spark-on-kubernetes.md)
* [Heavy processing on EKS instead of Snowflake compute](docs/platform/eks-vs-snowflake-compute.md)
* [LLM connections and LLM workloads](docs/platform/llm-mesh-and-llm-workloads.md)
* [Code Studios](docs/platform/code-studios.md)
* [Webapps](docs/platform/webapps.md)
* [Workshop infrastructure](docs/platform/workshop-infrastructure.md)
* [Temporary simulation infrastructure](docs/platform/simulation-infrastructure.md)
* [Recipe patterns](docs/platform/recipe-patterns.md)
* [Starting scenarios when new data arrives](docs/platform/event-driven-scenario-runs.md)
* [Getting files into Dataiku: S3 or a Snowflake stage](docs/platform/files-into-dataiku-s3-vs-snowflake-stage.md)

## Reference

* [Python API field notes](docs/reference/python-api-field-notes.md): behaviour confirmed on DSS 14.x, and approaches that were tried and dropped
* [Scenario reporters](docs/reference/scenario-reporters.md)
* [Automation node permissions](docs/reference/automation-node-permissions.md)
* [Licence reporting](docs/reference/licence-reporting.md)

## License

MIT. See [LICENSE](LICENSE).
