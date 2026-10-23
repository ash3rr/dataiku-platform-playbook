# -*- coding: utf-8 -*-
"""
Sending mail from Python through the DSS mail channel: NOT CONFIRMED WORKING.

Goal: from a scenario Python step, mail the CREATOR and
LAST_MODIFIED_BY of the duplicate rows in the latest VANITY_URL_AUDIT run,
through the mail channel already configured in DSS ("SMTP relay - <company>"),
without smtplib and without calling REST endpoints by hand.

What we know for sure:
* [m for m in dir(dataiku) if 'mail' in m.lower() or 'message' in m.lower()]
  returns ['MessageSender'].
* dataiku.MessageSender() with no argument fails:
      TypeError: __init__() missing 1 required positional argument: 'channel_id'
* We stopped at dataiku.MessageSender("SMTP relay - <company>") followed by
  dir(sender). Whether the display name is accepted as channel_id (or an
  internal id like "smtp-relay-..." is needed), and what the send method is
  called, were never confirmed.

What we used instead: scenario variables + a mail reporter
(see scripts/monitoring/vanity_url_audit/02_check_and_alert.py and
docs/reference/email-from-python.md, which also lists every rejected
approach and why).

Do not copy the recipient logic below into production as is: it takes
CREATOR / LAST_MODIFIED_BY from the FIRST row of the latest run, not from the
duplicate rows. The working version in 02_check_and_alert.py collects them
from every duplicate row.
"""
import dataiku

CHANNEL_ID = "SMTP relay - <company>"   # display name from the UI, unconfirmed as an id

ds = dataiku.Dataset("VANITY_URL_AUDIT")
df = ds.get_dataframe()

latest = df[df['CHECK_TIMESTAMP'] == df['CHECK_TIMESTAMP'].max()]
dupes = latest[latest['IS_DUPLICATE'] == True]  # noqa: E712

recipients = list({latest['CREATOR'].iloc[0], latest['LAST_MODIFIED_BY'].iloc[0]} - {''})

body = f"Duplicate vanity slugs detected:\n\n{dupes.to_string(index=False)}"

# This is as far as we got. Inspect the object and wire up the send call.
sender = dataiku.MessageSender(CHANNEL_ID)
print(dir(sender))

# ---------------------------------------------------------------------------
# Next step if picking this up again (untested):
#   help(sender)            -> look for send/send_mail and its arguments
#   then call it with sender address noreply@<company-domain>, recipients,
#   subject and body, on a scenario step, against one test recipient whose
#   domain is in the channel's authorised domains.
# ---------------------------------------------------------------------------
