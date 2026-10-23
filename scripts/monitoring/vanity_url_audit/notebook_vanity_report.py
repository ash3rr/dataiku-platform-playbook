# -*- coding: utf-8 -*-
"""
Notebook helpers used while investigating duplicate vanity URLs, before the
scenario existed. Run cell by cell in a DSS notebook.

Cell A finds where DSS keeps the vanity URL mapping (it took several
attempts, and the dead ends are listed in docs/reference/vanity-url-audit.md).
Cell B lists every vanity URL as a DataFrame with a duplicate flag.
Cell C is the KEEP/REMOVE recommendation we drafted for duplicates. It was
NOT adopted: only the webapp owner knows which webapp should keep a slug,
so the scenario mail asks them instead.
Cell D checks project display names (project key vs label).
"""
import json
from collections import defaultdict
from datetime import datetime, timezone

import dataiku
import pandas as pd

client = dataiku.api_client()

# ---------------------------------------------------------------------------
# Cell A: find the setting. Searching KEY names for "vanity" found nothing.
# Searching the JSON VALUES found it under webAppSecuritySettings.
# ---------------------------------------------------------------------------
settings = client.get_general_settings().get_raw()
raw = json.dumps(settings)
for keyword in ['vanityUrl', 'vanityURL', 'publicPath', 'public-webapps']:
    idx = raw.find(keyword)
    if idx >= 0:
        print(f"Found '{keyword}' at index {idx}:")
        print(raw[max(0, idx - 200):idx + 300])
        print("---")
# Result: settings['webAppSecuritySettings'] holds
#   requiredAuthenticationMode   : "ALL_BUT_ADMIN_WHITELIST"
#   requiredAuthenticationWhitelist : ["PROJECTKEY.webappId", ...]  (public webapps)
#   vanityURLs : [{"secret": false, "key": "PROJECTKEY.webappId", "value": "<slug>"}, ...]

# ---------------------------------------------------------------------------
# Cell B: every vanity URL, duplicates first.
# ---------------------------------------------------------------------------
ws = settings.get('webAppSecuritySettings', {})
vanity_urls = ws.get('vanityURLs', [])

by_slug = defaultdict(list)
for entry in vanity_urls:
    by_slug[entry['value']].append(entry['key'])
dup_slugs = {slug for slug, keys in by_slug.items() if len(keys) > 1}

rows = []
for entry in vanity_urls:
    key = entry['key']
    slug = entry['value']
    parts = key.split('.')
    project_key = parts[0]
    webapp_id = parts[1] if len(parts) > 1 else ''
    name = created = modified = creator = ''
    created_ts = 0
    running = '?'
    try:
        p = client.get_project(project_key)
        wa = p.get_webapp(webapp_id)
        wraw = wa.get_settings().get_raw()
        name = wraw.get('name', '')
        ct = wraw.get('creationTag', {})
        vt = wraw.get('versionTag', {})
        created_ts = ct.get('lastModifiedOn', 0)
        modified_ts = vt.get('lastModifiedOn', 0)
        if created_ts:
            created = datetime.fromtimestamp(created_ts / 1000, tz=timezone.utc).strftime('%Y-%m-%d')
        if modified_ts:
            modified = datetime.fromtimestamp(modified_ts / 1000, tz=timezone.utc).strftime('%Y-%m-%d')
        creator = ct.get('lastModifiedBy', {}).get('login', '').split('@')[0]
        try:
            running = wa.get_state().running
        except Exception:
            pass
    except Exception:
        name = 'ERROR'
    rows.append({
        'slug': slug,
        'project': project_key,
        'webapp_key': key,
        'webapp_name': name,
        'created': created,
        'last_modified': modified,
        'creator': creator,
        'running': running,
        'duplicate': slug in dup_slugs,
        'created_ts': created_ts or 0,
    })

# Long columns pushed the duplicate flag off screen, so narrow them.
pd.set_option('display.max_colwidth', 30)
pd.set_option('display.width', 200)

# reset_index: otherwise the left-most index column shows the pre-sort order.
df = (pd.DataFrame(rows)
      .sort_values(['duplicate', 'slug', 'created'], ascending=[False, True, True])
      .reset_index(drop=True))
df['dup'] = df['duplicate'].map({True: 'YES', False: ''})
df[['dup', 'slug', 'project', 'webapp_name', 'created', 'creator', 'running']]

# ---------------------------------------------------------------------------
# Cell C: drafted KEEP/REMOVE heuristic (not adopted).
# One running -> keep it. Otherwise keep the most recently created.
# ---------------------------------------------------------------------------
dups = df[df['duplicate']].copy()


def recommend(group):
    group = group.copy()
    running_mask = group['running'] == True  # noqa: E712
    if running_mask.sum() == 1:
        group['action'] = running_mask.map({True: 'KEEP', False: 'REMOVE'})
    else:
        newest = group['created_ts'].idxmax()
        group['action'] = 'REMOVE'
        group.loc[newest, 'action'] = 'KEEP'
    return group


if len(dups):
    dups = dups.groupby('slug', group_keys=False).apply(recommend)
    dups.sort_values(['slug', 'action']).reset_index(drop=True)

# ---------------------------------------------------------------------------
# Cell D: project key vs display name. The key is what URLs and the API use,
# and the label is what people search for in the UI.
# ---------------------------------------------------------------------------
for pk in sorted({e['key'].split('.')[0] for e in vanity_urls if e['value'] in dup_slugs}):
    try:
        print(f"Key: {pk} | Label: {client.get_project(pk).get_metadata().get('label', 'none')}")
    except Exception as e:
        print(f"Key: {pk} | Error: {e}")
