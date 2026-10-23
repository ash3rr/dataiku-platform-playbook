<#--
  Monthly licence report: scenario mail reporter body (FreeMarker).

  Sent on the 1st of each month to stakeholders and the platform team. The
  scenario attaches one image with two charts (licence trend line and the
  Sankey of inactive-user licence transitions), so the body only labels
  the attachment. It deliberately does not describe trends or counts: the
  standard scenario variables carry no licence numbers, so any "up/down"
  prose would be static and wrong half the time.

  No date variable exists in the scenario notification context, so the
  dates come from FreeMarker's built-in .now. Spelling out the real
  previous month by name needs date arithmetic in FTL. The cleaner
  option, not built, would be a custom variable set from Python.

  `&gt;` inside the <#if> is intentional (raw > inside FTL inside HTML can
  confuse parsers).

  Reporter settings: Message source = Inline, HTML, Template = FreeMarker.
  If it fails with "Mail has not been sent to x because its domain is not
  authorized", that is the mail channel's allowed-domains list, not this
  template.
-->
<html>
<body style="font-family: Arial, sans-serif; color: #333; max-width: 700px; margin: auto;">

  <#-- Header -->
  <div style="background-color: #003366; padding: 20px; color: white;">
    <h2 style="margin: 0;">Dataiku Monthly License Report</h2>
    <p style="margin: 6px 0 0 0; font-size: 13px;">
      Scenario: ${scenarioName} &mdash; Trigger: ${triggerType}
    </p>
  </div>

  <div style="padding: 24px;">

    <#-- Outcome warning -->
    <#if outcome != "SUCCESS">
    <div style="background:#fff3cd; border-left: 4px solid #ffc107;
                padding: 12px; margin-bottom: 20px;">
      &#9888; <strong>Scenario completed with status: ${outcome}.</strong>
      The attached report may be incomplete or missing.
      <a href="${scenarioRunURL}">View run details.</a>
    </div>
    </#if>

    <#-- Reporting period banner -->
    <#assign reportDate = .now?string("d MMMM yyyy")>
    <#assign reportMonth = .now?string("MMMM yyyy")>
    <div style="background:#f0f4f8; border-left: 4px solid #003366;
                padding: 12px; margin-bottom: 20px; font-size: 13px;">
      &#128197; <strong>Reporting period:</strong>
      Generated on <strong>${reportDate}</strong>.
      Charts cover the <strong>month prior to ${reportMonth}</strong>.
    </div>

    <p>Hi,</p>

    <p>Please find this month's <strong>Dataiku DSS License Usage Report</strong>
    attached. The report includes:</p>

    <ul>
      <li><strong>License Trends</strong> &mdash; daily average counts
      by license type over the prior month</li>
      <li><strong>License Transitions</strong> &mdash; Sankey diagram of
      inactive designer license reclaims</li>
    </ul>

    <p>For questions or to contest a license change,
    please contact the Dataiku platform team.</p>

    <#-- Footer -->
    <hr style="margin: 24px 0; border: none; border-top: 1px solid #ddd;" />
    <p style="font-size: 12px; color: #666;">
      <strong>Run summary:</strong>
      Steps ${successStepsCount}/${allStepsCount} succeeded
      <#if failedStepsCount &gt; 0>
        &nbsp;&bull;&nbsp;
        <span style="color:#c0392b;">${failedStepsCount} step(s) failed</span>
      </#if>
      <#if warningStepsCount &gt; 0>
        &nbsp;&bull;&nbsp;
        <span style="color:#e67e22;">${warningStepsCount} step(s) with warnings</span>
      </#if>
      <br/>
      Jobs: ${successJobsCount}/${allJobsCount} succeeded
      &nbsp;|&nbsp;
      Datasets built: ${successBuiltDatasetsCount}/${allBuiltDatasetsCount}
      <br/><br/>
      <a href="${dssURL}">Open DSS</a>
      &nbsp;&bull;&nbsp;
      <a href="${scenarioRunURL}">View scenario run</a>
    </p>

  </div>
</body>
</html>
