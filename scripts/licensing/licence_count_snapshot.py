# -*- coding: utf-8 -*-
"""
Daily licence count snapshot, contract threshold flag, and trending row.

Runs as step 1 of the PLATFORM_MAINTENANCE licence scenario (a Python
recipe whose output is platform_maintenance_license_trending). It:

1. Counts ENABLED users per userProfile, split into
     non-trial   : trialStatus.exists == False
     valid trial : trialStatus.valid  == True
   Users with an EXPIRED trial (exists True, valid False) fall into neither
   bucket on purpose. That is what makes full_designer_count match the
   licence page.
2. Writes the counts into project variables. The scenario's Teams message
   step reads them as ${variables.<name>} (see scripts/licensing/teams/).
3. Appends one row per day to the trending dataset, dedup by date.

Profile notes (DSS 14.5.1, confirmed against the licence page)
-------------------------------------------------------------
* READER is the legacy API name for AI Consumer. The UI shows both under
  "AI Consumer": API AI_CONSUMER + READER = the UI figure exactly. So
  they are merged into ai_consumer_and_reader_count.
* TECHNICAL_ACCOUNT has its own quota and is tracked separately.
* The other profiles on the licence page had 0/0 seats and are ignored.

Build it with a FORCED build step in the scenario. A smart build sees the
recipe unchanged and logs "...: not required, pruning" every day until the
code changes.

The Snowflake table column order must match today_row exactly
(scripts/licensing/licence_trending_table.sql), or the write fails with
"Name mismatch for column 3".

History of how this got here: docs/reference/licence-count-snapshot.md
"""
import dataiku
import pandas as pd
from collections import Counter
from datetime import date

# ---------------- CONFIG ----------------
# Alert threshold for FULL_DESIGNER seats. Set to your contract (it can be
# lower than the number of seats on the licence).
CONTRACTED_FULL_DESIGNERS = 100  # set to your contract
# ----------------------------------------

# NOTE: READER and AI_CONSUMER are the same license type in Dataiku.
# READER is the legacy API name and AI_CONSUMER is the current name.
# The UI combines both under "AI Consumer".
# We merge them here to match the UI count.

client = dataiku.api_client()
users = client.list_users()

non_trial_profiles = [
    u.get('userProfile', 'UNKNOWN')
    for u in users
    if u.get('enabled')
    and not u.get('trialStatus', {}).get('exists', False)
]

trial_profiles = [
    u.get('userProfile', 'UNKNOWN')
    for u in users
    if u.get('enabled')
    and u.get('trialStatus', {}).get('valid', False)
]

non_trial_counts = Counter(non_trial_profiles)
trial_counts = Counter(trial_profiles)

full_designer_count = non_trial_counts.get("FULL_DESIGNER", 0)
technical_account_count = non_trial_counts.get("TECHNICAL_ACCOUNT", 0)
ai_consumer_and_reader_count = non_trial_counts.get("AI_CONSUMER", 0) + non_trial_counts.get("READER", 0)

trial_full_designer_count = trial_counts.get("FULL_DESIGNER", 0)
trial_technical_account_count = trial_counts.get("TECHNICAL_ACCOUNT", 0)
trial_ai_consumer_and_reader_count = trial_counts.get("AI_CONSUMER", 0) + trial_counts.get("READER", 0)

total_non_trial = sum(non_trial_counts.values())
total_trial = sum(trial_counts.values())

# Contracted FULL_DESIGNER seats used as the alert threshold.
threshold_reached = full_designer_count > CONTRACTED_FULL_DESIGNERS

project = client.get_project(dataiku.default_project_key())
variables = project.get_variables()
variables["standard"].update({
    "full_designer_count": full_designer_count,
    "technical_account_count": technical_account_count,
    "trial_full_designer_count": trial_full_designer_count,
    "trial_technical_account_count": trial_technical_account_count,
    "total_non_trial_users": total_non_trial,
    "total_trial_users": total_trial,
    "threshold_reached": threshold_reached,
    "status_message": "Threshold exceeded!"
                      if threshold_reached else "Within limit",
    "ai_consumer_and_reader_count": ai_consumer_and_reader_count,
    "trial_ai_consumer_and_reader_count": trial_ai_consumer_and_reader_count,
})
project.set_variables(variables)

# Column order here = column order of the Snowflake table.
today_row = pd.DataFrame([{
    "date": date.today().isoformat(),
    "full_designer_count": full_designer_count,
    "technical_account_count": technical_account_count,
    "trial_full_designer_count": trial_full_designer_count,
    "trial_technical_account_count": trial_technical_account_count,
    "total_non_trial_users": total_non_trial,
    "total_trial_users": total_trial,
    "ai_consumer_and_reader_count": ai_consumer_and_reader_count,
    "trial_ai_consumer_and_reader_count": trial_ai_consumer_and_reader_count,
}])

# Read existing data or start fresh on first run (the dataset does not
# exist yet the first time, so the read throws).
output_dataset = dataiku.Dataset("platform_maintenance_license_trending")
try:
    existing_df = output_dataset.get_dataframe()
except Exception:
    existing_df = pd.DataFrame()

# Dedup by date in case the recipe runs more than once per day
platform_maintenance_license_trending_df = (
    pd.concat([existing_df, today_row], ignore_index=True)
    .drop_duplicates(subset=["date"], keep="last")
)
output_dataset.write_with_schema(platform_maintenance_license_trending_df)

print("Project variables updated successfully!")
print(f"FULL_DESIGNER              (non-trial): {full_designer_count}")
print(f"TECHNICAL_ACCOUNT          (non-trial): {technical_account_count}")
print(f"AI_CONSUMER+READER         (non-trial): {ai_consumer_and_reader_count}  (should match the UI)")
print(f"FULL_DESIGNER              (trial):     {trial_full_designer_count}")
print(f"TECHNICAL_ACCOUNT          (trial):     {trial_technical_account_count}")
print(f"AI_CONSUMER+READER         (trial):     {trial_ai_consumer_and_reader_count}")
print(f"Total non-trial: {total_non_trial} | Total trial: {total_trial}")
print(f"Total rows in trending dataset: {len(platform_maintenance_license_trending_df)}")
print(f"FULL_DESIGNER (expired trial, excluded): "
      f"{sum(1 for u in users if u.get('enabled') and u.get('userProfile') == 'FULL_DESIGNER' and u.get('trialStatus', {}).get('exists') and not u.get('trialStatus', {}).get('valid'))}")
