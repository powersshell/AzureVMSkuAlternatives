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
var workbookId = guid(resourceGroup().id, 'vmsku-usage-workbook-v3')

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
        "json": "### Errors & Exceptions"
      },
      "name": "section6-header"
    },
    {
      "type": 3,
      "content": {
        "version": "KqlItem/1.0",
        "query": "AppExceptions\n| where TimeGenerated {TimeRange}\n| summarize ExceptionCount = count() by bin(TimeGenerated, 1d)\n| order by TimeGenerated asc",
        "size": 0,
        "queryType": 0,
        "resourceType": "microsoft.operationalinsights/workspaces",
        "visualization": "timechart",
        "title": "Exceptions Over Time"
      },
      "name": "exceptions-chart"
    },
    {
      "type": 3,
      "content": {
        "version": "KqlItem/1.0",
        "query": "AppExceptions\n| where TimeGenerated {TimeRange}\n| summarize Count = count(), LastSeen = max(TimeGenerated) by ExceptionType, OuterMessage\n| order by Count desc\n| take 25\n| project ExceptionType, OuterMessage, Count, LastSeen",
        "size": 0,
        "queryType": 0,
        "resourceType": "microsoft.operationalinsights/workspaces",
        "visualization": "table",
        "title": "Top Exceptions (Last 25)"
      },
      "name": "exceptions-table"
    },
    {
      "type": 3,
      "content": {
        "version": "KqlItem/1.0",
        "query": "AppRequests\n| where TimeGenerated {TimeRange}\n| where Success == false\n| summarize FailedCount = count(), LastSeen = max(TimeGenerated) by Name, ResultCode\n| order by FailedCount desc\n| take 25\n| project Name, ResultCode, FailedCount, LastSeen",
        "size": 0,
        "queryType": 0,
        "resourceType": "microsoft.operationalinsights/workspaces",
        "visualization": "table",
        "title": "Failed HTTP Requests"
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
