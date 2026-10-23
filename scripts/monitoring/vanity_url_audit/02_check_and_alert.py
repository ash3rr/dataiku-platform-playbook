# -*- coding: utf-8 -*-
"""
Vanity URL check, step 2: duplicate check and alert (scenario "Execute
Python code" step, runs after step 1 builds VANITY_URL_AUDIT).

Reads the latest CHECK_TIMESTAMP only (the table keeps history, so reading
everything repeated every entry once per past run). If duplicates exist it
builds an HTML report, puts it and the recipient list into scenario
variables, prints one short line and exits with code 1 so the scenario
fails and the mail reporter fires.

Reporter (Settings > Reporters), final working configuration:
    Channel        : SMTP relay - <company> (smtp)
    Run condition  : outcome != 'SUCCESS'
    Send as HTML   : checked
    Template type  : Simple            (NOT FreeMarker, see "Why")
    To             : ${duplicate_recipients}
    Cc             : the platform team address
    Subject        : ALERT: Duplicate Vanity URLs Detected - ${scenarioName}: ${outcome}
    Message body   : ${duplicate_alert_html}      <- nothing else
    Trigger        : daily

Why:
* With FreeMarker the variable came through wrapped in quotes with literal
  \\n sequences, and ${duplicate_alert_html?no_esc} failed with "?no_esc
  can't be used here, as the current output format isn't a markup
  (escaping) format". Template type "Simple" passes the HTML through.
* Anything else in the body (${allEventsSummary}, free text,
  ${duplicate_recipients}) dumped step logs, tracebacks and raw HTML into
  the mail. All wording now lives in the HTML built here.
* raise Exception put a traceback in the mail, and print(message) put the
  raw HTML in it. sys.exit(1) plus one short print keeps it clean.
* The fallback variables at the top make sure a failure BEFORE the check
  (step 1 broke, Snowflake down) still sends a readable mail.
* Recipients must look like emails ('@' and a dot in the domain): 'admin'
  as a creator made sends fail. The SMTP channel only delivers to
  authorised domains. Add each recipient domain there.

The normal webapp URL pattern was confirmed by testing:
    /projects/{PROJECT_KEY}/webapps/{WEBAPP_ID}_{name lowercased, [a-z0-9] only}/view
/projects/{PROJECT_KEY}/webapps/{WEBAPP_ID}/view on its own does NOT work.
"""
import dataiku
import pandas as pd
import re
import sys
from dataiku.scenario import Scenario

DSS_URL = "https://<DSS_HOST>"
PLATFORM_TEAM_EMAIL = "platform-team@company.example"
VANITY_ADMIN_URL = f"{DSS_URL}/admin/general/security-other/#adminSettingUIView"
SCENARIO_URL = f"{DSS_URL}/projects/PLATFORM_MAINTENANCE/scenarios/VANITYURLCHECK/settings"

# Set fallback message in case script fails before duplicate check
s = Scenario()
s.set_scenario_variables(
    duplicate_alert_html="<html><body><p>The vanity URL check scenario failed before the duplicate check could run. "
                         "Please check the scenario logs.</p>"
                         f"<p><a href='{SCENARIO_URL}'>View scenario</a></p></body></html>",
    duplicate_recipients=PLATFORM_TEAM_EMAIL
)

ds = dataiku.Dataset("VANITY_URL_AUDIT")
df = ds.get_dataframe()

latest_check = df['CHECK_TIMESTAMP'].max()
df = df[df['CHECK_TIMESTAMP'] == latest_check]

duplicates = df[df['IS_DUPLICATE'] == True]  # noqa: E712

if len(duplicates) > 0:
    slug_count = duplicates['VANITY_SLUG'].nunique()
    any_running = duplicates['IS_RUNNING'].any()

    html = []
    html.append("<html><body style='font-family: Arial, sans-serif; font-size: 14px;'>")

    if any_running:
        html.append("<h2 style='color: #cc0000;'>CRITICAL: Duplicate Vanity URLs with Running Webapps</h2>")
        html.append("<p style='color: #cc0000;'>Running webapps with duplicate slugs cause nginx routing failures "
                    "and 404 errors for all users.</p>")
    else:
        html.append("<h2 style='color: #e68a00;'>WARNING: Duplicate Vanity URLs Detected</h2>")
        html.append("<p>No affected webapps are currently running, but this will cause issues if they are started.</p>")

    html.append(f"<p><b>{slug_count}</b> duplicate vanity URL slug(s) found. "
                f"A vanity URL is a short URL like <code>{DSS_URL}/public-webapps/my-app</code>. "
                "Each slug can only point to one webapp. When two webapps share a slug, routing breaks.</p>")

    html.append("<table border='1' cellpadding='6' cellspacing='0' style='border-collapse: collapse; font-size: 13px;'>")
    html.append("<tr style='background-color: #333; color: white;'>")
    html.append("<th>Vanity Slug</th><th>Project</th><th>WebApp ID</th><th>Webapp</th><th>Running</th><th>Creator</th>")
    html.append("</tr>")

    for slug in sorted(duplicates['VANITY_SLUG'].unique()):
        slug_rows = duplicates[duplicates['VANITY_SLUG'] == slug]
        for i, (_, row) in enumerate(slug_rows.iterrows()):
            running = row['IS_RUNNING']
            if running:
                row_style = "background-color: #ffe0e0;"
                running_text = "<b style='color: #cc0000;'>YES</b>"
            else:
                row_style = "background-color: #f9f9f9;" if i % 2 == 0 else ""
                running_text = "no"

            name_slug = re.sub(r'[^a-z0-9]', '', str(row['WEBAPP_NAME']).lower())
            webapp_url = f"{DSS_URL}/projects/{row['PROJECT_KEY']}/webapps/{row['WEBAPP_ID']}_{name_slug}/view"

            html.append(f"<tr style='{row_style}'>")
            html.append(f"<td>{row['VANITY_SLUG']}</td>")
            html.append(f"<td>{row['PROJECT_KEY']}</td>")
            html.append(f"<td>{row['WEBAPP_ID']}</td>")
            html.append(f"<td><a href='{webapp_url}'>{row['WEBAPP_NAME']}</a></td>")
            html.append(f"<td>{running_text}</td>")
            html.append(f"<td>{row['CREATOR']}</td>")
            html.append("</tr>")

    html.append("</table>")

    html.append("<h3>How to fix</h3>")
    html.append("<ol>")
    html.append(f"<li>Go to <a href='{VANITY_ADMIN_URL}'>Security &amp; Audit &gt; Other security settings</a></li>")
    html.append("<li>In the <b>Vanity URLs</b> section, find entries with these duplicate slugs:</li>")
    html.append("<ul>")
    for slug in sorted(duplicates['VANITY_SLUG'].unique()):
        html.append(f"<li><code>{slug}</code></li>")
    html.append("</ul>")
    html.append("<li>For each, <b>remove</b> the stale entry or <b>rename</b> its slug "
                "(e.g. append <code>-old</code> or <code>-v2</code>)</li>")
    html.append("<li>Click <b>Save</b></li>")
    html.append("</ol>")

    html.append("<hr>")
    html.append("<p><b>Note:</b> Duplicated Vanity URLs can cause system wide instability and should be rectified "
                "as soon as possible. When a new WebApp is created and the vanity URL should remain the same, "
                "the old entry must be removed.</p>")
    html.append("<p style='font-size: 12px;'>Maintained by: Dataiku platform team | "
                f"<a href='{VANITY_ADMIN_URL}'>Edit Vanity URLs</a> | "
                f"<a href='{SCENARIO_URL}'>Edit this scenario</a></p>")

    html.append("</body></html>")

    message = "\n".join(html)

    # Creator and last modifier of every duplicate row, skipping non-emails like 'admin'
    recipients = set()
    for _, row in duplicates.iterrows():
        for col in ['CREATOR', 'LAST_MODIFIED_BY']:
            val = str(row[col]).strip()
            if '@' in val and '.' in val.split('@')[-1]:
                recipients.add(val)

    s.set_scenario_variables(
        duplicate_alert_html=message,
        duplicate_recipients=",".join(recipients)
    )

    # Short summary only: anything printed here ends up in the scenario log.
    slug_list = list(duplicates['VANITY_SLUG'].unique())
    print(f"FAILED: {len(slug_list)} duplicate vanity URL slugs: {slug_list}")
    sys.exit(1)
else:
    print("OK: No duplicate vanity URLs found.")
