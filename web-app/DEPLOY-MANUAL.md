# Manual Deployment Guide

Your Azure infrastructure is ready, but the web app files haven't been deployed yet. Here are your options:

## Option 1: GitHub Actions (Recommended - No Node.js required)

### Step 1: Add GitHub Secrets

1. Go to your GitHub repository: https://github.com/YOUR-USERNAME/YOUR-REPO
2. Navigate to **Settings** → **Secrets and variables** → **Actions**
3. Click **New repository secret**

Add these two secrets:

**Secret 1:**
- Name: `AZURE_STATIC_WEB_APPS_API_TOKEN`
- Value: `bb18181120161d5f889a641c667d99d3d345cc83e2e55cb34885414b322ee7e001-349f170d-5f2f-4086-9abc-a622e14c1efa00f01030784c5d0f`

**Secret 2:**
- Name: `AZURE_SUBSCRIPTION_ID`
- Value: `e5ff2526-4548-4b13-b2fd-0f82ef7cd9e7`

### Step 2: Push to GitHub

```bash
cd C:\Azure\AzureVMSkuAlternatives
git add .
git commit -m "Deploy VM SKU comparison app"
git push origin main
```

### Step 3: Monitor Deployment

- Go to the **Actions** tab in your GitHub repository
- Watch the workflow run
- Deployment takes 2-3 minutes
- Your site will be live at: https://black-sea-0784c5d0f.1.azurestaticapps.net

---

## Option 2: Azure CLI Direct Upload

If you have Node.js installed, you can deploy directly:

```bash
# Install SWA CLI
npm install -g @azure/static-web-apps-cli

# Deploy
cd C:\Azure\AzureVMSkuAlternatives\web-app
swa deploy --app-location ./src --api-location ./api --deployment-token bb18181120161d5f889a641c667d99d3d345cc83e2e55cb34885414b322ee7e001-349f170d-5f2f-4086-9abc-a622e14c1efa00f01030784c5d0f
```

---

## Option 3: Install Node.js First

Download and install Node.js from: https://nodejs.org/en/download/

Then use Option 2 above.

---

## Verification

After deployment completes, visit:
- **Your App:** https://black-sea-0784c5d0f.1.azurestaticapps.net
- **API Health:** https://black-sea-0784c5d0f.1.azurestaticapps.net/api/compare-vms

Test the comparison tool with:
- SKU Name: `Standard_D4s_v3`
- Location: `eastus`

---

## Troubleshooting

**Issue:** "Your site will be ready soon" message persists
- **Solution:** Files haven't been deployed yet. Follow Option 1 or 2 above.

**Issue:** GitHub Actions fails
- **Solution:** Verify both secrets are added correctly with exact names.

**Issue:** API returns errors
- **Solution:** Configure subscription ID in app settings:
  ```bash
  az staticwebapp appsettings set --name vmsku-alternatives-webapp --resource-group rg-vmsku-alternatives --setting-names AZURE_SUBSCRIPTION_ID="e5ff2526-4548-4b13-b2fd-0f82ef7cd9e7"
  ```
