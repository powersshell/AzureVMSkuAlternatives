# API Troubleshooting Guide

## Quick Diagnostics

Run these commands to troubleshoot the API:

### 1. Check if Functions are deployed
```powershell
az staticwebapp functions show --name vmsku-alternatives-webapp --resource-group rg-vmsku-alternatives
```
**Expected:** Should show list of functions
**Actual:** Returns `[]` (empty) - **THIS IS THE PROBLEM**

### 2. Test Health Endpoint
```powershell
curl https://black-sea-0784c5d0f.1.azurestaticapps.net/api/health
```
**Expected:** JSON response with `{"status":"healthy"}`
**Actual:** 500 Internal Server Error

### 3. Check GitHub Actions
Visit: https://github.com/powersshell/AzureVMSkuAlternatives/actions

### 4. View Real-Time Logs (if Functions were working)
```powershell
az webapp log tail --name vmsku-alternatives-webapp --resource-group rg-vmsku-alternatives
```

## Possible Causes

### Issue #1: Functions Not Deploying
**Symptoms:** `az staticwebapp functions show` returns empty array

**Possible Causes:**
1. **Build failure during deployment** - Check GitHub Actions logs
2. **Wrong Node.js version** - Should be Node 18
3. **Missing dependencies** - `npm install` should run before deployment
4. **Incorrect api_location** - Should be `web-app/api`
5. **Static Web Apps doesn't detect Functions** - Needs proper structure

**Solution Steps:**
```powershell
# Check the package.json is valid
cd web-app/api
npm install
npm list

# Verify function.json files exist
Test-Path compare-vms/function.json
Test-Path health/function.json

# Trigger manual redeploy
git commit --allow-empty -m "Trigger redeploy"
git push
```

### Issue #2: Runtime Configuration
**Check Node version specified in workflow:**
- Should be Node.js 18 (currently set correctly in workflow)

**Check host.json:**
- Should use extension bundle `[3.*, 4.0.0)` for Functions v3

### Issue #3: Managed Identity Not Working
If Functions load but authentication fails:
```powershell
# Verify identity and permissions
az staticwebapp identity show --name vmsku-alternatives-webapp --resource-group rg-vmsku-alternatives

# Check role assignment
az role assignment list --assignee <principalId> --output table
```

## Testing the Frontend Error Logging

I just deployed better error logging. Now when you test the website:

1. **Open the website**: https://black-sea-0784c5d0f.1.azurestaticapps.net
2. **Open browser Developer Tools** (F12)
3. **Go to Console tab**
4. **Try a VM comparison**
5. **Check the console output** - it will show:
   - Response status
   - Response headers
   - Actual response text (even if it's not JSON)

This will tell us exactly what the API is returning.

## Manual API Testing

Test the API directly with PowerShell:

```powershell
# Test GET endpoint
$response = Invoke-WebRequest -Uri 'https://black-sea-0784c5d0f.1.azurestaticapps.net/api/compare-vms' -UseBasicParsing
$response.StatusCode
$response.Content

# Test POST endpoint
$body = @{
    skuName = "Standard_D4s_v3"
    location = "eastus"
} | ConvertTo-Json

try {
    $response = Invoke-WebRequest `
        -Uri 'https://black-sea-0784c5d0f.1.azurestaticapps.net/api/compare-vms' `
        -Method POST `
        -Body $body `
        -ContentType 'application/json' `
        -UseBasicParsing

    Write-Host "Success! Status: $($response.StatusCode)"
    Write-Host "Response: $($response.Content)"
} catch {
    Write-Host "Error: $($_.Exception.Message)"
    Write-Host "Status Code: $($_.Exception.Response.StatusCode.value__)"
}
```

## Check GitHub Actions Logs

1. Go to: https://github.com/powersshell/AzureVMSkuAlternatives/actions
2. Click on the most recent workflow run
3. Expand "Build And Deploy" step
4. Look for:
   - "Deploying build artifacts"
   - API-related messages
   - Any warnings or errors about Functions

**Look for these specific messages:**
- ✅ "Finished building app with Oryx"
- ✅ "Uploading build artifacts"
- ⚠️ Any "Skipping API" messages
- ❌ Any errors about node_modules or dependencies

## Next Steps

### Wait for Current Deployment (3-5 minutes)
The latest commit (`96667dd`) is deploying now with better logging.

### After Deployment:
1. **Test the website** and check browser console (F12 → Console)
2. **Copy the error message** from console
3. **Share the full error** including:
   - Response status
   - Response text
   - Any error messages

### If Functions Still Don't Deploy:
We may need to either:
1. **Use Bicep to redeploy** the Static Web App from scratch
2. **Try deploying with Azure CLI** instead of GitHub Actions
3. **Check if there's a quota/limit issue** on your subscription

## Command Reference

```powershell
# Force redeploy
git commit --allow-empty -m "Force redeploy"
git push

# Check deployment status
az staticwebapp show --name vmsku-alternatives-webapp --resource-group rg-vmsku-alternatives --query "{sku:sku.name,provider:provider,defaultHostname:defaultHostname}"

# List Functions (should not be empty!)
az staticwebapp functions show --name vmsku-alternatives-webapp --resource-group rg-vmsku-alternatives

# Check if API is even attempting to deploy
# Look in GitHub Actions logs for "API" mentions
```

## Current Status

- ✅ Frontend: **Working** (HTML/CSS/JS loads correctly)
- ❌ Functions: **Not Deployed** (empty functions array)
- ❌ API: **Returns 500 errors** (because Functions don't exist)
- ⏳ Deployment: **In progress** (commit 96667dd with better logging)

**Wait ~5 minutes**, then test again with browser console open!

---

## GitHub Actions Authentication Errors

### Error: "Login failed - Not all values are present"

**Full Error:**
```
Login failed with Error: Using auth-type: SERVICE_PRINCIPAL. 
Not all values are present. Ensure 'client-id' and 'tenant-id' are supplied.
```

**Root Cause:** GitHub Actions cannot find the required secrets.

**Solution: Add Secrets to GitHub**

#### Step 1: Go to Secrets Page
https://github.com/powersshell/AzureVMSkuAlternatives/settings/secrets/actions

#### Step 2: Add These 3 Secrets

Click **"New repository secret"** and add **exactly as shown**:

| Secret Name | Value |
|-------------|-------|
| `AZURE_CLIENT_ID` | `f8a64527-aad0-4015-acae-513733a8c0f7` |
| `AZURE_TENANT_ID` | `ec509388-6a83-4654-aaa8-9f078119b26d` |
| `AZURE_SUBSCRIPTION_ID` | `e5ff2526-4548-4b13-b2fd-0f82ef7cd9e7` |

**Important:**
- ✅ Names are **case-sensitive** - must match exactly
- ✅ Add to **Repository secrets** (under Actions), NOT Environments
- ✅ No quotes around values
- ✅ Copy entire GUID (no spaces)

#### Step 3: Re-run Workflow

After adding secrets:
1. Go to Actions tab
2. Click failed workflow run
3. Click "Re-run all jobs"

**Expected Result:** ✅ "Azure Login via OIDC - Login successful"

---

### Common Mistakes

#### ❌ Wrong Location
- Secrets in "Environments" → won't work
- Secrets in personal account → won't work
- ✅ Must be **Repository secrets** under **Actions**

#### ❌ Typos in Names
- Wrong: `AZURE_CLIENTID` (missing underscore)
- Wrong: `Azure_Client_Id` (wrong case)
- ✅ Correct: `AZURE_CLIENT_ID`

#### ❌ Extra Characters
- Wrong: ` f8a64527...` (leading space)
- Wrong: `"f8a64527..."` (quotes)
- ✅ Correct: `f8a64527...` (just the GUID)

---

## Quick Reference

### GitHub Secrets Location
**URL:** https://github.com/powersshell/AzureVMSkuAlternatives/settings/secrets/actions

Must be under: **Settings** → **Secrets and variables** → **Actions** → **Repository secrets**

### Verification
After adding secrets, check they appear in the list:
- AZURE_CLIENT_ID ✓
- AZURE_TENANT_ID ✓  
- AZURE_SUBSCRIPTION_ID ✓

(Values will show as •••• for security)

---
