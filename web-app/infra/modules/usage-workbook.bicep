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
var workbookId = guid(resourceGroup().id, 'vmsku-usage-workbook')

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
        "query": "AppEvents\n| where TimeGenerated {TimeRange}\n| summarize DAU = dcount(tostring(Properties.analyticsUserId)) by bin(TimeGenerated, 1d)\n| order by TimeGenerated asc",
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
        "query": "AppEvents\n| where TimeGenerated {TimeRange}\n| where Name in ('page_loaded', 'compare_submitted', 'compare_completed', 'export_csv_clicked')\n| summarize Events = count(), UniqueUsers = dcount(tostring(Properties.analyticsUserId)) by Name\n| extend SortOrder = case(\n    Name == 'page_loaded', 1,\n    Name == 'compare_submitted', 2,\n    Name == 'compare_completed', 3,\n    Name == 'export_csv_clicked', 4,\n    5)\n| order by SortOrder asc\n| project Name, Events, UniqueUsers",
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
        "query": "AppEvents\n| where TimeGenerated {TimeRange}\n| where Name == 'compare_submitted'\n| extend SKU = tostring(Properties.skuName)\n| where isnotempty(SKU)\n| summarize CompareCount = count(), UniqueUsers = dcount(tostring(Properties.analyticsUserId)) by SKU\n| order by CompareCount desc\n| take 20",
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
        "query": "AppEvents\n| where TimeGenerated {TimeRange}\n| where Name == 'compare_submitted'\n| extend Region = tostring(Properties.location)\n| where isnotempty(Region)\n| summarize CompareCount = count(), UniqueUsers = dcount(tostring(Properties.analyticsUserId)) by Region\n| order by CompareCount desc\n| take 20",
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
        "query": "AppEvents\n| where TimeGenerated {TimeRange}\n| where Name in ('compare_failed', 'compare_validation_failed', 'export_csv_failed')\n| summarize Count = count(), UniqueUsers = dcount(tostring(Properties.analyticsUserId)) by Name\n| order by Count desc",
        "size": 0,
        "queryType": 0,
        "resourceType": "microsoft.operationalinsights/workspaces",
        "visualization": "table",
        "title": "Frontend Error Events"
      },
      "name": "frontend-errors-table"
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
