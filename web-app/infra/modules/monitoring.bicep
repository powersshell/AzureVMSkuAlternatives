// Azure Monitor Alerting Module
// Provisions action groups, availability web tests, and scheduled query alerts

targetScope = 'resourceGroup'

@description('Name prefix for monitoring resources')
param resourcePrefix string

@description('Email address for alert notifications')
param alertEmailAddress string

@description('Function App hostname (e.g., myapp.azurewebsites.net)')
param functionAppHostname string

@description('Application Insights resource ID')
param appInsightsResourceId string

@description('Log Analytics Workspace resource ID')
param logAnalyticsWorkspaceResourceId string

@description('Azure region for monitoring resources')
param location string

@description('Tags to apply to all resources')
param tags object = {}

// ============================================================================
// ACTION GROUP - EMAIL NOTIFICATIONS
// ============================================================================

resource actionGroup 'Microsoft.Insights/actionGroups@2023-01-01' = {
  name: '${resourcePrefix}-action-group'
  location: 'Global'
  tags: tags
  properties: {
    groupShortName: substring('${resourcePrefix}-ag', 0, 12)  // Max 12 chars
    enabled: true
    emailReceivers: [
      {
        name: 'Email_Admin'
        emailAddress: alertEmailAddress
        useCommonAlertSchema: true
      }
    ]
  }
}

// ============================================================================
// STANDARD AVAILABILITY WEB TEST - /api/health
// ============================================================================

// Standard Web Test (Gen 2 - supports newer features)
resource healthWebTest 'Microsoft.Insights/webtests@2022-06-15' = {
  name: '${resourcePrefix}-health-webtest'
  location: location
  tags: union(tags, {
    'hidden-link:${appInsightsResourceId}': 'Resource'
  })
  kind: 'standard'
  properties: {
    SyntheticMonitorId: '${resourcePrefix}-health-webtest'
    Name: 'Health Endpoint Availability Test'
    Description: 'Monitors the /api/health endpoint for availability'
    Enabled: true
    Frequency: 300  // 5 minutes
    Timeout: 30     // 30 seconds
    Kind: 'standard'
    RetryEnabled: true
    Locations: [
      { Id: 'us-ca-sjc-azr' }  // West US
      { Id: 'us-tx-sn1-azr' }  // South Central US
      { Id: 'us-va-ash-azr' }  // East US
      { Id: 'us-il-ch1-azr' }  // North Central US
      { Id: 'us-fl-mia-edge' } // East US 2
    ]
    Request: {
      RequestUrl: 'https://${functionAppHostname}/api/health'
      HttpVerb: 'GET'
      ParseDependentRequests: false
      FollowRedirects: true
    }
    ValidationRules: {
      ExpectedHttpStatusCode: 200
      IgnoreHttpStatusCode: false
      ContentValidation: {
        ContentMatch: 'healthy'
        IgnoreCase: true
        PassIfTextFound: true
      }
      SSLCheck: true
      SSLCertRemainingLifetimeCheck: 7
    }
  }
}

// Alert Rule for Web Test Failures
resource healthWebTestAlert 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: '${resourcePrefix}-health-alert'
  location: 'Global'
  tags: tags
  properties: {
    description: 'Alert when the health endpoint fails from 2 or more locations'
    severity: 1  // High severity
    enabled: true
    scopes: [
      appInsightsResourceId
      healthWebTest.id
    ]
    evaluationFrequency: 'PT1M'  // Every 1 minute
    windowSize: 'PT5M'            // Over 5 minutes
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.WebtestLocationAvailabilityCriteria'
      webTestId: healthWebTest.id
      componentId: appInsightsResourceId
      failedLocationCount: 2  // Alert if 2+ locations fail
    }
    actions: [
      {
        actionGroupId: actionGroup.id
      }
    ]
  }
}

// ============================================================================
// SCHEDULED QUERY ALERT 1: API EXCEPTIONS
// ============================================================================

resource apiExceptionsAlert 'Microsoft.Insights/scheduledQueryRules@2023-03-15-preview' = {
  name: '${resourcePrefix}-api-exceptions-alert'
  location: location
  tags: tags
  properties: {
    displayName: 'API Exceptions Alert'
    description: 'Alert when API experiences repeated exceptions (5+ in 5 minutes)'
    severity: 2  // Warning
    enabled: true
    evaluationFrequency: 'PT5M'  // Every 5 minutes
    windowSize: 'PT5M'            // Over 5 minutes
    scopes: [
      logAnalyticsWorkspaceResourceId
    ]
    criteria: {
      allOf: [
        {
          query: '''
            AppExceptions
            | where TimeGenerated > ago(5m)
            | where AppRoleName == "${resourcePrefix}" or AppRoleName contains "${resourcePrefix}"
            | where SeverityLevel >= 2
            | summarize ExceptionCount = count() by ExceptionType
            | where ExceptionCount > 5
          '''
          timeAggregation: 'Count'
          dimensions: []
          operator: 'GreaterThan'
          threshold: 0
          failingPeriods: {
            numberOfEvaluationPeriods: 1
            minFailingPeriodsToAlert: 1
          }
        }
      ]
    }
    autoMitigate: true
    actions: {
      actionGroups: [
        actionGroup.id
      ]
    }
  }
}

// ============================================================================
// SCHEDULED QUERY ALERT 2: CACHE REFRESH MISSING
// ============================================================================

resource cacheRefreshMissingAlert 'Microsoft.Insights/scheduledQueryRules@2023-03-15-preview' = {
  name: '${resourcePrefix}-cache-refresh-missing-alert'
  location: location
  tags: tags
  properties: {
    displayName: 'Cache Refresh Missing Alert'
    description: 'Alert when the last successful cache refresh completion is older than 27 hours'
    severity: 1  // High severity
    enabled: true
    evaluationFrequency: 'PT1H'   // Every 1 hour
    windowSize: 'PT48H'            // Use 48h lookback to compute the last successful completion safely
    scopes: [
      logAnalyticsWorkspaceResourceId
    ]
    criteria: {
      allOf: [
        {
          query: '''
            let LastCompletion = toscalar(
              AppTraces
              | where TimeGenerated > ago(48h)
              | where AppRoleName == "${resourcePrefix}" or AppRoleName contains "${resourcePrefix}"
              | where Message contains "SKU cache refresh completed"
              | summarize max(TimeGenerated)
            );
            print LastCompletion = LastCompletion
            | extend HoursSinceCompletion = iff(isnull(LastCompletion), 99999.0, datetime_diff('minute', now(), LastCompletion) / 60.0)
            | where HoursSinceCompletion > 27.0
          '''
          timeAggregation: 'Count'
          dimensions: []
          operator: 'GreaterThan'
          threshold: 0
          failingPeriods: {
            numberOfEvaluationPeriods: 1
            minFailingPeriodsToAlert: 1
          }
        }
      ]
    }
    autoMitigate: true
    actions: {
      actionGroups: [
        actionGroup.id
      ]
    }
  }
}

// ============================================================================
// SCHEDULED QUERY ALERT 3: CACHE REFRESH COMPLETED WITH HIGH ERROR RATE
// ============================================================================

resource cacheRefreshHighErrorRateAlert 'Microsoft.Insights/scheduledQueryRules@2023-03-15-preview' = {
  name: '${resourcePrefix}-cache-refresh-error-rate-alert'
  location: location
  tags: tags
  properties: {
    displayName: 'Cache Refresh High Error Rate Alert'
    description: 'Alert when cache refresh completes but with high error count (20% or more regions failed)'
    severity: 2  // Warning
    enabled: true
    evaluationFrequency: 'PT1H'   // Every 1 hour
    windowSize: 'PT24H'            // Over 24 hours
    scopes: [
      logAnalyticsWorkspaceResourceId
    ]
    criteria: {
      allOf: [
        {
          query: '''
            AppTraces
            | where TimeGenerated > ago(24h)
            | where AppRoleName == "${resourcePrefix}" or AppRoleName contains "${resourcePrefix}"
            | where Message contains "SKU cache refresh completed"
            | parse Message with * "Updated: " updated:int ", Errors: " errors:int
            | where isnotnull(updated) and isnotnull(errors)
            | where errors > 0
            | extend ErrorRate = round(todouble(errors) / todouble(updated + errors) * 100.0, 2)
            | where ErrorRate >= 20.0
            | project TimeGenerated, updated, errors, ErrorRate
          '''
          timeAggregation: 'Count'
          dimensions: []
          operator: 'GreaterThan'
          threshold: 0
          failingPeriods: {
            numberOfEvaluationPeriods: 1
            minFailingPeriodsToAlert: 1
          }
        }
      ]
    }
    autoMitigate: true
    actions: {
      actionGroups: [
        actionGroup.id
      ]
    }
  }
}

// ============================================================================
// OUTPUTS
// ============================================================================

output actionGroupId string = actionGroup.id
output actionGroupName string = actionGroup.name
output healthWebTestId string = healthWebTest.id
output healthWebTestAlertId string = healthWebTestAlert.id
output apiExceptionsAlertId string = apiExceptionsAlert.id
output cacheRefreshMissingAlertId string = cacheRefreshMissingAlert.id
output cacheRefreshHighErrorRateAlertId string = cacheRefreshHighErrorRateAlert.id
