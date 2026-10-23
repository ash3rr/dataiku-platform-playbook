# Email from Python

We never got a confirmed way to send mail from Python code inside DSS. What works, and what all our alerts use, is a scenario mail reporter fed by scenario variables that a Python step sets. See [scenario-reporters.md](scenario-reporters.md) and the vanity URL alert in [vanity-url-audit.md](vanity-url-audit.md).

The attempt is kept in [`scripts/monitoring/email/message_sender_attempt.py`](../../scripts/monitoring/email/message_sender_attempt.py), marked **not confirmed**.

## The goal

From a scenario step or notebook, send one mail to the people in the `CREATOR` and `LAST_MODIFIED_BY` columns of the latest run of `VANITY_URL_AUDIT`, through the SMTP mail channel that is already configured in DSS (shown in the UI as the SMTP relay channel, sender address the design node's no-reply address).

The reporter UI was first thought unable to do this ("it can't loop over rows"). That turned out to be wrong in practice: a Python step can compute the recipient list and put it into a scenario variable that the reporter's To field reads (`${duplicate_recipients}`). That is the route we kept.

## Attempts, in order

| Attempt | Result |
|---|---|
| `dataiku.api_client().get_root().send_mail(sender=..., recipients=..., subject=..., body=...)` | Invented method. Not run. |
| `client.get_messaging_channel("<channel name>").send_mail(...)` | Invented method. Not run. |
| `smtplib.SMTP("<relay host>")` with a `MIMEText` message | Would bypass the DSS channel and need the relay host. Declined: test the existing channel first. |
| `client.perform_api_call("POST", "/messaging/send", body={"channelId": ..., "recipients": ..., ...})` | Guessed public REST endpoint and payload. Rejected: we use the internal API, not external calls. |
| `dataiku.api_client().get_internal_client().send_mail(channel=..., ...)` | Invented method. Not run. |
| `[m for m in dir(dataiku) if 'mail' in m.lower() or 'message' in m.lower()]` | Returned `['MessageSender']`. |
| `dir(dataiku.MessageSender())` | `TypeError: __init__() missing 1 required positional argument: 'channel_id'` |
| `dataiku.MessageSender("<channel display name>")` then `dir(sender)` | Errored when run in the same cell as the failing line above. Run on its own, the output was never recorded. |

Also mentioned and not tried: `dataiku.get_custom_variables()` for passing values, a `dataiku.core.messaging` module, a Swagger page at `https://<DSS_HOST>/public-api/`, and the scenario API's `Scenario().send_message(sender=..., channel_id=..., subject=..., message=..., recipient=...)` (drafted in the vanity work with a placeholder channel id and never run).

## Finding the channel id

Also unresolved:

* `get_general_settings().get_raw()["notifications"]` had only `notifyOnUserConnection: NOBODY` and `notifyOnTaskStartAndStop: INITIATOR`. There is no `channels` list there.
* `mailSettings` was `{}` even though the SMTP channel is configured and working in the UI. Messaging channels are not in general settings.
* `client._perform_json("GET", "/admin/messaging-channels")` was rejected as an external API call.
* The channel id may differ from its display name. Variants like a slugified name or `smtp` were suggested and not tested.

## Known bug in the drafted script

The last draft built recipients as:

```python
recipients = list({latest['CREATOR'].iloc[0], latest['LAST_MODIFIED_BY'].iloc[0]} - {''})
```

That takes the first row of the whole latest run, not the duplicate rows, so it emails the creator of whatever webapp sorts first. The reporter version collects from every duplicate row and drops values without an `@` and a dotted domain (the `admin` login).

## If someone picks this up

1. In a notebook: `s = dataiku.MessageSender("<channel id>")` alone in a cell, then `print([m for m in dir(s) if not m.startswith('_')])` and `help(s)`.
2. Get the channel id from Administration > Settings > Notifications & Integrations (messaging channels), not from guessed endpoints.
3. Recipient domains still have to be in the channel's authorised domains, as with the reporter.
