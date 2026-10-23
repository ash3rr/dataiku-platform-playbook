# Licence reporting

| Piece | File | Output | Detail |
|---|---|---|---|
| Daily snapshot of seats per profile, plus the threshold flag | `scripts/licensing/licence_count_snapshot.py` | `platform_maintenance_license_trending` + project variables | [licence-count-snapshot.md](licence-count-snapshot.md) |
| Weekly Teams card with the counts | `scripts/licensing/teams/` | Adaptive Card in a Teams channel | [licence-count-snapshot.md](licence-count-snapshot.md#teams-notification) |
| Daily reclaim of unused licences | `scripts/licensing/inactive_user_downgrade.py` | `inactive_designers` (one row per downgrade) | [inactive-user-downgrade.md](inactive-user-downgrade.md) |
| Monthly email to stakeholders and the platform team | `scripts/licensing/monthly_licence_report.ftl` | HTML mail with the dashboard image attached | below |
| Diagnostics when numbers do not match the licence page | `scripts/licensing/user_profile_diagnostics.py` | printout | [licence-count-snapshot.md](licence-count-snapshot.md) |

All of it lives in the platform maintenance project. The trending dataset and the downgrade log are on Snowflake and are referenced from the project metadata table (outside this repo).

## Licence profiles and API names

| Profile on the licence page | API profile(s) | Notes |
|---|---|---|
| Full Designer | `FULL_DESIGNER` | The script alerts above `CONTRACTED_FULL_DESIGNERS`, a configured threshold that can sit below the seat count on the licence |
| AI Consumer | `AI_CONSUMER` + `READER` (legacy name) | Counted together, as the licence page does |
| Technical Account | `TECHNICAL_ACCOUNT` | Own quota, tracked separately |
| Advanced Analytics Designer, Data Designer, Governance Manager, AI Access User | | No seats on our licence |

Trial credit from Dataiku shows on the licence page's "Fetch trial" button (total and remaining credit for DESIGNER and FULL_DESIGNER). Not readable from the Python client.

## Dashboard charts

The dashboard holds a KPI row and two charts, both built in the dataset Charts tab (native chart editor, no webapp).

### Licence trend

From `platform_maintenance_license_trending`: date on X (daily average per day), one series per count column. Shows whether paid seats are creeping toward the contract limit.

### Sankey of downgrades

From `inactive_designers`. The log is already an event table (one row per transition, with `user_profile`, `new_profile`, login and run timestamp), so no aggregation is needed:

* Chart type: Sankey
* Levels: `user_profile` then `new_profile`
* Weight: Count of records (no column needed)

At first we believed the native chart editor had no Sankey and that it would need a Plotly webapp or an HTML iframe. That was wrong. Sankey is in the editor and works with the settings above.

Reading it: the FULL_DESIGNER flow is the one that saves the most per user. Filter by date rather than reading all-time totals, to see whether the designer flow is speeding up.

### Alternatives considered

* Line chart of counts per licence type over time: better for ongoing monitoring, which is what the trend chart already does.
* Stacked bar of transitions per month (X month, Y count, colour by transition type): good for spikes or seasonality. Kept as the fallback when the Sankey was thought to be unavailable. Not built.
* A per-user journey view (Full then Reader then None) and a "current licence per user" view were mentioned but not built.

## Monthly email

A scenario sends a mail on the 1st of each month to stakeholders and internal readers, with the dashboard image (trend plus Sankey) attached. The body is [`monthly_licence_report.ftl`](../../scripts/licensing/monthly_licence_report.ftl). Reporter settings: Message source inline, HTML, FreeMarker template, static attachment reference.

How the template got to its final shape:

1. First draft: header, an outcome warning when `outcome != "SUCCESS"`, a list describing the two charts, and a footer with scenario, trigger, step counts and links.
2. After looking at the actual image, the prose was rewritten to describe it (the Reader trend and the number of Reader users moved to None, with the counts from that month's image), with emoji section headings.
3. A reporting-period banner was added, because the mail fires on the 1st and the charts cover the previous month.
4. The descriptions were then softened to directions without numbers ("gradual upward trend").
5. That was rejected: a static template sent every month cannot know which way anything moved, so any direction or count in the body is wrong half the time. The standard reporter variables carry no licence numbers. The final body only labels the two charts ("daily average counts by licence type over the prior month", "Sankey diagram of inactive designer licence reclaims") and says who to contact.

Details in the final template:

* Dates come from FreeMarker's `.now`, since the reporter context has no date variable: `<#assign reportDate = .now?string("d MMMM yyyy")>` and `<#assign reportMonth = .now?string("MMMM yyyy")>`. The banner says the charts cover the "month prior to" `reportMonth`. Printing the real previous month by name needs date arithmetic in FTL. The other option considered was a custom `${reportingMonth}` variable set by a Python step. Neither was done.
* The footer shows `failedStepsCount` and `warningStepsCount` only when non-zero, written as `<#if failedStepsCount &gt; 0>` (escaped `>` inside FTL inside HTML), plus job and built-dataset counts and links to `${dssURL}` and `${scenarioRunURL}`.
* The warning and calendar symbols are HTML entities (`&#9888;`, `&#128197;`), not literal emoji.

On the first send DSS reported `Mail has not been sent to x because its domain is not authorized.` That is the mail channel's allowed domain list (Administration > Settings > Notifications & Integrations, the mail channel) or the SMTP relay refusing the domain, not the template. See [scenario-reporters.md](scenario-reporters.md).

Standard reporter variables (the full list, for reference): `${scenarioName}`, `${triggerName}`, `${triggerType}`, `${outcome}`, `${allEventsSummary}`, `${warningsEventsSummary}`, `${failedEventsSummary}`, `${allStepsCount}`, `${successStepsCount}`, `${warningStepsCount}`, `${failedStepsCount}`, `${allJobsCount}`, `${successJobsCount}`, `${warningJobsCount}`, `${failedJobsCount}`, `${allBuiltDatasetsCount}`, `${successBuiltDatasetsCount}`, `${warningBuiltDatasetsCount}`, `${failedBuiltDatasetsCount}`, `${firstFailedStepName}`, `${firstFailedJobDisplayName}`, `${dssURL}`, `${scenarioRunURL}`.

## Email body rule

Do not put interpretation ("readers trending up") in a template. Label the attachment and let the charts carry the numbers.
