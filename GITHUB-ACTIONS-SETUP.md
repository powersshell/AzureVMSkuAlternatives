# GitHub Actions Deployment Guide
## Flex Consumption with OIDC Authentication

This guide explains how to set up and use GitHub Actions for deploying Azure Functions with **no secrets** using **OIDC (OpenID Connect)** federated credentials.

## Overview

### What is OIDC?

OIDC allows GitHub Actions to authenticate to Azure using **short-lived tokens** instead of storing secrets. This provides:

- ✅ **No secrets in GitHub** - Tokens are generated on-demand and expire quickly
- ✅ **Works with private storage** - No need for storage account keys
- ✅ **Scoped access** - Limited to specific repository and branch
- ✅ **Auditable** - All actions logged in Azure AD
- ✅ **Secure** - Azure AD validates every request

### Architecture

```
┌─────────────┐
│   GitHub    │
│   Actions   │
└──────┬──────┘
       │ 1. Request JWT token
       ▼
┌─────────────────────────┐
│ GitHub Token Service    │
│ (token.actions.github.com)│
└──────────┬──────────────┘
           │ 2. Issue JWT (signed)
           ▼
    ┌──────────────┐
    │ Azure AD     │◄──── 3. Validate JWT
    │ (Entra ID)   │      4. Issue Azure token
    └──────┬───────┘
           │ 5. Use token
           ▼
    ┌──────────────┐
    │ Azure API    │
    │ (Deploy)     │
    └──────────────┘
```

**No secrets are stored!** Everything happens through Azure AD validation.

## Setup Process

### Prerequisites

1. **Azure Subscription** with permission to create App Registrations
2. **GitHub Repository** with admin access
3. **Azure CLI** installed
4. **PowerShell 7+** (for setup script)

### Step 1: Run Setup Script

This creates the Azure AD App Registration and configures federated credentials.

```powershell
# Run from repository root
.\Setup-GitHub-OIDC.ps1
```

**What it does:**
1. Creates Azure AD App Registration
2. Creates Service Principal
3. Configures federated credential for GitHub
4. Assigns Azure RBAC roles (all at **subscription** scope — `deploy.bicep` is
   subscription-scoped, so it creates the resource group and assigns roles):
   - `Contributor` on subscription (deploy resources + create the RG)
   - `Role Based Access Control Administrator` on subscription (create the role
     assignments the Bicep defines — Function App MI Reader + Storage data roles).
     Least-privilege alternative to `User Access Administrator`; can be pinned to
     just the required role IDs with an ABAC `--condition`.
   - `Reader` on subscription (read VM SKUs; redundant under Contributor)
5. Outputs GitHub secrets to configure

**Output example:**
```
📋 GitHub Secrets to Configure:

  Name: AZURE_CLIENT_ID
  Value: 12345678-1234-1234-1234-123456789abc

  Name: AZURE_TENANT_ID
  Value: 87654321-4321-4321-4321-cba987654321

  Name: AZURE_SUBSCRIPTION_ID
  Value: abcdef12-3456-7890-abcd-ef1234567890
```

**Configuration saved to:** `github-oidc-config.txt` (for your reference)

### Step 2: Configure GitHub Secrets

1. Go to your repository on GitHub
2. Navigate to **Settings** → **Secrets and variables** → **Actions**
3. Click **New repository secret**
4. Add the three secrets from the setup script output:

| Secret Name | Description | Where to Get |
|-------------|-------------|--------------|
| `AZURE_CLIENT_ID` | Service Principal Application ID | From setup script output |
| `AZURE_TENANT_ID` | Azure AD Tenant ID | From setup script output |
| `AZURE_SUBSCRIPTION_ID` | Target Azure Subscription | From setup script output |

**Do NOT add:**
- ❌ `AZURE_FUNCTIONAPP_PUBLISH_PROFILE` (not needed anymore)
- ❌ Storage account keys (disabled by design)
- ❌ Any passwords or secrets

### Step 3: Remove Old Secret (Optional)

If you have the old `AZURE_FUNCTIONAPP_PUBLISH_PROFILE` secret:
1. Go to **Settings** → **Secrets and variables** → **Actions**
2. Find `AZURE_FUNCTIONAPP_PUBLISH_PROFILE`
3. Click **Delete**

This secret contained storage account keys and won't work with private storage anyway.

### Step 4: Commit Updated Workflow

The workflow file has been updated at `.github/workflows/deploy-functions.yml`.

```bash
git add .github/workflows/deploy-functions.yml
git add Setup-GitHub-OIDC.ps1
git commit -m "Update GitHub Actions to use OIDC for Flex Consumption deployment"
git push origin main
```

This will trigger the deployment!

## Workflow Features

### Automatic Triggers

The workflow runs automatically when:
- Code changes in `web-app/api/**`
- Infrastructure changes in `web-app/infra/functions-app-flex.bicep`
- Workflow file changes

### Manual Triggers

Run manually via GitHub UI:
1. Go to **Actions** tab
2. Select "Deploy Flex Consumption Functions"
3. Click **Run workflow**
4. Options:
   - ☑️ Deploy infrastructure - Check this to deploy Bicep template too

### Workflow Jobs

**Job 1: deploy-infrastructure** (Optional)
- Runs only if manually triggered with `deploy_infrastructure=true`
- Deploys Bicep template
- Creates VNet, storage, private endpoints, Function App
- Waits for RBAC propagation (2 minutes)

**Job 2: build-and-deploy** (Always runs)
- Installs Python dependencies
- Authenticates via OIDC
- Deploys function code
- Tests health endpoint

**Job 3: verify-deployment** (Always runs)
- Verifies Function App configuration
- Checks VNet integration
- Validates private storage security
- Prints deployment summary

## Testing the Deployment

### Monitor in GitHub

1. Go to **Actions** tab in GitHub
2. Click on the latest workflow run
3. Watch the logs in real-time
4. Green checkmarks = success ✅

### Verify in Azure Portal

1. Navigate to the Function App in Azure Portal
2. Check **Functions** blade - should see `health` and `compare_vms`
3. Check **Networking** blade - should see VNet integration
4. Check **Configuration** - should see managed identity settings (no keys!)

### Test Endpoints

```bash
# Health check
curl https://vmsku-api-functions-flex.azurewebsites.net/api/health

# Compare VMs
curl "https://vmsku-api-functions-flex.azurewebsites.net/api/compare_vms?currentVmSize=Standard_D2s_v3&region=eastus"
```

## Troubleshooting

### Error: "No subscription found"

**Cause:** Azure CLI in workflow can't authenticate

**Solution:**
1. Verify GitHub secrets are set correctly
2. Run `Setup-GitHub-OIDC.ps1` again
3. Check the App Registration still exists in Azure AD

### Error: "Federated credential not found"

**Cause:** Federated credential configuration is missing or incorrect

**Solution:**
```powershell
# Re-run setup script
.\Setup-GitHub-OIDC.ps1

# Or manually check in Azure Portal:
# Azure AD → App Registrations → [Your App] → Certificates & secrets → Federated credentials
```

### Error: "Insufficient permissions"

**Cause:** Service Principal doesn't have required roles

**Solution:**
```powershell
# Check current role assignments
$sp = az ad sp list --display-name "github-actions-vmsku-functions" --query "[0]" -o json | ConvertFrom-Json
az role assignment list --assignee $sp.id --all

# Required roles (all at SUBSCRIPTION scope — deploy.bicep is subscription-scoped,
# creates the RG, and assigns roles, so RG-scoped grants fail with ResourceGroupNotFound):
# 1. Contributor (Subscription scope) - Deploy resources + create the RG
# 2. Role Based Access Control Administrator (Subscription scope) - Create the role
#    assignments the Bicep defines (least-privilege vs User Access Administrator)
# 3. Reader (Subscription scope) - Read VM SKUs (redundant under Contributor)

# Re-assign roles if needed
az role assignment create --role "Contributor" --assignee $sp.id --scope "/subscriptions/{sub-id}"
az role assignment create --role "Role Based Access Control Administrator" --assignee $sp.id --scope "/subscriptions/{sub-id}"
az role assignment create --role "Reader" --assignee $sp.id --scope "/subscriptions/{sub-id}"

# Optional: pin RBAC Administrator to only the roles the Bicep assigns
# (Reader, Storage Blob Data Owner, Storage Queue/Table Data Contributor):
$roles = "acdd72a7-3385-48ef-bd42-f606fba81ae7,b7e6dc6d-f1e8-4753-8033-0f276bb0955b,974c5e8b-45b9-4653-ba55-5f855dd0fb88,0a9a7e1f-b9d0-4cc4-a60d-0319b160aaa3"
$cond = "((!(ActionMatches{'Microsoft.Authorization/roleAssignments/write'})) OR (@Request[Microsoft.Authorization/roleAssignments:RoleDefinitionId] ForAnyOfAnyValues:GuidEquals {$roles})) AND ((!(ActionMatches{'Microsoft.Authorization/roleAssignments/delete'})) OR (@Resource[Microsoft.Authorization/roleAssignments:RoleDefinitionId] ForAnyOfAnyValues:GuidEquals {$roles}))"
az role assignment create --role "Role Based Access Control Administrator" --assignee $sp.id --scope "/subscriptions/{sub-id}" --condition "$cond" --condition-version "2.0"
```

### Error: "Storage account access denied"

**Cause:** RBAC roles haven't propagated yet

**Solution:**
- Wait 2-3 minutes and try again
- RBAC propagation can take a few minutes
- Workflow has built-in waits, but you may need to re-run

### Workflow runs but functions not deployed

**Cause:** Private storage requires different deployment method

**Solution:**
- Ensure `Azure/functions-action@v1` is being used (workflow already updated)
- Check Function App logs in Azure Portal
- Verify storage account has private endpoints configured

### How to verify OIDC is working

```bash
# Check federated credential configuration
az ad app federated-credential list --id {CLIENT_ID}

# Should show:
# - Issuer: https://token.actions.githubusercontent.com
# - Subject: repo:your-org/your-repo:ref:refs/heads/main
# - Audiences: api://AzureADTokenExchange
```

## Security Best Practices

### ✅ Do's

- **Use OIDC** - No secrets to leak or rotate
- **Scope permissions** - Service Principal has minimal required access
- **Monitor deployments** - Review GitHub Actions logs regularly
- **Use branch protection** - Require PR reviews before merging to main
- **Enable audit logs** - Track all Azure AD authentication events

### ❌ Don'ts

- **Don't use publish profiles** - Contains storage keys
- **Don't use storage account keys** - Disabled by design
- **Don't grant excessive permissions** - Service Principal should only have what it needs
- **Don't bypass branch protection** - Direct commits to main = unreviewed deployments

## Maintenance

### Rotating Credentials

**Good news:** With OIDC, there's nothing to rotate!
- Tokens are short-lived (minutes)
- Generated on-demand
- Automatically expire

### Updating Permissions

If you need to change Service Principal permissions:

```powershell
# List current roles
az role assignment list --assignee {SERVICE_PRINCIPAL_OBJECT_ID}

# Add a new role
az role assignment create --role "Storage Blob Data Contributor" --assignee {SP_ID} --scope {SCOPE}

# Remove a role
az role assignment delete --role "Reader" --assignee {SP_ID} --scope {SCOPE}
```

### Adding More Repositories

To use the same Service Principal for another repository:

```powershell
# Add another federated credential
az ad app federated-credential create \
  --id {APP_ID} \
  --parameters '{
    "name": "github-actions-another-repo",
    "issuer": "https://token.actions.githubusercontent.com",
    "subject": "repo:your-org/another-repo:ref:refs/heads/main",
    "audiences": ["api://AzureADTokenExchange"]
  }'
```

## Advanced Usage

### Deploy Infrastructure on Every Push

Change the workflow condition:

```yaml
deploy-infrastructure:
  runs-on: ubuntu-latest
  # Remove the 'if' condition to run always
  steps:
    # ...
```

### Add Staging Environment

Add environment-specific secrets:

```yaml
deploy-to-staging:
  environment: staging
  steps:
    - uses: azure/login@v1
      with:
        client-id: ${{ secrets.STAGING_AZURE_CLIENT_ID }}
        # ...
```

### Parallel Deployments

Deploy to multiple regions:

```yaml
strategy:
  matrix:
    region: [eastus, westus, centralus]
```

## Comparison: Old vs New

| Feature | Old (Publish Profile) | New (OIDC) |
|---------|---------------------|------------|
| **Secrets** | Storage keys in publish profile | None (tokens on-demand) |
| **Security** | Keys can leak | Azure AD validated |
| **Rotation** | Manual (when keys rotate) | Automatic (tokens expire) |
| **Scope** | Full storage access | RBAC-scoped |
| **Private Storage** | ❌ Requires public access | ✅ Works with private |
| **Audit** | Limited | Full Azure AD audit trail |
| **Setup** | Download profile | Run setup script once |

## Resources

- [GitHub OIDC with Azure](https://docs.github.com/en/actions/deployment/security-hardening-your-deployments/configuring-openid-connect-in-azure)
- [Azure Functions Deployment](https://learn.microsoft.com/en-us/azure/azure-functions/functions-how-to-github-actions)
- [Workload Identity Federation](https://learn.microsoft.com/en-us/azure/active-directory/develop/workload-identity-federation)
- [Azure RBAC](https://learn.microsoft.com/en-us/azure/role-based-access-control/overview)

## Support

If you encounter issues:

1. Check the workflow logs in GitHub Actions
2. Review Azure Function App logs in Portal
3. Verify OIDC configuration: `az ad app federated-credential list --id {CLIENT_ID}`
4. Check Service Principal roles: `az role assignment list --assignee {SP_ID}`
5. Re-run `Setup-GitHub-OIDC.ps1` to recreate configuration

Remember: **No secrets = better security!** 🔒
