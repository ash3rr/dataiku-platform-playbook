# -*- coding: utf-8 -*-
"""
Offboarding scan: which projects does a departing user own, and where do
they have explicit permissions? DSS notebook, internal client, admin.

"Related to a user" means two different tickets:
  OWNS   -> the project needs a new owner before the account is removed
  MEMBER -> strip (or hand over) the user's explicit permission entry

Sections, run one at a time:
  1. Exact-login scan (owner + explicit user permission entries)
  2. Detail of the departing user's permission entry on each hit
  3. Cross-check ownership through list_projects()['ownerLogin']
  4. Positive control: run 1 against a login you KNOW owns a project
  5. Full owner dump of every project, no filter, to read by eye
  6. Scenarios whose "run as" user is the departing user (UNCONFIRMED)

STATUS: the scan ran and reported no owned projects for the departing
user. The positive control did not confirm it: a sandbox project the
control user had created did not show up in the control run, and the
reason was never settled (see docs/reference/offboarding-scan.md). Sections
4 and 5 are how to settle it. The full dump (5) was suggested but its
output was never reviewed. Treat results as unverified until section 4
returns the project you expect.

Confirmed facts (DSS 14.5.1):
* project.get_permissions() -> {"owner": "<login>", "permissions": [
      {"user" | "group": ..., "<flag>": bool, ...}, ...]}
* client.list_projects() entries carry 'ownerLogin'. On the sandbox project
  it matched get_permissions()['owner'] exactly.
* project.get_metadata().get("owner") came back empty for every project:
  do not use metadata for ownership.
* project.get_settings().get_raw().get("owner") was None.
* The "+ Add project_owner" pill under a project's About panel is a custom
  metadata field, unrelated to the DSS owner.
* Logins here are full email addresses and can differ from the display
  name (a "First Last" display name with a login spelled differently).
  Copy the login from Administration > Security > Users.
* list_users() is not needed once the login is known (the first version
  fuzzy-matched login/displayName/email with SEARCH_TERMS, and was dropped because
  it risks matching the wrong person and needs user-read rights).
* Projects whose permissions cannot be read print [skip], and any skip means
  the result is incomplete.

Documented Dataiku pattern for the same job (identical loop):
    for project_key in client.list_project_keys():
        project = client.get_project(project_key)
        if project.get_permissions().get('owner') == old_owner: ...
"""
import dataiku

client = dataiku.api_client()

# Exact login, no fuzzy matching.
TARGET_LOGIN = "departing.user@company.example"
CONTROL_LOGIN = "your.login@company.example"   # a login you KNOW owns a project

# ---------------------------------------------------------------------------
# 1. Exact-login scan
# ---------------------------------------------------------------------------
owned, member, skipped = [], [], []

for key in client.list_project_keys():
    try:
        perms = client.get_project(key).get_permissions()
    except Exception as e:
        print(f"  [skip] {key}: {e}")
        skipped.append(key)
        continue

    if perms.get("owner") == TARGET_LOGIN:
        owned.append(key)

    for p in perms.get("permissions", []):
        if p.get("user") == TARGET_LOGIN:
            member.append(key)
            break

print(f"Target: {TARGET_LOGIN}\n")

print("=== OWNS (needs owner reassignment) ===")
for key in sorted(owned):
    print(f"  {key}")
print(f"  total: {len(owned)}\n")

print("=== MEMBER / explicit permissions (needs perms change) ===")
for key in sorted(member):
    print(f"  {key}")
print(f"  total: {len(member)}")
print(f"  skipped (unreadable): {len(skipped)}")

# ---------------------------------------------------------------------------
# 2. Current owner and the departing user's exact flags on each hit, so the
#    ticket says what the successor needs (admin? write? read only?)
# ---------------------------------------------------------------------------
for key in sorted(set(owned) | set(member)):
    perms = client.get_project(key).get_permissions()
    his = next((p for p in perms.get("permissions", []) if p.get("user") == TARGET_LOGIN), None)
    print(f"{key}")
    print(f"  current owner: {perms.get('owner')}")
    print(f"  target's perms: {his}\n")

# ---------------------------------------------------------------------------
# 3. Independent code path for ownership
# ---------------------------------------------------------------------------
for p in client.list_projects():
    if p.get("ownerLogin") == TARGET_LOGIN or p.get("owner") == TARGET_LOGIN:
        print(p.get("projectKey"), "->", p.get("ownerLogin"))
print("done")

# ---------------------------------------------------------------------------
# 4. Positive control. If a project you know you own does not appear, stop:
#    either CONTROL_LOGIN is not your real login, or the method is wrong.
# ---------------------------------------------------------------------------
c_owned, c_member = [], []
for key in client.list_project_keys():
    perms = client.get_project(key).get_permissions()
    if perms.get("owner") == CONTROL_LOGIN:
        c_owned.append(key)
    for p in perms.get("permissions", []):
        if p.get("user") == CONTROL_LOGIN:
            c_member.append(key)
            break
print("CONTROL OWNS:", sorted(c_owned))
print("CONTROL MEMBER:", sorted(c_member))

# Distinct owners across visible projects (field populated? target absent?)
from collections import Counter  # noqa: E402
owners = Counter(p.get("ownerLogin") for p in client.list_projects())
for login, n in owners.most_common():
    print(f"{n:3}  {login}")
print(f"\n{len(owners)} distinct owners across visible projects")
print("Target present:", TARGET_LOGIN in owners)
print(f"Projects visible: {len(client.list_projects())} | keys: {len(client.list_project_keys())}")

# ---------------------------------------------------------------------------
# 5. Ground truth: every project and its owner, no filter.
# ---------------------------------------------------------------------------
print(f"{'PROJECT KEY':45} OWNER")
print("-" * 90)
for key in client.list_project_keys():
    try:
        owner = client.get_project(key).get_permissions().get("owner")
    except Exception as e:
        owner = f"[ERROR: {e}]"
    flag = "  <-- TARGET" if owner == TARGET_LOGIN else ""
    print(f"{key:45} {owner}{flag}")

# ---------------------------------------------------------------------------
# 6. Scenarios running as the departing user. NOT RUN against the instance:
#    the settings key name 'runAsUser' is an assumption. Print one
#    scenario's raw settings first and adjust. A deleted run-as user breaks
#    the scenario even when project ownership was handled.
# ---------------------------------------------------------------------------
run_as = []
for key in client.list_project_keys():
    try:
        project = client.get_project(key)
        for sc in project.list_scenarios():
            raw = project.get_scenario(sc["id"]).get_settings().get_raw()
            if raw.get("runAsUser") == TARGET_LOGIN:
                run_as.append((key, sc["id"]))
    except Exception:
        pass
print("=== SCENARIOS RUNNING AS TARGET (unconfirmed key) ===")
for k, sid in sorted(run_as):
    print(f"  {k} / {sid}")
print(f"  total: {len(run_as)}")
