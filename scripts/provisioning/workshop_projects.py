# -*- coding: utf-8 -*-
"""
Workshop provisioning: one copy of a template project per attendee, in one
project folder, visible and editable by the attendee.

Designed to be re-run (idempotent):
  Folder   : workshop group gets read + writeContents on the folder (once,
             before the loop). Without this, attendees could not see their
             projects: the group had been added to the folder by hand with
             every box unticked, and neither the old nor the first refactored
             script ever touched folder permissions.
  Phase 1  : create the user if missing. Duplicate the template and move it
             into the folder only if the project key does not exist yet.
  Phase 2  : ALWAYS overwrite the user and group permission entries and the
             metadata. The script is the source of truth.

The refactored version that broke a workshop treated "project
exists" as "fully configured" and skipped everything on re-runs, had the
group-permission block outside the loop (dead code), and swallowed user
setup errors. Full history and the original script:
docs/reference/workshop-provisioning.md

Confirmed API facts (DSS 14.5.1):
* DSSProjectFolder has no get_permissions() and no get_definition().
  Folder methods: client, create_project, create_sub_folder, delete,
  get_default_folder_for_project_creation, get_name, get_parent, get_path,
  get_settings, id, list_child_folders, list_project_keys, list_projects,
  move_project_to, move_to, name, project_folder_id.
* folder.get_settings() is a DSSProjectFolderSettings with get_name,
  get_owner, get_permissions, get_raw, save, set_name, set_owner.
  get_permissions() returns a FLAT LIST, e.g.
      [{'group': 'workshop_demo', 'read': False, 'writeContents': False, 'admin': False}]
  (project permissions wrap theirs in a 'permissions' key). Mutate the list
  in place, then settings.save().
* User-level project permissions ARE visible in the UI under
  Project > Security, next to the groups and the owner.
* Permissions are additive: a user gets a flag if their own entry or any of
  their groups grants it. The user and group entries were aligned
  (exportDatasetsData True in both) to avoid confusion when reading the
  Security page. The group gets read but not write on projects, so
  attendees cannot edit each other's work. Write comes from the user entry.
* editPermissions = may change who has access to the project. False for
  the group.
* For LDAP users: create_user(password=None, source_type='LDAP') and
  get_settings().save() rather than set_definition().
* A 'data_team' group on the projects comes from the template and is
  expected.

Run the config cell first (and `client = dataiku.api_client()`), otherwise
NameError: name 'client' is not defined.
"""
import re

import dataiku

# ---------------- CONFIG ----------------
client = dataiku.api_client()

PROJECT_TEMPLATE_ID = "<TEMPLATE_PROJECT_KEY>"
PROJECT_FOLDER_ID = "<project-folder-id>"      # from the folder URL, or "" to skip folder steps
DEFAULT_GROUPS = ["workshop_demo"]
# Profile given to new attendee accounts. The value used at the workshop was
# not recorded. Attendees need to write in their project. The old
# script set 'Reader' via set_definition().
DEFAULT_PROFILE = "FULL_DESIGNER"
DISPLAY_PREFIX = "Workshop - "                  # project display name prefix
KEY_PREFIX = "Workshop"                         # sanitised into the key prefix
PROJECT_NAME_SUFFIX = "YYYYMMDD"                # workshop date, part of the key

USERS_ALL = {
    # "First Last": "first.last@company.example",
}
# ----------------------------------------


# --- Helpers ---

def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def sanitize_for_key(text: str) -> str:
    """
    Transliterate a few non-ASCII letters to ASCII (example table, extend it
    for the names you have), then keep only [A-Za-z0-9], uppercase.
    Use ONLY for project keys.
    """
    if not text:
        return ""
    translit = text.translate(str.maketrans({
        'ç': 'c', 'Ç': 'C', 'ş': 's', 'Ş': 'S', 'ğ': 'g', 'Ğ': 'G',
        'ı': 'i', 'İ': 'I', 'ö': 'o', 'Ö': 'O', 'ü': 'u', 'Ü': 'U'
    }))
    return re.sub(r'[^A-Za-z0-9]', '', translit).upper()


def ensure_user(client, login: str, display_name: str, groups=None, source_type='LDAP'):
    """
    Create the user if missing, otherwise return the existing (verified) handle.
    For LDAP users use password=None and prefer get_settings().save() over set_definition().
    """
    groups = groups or []
    try:
        u = client.get_user(login)
        u.get_info()  # verifies existence
        return u
    except Exception:
        pass

    u = client.create_user(
        login,
        password=None if source_type == 'LDAP' else 'unused',
        display_name=display_name,
        source_type=source_type,
        groups=groups,
        profile=DEFAULT_PROFILE,
        email=login
    )
    try:
        settings = u.get_settings()
        raw = settings.get_raw()
        raw['email'] = login
        raw['userProfile'] = DEFAULT_PROFILE
        settings.save()
    except Exception as e:
        print(f"[WARN] Could not save user settings for {login}: {e}")
    return u


def apply_project_permissions(project, user_email: str, user_perm_dict: dict, group_perm_dict: dict):
    """
    Single get/set cycle for both user and group permissions.
    Overwrites existing entries, because the script is the source of truth.
    """
    perms = project.get_permissions()
    perms.setdefault('permissions', [])

    # User entry
    existing_user = next((p for p in perms['permissions'] if p.get('user') == user_email), None)
    if existing_user:
        for k, v in user_perm_dict.items():
            existing_user[k] = v
    else:
        perms['permissions'].append(user_perm_dict)

    # Group entry
    existing_group = next((p for p in perms['permissions'] if p.get('group') == 'workshop_demo'), None)
    if existing_group:
        for k, v in group_perm_dict.items():
            existing_group[k] = v
    else:
        perms['permissions'].append(group_perm_dict)

    project.set_permissions(perms)


# --- Main ---

# Cache existing keys once (faster and correct)
_EXISTING_KEYS = set(client.list_project_keys())
_SAN_KEY_PREFIX = sanitize_for_key(KEY_PREFIX)

# -- FOLDER PERMISSIONS (once, before the loop) --
# Ensures workshop_demo group can read and edit projects inside the folder.
# get_permissions() lives on the settings object, not the folder directly.
if PROJECT_FOLDER_ID:
    try:
        folder = client.get_project_folder(PROJECT_FOLDER_ID)
        folder_settings = folder.get_settings()
        folder_perms = folder_settings.get_permissions()

        folder_group_perm = {
            "group": "workshop_demo",
            "read": True,
            "writeContents": True,
            "admin": False
        }

        existing_folder_group = next(
            (p for p in folder_perms if p.get('group') == 'workshop_demo'), None
        )
        if existing_folder_group:
            for k, v in folder_group_perm.items():
                existing_folder_group[k] = v
        else:
            folder_perms.append(folder_group_perm)

        folder_settings.save()
        print(f"[INFO] Folder permissions updated for workshop_demo on folder: {PROJECT_FOLDER_ID}")
    except Exception as e:
        print(f"[WARN] Could not set folder permissions: {e}")

# -- PER-USER LOOP --
for user_full_name, raw_email in USERS_ALL.items():
    try:
        email = normalize_email(raw_email)

        parts = (user_full_name or "").split(' ', 1)
        first_name = parts[0]
        last_name = parts[1] if len(parts) > 1 else ""

        san_first = sanitize_for_key(first_name)
        san_last = sanitize_for_key(last_name)

        project_name = f"{DISPLAY_PREFIX}{first_name} {last_name}".strip()
        project_key = f"{_SAN_KEY_PREFIX}{san_first}{san_last}_{PROJECT_NAME_SUFFIX}"

        print(f"\n[START] {user_full_name} <{email}>")
        print(f"[INFO] Project name: {project_name}")
        print(f"[INFO] Project key:  {project_key}")

        # Ensure user exists regardless of whether project needs creating
        ensure_user(
            client, login=email, display_name=user_full_name,
            groups=DEFAULT_GROUPS, source_type='LDAP'
        )

        # -- PHASE 1: CREATE (skip if already exists) --
        if project_key not in _EXISTING_KEYS:
            template = client.get_project(PROJECT_TEMPLATE_ID)
            template.duplicate(target_project_key=project_key, target_project_name=project_name)
            _EXISTING_KEYS.add(project_key)
            print(f"[INFO] Project duplicated: {project_key}")

            if PROJECT_FOLDER_ID:
                try:
                    folder = client.get_project_folder(PROJECT_FOLDER_ID)
                    client.get_project(project_key).move_to_folder(folder)
                    print(f"[INFO] Project moved to folder: {PROJECT_FOLDER_ID}")
                except Exception as e:
                    print(f"[WARN] Could not move to folder: {e}")
        else:
            print(f"[SKIP CREATE] Project already exists: {project_key}")

        # -- PHASE 2: CONFIGURE (always runs) --
        project = client.get_project(project_key)

        user_permissions = {
            "user": email,
            "admin": False,
            "readProjectContent": True,
            "writeProjectContent": True,
            "exportDatasetsData": True,
            "readDashboards": True,
            "writeDashboards": True,
            "moderateDashboards": False,
            "publishToDataCollections": True,
            "shareToWorkspaces": True,
            "runScenarios": True,
            "manageDashboardAuthorizations": True,
            "manageExposedElements": False,
            "manageAdditionalDashboardUsers": False,
            "executeApp": False,
        }

        workshop_group_perm = {
            "group": "workshop_demo",
            "admin": False,
            "editPermissions": False,
            "readProjectContent": True,
            "writeProjectContent": False,
            "publishToDataCollections": True,
            "shareToWorkspaces": True,
            "exportDatasetsData": True,
            "readDashboards": False,
            "writeDashboards": False,
            "runScenarios": True,
            "manageAuthorizedObjects": False,
            "manageSharedObjects": False,
        }

        apply_project_permissions(project, email, user_permissions, workshop_group_perm)

        # 2c) Metadata
        try:
            meta = project.get_metadata()
            meta['tags'] = meta.get('tags', [])
            meta['shortDesc'] = f"Workshop project for {user_full_name}"
            project.set_metadata(meta)
        except Exception as e:
            print(f"[WARN] Could not set metadata: {e}")

        print("[SUCCESS] Project READY")

    except Exception as e:
        print(f"[ERROR] {user_full_name}: {e}")
