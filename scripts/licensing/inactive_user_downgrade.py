# -*- coding: utf-8 -*-
"""
Inactive user licence downgrade (Python recipe, scheduled daily by a scenario).

Rules
-----
* FULL_DESIGNER, account older than 90 days, no successful login AND no
  session activity for 90 days            -> READER
* AI_CONSUMER or READER, account older than 180 days, no login AND no
  session activity for 180 days           -> NONE  (NONE is deliberate: taking
  the licence away is how the user is disabled)
* Skipped entirely: logins on the exception list, logins ending in an
  excluded mail domain (separate licence agreement), and anything
  that looks like a service account (prefix / substring match below).

Inputs / outputs
----------------
Input  : snowflake_dataiku_license_exception_list   (column: login)
         Originally an uploaded CSV, moved to Snowflake with a Sync recipe.
         When swapping the input, change BOTH the dataset name below and the
         recipe's Inputs/Outputs tab.
Output : inactive_designers  (Snowflake). The dataset has "Append instead of
         overwrite" switched on, so each run only writes its new rows. Do not
         read + concat + rewrite the history yourself (see the reference doc).

Things that bit us (details in docs/reference/inactive-user-downgrade.md)
-----------------------------------------------------------------------
* raise SystemExit() for the dry run marked the recipe FAILED.
* Read + concat against an append-mode Snowflake dataset failed on the
  first run and worked on the second.
* Writing ISO strings gave "Type mismatch ... 'VARCHAR' in dataset,
  'TIMESTAMP'(93:TIMESTAMPNTZ) in table". Keep native datetimes AND fix the
  DSS dataset schema to "Date/time without timezone".
* get_activity() returns datetimes (or None), and safe_dt() treats None as
  1970.

Production ran with DRY_RUN = False. Start with True on a new instance and
read the per-user debug line before letting it change anything.
"""
import dataiku
import pandas as pd
from datetime import datetime, timedelta, timezone

############################################
# CONFIG
############################################
DRY_RUN = True  # <<< SET THIS TO False FOR REAL UPDATES

NOW = datetime.now(timezone.utc)
DESIGNER_INACTIVITY = timedelta(days=90)
AI_INACTIVITY = timedelta(days=180)

# Logins (which are email addresses on this instance) in this domain are
# never touched. Set to the domain of users on a separate licence agreement.
EXCLUDED_EMAIL_SUFFIX = "@excluded-domain.example"

############################################
# LOAD EXCEPTIONS
############################################
client = dataiku.api_client()
exceptions_df = dataiku.Dataset("snowflake_dataiku_license_exception_list").get_dataframe()
exceptions_list = set(exceptions_df["login"].tolist())

############################################
# COLLECT ACTIONS
############################################
to_disable_designers = []
to_disable_ai = []

all_users = client.list_users()


def safe_dt(dt):
    """Treat None as far past."""
    return dt if dt is not None else datetime(1970, 1, 1, tzinfo=timezone.utc)


for user in all_users:
    login = user["login"]
    if login in exceptions_list:
        continue

    if login.lower().endswith(EXCLUDED_EMAIL_SUFFIX):
        print(f"Skipping excluded-domain email: {login}")
        continue

    profile = (user["userProfile"] or "").strip().upper()
    created = datetime.fromtimestamp(user["creationDate"] / 1000, tz=timezone.utc)

    # --- SERVICE ACCOUNT FILTER ---
    service_like = (
        login.startswith(("svc_", "service_", "robot_", "tech_"))
        or "monitor" in login.lower()
        or "monitoring" in login.lower()
        or "ci_cd" in login.lower()
        or "automation" in login.lower()
        or "batch" in login.lower()
    )
    if service_like:
        print(f"Skipping service account: {login}")
        continue

    activity = client.get_user(login).get_activity()
    last_login = safe_dt(activity.last_successful_login)
    last_session = safe_dt(activity.last_session_activity)

    inactive_90 = last_login < NOW - DESIGNER_INACTIVITY and last_session < NOW - DESIGNER_INACTIVITY
    inactive_180 = last_login < NOW - AI_INACTIVITY and last_session < NOW - AI_INACTIVITY

    # DEBUG LINE - remove once counts look correct
    print(f"{login} | profile={profile} | created={created.date()} | last_login={last_login.date()} "
          f"| last_session={last_session.date()} | inactive_90={inactive_90} | inactive_180={inactive_180}")

    # FULL DESIGNER RULE
    if profile == "FULL_DESIGNER":
        if created < NOW - DESIGNER_INACTIVITY and inactive_90:
            to_disable_designers.append({
                "login": login,
                "user_profile": profile,
                "creation_date": created,
                "last_login": last_login,
                "last_session_activity": last_session,
                "run_timestamp": NOW,
                "new_profile": "READER"
            })

    # AI_CONSUMER / READER RULE
    if profile in ("AI_CONSUMER", "READER"):
        if created < NOW - AI_INACTIVITY and inactive_180:
            to_disable_ai.append({
                "login": login,
                "user_profile": profile,
                "creation_date": created,
                "last_login": last_login,
                "last_session_activity": last_session,
                "run_timestamp": NOW,
                "new_profile": "NONE"
            })

############################################
# SUMMARY OUTPUT
############################################
print("====================================")
print("DRY RUN:", DRY_RUN)
print("====================================")
print("FULL DESIGNERS to downgrade:", len(to_disable_designers))
print("AI_CONSUMERS to downgrade:", len(to_disable_ai))

if to_disable_designers:
    print("\n---- FULL DESIGNER DOWNGRADES ----")
    for row in to_disable_designers:
        print(f"{row['login']}  ->  READER")

if to_disable_ai:
    print("\n---- AI_CONSUMER DOWNGRADES ----")
    for row in to_disable_ai:
        print(f"{row['login']}  ->  NONE")

############################################
# APPLY CHANGES (skipped in dry run)
############################################
if DRY_RUN:
    # Do not use raise SystemExit() here: in a recipe it marks the job FAILED.
    print("\nDRY RUN MODE - NO CHANGES APPLIED.")
else:
    def update_profile(login, new_profile):
        try:
            u = client.get_user(login)
            s = u.get_settings()
            s.get_raw()["userProfile"] = new_profile
            s.save()
            print(f"Updated {login} -> {new_profile}")
        except Exception as e:
            print(f"[WARN] Failed to update {login}: {e}")

    for row in to_disable_designers:
        update_profile(row["login"], "READER")

    for row in to_disable_ai:
        update_profile(row["login"], "NONE")

    ############################################
    # LOG TO DATASET (append mode is set on the dataset itself)
    ############################################
    log_rows = to_disable_designers + to_disable_ai
    if log_rows:
        desired_cols = [
            "login", "user_profile", "creation_date",
            "last_login", "last_session_activity",
            "run_timestamp", "new_profile",
        ]
        df_new = pd.DataFrame(log_rows)
        for c in desired_cols:
            if c not in df_new.columns:
                df_new[c] = None
        df_new = df_new[desired_cols]

        # Native datetimes only. strftime() to ISO strings caused the
        # VARCHAR vs TIMESTAMP_NTZ schema mismatch on the Snowflake table.
        for c in ["creation_date", "last_login", "last_session_activity", "run_timestamp"]:
            df_new[c] = pd.to_datetime(df_new[c], utc=True, errors="coerce")

        dataiku.Dataset("inactive_designers").write_with_schema(df_new)
        print(f"Logged {len(log_rows)} row(s) to inactive_designers.")
    else:
        print("No changes to log.")

print("Done.")
