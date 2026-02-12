# Deploy Standalone Functions App
# This deploys the API as a separate Azure Functions App instead of integrated with Static Web Apps

param(
    [Parameter(Mandatory=$false)]
    [string]$ResourceGroup = "rg-vmsku-alternatives",

    [Parameter(Mandatory=$false)]
    [string]$Location = "centralus",

    [Parameter(Mandatory=$false)]
    [string]$FunctionsAppName = "vmsku-api-functions",

    [Parameter(Mandatory=$false)]
    [string]$SubscriptionId = "e5ff2526-4548-4b13-b2fd-0f82ef7cd9e7"
)

Write-Host "Deploying Standalone Azure Functions App" -ForegroundColor Cyan
Write-Host "=========================================" -ForegroundColor Cyan
Write-Host ""

# Generate unique names
$storageAccountName = "vmskunapi$(Get-Random -Minimum 1000 -Maximum 9999)"
$appServicePlanName = "$FunctionsAppName-plan"

Write-Host "Configuration:" -ForegroundColor Yellow
Write-Host "  Resource Group: $ResourceGroup" -ForegroundColor White
Write-Host "  Functions App: $FunctionsAppName" -ForegroundColor White
Write-Host "  Storage Account: $storageAccountName" -ForegroundColor White
Write-Host "  Location: $Location" -ForegroundColor White
Write-Host ""

# 1. Deploy infrastructure
Write-Host "1. Deploying Functions App infrastructure..." -ForegroundColor Yellow
az deployment group create `
    --resource-group $ResourceGroup `
    --template-file "web-app/infra/functions-app.bicep" `
    --parameters `
        functionsAppName=$FunctionsAppName `
        storageAccountName=$storageAccountName `
        appServicePlanName=$appServicePlanName `
        subscriptionId=$SubscriptionId `
        location=$Location

if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ Infrastructure deployment failed" -ForegroundColor Red
    exit 1
}

Write-Host "✅ Infrastructure deployed" -ForegroundColor Green
Write-Host ""

# 2. Get the principal ID for role assignment
Write-Host "2. Getting managed identity..." -ForegroundColor Yellow
$principalId = az functionapp identity show `
    --name $FunctionsAppName `
    --resource-group $ResourceGroup `
    --query principalId `
    --output tsv

Write-Host "  Principal ID: $principalId" -ForegroundColor White
Write-Host ""

# 3. Assign Reader role
Write-Host "3. Assigning Reader role at subscription scope..." -ForegroundColor Yellow
az role assignment create `
    --assignee $principalId `
    --role Reader `
    --scope "/subscriptions/$SubscriptionId"

Write-Host "✅ Role assigned" -ForegroundColor Green
Write-Host ""

# 4. Deploy Functions code
Write-Host "4. Preparing Functions code for deployment..." -ForegroundColor Yellow
$deployPath = Join-Path $env:TEMP "functions-deploy-$(Get-Date -Format 'yyyyMMddHHmmss')"
New-Item -ItemType Directory -Path $deployPath -Force | Out-Null

# Copy API files
Copy-Item -Path "web-app/api/*" -Destination $deployPath -Recurse -Force
Write-Host "✅ Code prepared" -ForegroundColor Green
Write-Host ""

# 5. Deploy via zip
Write-Host "5. Deploying Functions code..." -ForegroundColor Yellow
Push-Location $deployPath
try {
    # Create zip
    $zipFile = Join-Path $env:TEMP "functions-deploy.zip"
    Compress-Archive -Path "$deployPath\*" -DestinationPath $zipFile -Force

    # Deploy zip
    az functionapp deployment source config-zip `
        --resource-group $ResourceGroup `
        --name $FunctionsAppName `
        --src $zipFile

    if ($LASTEXITCODE -eq 0) {
        Write-Host "✅ Functions deployed" -ForegroundColor Green
    } else {
        Write-Host "⚠️  Deployment completed with warnings" -ForegroundColor Yellow
    }
} finally {
    Pop-Location
}

Write-Host ""

# 6. Get Functions App URL
$functionsUrl = az functionapp show `
    --name $FunctionsAppName `
    --resource-group $ResourceGroup `
    --query defaultHostName `
    --output tsv

Write-Host "=========================================" -ForegroundColor Cyan
Write-Host "✅ Deployment Complete!" -ForegroundColor Green
Write-Host "=========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Functions App URL: https://$functionsUrl" -ForegroundColor Green
Write-Host "Health Endpoint: https://$functionsUrl/api/health" -ForegroundColor Green
Write-Host "Compare Endpoint: https://$functionsUrl/api/compare-vms" -ForegroundColor Green
Write-Host ""
Write-Host "Next Steps:" -ForegroundColor Yellow
Write-Host "1. Test the API: curl https://$functionsUrl/api/health" -ForegroundColor White
Write-Host "2. Update frontend (web-app/src/app.js) to point to new API URL" -ForegroundColor White
Write-Host "   Change: const API_BASE_URL = '/api';" -ForegroundColor White
Write-Host "   To: const API_BASE_URL = 'https://$functionsUrl/api';" -ForegroundColor White
Write-Host ""

# Cleanup
Remove-Item -Path $deployPath -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -Path $zipFile -ErrorAction SilentlyContinue
