# -*- coding: utf-8 -*-
"""
Inspect Code Studio templates: build dates, a single build's real result,
and the settings fields. Cell by cell in a DSS notebook.

These are the cells that showed our first bulk rebuild had silently failed.
"""
import json
from datetime import datetime, timezone

import dataiku

client = dataiku.api_client()
templates = client.list_code_studio_templates()

# Cell 1: what attributes does a list item have?
if templates:
    print("Available attributes:", [a for a in dir(templates[0]) if not a.startswith('_')])
    # ['build_for_configs', 'client', 'id', 'label', 'last_built', 'to_code_studio_template']

# Cell 2: last build date per template (tz-aware datetime or None)
for t in templates:
    print(f"{t.label} ({t.id}) - last built: {t.last_built}")

# Cell 3: which ones are older than the upgrade?
UPGRADE_DATE = datetime(2000, 1, 1, tzinfo=timezone.utc)  # placeholder: set to the date of your DSS upgrade
for t in templates:
    if t.last_built is not None and t.last_built < UPGRADE_DATE:
        print(f"WOULD REBUILD: {t.label} - last built: {t.last_built}")

# Cell 4: build ONE template and print the whole result. This is how we
# found that wait_for_result() returns success False instead of raising.
TEMPLATE_ID = "<TEMPLATE_ID>"
handle = client.get_code_studio_template(TEMPLATE_ID)
future = handle.build()
result = future.wait_for_result()
print(result)
print("success:", result.get("messages", {}).get("success"))

# Cell 5: settings, timestamps and container confs
settings = handle.get_settings().get_raw()
print("All keys:", list(settings.keys()))
print("versionTag:", settings.get("versionTag"))      # definition edit, not build
print("creationTag:", settings.get("creationTag"))
print("containerConfs:", settings.get("containerConfs"))
print(json.dumps(settings.get("params", {}), indent=2, default=str)[:2000])
# params.blocks lists the template blocks, e.g. dss_base_image (synced
# zones), simple_deployment (k8s params), pycdstdioblk_code-studio-blocks_vscode,
# pycdstdioblk_code-studio-blocks_streamlit. The built-in Streamlit block
# has no "Exposed location" field (relevant to the vendor's "/?foo=bar"
# workaround for the nginx "invalid port in upstream" bug).
