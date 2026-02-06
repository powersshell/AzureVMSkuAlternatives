# Azure VM SKU Alternatives - Deployment Guide

This document provides step-by-step instructions for deploying the Azure VM SKU Alternatives web application.

## Table of Contents
1. [Prerequisites](#prerequisites)
2. [Deployment Options](#deployment-options)
3. [Option 1: Automated Deployment](#option-1-automated-deployment-recommended)
4. [Option 2: Manual Deployment](#option-2-manual-deployment)
5. [Post-Deployment Configuration](#post-deployment-configuration)
6. [Verification](#verification)
7. [Troubleshooting](#troubleshooting)

## Prerequisites

### Required Tools
- **Azure CLI**: Version 2.40.0 or later
  ```bash
  az --version
  ```
  Install from: https://aka.ms/installazurecli

- **PowerShell**: Version 7.0 or later (for Windows users)
  ```powershell
  $PSVersionTable.PSVersion
  ```
  Install from: https://aka.ms/powershell

- **Node.js**: Version 18.x or later (for local development)
  ```bash
  node --version
  ```
  Install from: https://nodejs.org/

### Azure Requirements
- Active Azure subscription
- Permissions: Contributor or Owner role on the subscription
- Available quota for Static Web Apps in your region

### GitHub Requirements (for CI/CD)
- GitHub account
- Repository access (fork or own repository)

## Deployment Options

### Option 1: Automated Deployment (Recommended)

Use the PowerShell deployment script for a fully automated setup.

#### Step 1: Login to Azure
```powershell
az login
```

#### Step 2: Set Your Subscription (if you have multiple)
```powershell
az account set --subscription "Your-Subscription-Name"
```

#### Step 3: Run Deployment Script
```powershell
cd web-app/infra
.\Deploy-Infrastructure.ps1
```

**With Custom Parameters:**
```powershell
.\Deploy-Infrastructure.ps1 `
  -Location "westus2" `
  -ResourceGroupName "my-vmsku-app-rg" `
  -StaticWebAppName "my-vmsku-webapp" `
  -Sku "Standard"
```

#### Step 4: Save Deployment Token
The script will output a deployment token. Save this for GitHub Actions configuration:
```
AZURE_STATIC_WEB_APPS_API_TOKEN=<your-token-here>
```

### Option 2: Manual Deployment

#### Step 1: Create Resource Group
```bash
az group create \
  --name rg-vmsku-alternatives \
  --location eastus2
```

#### Step 2: Deploy Bicep Template
```bash
az deployment sub create \
  --name vmsku-deployment \
  --location eastus2 \
  --template-file infra/deploy.bicep \
  --parameters infra/deploy.parameters.json
```

#### Step 3: Get Deployment Outputs
```bash
az deployment sub show \
  --name vmsku-deployment \
  --query properties.outputs
```

#### Step 4: Retrieve Deployment Token
```bash
az staticwebapp secrets list \
  --name vmsku-alternatives-webapp \
  --resource-group rg-vmsku-alternatives \
  --query "properties.apiKey" \
  --output tsv
```

## Post-Deployment Configuration

### 1. Configure GitHub Actions

#### Add Repository Secrets
1. Go to your GitHub repository
2. Navigate to **Settings** → **Secrets and variables** → **Actions**
3. Click **New repository secret**
4. Add the following secrets:

| Secret Name | Value | Description |
|------------|-------|-------------|
| `AZURE_STATIC_WEB_APPS_API_TOKEN` | From deployment output | Deployment token |
| `AZURE_SUBSCRIPTION_ID` | Your subscription ID | For VM SKU queries |

#### Verify Workflow File
Ensure [.github/workflows/azure-static-web-apps.yml](../.github/workflows/azure-static-web-apps.yml) exists with correct paths:
```yaml
app_location: "/web-app/src"
api_location: "/web-app/api"
```

### 2. Configure Application Settings

Set the subscription ID in your Static Web App:

```bash
az staticwebapp appsettings set \
  --name vmsku-alternatives-webapp \
  --resource-group rg-vmsku-alternatives \
  --setting-names AZURE_SUBSCRIPTION_ID="your-subscription-id-here"
```

### 3. Assign Permissions (Standard SKU only)

If using Standard SKU, ensure the managed identity has Reader access:

```bash
# Get the Static Web App's managed identity
identityId=$(az staticwebapp show \
  --name vmsku-alternatives-webapp \
  --resource-group rg-vmsku-alternatives \
  --query identity.principalId \
  --output tsv)

# Assign Reader role
az role assignment create \
  --assignee $identityId \
  --role Reader \
  --scope /subscriptions/YOUR-SUBSCRIPTION-ID
```

## Verification

### 1. Check Deployment Status

```bash
az deployment sub show \
  --name vmsku-deployment \
  --query properties.provisioningState
```

Expected output: `"Succeeded"`

### 2. Verify Static Web App

```bash
az staticwebapp show \
  --name vmsku-alternatives-webapp \
  --resource-group rg-vmsku-alternatives \
  --query "{Name:name, Status:defaultHostname, Sku:sku.name}"
```

### 3. Test the Application

1. Open the Static Web App URL (from deployment output)
2. Fill in the comparison form:
   - SKU Name: `Standard_D4s_v3`
   - Location: `eastus`
3. Click "Compare VM SKUs"
4. Verify results are displayed

### 4. Check GitHub Actions

1. Go to your repository's **Actions** tab
2. Verify the workflow runs successfully
3. Check deployment status

### 5. Monitor with Application Insights

```bash
# Get Application Insights instrumentation key
az monitor app-insights component show \
  --app vmsku-alternatives-webapp-insights \
  --resource-group rg-vmsku-alternatives \
  --query instrumentationKey
```

Access Application Insights in Azure Portal to view:
- Live Metrics
- Performance
- Failures
- Usage

## Troubleshooting

### Issue: Deployment Script Fails

**Symptoms:**
```
Deployment failed: InvalidTemplate
```

**Solutions:**
1. Verify Azure CLI version: `az --version`
2. Update Azure CLI: `az upgrade`
3. Check parameter file syntax:
   ```bash
   az bicep build --file infra/deploy.bicep
   ```
4. Verify you have sufficient permissions

### Issue: GitHub Actions Deployment Fails

**Symptoms:**
```
Error: Invalid static web apps token
```

**Solutions:**
1. Regenerate deployment token:
   ```bash
   az staticwebapp secrets list \
     --name vmsku-alternatives-webapp \
     --resource-group rg-vmsku-alternatives \
     --query "properties.apiKey" \
     --output tsv
   ```
2. Update GitHub secret `AZURE_STATIC_WEB_APPS_API_TOKEN`
3. Re-run the workflow

### Issue: API Returns "Subscription Not Configured"

**Symptoms:**
```json
{"error": "Azure subscription not configured"}
```

**Solutions:**
1. Set the subscription ID:
   ```bash
   az staticwebapp appsettings set \
     --name vmsku-alternatives-webapp \
     --resource-group rg-vmsku-alternatives \
     --setting-names AZURE_SUBSCRIPTION_ID="your-sub-id"
   ```
2. Restart the Static Web App
3. Wait a few minutes for settings to propagate

### Issue: "SKU Not Found" Error

**Symptoms:**
```json
{"error": "SKU 'Standard_D4s_v3' not found in location 'eastus'"}
```

**Solutions:**
1. Verify SKU name spelling (case-sensitive)
2. Check if SKU exists in region:
   ```bash
   az vm list-skus --location eastus --output table | grep D4s
   ```
3. Try a different region
4. Ensure you're logged into the correct subscription

### Issue: No Pricing Data Displayed

**Symptoms:**
- Pricing shows as "N/A"

**Solutions:**
1. Verify internet connectivity
2. Check Azure Retail Prices API:
   ```bash
   curl "https://prices.azure.com/api/retail/prices?api-version=2023-01-01-preview"
   ```
3. Some SKUs may not have publicly available pricing
4. Try waiting a few minutes and retry

### Issue: Managed Identity Authentication Fails

**Symptoms:**
```
DefaultAzureCredential failed to retrieve token
```

**Solutions:**
1. Verify you're using Standard SKU (Free doesn't support managed identity)
2. Check role assignment:
   ```bash
   az role assignment list \
     --assignee YOUR-IDENTITY-ID \
     --all
   ```
3. Wait a few minutes for identity propagation
4. Assign Reader role manually (see Post-Deployment Configuration)

## Advanced Configuration

### Custom Domain Setup

1. Add custom domain in Azure Portal or CLI:
```bash
az staticwebapp hostname set \
  --name vmsku-alternatives-webapp \
  --resource-group rg-vmsku-alternatives \
  --hostname www.yourdomain.com
```

2. Configure DNS records as instructed

### Enable API Authentication

Update [staticwebapp.config.json](../staticwebapp.config.json):
```json
{
  "routes": [
    {
      "route": "/api/*",
      "allowedRoles": ["authenticated"]
    }
  ],
  "auth": {
    "identityProviders": {
      "azureActiveDirectory": {
        "registration": {
          "openIdIssuer": "https://login.microsoftonline.com/YOUR-TENANT-ID",
          "clientIdSettingName": "AAD_CLIENT_ID",
          "clientSecretSettingName": "AAD_CLIENT_SECRET"
        }
      }
    }
  }
}
```

### Scale Up to Standard SKU

```bash
az staticwebapp update \
  --name vmsku-alternatives-webapp \
  --resource-group rg-vmsku-alternatives \
  --sku Standard
```

## Cleanup

To remove all resources:

```powershell
# Using the cleanup script
cd web-app/infra
.\Remove-Infrastructure.ps1

# Or manually
az group delete --name rg-vmsku-alternatives --yes
```

## Next Steps

1. **Enable Monitoring**: Set up alerts in Application Insights
2. **Configure Backups**: Set up backup and disaster recovery
3. **Security Review**: Review security best practices
4. **Performance Optimization**: Analyze and optimize API performance
5. **Cost Optimization**: Review and optimize Azure costs

## Support Resources

- [Azure Static Web Apps Documentation](https://docs.microsoft.com/azure/static-web-apps/)
- [Azure Functions Documentation](https://docs.microsoft.com/azure/azure-functions/)
- [GitHub Actions Documentation](https://docs.github.com/actions)
- [Azure Bicep Documentation](https://docs.microsoft.com/azure/azure-resource-manager/bicep/)

## Feedback

For issues, questions, or feedback, please open an issue in the GitHub repository.
