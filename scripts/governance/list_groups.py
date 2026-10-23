# -*- coding: utf-8 -*-
"""
Print every DSS group and its metadata to screen (DSS notebook or recipe,
internal client).

list_groups() returns a plain list of dicts, so json.dumps prints them as
is. Each dict carries at least name, admin, description and sourceType
(LOCAL, LDAP, ...). Groups carry permissions only: when we checked during
the licence reconciliation, no group had a userProfile field, so licence
profiles are not assigned through groups on this instance.

Not confirmed: whether the dicts include a 'users' member list. A
running-total example that summed len(group.get("users", [])) across
groups was written but never run. If the key is absent it silently sums 0.
"""
import json

import dataiku

client = dataiku.api_client()
groups = client.list_groups()

print(f"Found {len(groups)} group(s)\n")
for group in groups:
    print(json.dumps(group, indent=2))
    print("-" * 40)

# Unconfirmed: members per group (see docstring)
# total_users = 0
# for group in client.list_groups():
#     member_count = len(group.get("users", []))
#     total_users += member_count
# print(f"Total users across all groups: {total_users}")
