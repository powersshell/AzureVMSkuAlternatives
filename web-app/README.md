# Azure VM SKU Alternatives - Web Application

A serverless web application for comparing Azure Virtual Machine SKUs based on comprehensive hardware specifications, capabilities, and pricing. Built with Azure Static Web Apps and Azure Functions.

## Architecture

- **Frontend**: HTML, CSS, JavaScript (hosted on Azure Static Web Apps)
- **Backend**: Azure Functions (Node.js) for API endpoints
- **Infrastructure**: Bicep templates for Infrastructure as Code (IaC)
- **Authentication**: Azure Managed Identity for secure Azure resource access
- **Monitoring**: Application Insights and Log Analytics

## Features

- 🔍 **Comprehensive VM Comparison**: Compare VMs across all capabilities (CPU, Memory, GPU, Storage, Network)
- ⚖️ **Customizable Weighting**: Adjust importance of different hardware aspects
- 💰 **Real-time Pricing**: Integration with Azure Retail Prices API
- 📊 **Similarity Scoring**: Intelligent weighted scoring algorithm
- 🌍 **Multi-region Support**: Search across all Azure regions
- 💱 **Multi-currency**: Pricing in multiple currencies (USD, EUR, GBP, etc.)
- 📈 **Export Capability**: Export results to CSV
- 🎯 **Special Filters**: NVMe and GPU matching requirements
- 📱 **Responsive Design**: Modern, mobile-friendly UI

## Prerequisites

- **Azure Subscription**: Active Azure subscription
- **Azure CLI**: Version 2.40.0 or higher ([Install](https://aka.ms/installazurecli))
- **Node.js**: Version 18.x or higher (for local development)
- **PowerShell**: Version 7.0 or higher (for deployment scripts)
- **Git**: For version control and GitHub Actions

## Project Structure

```
web-app/
├── src/                      # Frontend application
│   ├── index.html           # Main HTML file
│   ├── styles.css           # Styling
│   └── app.js               # Frontend logic
├── api/                      # Azure Functions API
│   ├── compare-vms.js       # VM comparison function
│   ├── package.json         # Node.js dependencies
│   ├── host.json            # Functions host configuration
│   └── local.settings.json  # Local development settings
├── infra/                    # Infrastructure as Code
│   ├── deploy.bicep         # Main deployment template
│   ├── main.bicep           # Core infrastructure
│   ├── modules/             # Bicep modules
│   │   └── role-assignment.bicep
│   ├── *.parameters.json    # Parameter files
│   ├── Deploy-Infrastructure.ps1   # Deployment script
│   └── Remove-Infrastructure.ps1   # Cleanup script
├── .github/
│   └── workflows/
│       └── azure-static-web-apps.yml  # CI/CD pipeline
├── staticwebapp.config.json  # Static Web App configuration
├── .gitignore
└── README.md
```

## Quick Start

### 1. Deploy Infrastructure

```powershell
# Navigate to the infrastructure directory
cd web-app/infra

# Login to Azure
az login

# Deploy the infrastructure
.\Deploy-Infrastructure.ps1 -Location "eastus2" -Sku "Free"
```

The script will:
- Create a resource group
- Deploy Azure Static Web App
- Configure Application Insights and Log Analytics
- Set up Managed Identity (Standard SKU)
- Assign necessary permissions
- Output deployment token for GitHub Actions

### 2. Configure GitHub Actions

1. Fork or clone this repository to your GitHub account
2. Add the following secrets to your GitHub repository:
   - `AZURE_STATIC_WEB_APPS_API_TOKEN`: Token from deployment output
   - `AZURE_SUBSCRIPTION_ID`: Your Azure subscription ID

To add secrets:
```
Repository → Settings → Secrets and variables → Actions → New repository secret
```

### 3. Deploy Application

Push your code to the `main` branch to trigger automatic deployment:

```bash
git add .
git commit -m "Initial deployment"
git push origin main
```

GitHub Actions will automatically build and deploy your application.

### 4. Access Your Application

After deployment completes, access your application at:
```
https://<your-static-web-app-name>.azurestaticapps.net
```

## Local Development

### Frontend Development

1. Open [src/index.html](src/index.html) in your browser
2. Use a local web server for better experience:

```bash
cd src
npx serve
```

### API Development

1. Install dependencies:
```bash
cd api
npm install
```

2. Configure local settings:
```bash
# Edit api/local.settings.json
{
  "Values": {
    "AZURE_SUBSCRIPTION_ID": "your-subscription-id"
  }
}
```

3. Start Azure Functions locally:
```bash
npm start
# or
func start
```

4. The API will be available at `http://localhost:7071/api/compare-vms`

## Configuration

### Static Web App SKU

- **Free**: Suitable for development and small-scale deployments
  - Managed identity not available
  - API requires anonymous access

- **Standard**: Recommended for production
  - Managed identity support
  - Custom domains
  - Enhanced performance

### Environment Variables

Configure these in your Static Web App settings:

- `AZURE_SUBSCRIPTION_ID`: Subscription ID for VM SKU queries

To set via Azure CLI:
```bash
az staticwebapp appsettings set \
  --name <static-web-app-name> \
  --resource-group <resource-group> \
  --setting-names AZURE_SUBSCRIPTION_ID="your-subscription-id"
```

## API Reference

### POST /api/compare-vms

Compare a VM SKU with alternatives in a region.

**Request Body:**
```json
{
  "skuName": "Standard_D4s_v3",
  "location": "eastus",
  "tolerance": 20,
  "minSimilarityScore": 60,
  "currencyCode": "USD",
  "weightCPU": 2.0,
  "weightMemory": 2.0,
  "weightGPU": 2.0,
  "weightStorage": 1.0,
  "weightNetwork": 1.0,
  "weightFeatures": 0.5,
  "requireNVMeMatch": false,
  "requireGPUMatch": false
}
```

**Response:**
```json
{
  "targetSku": {
    "name": "Standard_D4s_v3",
    "vCPUs": 4,
    "memoryGB": 16,
    "pricing": {
      "hourlyPrice": 0.192,
      "monthlyPrice": 140.16,
      "currency": "USD"
    },
    "zones": "1, 2, 3"
  },
  "alternatives": [
    {
      "name": "Standard_D4as_v4",
      "similarityScore": 95.5,
      "vCPUs": 4,
      "memoryGB": 16,
      "pricing": {...},
      "zones": "1, 2, 3"
    }
  ]
}
```

## Monitoring

### Application Insights

Monitor your application performance:

1. Navigate to your Application Insights resource in Azure Portal
2. View:
   - Live Metrics
   - Failures and exceptions
   - Performance metrics
   - Usage analytics

### Log Analytics

Query logs using Kusto Query Language (KQL):

```kql
// Function execution logs
traces
| where timestamp > ago(1h)
| where severityLevel >= 2
| project timestamp, message, severityLevel
| order by timestamp desc

// Function performance
requests
| where timestamp > ago(24h)
| summarize
    Count = count(),
    AvgDuration = avg(duration),
    P95Duration = percentile(duration, 95)
    by name
```

## Troubleshooting

### Common Issues

1. **"SKU not found" error**
   - Verify the SKU name is correct
   - Ensure the SKU is available in the specified region
   - Check if you have access to the subscription

2. **"Azure subscription not configured" error**
   - Set `AZURE_SUBSCRIPTION_ID` in Static Web App settings
   - Verify the managed identity has Reader access

3. **Pricing data not showing**
   - Some SKUs may not have pricing available
   - Check internet connectivity to Azure Retail Prices API
   - Verify currency code is supported

4. **Deployment fails**
   - Ensure Azure CLI is up to date
   - Verify you have Owner or Contributor role on subscription
   - Check resource naming conventions

### Enable Verbose Logging

Update [api/host.json](api/host.json):
```json
{
  "logging": {
    "logLevel": {
      "default": "Information",
      "Function": "Trace"
    }
  }
}
```

## Security Considerations

- Managed Identity used for Azure resource access (no stored credentials)
- API authentication can be configured in [staticwebapp.config.json](staticwebapp.config.json)
- Content Security Policy configured
- HTTPS enforced for all traffic
- Secrets stored in Azure Key Vault (recommended for production)

## Cost Estimation

### Free Tier
- Static Web Apps: Free (with limits)
- Azure Functions: Free tier (1M executions/month)
- Application Insights: Free tier (5GB/month)
- Estimated: **$0-5/month** for low usage

### Standard Tier
- Static Web Apps: ~$9/month
- Azure Functions: Consumption pricing
- Application Insights: Pay-as-you-go
- Estimated: **$20-50/month** for moderate usage

## Cleanup

To remove all deployed resources:

```powershell
cd web-app/infra
.\Remove-Infrastructure.ps1 -ResourceGroupName "rg-vmsku-alternatives"
```

## Contributing

Contributions are welcome! Please:
1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Submit a pull request

## License

This project is provided as-is for educational and demonstration purposes.

## Related Resources

- [Azure Static Web Apps Documentation](https://docs.microsoft.com/azure/static-web-apps/)
- [Azure Functions Documentation](https://docs.microsoft.com/azure/azure-functions/)
- [Azure Bicep Documentation](https://docs.microsoft.com/azure/azure-resource-manager/bicep/)
- [Azure VM Sizes](https://docs.microsoft.com/azure/virtual-machines/sizes)

## Support

For issues and questions:
- Open an issue in the GitHub repository
- Check existing issues for solutions
- Review Azure documentation

---

**Note**: This application is a companion to the PowerShell script [Compare-AzureVms.ps1](../Compare-AzureVms.ps1) located in the root directory. The PowerShell script remains unchanged and can be used independently for command-line VM comparisons.
