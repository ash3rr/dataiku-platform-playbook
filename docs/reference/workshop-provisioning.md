# Workshop provisioning

Script: [`scripts/provisioning/workshop_projects.py`](../../scripts/provisioning/workshop_projects.py). DSS notebook, admin. For each attendee: make sure the LDAP user exists, duplicate a template project into one project folder, and set user, group and folder permissions so attendees can see and edit their own project.

## What went wrong at a workshop

Attendees could not see their project and could not open it. The script used to prepare it was a refactor of an older script, meant to be safe to re-run, and it had been run several times.

## The original script

This is the older version that had worked before (transcribed as it was, apart from scrubbing and a neutral project name prefix). It does not parse, because the colon of the `for` line is inside the comment:

```python
print("USERS type:", type(USERS))
print("USERS length:", len(USERS))
print("USERS keys sample:", list(USERS.keys())[:3])
for user_full_name in LAST_MINUTE_USERS.keys() #USERS_ALL.keys():
    # Get user details
    user_name = user_full_name
    user_first_name = user_name.split(' ', 1)[0]
    user_email = LAST_MINUTE_USERS[user_full_name] #USERS_ALL[user_full_name]
    user_email = ''.join(i.lower() for i in LAST_MINUTE_USERS[user_full_name]) #USERS_ALL[user_full_name])
    print(user_name)
    print(user_email)

    # Get project name and key
    project_name = f'Workshop - {user_first_name}'
    project_key = f"{''.join(i.upper() for i in project_name if i.isalnum())}_{PROJECT_NAME_SUFFIX}_1"
    print(project_name)
    print(project_key)

    # Create user
    user = client.create_user(user_email, '<unused>', source_type='LDAP', display_name=user_full_name, groups=['workshop_demo'])
    user_definition = user.get_definition()
    user_definition['email'] = user_email
    user_definition['userProfile'] = 'Reader'
    user.set_definition(user_definition)

    # Duplicate workshop project
    project_template = client.get_project(PROJECT_TEMPLATE_ID)
    copy_result = project_template.duplicate(target_project_key=project_key, target_project_name=project_name)
    project = client.get_project(project_key)

    # Ensure workshop_demo group permission exists
    project_perms = project.get_permissions()
    project_perms.setdefault('permissions', [])
    workshop_group_perm = {
        "group": "workshop_demo",
        "admin": False,
        "editPermissions": True,
        "readProjectContent": True,
        "writeProjectContent": True,
        "publishToDataCollections": True,
        "shareToWorkspaces": True,
        "exportDatasetsData": True,
        "readDashboards": False,
        "writeDashboards": False,
        "runScenarios": True,
        "manageAuthorizedObjects": False,
        "manageSharedObjects": False
    }
    # Find if it exists
    existing = None
    for p in project_perms["permissions"]:
        if p.get("group") == "workshop_demo":
            existing = p
            break
    if existing:
        # MERGE (do not remove anything)
        for k, v in workshop_group_perm.items():
            if k not in existing:
                existing[k] = v
    else:
        # Add group entry
        project_perms["permissions"].append(workshop_group_perm)
    project.set_permissions(project_perms)

    # Move it to project folder
    project_folder = client.get_project_folder(PROJECT_FOLDER_ID)
    project.move_to_folder(project_folder)

    # Update project permissions
    project_permissions = project.get_permissions()
    user_permissions = {
        "user": user_email,
        "admin": False,
        "readProjectContent": True,
        "writeProjectContent": True,
        "exportDatasetsData": False,
        "readDashboards": True,
        "writeDashboards": True,
        "moderateDashboards": False,
        "publishToDataCollections": True,
        "shareToWorkspaces": True,
        "runScenarios": True,
        "manageDashboardAuthorizations": True,
        "manageExposedElements": False,
        "manageAdditionalDashboardUsers": False,
        "executeApp": False
    }

    project_permissions['permissions'].append(user_permissions)
    project.set_permissions(project_permissions)

    # Manage tags and description
    project_metadata = project.get_metadata()
    project_metadata['tags'] = []
    project_metadata['shortDesc'] = ''
    project.set_metadata(project_metadata)

    print("Project CREATED")
```

(The original passed a literal placeholder password to `create_user` for an LDAP user, shown here as `'<unused>'`. A commented-out earlier `user_permissions` dict was also in the original and is omitted.) It used the first name only for the project name, set the profile through `set_definition()` with the value `'Reader'`, crashed on re-run when the project already existed, and appended the user permission entry each time instead of updating it.

## Regressions in the refactored version

1. The `workshop_demo` group permission block had moved outside the loop, at module level, referring to a `project` variable that did not exist there. Dead code: no project got the group entry.
2. Skip logic blocked repair. It cached existing project keys and skipped any project that already existed, treating "exists" as "fully configured". After a partial first run, every later run skipped those users entirely: no permissions, no metadata.
3. Silent failure in user setup. `ensure_user` wrapped `get_settings().save()` in a bare `except Exception: pass`, so a failed email or profile update left no trace. (The old script crashed visibly.)

The refactor treated "idempotent" as "skip if present". It should have meant "create if missing, configure every time".

## Fixes, in order

1. Two phases. Phase 1 creates (duplicate and move to folder) only when the key is missing. Phase 2 (permissions and metadata) always runs. `ensure_user` is called once per attendee before both phases.
2. Overwrite instead of fill-missing. The merges used `setdefault` / `if k not in existing`, so a wrong value from an earlier run was never corrected. Changed to `existing[k] = v`: the script is the source of truth.
3. Warn instead of `pass` in `ensure_user`.
4. Removed an unused `project_exists()` helper. Merged the user and group updates into one `get_permissions()` / `set_permissions()` cycle (`apply_project_permissions`), avoiding two round trips and a possible stale read between them.

## The folder permissions (the root cause)

The folder's permission list in the UI showed the `workshop_demo` group with every box unticked. Neither the old nor the refactored script ever touched folder permissions. Both only called `project.move_to_folder()`. Someone had added the group to the folder by hand once and saved it without ticking anything, so the group had no access to the folder and the projects inside were invisible, whatever the project permissions said. (One manual step for one group entry, not one per attendee, which is why it was plausible.)

Setting one group entry on the folder covers all attendees, since they are all in `workshop_demo`. Getting the API call right took several tries:

| Attempt | Result |
|---|---|
| `folder.get_permissions()` / `set_permissions()` (assuming the project shape with a `permissions` key) | `DSSProjectFolder` has no `get_permissions` |
| `folder.get_definition()` to find where permissions live | `'DSSProjectFolder' object has no attribute 'get_definition'` |
| `dir(folder)` | `client, create_project, create_sub_folder, delete, get_default_folder_for_project_creation, get_name, get_parent, get_path, get_settings, id, list_child_folders, list_project_keys, list_projects, move_project_to, move_to, name, project_folder_id` |
| `folder.get_settings()` | `dataikuapi.dss.projectfolder.DSSProjectFolderSettings` with `get_name, get_owner, get_permissions, get_raw, save, set_name, set_owner` |
| `settings.get_permissions()` | `[{'group': 'workshop_demo', 'read': False, 'writeContents': False, 'admin': False}]`: a flat list, not a dict |

Final: get the settings, update or append the `workshop_demo` entry in that list, `settings.save()`. First with `read` only, then `writeContents: True` as well, because attendees need to read and edit the projects in the folder. It runs once before the per-user loop.

Later runs still printed `[WARN] Could not get folder definition`: the notebook cell was a stale copy with the debug block. Re-pasting the current version fixed it. A `NameError: name 'client' is not defined` meant the config cell had not been run first. One test run showed truncated names and email addresses, a problem with how the `USERS_ALL` dict had been filled in, not with the script. It was set aside.

## User versus group permissions

DSS permissions are additive: a user gets a flag if their own entry or any of their groups grants it. Options discussed: group only (simplest, and Dataiku's own recommendation is to manage access through groups), user only, or both.

We first believed the UI only showed group entries, which argued for group only. That was wrong: Project > Security lists user entries next to groups and the owner. Looking there, the user entry had `exportDatasetsData` False while the group had it True, so the user could export anyway through the group, which was confusing to read.

Decision: keep both, but the group must not let attendees edit each other's work. The group gets read access to project content (plus run scenarios, publish, share, export) and **no** write, no `editPermissions` (that flag allows someone to change who has access to the project). Write comes from each attendee's own user entry. `exportDatasetsData` is True in both entries so they match.

A `data_team` group on every workshop project comes from the template's permissions on duplication. Expected.

## Things to fill in before running

* `PROJECT_TEMPLATE_ID`, `PROJECT_FOLDER_ID` (from the folder URL), `PROJECT_NAME_SUFFIX` (workshop date, part of the key), `USERS_ALL` as `{"First Last": "email"}`.
* `DEFAULT_PROFILE`: the value used at the workshop was not recorded. The old script set `'Reader'`. Attendees need to write in their project, so the repo uses FULL_DESIGNER. Check licence headroom first ([licence-reporting.md](licence-reporting.md)).
* The key sanitiser transliterates a small table of non-ASCII letters to ASCII before stripping, so names with accented or dotless letters still give readable keys. Extend the table for the names you have. Keys keep only A-Z and 0-9.

## Unrelated question raised at the same time

Whether an EKS 1.33 to 1.34 upgrade causes downtime, and how node groups are upgraded, came up at the end of this work. That belongs to the EKS upgrade runbook.
