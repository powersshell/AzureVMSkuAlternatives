# Flex Consumption with Private Storage - Architecture Guide

## Overview

This deployment uses **Azure Functions Flex Consumption** with **private-only storage** and **managed identity authentication** (no storage account keys).

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                     Azure Subscription                       │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐ │
│  │ Virtual Network (10.0.0.0/16)                          │ │
│  │                                                         │ │
│  │  ┌──────────────────────────────────────────────────┐  │ │
│  │  │ Function Integration Subnet (10.0.1.0/24)        │  │ │
│  │  │                                                   │  │ │
│  │  │  ┌─────────────────────────────────────────────┐ │  │ │
│  │  │  │  Azure Function App (Flex Consumption)      │ │  │ │
│  │  │  │  - Python 3.11                              │ │  │ │
│  │  │  │  - System Managed Identity                 │ │  │ │
│  │  │  │  - VNet Integrated                         │ │  │ │
│  │  │  └─────────────────────────────────────────────┘ │  │ │
│  │  └──────────────────────────────────────────────────┘  │ │
│  │                                                         │ │
│  │  ┌──────────────────────────────────────────────────┐  │ │
│  │  │ Private Endpoint Subnet (10.0.2.0/24)           │  │ │
│  │  │                                                   │  │ │
│  │  │  ┌────┐ ┌────┐ ┌─────┐ ┌─────┐                  │  │ │
│  │  │  │Blob│ │File│ │Queue│ │Table│ Private Endpoints │  │ │
│  │  │  └─┬──┘ └─┬──┘ └──┬──┘ └──┬──┘                  │  │ │
│  │  └────┼──────┼───────┼───────┼─────────────────────┘  │ │
│  └───────┼──────┼───────┼───────┼────────────────────────┘ │
│          │      │       │       │                           │
│  ┌───────┴──────┴───────┴───────┴─────────────────────┐    │
│  │ Storage Account (Private Only)                     │    │
│  │ - Public Access: Disabled                          │    │
│  │ - Shared Key Access: Disabled                      │    │
│  │ - Authentication: Managed Identity + RBAC          │    │
│  └────────────────────────────────────────────────────┘    │
│                                                              │
│  ┌────────────────────────────────────────────────────┐    │
│  │ Private DNS Zones                                  │    │
│  │ - privatelink.blob.core.windows.net                │    │
│  │ - privatelink.file.core.windows.net                │    │
│  │ - privatelink.queue.core.windows.net               │    │
│  │ - privatelink.table.core.windows.net               │    │
│  └────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────┘

Internet ─────▶ Function App Public Endpoint (HTTPS only)
                     │
                     ▼
              [Azure Functions]
                     │
                     ▼ (Private VNet)
              [Storage via Private Endpoints]
```

## Key Security Features

### 1. **No Public Storage Access**
- Storage account has `publicNetworkAccess: Disabled`
- All access must go through private endpoints
- Cannot access storage from internet even with keys

### 2. **No Storage Account Keys**
- Storage account has `allowSharedKeyAccess: false`
- Keys are disabled and cannot be used
- All access uses managed identity + RBAC

### 3. **Managed Identity Authentication**
- Function App uses system-assigned managed identity
- RBAC roles grant specific permissions:
  - `Storage Blob Data Owner` - Read/write blobs
  - `Storage Queue Data Contributor` - Queue access
  - `Storage Table Data Contributor` - Table access
  - `Storage File Data Privileged Contributor` - File access

### 4. **Private Network Communication**
- Function App integrates with VNet
- All storage traffic flows through private endpoints
- DNS resolution via private DNS zones
- No data leaves Azure backbone network

## Resource Configuration

### Virtual Network
```
Address Space: 10.0.0.0/16

Subnets:
  - function-integration-subnet: 10.0.1.0/24
    - Delegated to Microsoft.Web/serverFarms
    - Used for Function App VNet integration
    
  - private-endpoint-subnet: 10.0.2.0/24
    - Used for storage private endpoints
    - Private endpoint policies: Disabled
```

### Storage Account
```
Name: vmskunapi<uniqueString>
Type: StorageV2
SKU: Standard_LRS

Security Settings:
  - publicNetworkAccess: Disabled
  - allowSharedKeyAccess: false
  - allowBlobPublicAccess: false
  - supportsHttpsTrafficOnly: true
  - minimumTlsVersion: TLS1_2
  
Network Rules:
  - defaultAction: Deny
  - bypass: None
  - Access via: Private endpoints only
```

### Function App
```
Name: vmsku-api-functions-flex
Plan: Flex Consumption (FC1)
Runtime: Python 3.11
OS: Linux

Key Settings:
  - VNet Integration: Enabled (function-integration-subnet)
  - Route All Traffic: Enabled
  - Managed Identity: System-assigned
  
Storage Connection (Keyless):
  AzureWebJobsStorage__accountName: <storage-name>
  AzureWebJobsStorage__credential: managedidentity
  AzureWebJobsStorage__blobServiceUri: https://<storage>.blob.core.windows.net
  AzureWebJobsStorage__queueServiceUri: https://<storage>.queue.core.windows.net
  AzureWebJobsStorage__tableServiceUri: https://<storage>.table.core.windows.net
```

## RBAC Role Assignments

| Role | Scope | Purpose |
|------|-------|---------|
| Storage Blob Data Owner | Storage Account | Read/write deployment packages, function artifacts |
| Storage Queue Data Contributor | Storage Account | Queue-based triggers (if used) |
| Storage Table Data Contributor | Storage Account | Table storage access (if used) |
| Storage File Data Privileged Contributor | Storage Account | File share access (if used) |
| Reader | Subscription | Read Azure VM SKUs for comparison |

## Deployment Process

### Prerequisites
1. Azure CLI installed and authenticated
2. PowerShell 7+ (recommended)
3. VS Code with Azure Functions extension (for code deployment)

### Infrastructure Deployment

```powershell
# Deploy infrastructure
.\Deploy-Flex-Functions.ps1 -ResourceGroupName "rg-vmsku-alternatives" -FunctionsAppName "vmsku-api-functions-flex"

# Infrastructure only (skip code deployment)
.\Deploy-Flex-Functions.ps1 -SkipDeployment
```

**What gets deployed:**
1. Virtual Network with 2 subnets
2. Storage account (private, keyless)
3. 4 Private endpoints (Blob, File, Queue, Table)
4. 4 Private DNS zones
5. App Service Plan (Flex Consumption)
6. Function App with VNet integration
7. Application Insights
8. RBAC role assignments

### Function Code Deployment

**Option 1: VS Code (Recommended)**
1. Install Azure Functions extension in VS Code
2. Sign in to Azure (Ctrl+Shift+P → Azure: Sign In)
3. Right-click `web-app/api` folder
4. Select "Deploy to Function App..."
5. Choose `vmsku-api-functions-flex`
6. VS Code uses your Azure AD credentials (no keys needed)

**Option 2: Azure Functions Core Tools**
```bash
cd web-app/api
func azure functionapp publish vmsku-api-functions-flex --python
```

**Option 3: GitHub Actions (CI/CD)**
```yaml
- uses: azure/login@v1
  with:
    client-id: ${{ secrets.AZURE_CLIENT_ID }}
    tenant-id: ${{ secrets.AZURE_TENANT_ID }}
    subscription-id: ${{ secrets.AZURE_SUBSCRIPTION_ID }}
    
- uses: Azure/functions-action@v1
  with:
    app-name: vmsku-api-functions-flex
    package: ./web-app/api
```

## Testing

### Verify Infrastructure
```powershell
# Check Function App
az functionapp show --name vmsku-api-functions-flex --resource-group rg-vmsku-alternatives

# Check VNet integration
az functionapp vnet-integration list --name vmsku-api-functions-flex --resource-group rg-vmsku-alternatives

# Check private endpoints
az network private-endpoint list --resource-group rg-vmsku-alternatives

# Verify storage has no public access
az storage account show --name <storage-name> --resource-group rg-vmsku-alternatives --query "{PublicAccess:publicNetworkAccess,KeyAccess:allowSharedKeyAccess}"
```

### Test Function Endpoints
```powershell
# Health check
curl https://vmsku-api-functions-flex.azurewebsites.net/api/health

# Compare VMs
curl "https://vmsku-api-functions-flex.azurewebsites.net/api/compare_vms?currentVmSize=Standard_D2s_v3&region=eastus"
```

### Check Application Insights
```powershell
# Get latest logs
az monitor app-insights query \
  --app <app-insights-name> \
  --resource-group rg-vmsku-alternatives \
  --analytics-query "traces | where timestamp > ago(1h) | order by timestamp desc | take 50"
```

## Troubleshooting

### Function App Can't Access Storage
**Symptom**: 500 errors, logs show storage access denied

**Solutions:**
1. Wait 2-3 minutes for RBAC propagation
2. Verify RBAC roles assigned:
   ```powershell
   az role assignment list --assignee <function-app-principal-id> --scope <storage-account-id>
   ```
3. Check managed identity is enabled:
   ```powershell
   az functionapp identity show --name <function-app> --resource-group <rg>
   ```

### Deployment Fails with Network Error
**Symptom**: Cannot deploy functions, network timeout

**Solutions:**
1. Ensure you're deploying from a machine with internet access
2. VS Code and Azure CLI connect to Azure over internet (this is normal)
3. Function code deployment doesn't need to be from within the VNet

### Private Endpoints Not Resolving
**Symptom**: Function App can't reach storage

**Solutions:**
1. Verify private DNS zones are linked to VNet:
   ```powershell
   az network private-dns link vnet list --resource-group <rg> --zone-name privatelink.blob.core.windows.net
   ```
2. Check DNS resolution from Function App:
   ```powershell
   # Use Kudu console or Advanced Tools
   nslookup <storage-name>.blob.core.windows.net
   # Should resolve to 10.0.2.x address
   ```

### Storage Keys Are Disabled, Need Emergency Access
**Symptom**: Need to access storage but keys are disabled

**Solutions:**
1. **Temporary**: Enable shared key access:
   ```powershell
   az storage account update --name <storage> --resource-group <rg> --allow-shared-key-access true
   ```
2. **Permanent**: Use Azure AD authentication:
   ```powershell
   az storage blob list --account-name <storage> --container-name <container> --auth-mode login
   ```

## Migration from Consumption Plan

### Before Migration
1. **Backup current configuration**:
   ```powershell
   az functionapp config appsettings list --name vmsku-api-functions --resource-group rg-vmsku-alternatives > backup-settings.json
   ```

2. **Export function code**:
   ```powershell
   # Already in Git, but verify
   cd web-app/api
   git status
   ```

3. **Document current URLs and dependencies**

### Migration Steps
1. Deploy new Flex Consumption infrastructure (parallel deployment)
2. Deploy function code to new app
3. Test new app thoroughly
4. Update frontend to use new URL (if name changed)
5. Monitor both apps for 24-48 hours
6. Decommission old app

### Rollback Plan
If issues arise:
1. Keep old Consumption app running
2. Revert frontend to old URL
3. Investigate issues with Flex app
4. Fix and redeploy
5. Try migration again

## Cost Analysis

### Estimated Monthly Costs

**Flex Consumption Function App:**
- Execution time: ~$10-20 (depends on usage)
- Always-ready instances: $0 (not using reserved capacity)

**Private Networking:**
- Private Endpoints: 4 × $7.50 = $30/month
- Private DNS Zones: Negligible (~$1)
- Data processing: $0.01/GB (minimal)

**Storage:**
- Storage account: ~$2/month
- Transactions: ~$1/month

**Application Insights:**
- Data ingestion: ~$5/month (first 5GB free)

**Total Estimated: $48-60/month**

**Previous (Consumption): $5-20/month**

**Cost Increase: +$40-50/month for enhanced security**

## Benefits vs Costs

### Security Benefits (Worth the Cost)
- ✅ Zero public storage access
- ✅ No storage keys in configuration
- ✅ Private network communication
- ✅ Managed identity authentication
- ✅ Compliance-ready architecture

### Performance Benefits
- ✅ Better cold start performance (Flex Consumption)
- ✅ More predictable scaling
- ✅ Lower latency (private network)

### Operational Benefits
- ✅ Simpler key management (none needed)
- ✅ Better security posture
- ✅ Audit-friendly (RBAC-based access)

## Best Practices

1. **Always use managed identity** - Never use storage keys even if enabled
2. **Monitor RBAC changes** - Track who can access storage
3. **Use staging slots** - Test changes before production
4. **Enable diagnostic logs** - Send to Log Analytics
5. **Regular security reviews** - Verify no keys are stored anywhere
6. **Document network topology** - Keep architecture diagrams updated
7. **Test disaster recovery** - Practice infrastructure redeployment

## References

- [Azure Functions Flex Consumption](https://learn.microsoft.com/azure/azure-functions/flex-consumption-plan)
- [Private Endpoints for Azure Storage](https://learn.microsoft.com/azure/storage/common/storage-private-endpoints)
- [Managed Identity for Azure Functions](https://learn.microsoft.com/azure/app-service/overview-managed-identity)
- [Azure Functions Networking Options](https://learn.microsoft.com/azure/azure-functions/functions-networking-options)
