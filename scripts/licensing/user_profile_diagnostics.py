# -*- coding: utf-8 -*-
"""
Diagnostics used to make the licence counts match the DSS licence page.

Run cell by cell in a DSS notebook. Each block answers one question we hit
while reconciling API counts with Administration > Licensing. The outputs
described in the comments are from DSS 14.5.1 on our instance.

Full story: docs/reference/licence-count-snapshot.md
"""
import json
from collections import Counter

import dataiku

client = dataiku.api_client()
users = client.list_users()

# ---------------------------------------------------------------------------
# 1. What does trialStatus look like? (answer: it is ALWAYS a populated dict)
# ---------------------------------------------------------------------------
# Normal user        : {'exists': False, 'expired': False, 'valid': False, ...}
# Valid trial        : {'exists': True,  'expired': False, 'valid': True,  ...}
# Expired trial      : {'exists': True,  'expired': True,  'valid': False, ...}
# Full expired example:
#   'trialStatus': {'exists': True, 'expired': True, 'illegal': False,
#                   'valid': False, 'grantedOn': <ms>, 'expiresOn': <ms>,
#                   'invalidReason': 'Expired (on <ISO timestamp>)'}
# There is no 'token' field, so filtering on trialStatus.get('token') did
# nothing. And `not u.get('trialStatus')` is False for EVERY user (every
# enabled user "had" a trialStatus), which drove full_designer_count to 0.
count = 0
for u in users:
    if u.get('enabled') and u.get('userProfile') == 'FULL_DESIGNER':
        print(f"{u.get('login')} | trialStatus: {u.get('trialStatus')}")
        count += 1
        if count >= 10:
            break

# ---------------------------------------------------------------------------
# 2. Raw profile distribution, enabled and disabled
# ---------------------------------------------------------------------------
# All profile types found: TECHNICAL_ACCOUNT, NONE, FULL_DESIGNER,
#   AI_CONSUMER, READER (counts summed to len(users))
print("All profile types found:", dict(Counter(u.get('userProfile') for u in users)))

all_ai = [u for u in users if u.get('userProfile') == 'AI_CONSUMER']
print(f"AI_CONSUMER total (all): {len(all_ai)}")
print(f"AI_CONSUMER enabled: {sum(1 for u in all_ai if u.get('enabled'))}")
print(f"AI_CONSUMER disabled: {sum(1 for u in all_ai if not u.get('enabled'))}")     # only one: not the gap
print(f"Total users from API: {len(users)}")

# Expired trials per profile (many for FULL_DESIGNER and READER, a couple for AI_CONSUMER)
for prof in ("FULL_DESIGNER", "AI_CONSUMER", "READER"):
    n = sum(1 for u in users
            if u.get('enabled') and u.get('userProfile') == prof
            and u.get('trialStatus', {}).get('exists')
            and not u.get('trialStatus', {}).get('valid'))
    print(f"{prof:13} (expired trial): {n}")

# ---------------------------------------------------------------------------
# 3. resultingUserProfile (the effective profile after trial expiry)
# ---------------------------------------------------------------------------
# resultingUserProfile counts: same five profiles.
# AI_CONSUMER + READER = the licence page's "AI Consumer" line.
resulting = Counter(u.get('resultingUserProfile', 'UNKNOWN') for u in users if u.get('enabled'))
print("resultingUserProfile counts:", dict(resulting))

# ---------------------------------------------------------------------------
# 4. Full raw user dict, to see every field the API returns
# ---------------------------------------------------------------------------
# Keys seen: activeWebSocketSesssions (sic), login, sourceType, displayName,
# groups, email, userProfile, creationDate, npsSurveySettings,
# atSurveySettings, enabled, objectImgHash, trialStatus, resultingUserProfile
for u in users:
    if u.get('userProfile') == 'AI_CONSUMER':
        print(json.dumps(u, indent=2, default=str))
        break

# ---------------------------------------------------------------------------
# 5. Do groups carry a licence profile? (answer: no userProfile on groups)
# ---------------------------------------------------------------------------
for g in client.list_groups():
    print(g)

# ---------------------------------------------------------------------------
# 6. Group membership of FULL_DESIGNERs (tested the "group override" theory,
#    which turned out to be wrong. The real cause was expired trials.)
# ---------------------------------------------------------------------------
for u in users:
    if u.get('enabled') and u.get('userProfile') == 'FULL_DESIGNER':
        print(f"{u.get('login')} | groups: {u.get('groups', [])}")
