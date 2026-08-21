// Azure Monitor Workbook — Site Usage Analytics
// Visualises frontend telemetry events, unique users, funnel, and errors

targetScope = 'resourceGroup'

@description('Log Analytics Workspace resource ID (workbook data source)')
param logAnalyticsWorkspaceResourceId string

@description('Azure region for the workbook resource')
param location string

@description('Tags to apply to the workbook')
param tags object = {}

// Deterministic GUID so redeployments update in place
// Bump the suffix to force ARM to replace the workbook resource with fresh content
var workbookId = guid(resourceGroup().id, 'vmsku-usage-workbook-v4')

var serializedData = '''
{
  "version": "Notebook/1.0",
  "items": [
    {
      "type": 9,
      "content": {
        "version": "KqlParameterItem/1.0",
        "parameters": [
          {
            "id": "time_range",
            "version": "KqlParameterItem/1.0",
            "name": "TimeRange",
            "type": 4,
            "isRequired": true,
            "typeSettings": {
              "selectableValues": [
                { "durationMs": 3600000 },
                { "durationMs": 14400000 },
                { "durationMs": 43200000 },
                { "durationMs": 86400000 },
                { "durationMs": 259200000 },
                { "durationMs": 604800000 },
                { "durationMs": 1209600000 },
                { "durationMs": 2592000000 }
              ],
              "allowCustom": true
            },
            "value": {
              "durationMs": 604800000
            },
            "label": "Time Range"
          }
        ],
        "style": "pills"
      },
      "name": "parameters"
    },
    {
      "type": 1,
      "content": {
        "json": "## Site Usage Analytics\nTelemetry from the Azure VM SKU Comparison Tool frontend."
      },
      "name": "header"
    },
    {
      "type": 1,
      "content": {
        "json": "### Event Volume Over Time"
      },
      "name": "section1-header"
    },
    {
      "type": 3,
      "content": {
        "version": "KqlItem/1.0",
        "query": "AppEvents\n| where TimeGenerated {TimeRange}\n| summarize EventCount = count() by bin(TimeGenerated, 1d), Name\n| order by TimeGenerated asc",
        "size": 0,
        "queryType": 0,
        "resourceType": "microsoft.operationalinsights/workspaces",
        "visualization": "timechart",
        "title": "Daily Event Volume by Type"
      },
      "name": "event-volume-chart"
    },
    {
      "type": 1,
      "content": {
        "json": "### Daily Unique Users"
      },
      "name": "section2-header"
    },
    {
      "type": 3,
      "content": {
        "version": "KqlItem/1.0",
        "query": "AppEvents\n| where TimeGenerated {TimeRange}\n| summarize DAU = dcount(tostring(Properties.anonymousUserId)) by bin(TimeGenerated, 1d)\n| order by TimeGenerated asc",
        "size": 0,
        "queryType": 0,
        "resourceType": "microsoft.operationalinsights/workspaces",
        "visualization": "timechart",
        "title": "Daily Active Users (Anonymous)"
      },
      "name": "dau-chart"
    },
    {
      "type": 1,
      "content": {
        "json": "### Usage Funnel"
      },
      "name": "section3-header"
    },
    {
      "type": 3,
      "content": {
        "version": "KqlItem/1.0",
        "query": "AppEvents\n| where TimeGenerated {TimeRange}\n| where Name in ('page_loaded', 'compare_submitted', 'compare_completed', 'export_csv_clicked')\n| summarize Events = count(), UniqueUsers = dcount(tostring(Properties.anonymousUserId)) by Name\n| extend SortOrder = case(\n    Name == 'page_loaded', 1,\n    Name == 'compare_submitted', 2,\n    Name == 'compare_completed', 3,\n    Name == 'export_csv_clicked', 4,\n    5)\n| order by SortOrder asc\n| project Name, Events, UniqueUsers",
        "size": 0,
        "queryType": 0,
        "resourceType": "microsoft.operationalinsights/workspaces",
        "visualization": "categoricalbar",
        "title": "Funnel: Page Load -> Compare -> Export"
      },
      "name": "funnel-chart"
    },
    {
      "type": 1,
      "content": {
        "json": "### Top SKUs Compared"
      },
      "name": "section4-header"
    },
    {
      "type": 3,
      "content": {
        "version": "KqlItem/1.0",
        "query": "AppEvents\n| where TimeGenerated {TimeRange}\n| where Name == 'compare_submitted'\n| extend SKU = tostring(Properties.targetSku)\n| where isnotempty(SKU)\n| summarize CompareCount = count(), UniqueUsers = dcount(tostring(Properties.anonymousUserId)) by SKU\n| order by CompareCount desc\n| take 20",
        "size": 0,
        "queryType": 0,
        "resourceType": "microsoft.operationalinsights/workspaces",
        "visualization": "table",
        "title": "Most Compared SKUs (Top 20)"
      },
      "name": "top-skus-table"
    },
    {
      "type": 1,
      "content": {
        "json": "### Top Regions Used"
      },
      "name": "section5-header"
    },
    {
      "type": 3,
      "content": {
        "version": "KqlItem/1.0",
        "query": "AppEvents\n| where TimeGenerated {TimeRange}\n| where Name == 'compare_submitted'\n| extend Region = tostring(Properties.location)\n| where isnotempty(Region)\n| summarize CompareCount = count(), UniqueUsers = dcount(tostring(Properties.anonymousUserId)) by Region\n| order by CompareCount desc\n| take 20",
        "size": 0,
        "queryType": 0,
        "resourceType": "microsoft.operationalinsights/workspaces",
        "visualization": "table",
        "title": "Most Used Azure Regions (Top 20)"
      },
      "name": "top-regions-table"
    },
    {
      "type": 1,
      "content": {
        "json": "### Errors & Exceptions\nApplication-level faults only. Azure Functions host worker-recycle events (`python exited with code 143` / SIGTERM) are **excluded** here because they are normal platform scale-down activity, are not attached to any request, and would otherwise bury genuine errors. See the *Platform Health* section below for that signal.\n\nAll tiles are split by `AppRoleName` — `vmsku-api-func-cus` is the Functions API, `vmsku-mcp-server` is the hosted MCP server. They share this workspace but are unrelated systems."
      },
      "name": "section6-header"
    },
    {
      "type": 3,
      "content": {
        "version": "KqlItem/1.0",
        "query": "AppExceptions\n| where TimeGenerated {TimeRange}\n| where OuterMessage !contains 'Language Worker Process exited'\n| where ProblemId !contains 'WorkerProcess.ThrowIfExitError'\n| summarize ExceptionCount = count() by bin(TimeGenerated, 1d), AppRoleName\n| order by TimeGenerated asc",
        "size": 0,
        "queryType": 0,
        "resourceType": "microsoft.operationalinsights/workspaces",
        "visualization": "timechart",
        "title": "Application Exceptions Over Time (by service)"
      },
      "name": "exceptions-chart"
    },
    {
      "type": 3,
      "content": {
        "version": "KqlItem/1.0",
        "query": "AppExceptions\n| where TimeGenerated {TimeRange}\n| where OuterMessage !contains 'Language Worker Process exited'\n| where ProblemId !contains 'WorkerProcess.ThrowIfExitError'\n| summarize Count = count(), LastSeen = max(TimeGenerated) by AppRoleName, ExceptionType, OuterMessage\n| order by Count desc\n| take 25\n| project AppRoleName, ExceptionType, OuterMessage, Count, LastSeen",
        "size": 0,
        "queryType": 0,
        "resourceType": "microsoft.operationalinsights/workspaces",
        "visualization": "table",
        "title": "Top Application Exceptions (Last 25)"
      },
      "name": "exceptions-table"
    },
    {
      "type": 3,
      "content": {
        "version": "KqlItem/1.0",
        "query": "AppRequests\n| where TimeGenerated {TimeRange}\n| where Success == false\n| summarize FailedCount = count(), LastSeen = max(TimeGenerated), AvgDurationMs = round(avg(DurationMs)), MaxDurationMs = round(max(DurationMs)), SampleOperationId = any(OperationId) by AppRoleName, Name, ResultCode\n| order by FailedCount desc\n| take 25\n| project AppRoleName, Name, ResultCode, FailedCount, AvgDurationMs, MaxDurationMs, LastSeen, SampleOperationId",
        "size": 0,
        "queryType": 0,
        "resourceType": "microsoft.operationalinsights/workspaces",
        "visualization": "table",
        "title": "Failed HTTP Requests (by service)"
      },
      "name": "failed-requests-table"
    },
    {
      "type": 3,
      "content": {
        "version": "KqlItem/1.0",
        "query": "AppEvents\n| where TimeGenerated {TimeRange}\n| where Name in ('compare_failed', 'compare_validation_failed', 'export_csv_failed')\n| summarize Count = count(), UniqueUsers = dcount(tostring(Properties.anonymousUserId)) by Name\n| order by Count desc",
        "size": 0,
        "queryType": 0,
        "resourceType": "microsoft.operationalinsights/workspaces",
        "visualization": "table",
        "title": "Frontend Error Events"
      },
      "name": "frontend-errors-table"
    },
    {
      "type": 1,
      "content": {
        "json": "### Platform Health\nAzure Functions Flex Consumption recycles the Python language worker as it scales instances up and down. Each recycle is logged by the host as `System.Exception: python exited with code 143` — **143 = 128 + 15 = SIGTERM, a graceful shutdown, not a crash** (an out-of-memory kill would be 137). These events carry no `OperationName`, so they are never attached to a user request and cannot cause a failed call.\n\nThey are shown here as **instance churn** because that is what they actually measure. A rising line means the platform is cycling instances more aggressively; it is a scaling signal, not an error signal. Correlate against the Failed HTTP Requests tile above — if churn rises but failures stay at zero, there is no user impact.\n\n#### Nightly cache refresh\nThe tiles below track the `refresh_sku_cache` timer. Duration is normally in the **245–355 s** band and is noisy rather than monotonic — treat a single high reading as variance and only investigate a sustained climb toward the host timeout. **A missing day in the duration table means the invocation never completed** and should be treated as a failure.\n\nThe region tiles are the important ones: a region can fail its pricing fetch while the run still finishes, which leaves that region serving **stale prices** with no other visible signal (issue #21). Any row in *regions that failed to refresh*, or any `PricedPct` of 0 in *latest per-region outcome*, means that region's pricing is out of date."
      },
      "name": "platform-health-header"
    },
    {
      "type": 3,
      "content": {
        "version": "KqlItem/1.0",
        "query": "AppExceptions\n| where TimeGenerated {TimeRange}\n| where OuterMessage contains 'Language Worker Process exited' or ProblemId contains 'WorkerProcess.ThrowIfExitError'\n| extend HostInstance = tostring(parse_json(tostring(Properties)).HostInstanceId)\n| summarize InstanceRecycles = dcount(HostInstance) by bin(TimeGenerated, 1h), AppRoleName\n| order by TimeGenerated asc",
        "size": 0,
        "queryType": 0,
        "resourceType": "microsoft.operationalinsights/workspaces",
        "visualization": "timechart",
        "title": "Worker Instance Churn (recycles per hour)"
      },
      "name": "platform-churn-chart"
    },
    {
      "type": 3,
      "content": {
        "version": "KqlItem/1.0",
        "query": "AppRequests\n| where TimeGenerated {TimeRange}\n| where Name == 'refresh_sku_cache'\n| summarize DurationSec = round(max(DurationMs) / 1000.0, 1), Outcome = any(ResultCode), Succeeded = any(Success) by bin(TimeGenerated, 1d)\n| order by TimeGenerated desc",
        "size": 0,
        "queryType": 0,
        "resourceType": "microsoft.operationalinsights/workspaces",
        "visualization": "table",
        "title": "Nightly Cache Refresh — duration by day (a missing day = a lost invocation)"
      },
      "name": "refresh-duration-table"
    },
    {
      "type": 3,
      "content": {
        "version": "KqlItem/1.0",
        "query": "AppTraces\n| where TimeGenerated {TimeRange}\n| where Message contains 'Error processing region' or Message contains 'Retry failed for region'\n| extend Region = extract('region ([a-z0-9]+)', 1, Message)\n| summarize Failures = count(), DaysAffected = dcount(bin(TimeGenerated, 1d)), LastFailure = max(TimeGenerated) by Region\n| order by Failures desc",
        "size": 0,
        "queryType": 0,
        "resourceType": "microsoft.operationalinsights/workspaces",
        "visualization": "table",
        "title": "Nightly Cache Refresh — regions that failed to refresh (stale pricing risk)"
      },
      "name": "refresh-region-failures"
    },
    {
      "type": 3,
      "content": {
        "version": "KqlItem/1.0",
        "query": "AppTraces\n| where TimeGenerated {TimeRange}\n| where Message has 'sku_refresh_region'\n| extend d = parse_json(Message)\n| where tostring(d.event_type) == 'sku_refresh_region'\n| summarize arg_max(TimeGenerated, d) by Region = tostring(d.region)\n| project Region, Status = tostring(d.status), PricedSkus = toint(d.pricedSkus), TotalSkus = toint(d.totalSkus), PricedPct = todouble(d.pricedPct), DurationSec = todouble(d.durationSec), LastRun = TimeGenerated\n| order by PricedPct asc, Region asc",
        "size": 0,
        "queryType": 0,
        "resourceType": "microsoft.operationalinsights/workspaces",
        "visualization": "table",
        "title": "Nightly Cache Refresh — latest per-region outcome"
      },
      "name": "refresh-region-outcomes"
    },
    {
      "type": 1,
      "content": {
        "json": "---\n## Value & Impact\nMetrics that quantify how the tool helps visitors: engagement, conversion, savings surfaced, feature adoption, and reach."
      },
      "name": "value-header"
    },
    {
      "type": 1,
      "content": {
        "json": "### Engagement & Retention"
      },
      "name": "engagement-header"
    },
    {
      "type": 3,
      "content": {
        "version": "KqlItem/1.0",
        "query": "AppEvents\n| where TimeGenerated > ago(30d)\n| extend u = tostring(Properties.anonymousUserId)\n| where isnotempty(u)\n| summarize DAU = dcountif(u, TimeGenerated > ago(1d)), WAU = dcountif(u, TimeGenerated > ago(7d)), MAU = dcountif(u, TimeGenerated > ago(30d))\n| extend ['Stickiness (DAU/MAU %)'] = iff(MAU > 0, round(100.0 * DAU / MAU, 1), 0.0)",
        "size": 4,
        "queryType": 0,
        "resourceType": "microsoft.operationalinsights/workspaces",
        "visualization": "tiles",
        "title": "Active Users - DAU / WAU / MAU (fixed windows, ignores Time Range)"
      },
      "name": "active-users-tiles"
    },
    {
      "type": 3,
      "content": {
        "version": "KqlItem/1.0",
        "query": "AppEvents\n| where TimeGenerated {TimeRange}\n| extend u = tostring(Properties.anonymousUserId)\n| where isnotempty(u)\n| summarize ActiveDays = dcount(bin(TimeGenerated, 1d)) by u\n| summarize ['Returning (multi-day)'] = countif(ActiveDays > 1), ['Single-visit'] = countif(ActiveDays == 1)",
        "size": 1,
        "queryType": 0,
        "resourceType": "microsoft.operationalinsights/workspaces",
        "visualization": "table",
        "title": "New vs Returning Visitors"
      },
      "name": "new-returning-table"
    },
    {
      "type": 3,
      "content": {
        "version": "KqlItem/1.0",
        "query": "AppEvents\n| where TimeGenerated {TimeRange}\n| extend u = tostring(Properties.anonymousUserId)\n| where isnotempty(u)\n| summarize Events = count() by u, Day = bin(TimeGenerated, 1d)\n| summarize ['Avg events / user / day'] = round(avg(Events), 1), ['Median events / user / day'] = round(percentile(Events, 50), 1)",
        "size": 1,
        "queryType": 0,
        "resourceType": "microsoft.operationalinsights/workspaces",
        "visualization": "table",
        "title": "Engagement Depth (events per active user per day)"
      },
      "name": "engagement-depth-table"
    },
    {
      "type": 1,
      "content": {
        "json": "### Value & Conversion"
      },
      "name": "value-conversion-header"
    },
    {
      "type": 3,
      "content": {
        "version": "KqlItem/1.0",
        "query": "AppEvents\n| where TimeGenerated {TimeRange}\n| where Name == 'compare_completed'\n| extend Status = tostring(Properties.resultStatus)\n| summarize Count = count() by Status",
        "size": 0,
        "queryType": 0,
        "resourceType": "microsoft.operationalinsights/workspaces",
        "visualization": "piechart",
        "title": "Compare Outcomes: Answered vs No Results"
      },
      "name": "answered-rate-chart"
    },
    {
      "type": 3,
      "content": {
        "version": "KqlItem/1.0",
        "query": "AppEvents\n| where TimeGenerated {TimeRange}\n| summarize Compares = countif(Name == 'compare_completed' and tostring(Properties.resultStatus) == 'results'), Exports = countif(Name in ('export_csv_clicked', 'export_xlsx_clicked'))\n| extend ['Export conversion %'] = iff(Compares > 0, round(100.0 * Exports / Compares, 1), 0.0)",
        "size": 4,
        "queryType": 0,
        "resourceType": "microsoft.operationalinsights/workspaces",
        "visualization": "tiles",
        "title": "Export Conversion (exports per successful compare)"
      },
      "name": "export-conversion-tiles"
    },
    {
      "type": 3,
      "content": {
        "version": "KqlItem/1.0",
        "query": "AppEvents\n| where TimeGenerated {TimeRange}\n| where Name == 'compare_completed'\n| extend n = todouble(Measurements.alternativesCount)\n| where isnotnull(n)\n| summarize ['Avg alternatives / compare'] = round(avg(n), 1), ['Median'] = round(percentile(n, 50), 1), Compares = count()",
        "size": 4,
        "queryType": 0,
        "resourceType": "microsoft.operationalinsights/workspaces",
        "visualization": "tiles",
        "title": "Alternatives Surfaced per Comparison"
      },
      "name": "avg-alternatives-tiles"
    },
    {
      "type": 3,
      "content": {
        "version": "KqlItem/1.0",
        "query": "AppEvents\n| where TimeGenerated {TimeRange}\n| where Name == 'compare_completed'\n| extend s = todouble(Measurements.maxMonthlySavings), pct = todouble(Measurements.maxSavingsPct)\n| where isnotnull(s) and s > 0\n| summarize ['Median $/mo savings surfaced'] = round(percentile(s, 50), 2), ['95th pct $/mo'] = round(percentile(s, 95), 2), ['Median savings %'] = round(percentile(pct, 50), 1), ['Compares with savings'] = count()",
        "size": 4,
        "queryType": 0,
        "resourceType": "microsoft.operationalinsights/workspaces",
        "visualization": "tiles",
        "title": "Potential Monthly Savings Surfaced (target vs cheapest alternative, USD)"
      },
      "name": "savings-surfaced-tiles"
    },
    {
      "type": 1,
      "content": {
        "json": "### Feature Adoption"
      },
      "name": "feature-adoption-header"
    },
    {
      "type": 3,
      "content": {
        "version": "KqlItem/1.0",
        "query": "AppEvents\n| where TimeGenerated {TimeRange}\n| where Name in ('where_cheapest_opened', 'price_history_opened', 'region_check_completed', 'grid_loaded', 'pricing_model_toggled', 'mode_switched')\n| extend Feature = case(\n    Name == 'where_cheapest_opened', 'Where is cheapest?',\n    Name == 'price_history_opened', 'Price history',\n    Name == 'region_check_completed', 'Cross-region availability',\n    Name == 'grid_loaded', 'Browse / Grid view',\n    Name == 'pricing_model_toggled', 'Spot / RI pricing toggle',\n    Name == 'mode_switched', 'Mode switch',\n    Name)\n| summarize Events = count(), UniqueUsers = dcount(tostring(Properties.anonymousUserId)) by Feature\n| order by UniqueUsers desc",
        "size": 0,
        "queryType": 0,
        "resourceType": "microsoft.operationalinsights/workspaces",
        "visualization": "categoricalbar",
        "title": "Feature Adoption (unique users per feature)"
      },
      "name": "feature-adoption-chart"
    },
    {
      "type": 3,
      "content": {
        "version": "KqlItem/1.0",
        "query": "AppEvents\n| where TimeGenerated {TimeRange}\n| where Name == 'where_cheapest_result'\n| extend s = todouble(Measurements.maxRegionSavings), regions = todouble(Measurements.regionCount)\n| summarize Checks = count(), UniqueUsers = dcount(tostring(Properties.anonymousUserId)), ['Median region savings $/mo'] = round(percentile(s, 50), 2), ['Avg regions compared'] = round(avg(regions), 1)",
        "size": 4,
        "queryType": 0,
        "resourceType": "microsoft.operationalinsights/workspaces",
        "visualization": "tiles",
        "title": "Where-is-Cheapest Impact"
      },
      "name": "where-cheapest-tiles"
    },
    {
      "type": 3,
      "content": {
        "version": "KqlItem/1.0",
        "query": "AppEvents\n| where TimeGenerated {TimeRange}\n| where Name == 'region_check_completed'\n| extend avail = todouble(Measurements.availableCount), total = todouble(Measurements.totalChecked)\n| summarize Checks = count(), UniqueUsers = dcount(tostring(Properties.anonymousUserId)), ['Avg regions available'] = round(avg(avail), 1), ['Avg regions checked'] = round(avg(total), 1)",
        "size": 4,
        "queryType": 0,
        "resourceType": "microsoft.operationalinsights/workspaces",
        "visualization": "tiles",
        "title": "Cross-Region Availability Checks"
      },
      "name": "region-check-tiles"
    },
    {
      "type": 1,
      "content": {
        "json": "### Reach & Timing"
      },
      "name": "reach-header"
    },
    {
      "type": 3,
      "content": {
        "version": "KqlItem/1.0",
        "query": "AppEvents\n| where TimeGenerated {TimeRange}\n| where isnotempty(ClientCountryOrRegion)\n| summarize Users = dcount(tostring(Properties.anonymousUserId)), Events = count() by Country = ClientCountryOrRegion\n| order by Users desc\n| take 25",
        "size": 0,
        "queryType": 0,
        "resourceType": "microsoft.operationalinsights/workspaces",
        "visualization": "table",
        "title": "Visitors by Country / Region (Top 25)"
      },
      "name": "geo-table"
    },
    {
      "type": 3,
      "content": {
        "version": "KqlItem/1.0",
        "query": "AppEvents\n| where TimeGenerated {TimeRange}\n| where Name == 'page_loaded'\n| summarize Visits = count() by Hour = hourofday(TimeGenerated)\n| order by Hour asc",
        "size": 0,
        "queryType": 0,
        "resourceType": "microsoft.operationalinsights/workspaces",
        "visualization": "barchart",
        "title": "Usage by Hour of Day (UTC)"
      },
      "name": "usage-by-hour-chart"
    },
    {
      "type": 3,
      "content": {
        "version": "KqlItem/1.0",
        "query": "AppEvents\n| where TimeGenerated {TimeRange}\n| where Name == 'page_loaded'\n| extend d = dayofweek(TimeGenerated)\n| extend DayNum = toint(d / 1d), DayOfWeek = case(d == 0d, 'Sun', d == 1d, 'Mon', d == 2d, 'Tue', d == 3d, 'Wed', d == 4d, 'Thu', d == 5d, 'Fri', 'Sat')\n| summarize Visits = count() by DayOfWeek, DayNum\n| order by DayNum asc\n| project DayOfWeek, Visits",
        "size": 0,
        "queryType": 0,
        "resourceType": "microsoft.operationalinsights/workspaces",
        "visualization": "barchart",
        "title": "Usage by Day of Week (UTC)"
      },
      "name": "usage-by-day-chart"
    },
    {
      "type": 1,
      "content": {
        "json": "---\n## Where Users Find Answers\nThe site has two ways in: **Find Alternatives** (pick a SKU, get ranked replacements) and **Browse all VMs** (scan every size in a region). These tiles show *coverage* — which area people reach for, who uses both, and how the two feed each other. This is not a competition between the two: both are valid paths to an answer, and the goal is to confirm each one is pulling its weight.\n\nEvery event carries the surface the user was on when it fired, so shared features (price history, where-is-cheapest) are attributed to where they were opened from. Events sent before this stamping shipped fall back to inference from the event name."
      },
      "name": "surface-usage-header"
    },
    {
      "type": 3,
      "content": {
        "version": "KqlItem/1.0",
        "query": "AppEvents\n| where TimeGenerated {TimeRange}\n| where Name !in ('page_loaded', 'report_issue_clicked', 'mcp_modal_opened')\n| extend uid = tostring(Properties.anonymousUserId)\n| where isnotempty(uid) and uid != 'unknown'\n| extend sp = tostring(Properties.surface)\n| extend Surface = case(sp in ('browse', 'compare'), sp, Name startswith 'grid_', 'browse', 'compare')\n| summarize surfaces = make_set(Surface) by uid\n| extend usedBrowse = set_has_element(surfaces, 'browse'), usedCompare = set_has_element(surfaces, 'compare')\n| summarize ['Active users'] = count(), ['Used Find Alternatives'] = countif(usedCompare), ['Used Browse all VMs'] = countif(usedBrowse), ['Used both areas'] = countif(usedBrowse and usedCompare)",
        "size": 4,
        "queryType": 0,
        "resourceType": "microsoft.operationalinsights/workspaces",
        "visualization": "tiles",
        "title": "Surface Coverage (users who reached each area)"
      },
      "name": "surface-coverage-tiles"
    },
    {
      "type": 3,
      "content": {
        "version": "KqlItem/1.0",
        "query": "AppEvents\n| where TimeGenerated {TimeRange}\n| where Name !in ('page_loaded', 'report_issue_clicked', 'mcp_modal_opened')\n| extend uid = tostring(Properties.anonymousUserId)\n| where isnotempty(uid) and uid != 'unknown'\n| extend sp = tostring(Properties.surface)\n| extend Surface = case(sp == 'browse', 'Browse all VMs', sp == 'compare', 'Find Alternatives', Name startswith 'grid_', 'Browse all VMs', 'Find Alternatives')\n| summarize Users = dcount(uid) by bin(TimeGenerated, 1d), Surface\n| order by TimeGenerated asc",
        "size": 0,
        "queryType": 0,
        "resourceType": "microsoft.operationalinsights/workspaces",
        "visualization": "linechart",
        "title": "Daily Unique Users in Each Area"
      },
      "name": "surface-daily-users-chart"
    },
    {
      "type": 3,
      "content": {
        "version": "KqlItem/1.0",
        "query": "AppEvents\n| where TimeGenerated {TimeRange}\n| where Name !in ('page_loaded', 'report_issue_clicked', 'mcp_modal_opened')\n| extend uid = tostring(Properties.anonymousUserId)\n| where isnotempty(uid) and uid != 'unknown'\n| extend sp = tostring(Properties.surface)\n| extend Surface = case(sp == 'browse', 'Browse all VMs', sp == 'compare', 'Find Alternatives', Name startswith 'grid_', 'Browse all VMs', 'Find Alternatives')\n| summarize Actions = count() by uid, Surface\n| summarize ['Unique users'] = dcount(uid), ['Total actions'] = sum(Actions), ['Avg actions per user'] = round(avg(Actions), 1), ['Median actions per user'] = round(percentile(Actions, 50), 1) by Surface\n| order by ['Unique users'] desc",
        "size": 0,
        "queryType": 0,
        "resourceType": "microsoft.operationalinsights/workspaces",
        "visualization": "table",
        "title": "Engagement Depth per Area"
      },
      "name": "surface-depth-table"
    },
    {
      "type": 3,
      "content": {
        "version": "KqlItem/1.0",
        "query": "AppEvents\n| where TimeGenerated {TimeRange}\n| where Name !in ('page_loaded', 'report_issue_clicked', 'mcp_modal_opened')\n| extend uid = tostring(Properties.anonymousUserId)\n| where isnotempty(uid) and uid != 'unknown'\n| extend sp = tostring(Properties.surface)\n| extend Area = case(sp == 'browse', 'Browse all VMs', sp == 'compare', 'Find Alternatives', Name startswith 'grid_', 'Browse all VMs', 'Find Alternatives')\n| extend Action = case(\n    Name == 'compare_submitted', 'Ran a comparison',\n    Name == 'compare_completed', 'Comparison returned results',\n    Name == 'compare_failed', 'Comparison failed',\n    Name == 'compare_validation_failed', 'Comparison blocked by validation',\n    Name == 'grid_loaded', 'Loaded the region grid',\n    Name == 'grid_find_alternatives', 'Jumped from grid to Find Alternatives',\n    Name == 'grid_export_csv', 'Exported grid to CSV',\n    Name == 'grid_export_xlsx', 'Exported grid to Excel',\n    Name == 'export_csv_clicked', 'Exported results to CSV',\n    Name == 'export_xlsx_clicked', 'Exported results to Excel',\n    Name in ('export_csv_failed', 'export_xlsx_failed'), 'Export failed',\n    Name == 'where_cheapest_opened', 'Opened where-is-cheapest',\n    Name == 'where_cheapest_result', 'Got where-is-cheapest result',\n    Name == 'price_history_opened', 'Opened price history',\n    Name == 'region_check_completed', 'Checked cross-region availability',\n    Name == 'result_vendor_filter_changed', 'Filtered by CPU vendor',\n    Name == 'result_generation_filter_changed', 'Filtered by CPU generation',\n    Name in ('results_expand_all', 'results_collapse_all'), 'Expanded / collapsed results',\n    Name in ('pricing_os_toggled', 'pricing_model_toggled'), 'Changed pricing view',\n    Name == 'mode_switched', 'Switched area',\n    Name)\n| summarize Events = count(), ['Unique users'] = dcount(uid) by Area, Action\n| order by Area asc, ['Unique users'] desc",
        "size": 0,
        "queryType": 0,
        "resourceType": "microsoft.operationalinsights/workspaces",
        "visualization": "table",
        "title": "What Users Actually Do in Each Area"
      },
      "name": "surface-actions-table"
    },
    {
      "type": 3,
      "content": {
        "version": "KqlItem/1.0",
        "query": "AppEvents\n| where TimeGenerated {TimeRange}\n| where Name in ('grid_loaded', 'grid_find_alternatives')\n| extend uid = tostring(Properties.anonymousUserId)\n| where isnotempty(uid) and uid != 'unknown'\n| summarize ['Grid loads'] = countif(Name == 'grid_loaded'), ['Users who browsed'] = dcountif(uid, Name == 'grid_loaded'), ['Handoffs to Find Alternatives'] = countif(Name == 'grid_find_alternatives'), ['Users who handed off'] = dcountif(uid, Name == 'grid_find_alternatives')",
        "size": 4,
        "queryType": 0,
        "resourceType": "microsoft.operationalinsights/workspaces",
        "visualization": "tiles",
        "title": "Browse → Find Alternatives Handoff (browsing feeding the compare flow)"
      },
      "name": "surface-handoff-tiles"
    },
    {
      "type": 3,
      "content": {
        "version": "KqlItem/1.0",
        "query": "AppEvents\n| where TimeGenerated {TimeRange}\n| where Name == 'mode_switched'\n| extend uid = tostring(Properties.anonymousUserId)\n| extend toMode = tostring(Properties.mode), fromMode = tostring(Properties.fromMode)\n| extend Direction = case(\n    toMode == 'browse' and fromMode == 'compare', 'Find Alternatives → Browse all VMs',\n    toMode == 'compare' and fromMode == 'browse', 'Browse all VMs → Find Alternatives',\n    toMode == 'browse', 'Switched into Browse all VMs',\n    toMode == 'compare', 'Switched into Find Alternatives',\n    'Unknown')\n| summarize Switches = count(), ['Unique users'] = dcount(uid) by Direction\n| order by Switches desc",
        "size": 0,
        "queryType": 0,
        "resourceType": "microsoft.operationalinsights/workspaces",
        "visualization": "table",
        "title": "Movement Between Areas"
      },
      "name": "surface-movement-table"
    },
    {
      "type": 3,
      "content": {
        "version": "KqlItem/1.0",
        "query": "AppEvents\n| where TimeGenerated {TimeRange}\n| where Name in ('export_csv_clicked', 'export_xlsx_clicked', 'grid_export_csv', 'grid_export_xlsx', 'where_cheapest_result', 'price_history_opened', 'region_check_completed')\n| extend uid = tostring(Properties.anonymousUserId)\n| where isnotempty(uid) and uid != 'unknown'\n| extend sp = tostring(Properties.surface)\n| extend Area = case(sp == 'browse', 'Browse all VMs', sp == 'compare', 'Find Alternatives', Name startswith 'grid_', 'Browse all VMs', 'Find Alternatives')\n| extend Outcome = case(\n    Name in ('export_csv_clicked', 'export_xlsx_clicked', 'grid_export_csv', 'grid_export_xlsx'), 'Took the data away (export)',\n    Name == 'where_cheapest_result', 'Found a cheaper region',\n    Name == 'price_history_opened', 'Checked price trend',\n    Name == 'region_check_completed', 'Checked regional availability',\n    Name)\n| summarize Events = count(), ['Unique users'] = dcount(uid) by Area, Outcome\n| order by Area asc, Events desc",
        "size": 0,
        "queryType": 0,
        "resourceType": "microsoft.operationalinsights/workspaces",
        "visualization": "table",
        "title": "Where the Answer Landed (deep-dive & export actions by area)"
      },
      "name": "surface-outcome-table"
    },
    {
      "type": 1,
      "content": {
        "json": "---\n## MCP Server Usage\nTool calls made by AI agents (GitHub Copilot CLI, Scout, VS Code, etc.) through the MCP server. Agents are anonymous, so reach metrics use the caller IP and the client-reported app name as proxies for distinct users."
      },
      "name": "mcp-header"
    },
    {
      "type": 3,
      "content": {
        "version": "KqlItem/1.0",
        "query": "AppEvents\n| where TimeGenerated {TimeRange}\n| where Name in ('mcp_modal_opened', 'mcp_config_copied')\n| extend uid = tostring(Properties.anonymousUserId)\n| where isnotempty(uid) and uid != 'unknown'\n| summarize ['Opened MCP instructions'] = countif(Name == 'mcp_modal_opened'), ['Users who opened'] = dcountif(uid, Name == 'mcp_modal_opened'), ['Config copied'] = countif(Name == 'mcp_config_copied'), ['Users who copied config'] = dcountif(uid, Name == 'mcp_config_copied')",
        "size": 4,
        "queryType": 0,
        "resourceType": "microsoft.operationalinsights/workspaces",
        "visualization": "tiles",
        "title": "Site → MCP Funnel (visitors picking up the MCP server from the site)"
      },
      "name": "mcp-site-funnel-tiles"
    },
    {
      "type": 3,
      "content": {
        "version": "KqlItem/1.0",
        "query": "AppTraces\n| where TimeGenerated {TimeRange}\n| where Message has 'mcp_tool_invoked'\n| extend cd = parse_json(Message)\n| where cd.event_type == 'mcp_tool_invoked'\n| summarize ['Total tool calls'] = count(), ['Active days'] = dcount(startofday(TimeGenerated)), ['Distinct tools'] = dcount(tostring(cd.tool)), ['Distinct callers (by IP)'] = dcountif(tostring(cd.clientIp), isnotempty(tostring(cd.clientIp)))",
        "size": 4,
        "queryType": 0,
        "resourceType": "microsoft.operationalinsights/workspaces",
        "visualization": "tiles",
        "title": "MCP Server — Headline Totals"
      },
      "name": "mcp-headline-tiles"
    },
    {
      "type": 3,
      "content": {
        "version": "KqlItem/1.0",
        "query": "AppTraces\n| where TimeGenerated {TimeRange}\n| where Message has 'mcp_tool_invoked'\n| extend cd = parse_json(Message)\n| where cd.event_type == 'mcp_tool_invoked'\n| summarize Calls = count() by bin(TimeGenerated, 1d), Tool = tostring(cd.tool)\n| order by TimeGenerated asc",
        "size": 0,
        "queryType": 0,
        "resourceType": "microsoft.operationalinsights/workspaces",
        "visualization": "timechart",
        "title": "Tool Invocations Over Time (by tool)"
      },
      "name": "mcp-volume-over-time"
    },
    {
      "type": 3,
      "content": {
        "version": "KqlItem/1.0",
        "query": "AppTraces\n| where TimeGenerated {TimeRange}\n| where Message has 'mcp_tool_invoked'\n| extend cd = parse_json(Message)\n| where cd.event_type == 'mcp_tool_invoked'\n| summarize Calls = count() by Tool = tostring(cd.tool)\n| order by Calls desc",
        "size": 0,
        "queryType": 0,
        "resourceType": "microsoft.operationalinsights/workspaces",
        "visualization": "categoricalbar",
        "title": "Invocations by Tool"
      },
      "name": "mcp-by-tool"
    },
    {
      "type": 3,
      "content": {
        "version": "KqlItem/1.0",
        "query": "AppTraces\n| where TimeGenerated {TimeRange}\n| where Message has 'mcp_tool_invoked'\n| extend cd = parse_json(Message)\n| where cd.event_type == 'mcp_tool_invoked'\n| summarize Calls = count() by Status = tostring(cd.status)",
        "size": 0,
        "queryType": 0,
        "resourceType": "microsoft.operationalinsights/workspaces",
        "visualization": "piechart",
        "title": "Success vs Error"
      },
      "name": "mcp-success-error"
    },
    {
      "type": 3,
      "content": {
        "version": "KqlItem/1.0",
        "query": "AppTraces\n| where TimeGenerated {TimeRange}\n| where Message has 'mcp_tool_invoked'\n| extend cd = parse_json(Message)\n| where cd.event_type == 'mcp_tool_invoked'\n| extend d = todouble(cd.durationMs)\n| where isnotnull(d)\n| summarize ['Avg ms'] = round(avg(d), 0), ['p50 ms'] = round(percentile(d, 50), 0), ['p95 ms'] = round(percentile(d, 95), 0)",
        "size": 4,
        "queryType": 0,
        "resourceType": "microsoft.operationalinsights/workspaces",
        "visualization": "tiles",
        "title": "Tool Call Latency (proxy round-trip to API)"
      },
      "name": "mcp-latency-tiles"
    },
    {
      "type": 3,
      "content": {
        "version": "KqlItem/1.0",
        "query": "AppTraces\n| where TimeGenerated {TimeRange}\n| where Message has 'mcp_tool_invoked'\n| extend cd = parse_json(Message)\n| where cd.event_type == 'mcp_tool_invoked'\n| extend d = todouble(cd.durationMs)\n| where isnotnull(d)\n| summarize ['Avg ms'] = round(avg(d), 0), ['p95 ms'] = round(percentile(d, 95), 0), Calls = count() by Tool = tostring(cd.tool)\n| order by Calls desc",
        "size": 0,
        "queryType": 0,
        "resourceType": "microsoft.operationalinsights/workspaces",
        "visualization": "table",
        "title": "Latency by Tool"
      },
      "name": "mcp-latency-by-tool"
    },
    {
      "type": 1,
      "content": {
        "json": "### MCP Reach (proxies for distinct users)\nAgents are anonymous. Distinct caller IPs approximate distinct users (shared egress / NAT may under-count), and the client app name shows which AI tools connect."
      },
      "name": "mcp-reach-header"
    },
    {
      "type": 3,
      "content": {
        "version": "KqlItem/1.0",
        "query": "AppTraces\n| where TimeGenerated {TimeRange}\n| where Message has 'mcp_tool_invoked'\n| extend cd = parse_json(Message)\n| where cd.event_type == 'mcp_tool_invoked' and isnotempty(tostring(cd.clientIp))\n| summarize ['Distinct callers'] = dcount(tostring(cd.clientIp)) by bin(TimeGenerated, 1d)\n| order by TimeGenerated asc",
        "size": 0,
        "queryType": 0,
        "resourceType": "microsoft.operationalinsights/workspaces",
        "visualization": "timechart",
        "title": "Distinct Callers Over Time (by IP)"
      },
      "name": "mcp-callers-over-time"
    },
    {
      "type": 3,
      "content": {
        "version": "KqlItem/1.0",
        "query": "AppTraces\n| where TimeGenerated {TimeRange}\n| where Message has 'mcp_session_started'\n| extend cd = parse_json(Message)\n| where cd.event_type == 'mcp_session_started'\n| summarize ['MCP sessions started'] = count(), ['Distinct callers (by IP)'] = dcountif(tostring(cd.clientIp), isnotempty(tostring(cd.clientIp))), ['Distinct client apps'] = dcountif(tostring(cd.clientName), isnotempty(tostring(cd.clientName)))",
        "size": 4,
        "queryType": 0,
        "resourceType": "microsoft.operationalinsights/workspaces",
        "visualization": "tiles",
        "title": "MCP Sessions & Distinct Clients"
      },
      "name": "mcp-sessions-tiles"
    },
    {
      "type": 3,
      "content": {
        "version": "KqlItem/1.0",
        "query": "AppTraces\n| where TimeGenerated {TimeRange}\n| where Message has 'mcp_session_started'\n| extend cd = parse_json(Message)\n| where cd.event_type == 'mcp_session_started'\n| summarize Sessions = count(), ['Distinct callers'] = dcountif(tostring(cd.clientIp), isnotempty(tostring(cd.clientIp))) by ['Client app'] = iff(isempty(tostring(cd.clientName)), 'unknown', tostring(cd.clientName)), Version = tostring(cd.clientVersion)\n| order by Sessions desc",
        "size": 0,
        "queryType": 0,
        "resourceType": "microsoft.operationalinsights/workspaces",
        "visualization": "table",
        "title": "Clients by App (which AI tools connect)"
      },
      "name": "mcp-clients-by-app"
    },
    {
      "type": 1,
      "content": {
        "json": "---\n## SKU Data Coverage\nAnalysis of cached VM SKU data completeness, refreshed daily at 2:00 AM UTC."
      },
      "name": "coverage-header"
    },
    {
      "type": 3,
      "content": {
        "version": "KqlItem/1.0",
        "query": "AppTraces\n| where Message has 'sku_coverage_summary'\n| extend cd = parse_json(Message)\n| where cd.event_type == 'sku_coverage_summary'\n| top 1 by TimeGenerated desc\n| project\n    ['Last Refresh'] = strcat(format_datetime(TimeGenerated, 'yyyy-MM-dd HH:mm'), ' UTC'),\n    ['Total Regions'] = toint(cd.totalRegions),\n    ['Total SKUs'] = toint(cd.totalSkus),\n    ['PAYG Pricing'] = strcat(tostring(cd.overallPaygPct), '%'),\n    ['1yr RI Pricing'] = strcat(tostring(cd.overallRi1YearPct), '%'),\n    ['3yr RI Pricing'] = strcat(tostring(cd.overallRi3YearPct), '%'),\n    ['Network Bandwidth'] = strcat(tostring(cd.overallNetworkBwPct), '%'),\n    ['vCPUs'] = strcat(tostring(cd.overallVCPUsPct), '%'),\n    ['Memory'] = strcat(tostring(cd.overallMemoryPct), '%')",
        "size": 4,
        "queryType": 0,
        "resourceType": "microsoft.operationalinsights/workspaces",
        "visualization": "tiles",
        "tileSettings": {
          "titleContent": { "columnMatch": "Last Refresh", "formatter": 1 },
          "subtitleContent": { "columnMatch": "Total SKUs" },
          "showBorder": true
        },
        "title": "Overall Coverage Summary (Latest Refresh)"
      },
      "name": "coverage-summary-tiles"
    },
    {
      "type": 3,
      "content": {
        "version": "KqlItem/1.0",
        "query": "AppTraces\n| where Message has 'sku_coverage_region'\n| extend cd = parse_json(Message)\n| where cd.event_type == 'sku_coverage_region'\n| summarize arg_max(TimeGenerated, cd) by tostring(cd.region)\n| project\n    Region = tostring(cd.region),\n    ['Total SKUs'] = toint(cd.totalSkus),\n    ['PAYG Linux'] = strcat(tostring(cd.paygLinuxPct), '%'),\n    ['PAYG Windows'] = strcat(tostring(cd.paygWindowsPct), '%'),\n    ['1yr RI'] = strcat(tostring(cd.ri1YearPct), '%'),\n    ['3yr RI'] = strcat(tostring(cd.ri3YearPct), '%'),\n    ['1yr RI Win'] = strcat(tostring(cd.ri1YearWindowsPct), '%'),\n    ['3yr RI Win'] = strcat(tostring(cd.ri3YearWindowsPct), '%'),\n    ['Network BW'] = strcat(tostring(cd.networkBandwidthPct), '%')\n| order by Region asc",
        "size": 0,
        "queryType": 0,
        "resourceType": "microsoft.operationalinsights/workspaces",
        "visualization": "table",
        "title": "Pricing Coverage by Region"
      },
      "name": "coverage-region-pricing"
    },
    {
      "type": 3,
      "content": {
        "version": "KqlItem/1.0",
        "query": "AppTraces\n| where Message has 'sku_coverage_region'\n| extend cd = parse_json(Message)\n| where cd.event_type == 'sku_coverage_region'\n| summarize arg_max(TimeGenerated, cd) by tostring(cd.region)\n| project\n    Region = tostring(cd.region),\n    ['Total SKUs'] = toint(cd.totalSkus),\n    ['vCPUs'] = strcat(tostring(cd.vCPUsPct), '%'),\n    ['Memory'] = strcat(tostring(cd.memoryPct), '%'),\n    ['Disk IOPS'] = strcat(tostring(cd.diskIOPSPct), '%'),\n    ['Disk Throughput'] = strcat(tostring(cd.diskThroughputPct), '%'),\n    ['Network BW'] = strcat(tostring(cd.networkBandwidthPct), '%'),\n    ['Avail. Zones'] = strcat(tostring(cd.availabilityZonesPct), '%'),\n    ['HyperV Gen'] = strcat(tostring(cd.hyperVGenerationsPct), '%')\n| order by Region asc",
        "size": 0,
        "queryType": 0,
        "resourceType": "microsoft.operationalinsights/workspaces",
        "visualization": "table",
        "title": "Capability Coverage by Region"
      },
      "name": "coverage-region-capabilities"
    },
    {
      "type": 3,
      "content": {
        "version": "KqlItem/1.0",
        "query": "AppTraces\n| where Message has 'sku_coverage_region'\n| extend cd = parse_json(Message)\n| where cd.event_type == 'sku_coverage_region'\n| summarize arg_max(TimeGenerated, cd) by tostring(cd.region)\n| extend Region = tostring(cd.region), MissingPricing = cd.missingPricingSkus\n| where array_length(MissingPricing) > 0\n| mv-apply MissingSku = MissingPricing on (\n    project Region, SKU = tostring(MissingSku), MissingCategory = 'PAYG Pricing'\n)\n| union (\n    AppTraces\n    | where Message has 'sku_coverage_region'\n    | extend cd = parse_json(Message)\n    | where cd.event_type == 'sku_coverage_region'\n    | summarize arg_max(TimeGenerated, cd) by tostring(cd.region)\n    | extend Region = tostring(cd.region), MissingRI = cd.missingRiSkus\n    | where array_length(MissingRI) > 0\n    | mv-apply MissingSku = MissingRI on (\n        project Region, SKU = tostring(MissingSku), MissingCategory = 'RI Pricing'\n    )\n)\n| summarize MissingCategories = make_set(MissingCategory), Regions = make_set(Region) by SKU\n| extend ['Missing Data'] = strcat_array(MissingCategories, ', '), ['Regions Affected'] = array_length(Regions)\n| project SKU, ['Missing Data'], ['Regions Affected']\n| order by ['Regions Affected'] desc\n| take 50",
        "size": 0,
        "queryType": 0,
        "resourceType": "microsoft.operationalinsights/workspaces",
        "visualization": "table",
        "title": "SKUs with Missing Data (Top 50)"
      },
      "name": "coverage-missing-skus"
    },
    {
      "type": 3,
      "content": {
        "version": "KqlItem/1.0",
        "query": "AppTraces\n| where Message has 'sku_coverage_summary'\n| extend cd = parse_json(Message)\n| where cd.event_type == 'sku_coverage_summary'\n| top 14 by TimeGenerated desc\n| project\n    TimeGenerated,\n    ['PAYG Pct'] = todouble(cd.overallPaygPct),\n    ['1yr RI Pct'] = todouble(cd.overallRi1YearPct),\n    ['3yr RI Pct'] = todouble(cd.overallRi3YearPct),\n    ['Network BW Pct'] = todouble(cd.overallNetworkBwPct)\n| order by TimeGenerated asc",
        "size": 0,
        "queryType": 0,
        "resourceType": "microsoft.operationalinsights/workspaces",
        "visualization": "linechart",
        "chartSettings": {
          "ySettings": { "min": 0, "max": 100 }
        },
        "title": "Coverage Trend (Last 14 Refreshes)"
      },
      "name": "coverage-trend-chart"
    }
  ],
  "fallbackResourceIds": [],
  "$schema": "https://github.com/Microsoft/Application-Insights-Workbooks/blob/master/schema/workbook.json"
}
'''

resource usageWorkbook 'Microsoft.Insights/workbooks@2023-06-01' = {
  name: workbookId
  location: location
  tags: tags
  kind: 'shared'
  properties: {
    displayName: 'VM SKU Comparison - Site Usage Analytics'
    category: 'workbook'
    sourceId: logAnalyticsWorkspaceResourceId
    serializedData: serializedData
    version: 'Notebook/1.0'
  }
}

output workbookId string = usageWorkbook.id
output workbookName string = usageWorkbook.properties.displayName
