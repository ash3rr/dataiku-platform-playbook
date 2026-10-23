# Offboarding scan

Script: [`scripts/governance/offboarding_scan.py`](../../scripts/governance/offboarding_scan.py). DSS notebook, internal client, admin. Run the sections one at a time.

## The request

Which projects belong to someone who has left, so that tickets can be raised to transfer them to a successor?

"Related to the user" means two different tickets:

* OWNS: the project needs a new owner before the account goes.
* MEMBER: the user's explicit permission entry needs removing, or handing over to the successor at the same level.

Access through groups is out of scope: it is handled at group level, not per project.

## Ownership in DSS

Per Dataiku's permission docs, each project has exactly one owner login. Being owner grants nothing beyond project admin. It mainly gives the creator access. When that login is deleted the project keeps pointing at a user that no longer exists, and someone has to reassign it (Project > Security > Project Owner). Scenarios have a separate "run as" user that breaks in the same way.

Where the owner can and cannot be read:

| Source | Result |
|---|---|
| `project.get_permissions()["owner"]` | The owner login. This is what Dataiku's own published offboarding snippet uses. |
| `client.list_projects()[i]["ownerLogin"]` | Same value (confirmed on one project) |
| `project.get_metadata()["owner"]` | Empty for every project. Do not use. |
| `project.get_settings().get_raw()["owner"]` | `None` |
| "+ Add project_owner" pill under About | A custom metadata field. Unrelated to the DSS owner. |
| "Created by" / last modified | `creationTag` / `versionTag`. Not ownership. |
| `GET /public/api/projects/` | Same data as `list_projects()`. Mentioned, not used |

## First attempt

1. First draft used `dataikuapi.DSSClient(url, key)` and filtered `get_metadata().get("owner")` for a guessed username. Switched to the internal client.
2. The username was not known, so the filter was dropped to print every project with its owner, then a deduplicated owner list. Both came back with no owners: `get_metadata()` does not carry the owner.
3. Next step suggested: print one project's raw metadata to find the right field (`ownerLogin`, `createdBy` were guesses). The work stopped there. The answer (`get_permissions()["owner"]` or `list_projects()["ownerLogin"]`) came out of the second attempt.

## Second attempt

1. Fuzzy match. The first script matched `SEARCH_TERMS` (surname, first name) against `login`, `displayName` and `email` from `list_users()`, then scanned every project's permissions for those logins. One unambiguous match. The login is the user's email address.
2. Exact login. Fuzzy matching was dropped: it risks catching another person with the same first name and needs user-read rights. `TARGET_LOGIN` with `==`. Result: nothing owned, a few explicit memberships, no `[skip]` lines.
3. Detail per hit. Current owner and the leaver's exact flags on each project found, so the ticket says what the successor needs (section 2 of the script).
4. Second code path. The same check through `list_projects()` and `ownerLogin` printed only `done`, which agrees with nothing owned.
5. Positive control. The empty results could only be trusted if the check was shown to find a known owner. Run against the login of a user who had created a sandbox project and expected to own it, it returned zero owned projects.
6. Ground truth for that project. Raw `list_projects()` dict, `get_permissions()["owner"]` and the settings owner field for the sandbox. `ownerLogin` and `get_permissions()["owner"]` were both populated, both with that user's login. The settings field was `None`, and the dict had `isProjectAdmin: true`.
7. Where it stuck. The likeliest explanation is that the control was run with the wrong login string (the display name and the login differ on this instance), since the owner fields named the control user. That was not confirmed: the control was reported as run with the email address. Two UI traps make a project look ownerless when it is not: "created by / last modified by" is `creationTag` / `versionTag`, not ownership, and the custom `project_owner` tag is usually empty. The full owner dump (section 5) reads the owner column directly and would settle it.

Unresolved. The full dump was never reviewed. Treat an empty OWNS result as unverified. Before trusting a result from this script:

* run section 4 with a login you know owns a project, copied from Administration > Security > Users (not typed from the display name), and check the project appears
* run section 5 and read the owner column yourself
* compare `len(client.list_projects())` with `len(client.list_project_keys())`

## Run-as users (section 6)

A deleted account also breaks scenarios that run as that user. Section 6 scans every scenario's raw settings for `runAsUser == TARGET_LOGIN`. **This check was written without any verified example to work from**, so the key name is an assumption. Print one scenario's `get_settings().get_raw()` first and adjust the key before relying on it.

## Not done

* An automated owner reassignment (`perms["owner"] = successor; project.set_permissions(perms)`) was proposed. We kept discovery separate from changes, and reassignment went through tickets.
* Do not deactivate or delete the account until any OWNS reassignments are done, not only ticketed. External accounts may be purged on a schedule.
* A process gap worth raising: if offboarding looks at the custom `project_owner` tag, it misses real ownership, because the tag is usually empty.
