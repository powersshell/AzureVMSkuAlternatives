# Deploy Azure Functions - Flex Consumption with Private Storage
# Security: No public access, no storage keys, managed identity only

param(
    [Parameter(Mandatory=$false)]
    [string]$ResourceGroupName = "rg-vmsku-alternatives",
    
    [Parameter(Mandatory=$false)]
    [string]$Location = "centralus",
    
    [Parameter(Mandatory=$false)]
    [string]$FunctionsAppName = "vmsku-api-functions-flex",
    
    [Parameter(Mandatory=$false)]
    [switch]$SkipDeployment  # Skip function code deployment, infrastructure only
)

Write-Host "`n═══════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "DEPLOYING FLEX CONSUMPTION FUNCTION APP" -ForegroundColor Yellow
Write-Host "Security: Private Storage + Managed Identity" -ForegroundColor Green
Write-Host "═══════════════════════════════════════════════════════`n" -ForegroundColor Cyan

Write-Host "Configuration:" -ForegroundColor White
Write-Host "  Resource Group: $ResourceGroupName" -ForegroundColor Gray
Write-Host "  Location: $Location" -ForegroundColor Gray
Write-Host "  Functions App: $FunctionsAppName" -ForegroundColor Gray
Write-Host "  Plan: Flex Consumption (FC1)" -ForegroundColor Gray
Write-Host "  Storage: Private, keyless" -ForegroundColor Gray

# Check if resource group exists
$rgExists = az group exists --name $ResourceGroupName --output tsv
if ($rgExists -eq "false") {
    Write-Host "`nCreating resource group..." -ForegroundColor Yellow
    az group create --name $ResourceGroupName --location $Location
}

Write-Host "`n1. Deploying infrastructure (this may take 5-10 minutes)..." -ForegroundColor Cyan
Write-Host "   Components:" -ForegroundColor Gray
Write-Host "   - Virtual Network with subnets" -ForegroundColor Gray
Write-Host "   - Private storage account" -ForegroundColor Gray
Write-Host "   - 4 Private endpoints (Blob, File, Queue, Table)" -ForegroundColor Gray
Write-Host "   - Private DNS zones" -ForegroundColor Gray
Write-Host "   - Flex Consumption Function App" -ForegroundColor Gray
Write-Host "   - RBAC role assignments`n" -ForegroundColor Gray

$deploymentName = "functions-app-flex-$(Get-Date -Format 'yyyyMMddHHmmss')"

$deployment = az deployment group create `
    --resource-group $ResourceGroupName `
    --template-file "web-app\infra\functions-app-flex.bicep" `
    --parameters functionsAppName=$FunctionsAppName `
    --name $deploymentName `
    --output json | ConvertFrom-Json

if ($LASTEXITCODE -ne 0) {
    Write-Host "`n❌ Infrastructure deployment failed!" -ForegroundColor Red
    exit 1
}

Write-Host "`n✅ Infrastructure deployed successfully!`n" -ForegroundColor Green

# Extract outputs
$outputs = $deployment.properties.outputs
$functionAppName = $outputs.functionsAppName.value
$storageAccountName = $outputs.storageAccountName.value
$principalId = $outputs.functionsAppPrincipalId.value

Write-Host "Outputs:" -ForegroundColor White
Write-Host "  Function App: $functionAppName" -ForegroundColor Gray
Write-Host "  Storage Account: $storageAccountName" -ForegroundColor Gray
Write-Host "  Managed Identity: $principalId" -ForegroundColor Gray

if ($SkipDeployment) {
    Write-Host "`n⚠️  Skipping function code deployment (--SkipDeployment flag)`n" -ForegroundColor Yellow
    Write-Host "To deploy code manually:" -ForegroundColor White
    Write-Host "  1. Use VS Code Azure Functions extension" -ForegroundColor Gray
    Write-Host "  2. Right-click web-app/api folder" -ForegroundColor Gray
    Write-Host "  3. Deploy to Function App → $functionAppName`n" -ForegroundColor Gray
    exit 0
}

Write-Host "`n2. Waiting for RBAC propagation (30 seconds)..." -ForegroundColor Yellow
Write-Host "   (Managed identity needs time to receive storage permissions)" -ForegroundColor Gray
Start-Sleep -Seconds 30

Write-Host "`n3. Deploying Function code..." -ForegroundColor Cyan

# Check if we can use VS Code method or need to use zip
$useVsCode = $false

if (Get-Command func -ErrorAction SilentlyContinue) {
    Write-Host "   Azure Functions Core Tools detected" -ForegroundColor Green
    $useVsCode = $true
}

if ($useVsCode) {
    Write-Host "   Using Azure Functions Core Tools for deployment..." -ForegroundColor White
    
    Push-Location "web-app\api"
    
    # Deploy using func azure functionapp publish
    func azure functionapp publish $functionAppName --python
    
    Pop-Location
    
    if ($LASTEXITCODE -eq 0) {
        Write-Host "   ✅ Code deployed via Core Tools" -ForegroundColor Green
    } else {
        Write-Host "   ⚠️  Core Tools deployment had issues" -ForegroundColor Yellow
        Write-Host "   Falling back to manual instructions..." -ForegroundColor Gray
        $useVsCode = $false
    }
}

if (-not $useVsCode) {
    Write-Host "`n   ⚠️  Automatic deployment not available" -ForegroundColor Yellow
    Write-Host "   Please deploy manually using VS Code:" -ForegroundColor White
    Write-Host "   1. Install Azure Functions extension in VS Code" -ForegroundColor Gray
    Write-Host "   2. Right-click 'web-app/api' folder" -ForegroundColor Gray
    Write-Host "   3. Select 'Deploy to Function App...'" -ForegroundColor Gray
    Write-Host "   4. Choose '$functionAppName'" -ForegroundColor Gray
    Write-Host "   5. VS Code will use your Azure credentials (no keys needed)`n" -ForegroundColor Gray
}

Write-Host "`n4. Waiting for Function App to initialize (45 seconds)..." -ForegroundColor Yellow
Start-Sleep -Seconds 45

Write-Host "`n5. Testing endpoints..." -ForegroundColor Cyan

try {
    $healthUrl = "https://${functionAppName}.azurewebsites.net/api/health"
    Write-Host "   Testing: $healthUrl" -ForegroundColor Gray
    
    $health = Invoke-RestMethod -Uri $healthUrl -Method Get -TimeoutSec 30 -ErrorAction Stop
    Write-Host "   ✅ Health endpoint responding!" -ForegroundColor Green
    Write-Host "      Status: $($health.status)" -ForegroundColor Gray
    Write-Host "      Python: $($health.pythonVersion)" -ForegroundColor Gray
} catch {
    $statusCode = $_.Exception.Response.StatusCode.value__
    Write-Host "   ⚠️  Health endpoint: HTTP $statusCode" -ForegroundColor Yellow
    
    if ($statusCode -eq 404) {
        Write-Host "   → Functions not deployed yet. Use VS Code to deploy." -ForegroundColor Gray
    } elseif ($statusCode -eq 500) {
        Write-Host "   → Function App starting up. Wait 1-2 minutes and try again." -ForegroundColor Gray
    } else {
        Write-Host "   → Error: $($_.Exception.Message)" -ForegroundColor Gray
    }
}

Write-Host "`n═══════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "DEPLOYMENT COMPLETE!" -ForegroundColor Green
Write-Host "═══════════════════════════════════════════════════════`n" -ForegroundColor Cyan

Write-Host "Function App Details:" -ForegroundColor White
Write-Host "  URL: https://${functionAppName}.azurewebsites.net" -ForegroundColor Gray
Write-Host "  Health: https://${functionAppName}.azurewebsites.net/api/health" -ForegroundColor Gray
Write-Host "  Compare: https://${functionAppName}.azurewebsites.net/api/compare_vms" -ForegroundColor Gray

Write-Host "`nSecurity Configuration:" -ForegroundColor White
Write-Host "  ✅ Storage: Private endpoints only (no public access)" -ForegroundColor Green
Write-Host "  ✅ Authentication: Managed identity (no storage keys)" -ForegroundColor Green
Write-Host "  ✅ Network: VNet integrated" -ForegroundColor Green
Write-Host "  ✅ TLS: Minimum TLS 1.2" -ForegroundColor Green

Write-Host "`nNext Steps:" -ForegroundColor Yellow
if (-not $useVsCode) {
    Write-Host "  1. Deploy functions using VS Code (see instructions above)" -ForegroundColor White
}
Write-Host "  2. Test the API endpoints" -ForegroundColor White
Write-Host "  3. Update frontend to use new Function App URL (if changed)" -ForegroundColor White
Write-Host "  4. Monitor Application Insights for any issues" -ForegroundColor White

Write-Host "`nMonitoring:" -ForegroundColor White
Write-Host "  Application Insights: $($outputs.appInsightsConnectionString.value)" -ForegroundColor Gray

Write-Host ""
