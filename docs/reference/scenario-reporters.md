# Scenario reporters

How we send alerts and reports from DSS scenarios. Used by the vanity URL alert ([vanity-url-audit.md](vanity-url-audit.md)), the monthly licence report ([licence-reporting.md](licence-reporting.md)) and scenario failure notifications to the team mailbox.

## Pattern

1. A Python step does the work and calls `Scenario().set_scenario_variables(name=value, ...)` with the mail body (HTML) and the recipient list.
2. If the reporter should fire, the step fails on purpose with `sys.exit(1)`.
3. A mail reporter with run condition `outcome != 'SUCCESS'` sends `${name}` variables.
4. Steps that must run anyway (cleanup) get the step run condition "Run always".

```python
import sys
from dataiku.scenario import Scenario

s = Scenario()
s.set_scenario_variables(alert_html=fallback_html, alert_recipients=PLATFORM_TEAM_EMAIL)  # fallback first
# ... work ...
s.set_scenario_variables(alert_html=html, alert_recipients=",".join(recipients))
print("FAILED: short one-line summary")
sys.exit(1)
```

Set fallback values at the top of the step. If the scenario fails for another reason the reporter still fires, and without a fallback the mail body is empty or shows the raw variable name.

## Run condition

The reporter's Run condition field takes a bare expression, single quotes, no `${}` and no `if`:

```
outcome != 'SUCCESS'
```

That is also Dataiku's documented default for "notify on failure". The documented comparisons use `SUCCESS`, `FAILED` and `ABORTED`. `outcome != 'SUCCESS'` catches aborted runs and `outcome == 'FAILED'` does not. The `${...}` syntax belongs in the message template, not here. For the simple case, changing the reporter's "Send on scenario" dropdown from End to Failed does the same job.

We first wrote `IF ${outcome} != SUCCESS`. Then `outcome != "SUCCESS"` (double quotes) was suggested. The docs show single quotes, and that is what we use.

## Template type

| Template type | Behaviour we saw |
|---|---|
| FreeMarker | A variable holding HTML was wrapped in quotes and its newlines appeared as literal `\n` in the mail. `${var?no_esc}` failed with: `Syntax error in template "tmpl0" in line 1, column 24: ?no_esc can't be used here, as the current output format isn't a markup (escaping) format: undefined(mimeType=null, class=f.c.UndefinedOutputFormat)` (`freemarker.core.ParseException`). `<#noescape>` was suggested and not tried. |
| Simple | Passes the variable through unchanged. Use this when the whole body is one HTML variable. |

FreeMarker is still right for a hand-written template that uses the built-in variables and logic, such as the monthly licence report (`<#if outcome != "SUCCESS">`, `.now?string("d MMMM yyyy")`). Inside FTL in an HTML body, write comparisons as `&gt;` (`<#if failedStepsCount &gt; 0>`).

## Body content

* Tick Send as HTML, otherwise HTML shows as raw tags.
* Do not put `${allEventsSummary}` in a body meant for people. It contains the step log: the child process lines, the script path, everything the step printed, and any traceback.
* Everything a step prints lands in `${allEventsSummary}` and the scenario log, so keep prints short. `raise Exception(...)` puts a traceback there and `sys.exit(1)` does not.
* When the body is a single variable, the template must be exactly `${var}`. Extra text, or another variable such as the recipient list, around it ended up in the mail as noise.
* Put notes, links and the contact line inside the HTML that Python generates.
* Line breaks in plain-text bodies were stripped, which is another reason for HTML.
* The reporter "Message source" must be Inline for a pasted template (the default is Template file).

## Recipients

* The To field accepts variables: `${duplicate_recipients}` with a comma-separated list. Cc a fixed platform team address so someone always gets it.
* Drop values that are not real addresses before building the list. The `admin` login made sends fail. The filter we use: `'@' in val and '.' in val.split('@')[-1]`.
* `Mail has not been sent to x because its domain is not authorized.` means the recipient's domain is not in the mail channel's authorised domains (Administration > Settings > Notifications & Integrations, the SMTP channel) or the relay refused it. It is not a template problem. Add the domain of every recipient group, including affiliated companies and any separate subdomain used for external contractors.
* The "notification email" field in a user's settings is where DSS sends platform notifications to that user. It has nothing to do with reporters.

## Standard variables

`${scenarioName}`, `${triggerName}`, `${triggerType}`, `${outcome}`, `${allEventsSummary}`, `${warningsEventsSummary}`, `${failedEventsSummary}`, `${allStepsCount}`, `${successStepsCount}`, `${warningStepsCount}`, `${failedStepsCount}`, `${allJobsCount}`, `${successJobsCount}`, `${warningJobsCount}`, `${failedJobsCount}`, `${allBuiltDatasetsCount}`, `${successBuiltDatasetsCount}`, `${warningBuiltDatasetsCount}`, `${failedBuiltDatasetsCount}`, `${firstFailedStepName}`, `${firstFailedJobDisplayName}`, `${dssURL}`, `${scenarioRunURL}`.

None carries a date: use FreeMarker `.now`. None carries business numbers: set your own scenario variables, or (for the Teams card) project variables read as `${variables.name}`.

## Teams

The licence Teams card is a scenario "send message" step posting JSON to a Power Automate flow. The body references project variables as `${variables.<name>}`, strings quoted, numbers and booleans bare. New variables only appear after one full scenario run. Details in [licence-count-snapshot.md](licence-count-snapshot.md#teams-notification).

## Sending mail from code

Not confirmed. See [email-from-python.md](email-from-python.md).
