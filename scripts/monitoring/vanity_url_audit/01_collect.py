# -*- coding: utf-8 -*-
"""
Vanity URL check, step 1: collect every vanity URL mapping and flag duplicates.

Python recipe in PLATFORM_MAINTENANCE, output dataset VANITY_URL_AUDIT
(Snowflake table PLATFORM_MAINTENANCE_VANITY_URL_AUDIT, see 00_schema.sql).
The scenario builds it as its first step, every day.

Why: a vanity URL maps a slug to "PROJECTKEY.webappId". Admins type both by
hand in Administration > Settings > Security & Audit > Other security
settings, and DSS does not check that slugs are unique. Two RUNNING webapps
on the same slug broke nginx routing and gave 404s on webapps across the
whole instance after the 14.5.1 upgrade.

Where the mapping lives (found by searching the JSON of get_general_settings):
    client.get_general_settings().get_raw()['webAppSecuritySettings']['vanityURLs']
    -> [{"secret": False, "key": "PROJECT.webappId", "value": "<slug>"}, ...]

Each run stamps every row with the same CHECK_TIMESTAMP. Step 2 only looks
at the latest stamp, and step 3 deletes rows older than six months.

Look the webapp up directly with project.get_webapp(id). Building a lookup
from project.list_web_apps() across all projects gave '?' for every row.
"""
import dataiku
import pandas as pd, numpy as np
from dataiku import pandasutils as pdu
from collections import defaultdict

client = dataiku.api_client()
settings = client.get_general_settings().get_raw()
ws = settings.get('webAppSecuritySettings', {})
vanity_urls = ws.get('vanityURLs', [])

# Find duplicate slugs
by_slug = defaultdict(list)
for entry in vanity_urls:
    by_slug[entry['value']].append(entry['key'])
dup_slugs = {slug for slug, keys in by_slug.items() if len(keys) > 1}

now = pd.Timestamp.utcnow()

rows = []
for entry in vanity_urls:
    key = entry['key']
    slug = entry['value']
    parts = key.split('.')
    project_key = parts[0]
    webapp_id = parts[1] if len(parts) > 1 else ''
    name = creator = modifier = ''
    created = None
    modified = None
    running = None
    try:
        p = client.get_project(project_key)
        wa = p.get_webapp(webapp_id)
        raw = wa.get_settings().get_raw()
        name = raw.get('name', '')
        ct = raw.get('creationTag', {})
        vt = raw.get('versionTag', {})
        created_ts = ct.get('lastModifiedOn', 0)
        modified_ts = vt.get('lastModifiedOn', 0)
        if created_ts:
            created = pd.Timestamp(created_ts, unit='ms')
        if modified_ts:
            modified = pd.Timestamp(modified_ts, unit='ms')
        creator = ct.get('lastModifiedBy', {}).get('login', '')
        modifier = vt.get('lastModifiedBy', {}).get('login', '')
        try:
            running = wa.get_state().running
        except:
            pass
    except:
        # Mapping points at a webapp or project that no longer exists
        name = 'LOOKUP_FAILED'
    rows.append({
        'CHECK_TIMESTAMP': now,
        'VANITY_SLUG': slug,
        'WEBAPP_KEY': key,
        'PROJECT_KEY': project_key,
        'WEBAPP_ID': webapp_id,
        'WEBAPP_NAME': name,
        'CREATED_AT': created,
        'LAST_MODIFIED_AT': modified,
        'CREATOR': creator,
        'LAST_MODIFIED_BY': modifier,
        'IS_RUNNING': running,
        'IS_DUPLICATE': slug in dup_slugs,
    })

VANITY_URL_AUDIT_df = pd.DataFrame(rows)

# Write recipe outputs
VANITY_URL_AUDIT = dataiku.Dataset("VANITY_URL_AUDIT")
VANITY_URL_AUDIT.write_with_schema(VANITY_URL_AUDIT_df)

print(f"Written {len(VANITY_URL_AUDIT_df)} rows, {VANITY_URL_AUDIT_df['IS_DUPLICATE'].sum()} duplicates")
