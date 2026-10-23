# A service request catalogue for a DSS platform

Once a platform has a few hundred users, requests need a front door. This was a first pass at the catalogue items for a ServiceNow-style portal, with the fields each needs and what fulfilment does in DSS.

The rule that shaped it: every field should map to something the fulfilment step actually uses. If a field only exists for a human to read, ask whether it's needed.

## Fields on every request

Requester and their manager (filled from SSO), target environment (DEV, UAT, PROD), and a link to the related project or ticket. Approval routing hangs off the manager and the environment: PROD requests get an extra approver.

## Request types

| Request | Key fields | Fulfilment | Automate? |
|---------|-----------|------------|-----------|
| New project | project name (validated against the naming convention), owning group, description | `client.create_project()`, then grant the group | yes, after manager approval |
| Licence / user profile | user, profile (reader, designer, etc.) | set the user's profile | yes |
| Add user to a project | user, project, role | add the user to the owning group (not a direct project grant, see below) | yes |
| Code environment | name, Python version, packages, shared or project-only | `client.create_code_env()` and install packages | after an automated package check |
| Code Studio template | base image, IDE, extra packages | admin builds the template | manual |
| New connection (S3 or Snowflake) | type, environment, owning group, bucket or database/schema/warehouse, auth method, justification | for per-group S3, the group storage pipeline and [`clone_connection_for_group.py`](../../scripts/connections/clone_connection_for_group.py) | after security approval |
| LLM access | provider, models, project, data classification, use case | add an LLM Mesh connection or allow the project on an existing one | after security approval |
| Offboard user | user, leaving date, who takes over their projects | remove from groups, revoke keys, reassign ownership, disable | yes, on the leaving date |
| Decommission project | project, retention requirement | export bundle, archive data, delete | manual |
| More resources | scenario or recipe, CPU/memory needed, why | change the containerised execution config | manual |

"Add user to a project" fulfils by group membership deliberately. Per-group storage and per-group connections assume the group is the unit of access. A user granted directly on a project gets the project but not its data, which turns into a confusing ticket a week later.

## What stayed open

Whether to have one catalogue item with dynamic fields per request type, or a separate item for each. One item is easier to maintain. Separate items give clearer reporting and simpler approval rules. The team hadn't settled it.
