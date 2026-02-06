# Azure VM SKU Alternatives - Quick Start Guide

Get your Azure VM SKU comparison web app running in under 10 minutes!

## Prerequisites Checklist

- [ ] Azure subscription ([Get free trial](https://azure.microsoft.com/free/))
- [ ] Azure CLI installed ([Download](https://aka.ms/installazurecli))
- [ ] PowerShell 7+ installed ([Download](https://aka.ms/powershell))
- [ ] GitHub account (for CI/CD)

## Step-by-Step Deployment

### 1. Prepare Your Environment (2 minutes)

```powershell
# Clone or navigate to your repository
cd c:\Azure\AzureVMSkuAlternatives\web-app

# Login to Azure
az login

# Verify login
az account show
```

### 2. Deploy Infrastructure (5 minutes)

```powershell
# Navigate to infrastructure folder
cd infra

# Run deployment script (uses default settings)
.\Deploy-Infrastructure.ps1
```

**What happens:**
- Creates resource group `rg-vmsku-alternatives` in `centralus`
- Deploys Azure Static Web App (Free tier)
- Sets up Application Insights for monitoring
- Configures Log Analytics workspace
- Outputs deployment token

**Save this output!** You'll need it for GitHub Actions:
```
AZURE_STATIC_WEB_APPS_API_TOKEN=<your-token>
```

### 3. Configure GitHub Actions (2 minutes)

1. **Add Deployment Token:**
   - Go to your GitHub repo → **Settings** → **Secrets and variables** → **Actions**
   - Click **New repository secret**
   - Name: `AZURE_STATIC_WEB_APPS_API_TOKEN`
   - Value: `<token-from-step-2>`

2. **Add Subscription ID:**
   - Add another secret
   - Name: `AZURE_SUBSCRIPTION_ID`
   - Value: Your Azure subscription ID (from deployment output)

### 4. Deploy Your App (1 minute)

```bash
# Push to GitHub to trigger deployment
git add .
git commit -m "Deploy VM SKU comparison app"
git push origin main
```

**Monitor deployment:**
- Go to **Actions** tab in GitHub
- Watch the workflow run
- Deployment typically takes 2-3 minutes

### 5. Access Your App (30 seconds)

Your app URL was in the deployment output:
```
https://vmsku-alternatives-webapp-<random>.azurestaticapps.net
```

**Or find it in Azure:**
```powershell
az staticwebapp show \
  --name vmsku-alternatives-webapp \
  --resource-group rg-vmsku-alternatives \
  --query defaultHostname -o tsv
```

### 6. Test It Out!

1. Open your app URL
2. Fill in the form:
   - **SKU Name:** `Standard_D4s_v3`
   - **Location:** `eastus`
3. Click **Compare VM SKUs**
4. View your results!

## Troubleshooting

### Issue: "Azure subscription not configured"

**Fix:**
```bash
az staticwebapp appsettings set \
  --name vmsku-alternatives-webapp \
  --resource-group rg-vmsku-alternatives \
  --setting-names AZURE_SUBSCRIPTION_ID="<your-subscription-id>"
```

### Issue: GitHub Actions fails

**Fix:**
1. Regenerate deployment token:
   ```bash
   az staticwebapp secrets list \
     --name vmsku-alternatives-webapp \
     --resource-group rg-vmsku-alternatives \
     --query "properties.apiKey" -o tsv
   ```
2. Update the `AZURE_STATIC_WEB_APPS_API_TOKEN` secret in GitHub

### Issue: No pricing data

- Some SKUs don't have public pricing
- Wait a few seconds and retry
- Try a different region

## Next Steps

✅ **Your app is live!** Here's what to do next:

1. **Customize:** Update [src/index.html](src/index.html) with your branding
2. **Monitor:** Check Application Insights in Azure Portal
3. **Secure:** Add authentication (see [README.md](README.md))
4. **Scale:** Upgrade to Standard SKU for production (see [DEPLOYMENT.md](DEPLOYMENT.md))
5. **Custom Domain:** Add your own domain (see [README.md](README.md))

## Cleanup

When you're done testing:

```powershell
cd infra
.\Remove-Infrastructure.ps1
```

This deletes all Azure resources to avoid charges.

## Need Help?

- 📚 Full documentation: [README.md](README.md)
- 🚀 Deployment guide: [DEPLOYMENT.md](DEPLOYMENT.md)
- 💻 PowerShell version: [../Compare-AzureVms.ps1](../Compare-AzureVms.ps1)

## Estimated Costs

**Free Tier:** $0-5/month
- Perfect for testing and small deployments
- 100GB bandwidth/month
- 2 custom domains

**Standard Tier:** $20-50/month
- Production ready
- Unlimited bandwidth
- Enhanced performance

---

**Congratulations!** 🎉 You now have a serverless VM SKU comparison tool running on Azure!
