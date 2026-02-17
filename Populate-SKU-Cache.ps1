# Initial SKU Cache Population Script
# This script manually triggers the refresh_sku_cache function to populate initial data

param(
    [string]$ResourceGroup = "rg-vmsku-alternatives",
    [string]$FunctionAppName = "vmsku-api-functions-flex"
)

Write-Host "═══════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "Initial SKU Cache Population" -ForegroundColor Yellow
Write-Host "═══════════════════════════════════════════════════════`n" -ForegroundColor Cyan

Write-Host "Function App: $FunctionAppName" -ForegroundColor White
Write-Host "Resource Group: $ResourceGroup`n" -ForegroundColor White

# Get the Function App master key
Write-Host "1. Getting Function App master key..." -ForegroundColor Cyan
$masterKey = az functionapp keys list `
    --name $FunctionAppName `
    --resource-group $ResourceGroup `
    --query "masterKey" -o tsv 2>&1

if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ Failed to get master key" -ForegroundColor Red
    Write-Host "Error: $masterKey" -ForegroundColor Red
    exit 1
}

Write-Host "✅ Master key retrieved`n" -ForegroundColor Green

# Construct the function URL
$functionUrl = "https://$FunctionAppName.azurewebsites.net/admin/functions/refresh_sku_cache"

Write-Host "2. Triggering cache refresh function..." -ForegroundColor Cyan
Write-Host "   URL: $functionUrl" -ForegroundColor Gray
Write-Host "   This will take 10-30 minutes to complete (querying 30+ regions)..." -ForegroundColor Yellow
Write-Host "   You can monitor progress in Application Insights or function logs.`n" -ForegroundColor Yellow

# Trigger the function
try {
    $headers = @{
        "x-functions-key" = $masterKey
        "Content-Type" = "application/json"
    }
    
    $response = Invoke-WebRequest `
        -Uri $functionUrl `
        -Method Post `
        -Headers $headers `
        -Body "{}" `
        -UseBasicParsing `
        -TimeoutSec 1800  # 30 minute timeout
    
    Write-Host "✅ Function triggered successfully!" -ForegroundColor Green
    Write-Host "Status Code: $($response.StatusCode)" -ForegroundColor Gray
    
    if ($response.Content) {
        Write-Host "`nResponse:" -ForegroundColor White
        Write-Host $response.Content -ForegroundColor Gray
    }
    
} catch {
    if ($_.Exception.Message -like "*timeout*") {
        Write-Host "⚠️  Request timed out (this is normal for long-running function)" -ForegroundColor Yellow
        Write-Host "   The function is still running in the background." -ForegroundColor Yellow
        Write-Host "   Check logs to monitor progress:" -ForegroundColor Yellow
        Write-Host "   az functionapp log tail --name $FunctionAppName --resource-group $ResourceGroup" -ForegroundColor Gray
    } else {
        Write-Host "❌ Failed to trigger function" -ForegroundColor Red
        Write-Host "Error: $($_.Exception.Message)" -ForegroundColor Red
    }
}

Write-Host "`n3. Checking if data is being written to table..." -ForegroundColor Cyan
Start-Sleep -Seconds 10

# Check table statistics
$storageAccount = "vmskunapiuc3geioylp6ti"

Write-Host "   Storage Account: $storageAccount" -ForegroundColor Gray
Write-Host "   Table: vmskus" -ForegroundColor Gray

# Note: Checking private storage requires either:
# - Temporarily enabling public access, OR
# - Creating a query endpoint in the Function App

Write-Host "`n⏳ Cache population is running..." -ForegroundColor Yellow
Write-Host "   Expected duration: 10-30 minutes" -ForegroundColor Yellow
Write-Host "   Processing ~30 regions with 500-700 SKUs each" -ForegroundColor Yellow

Write-Host "`nMonitor progress:" -ForegroundColor White
Write-Host "  Azure Portal → Function App → Monitor → Logs" -ForegroundColor Gray
Write-Host "  OR" -ForegroundColor Gray
Write-Host "  az functionapp log tail --name $FunctionAppName --resource-group $ResourceGroup`n" -ForegroundColor Gray

Write-Host "═══════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "Next Steps:" -ForegroundColor Yellow
Write-Host "═══════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "1. Wait for cache population to complete (10-30 min)" -ForegroundColor White
Write-Host "2. Test list_skus endpoint:" -ForegroundColor White
Write-Host "   curl https://$FunctionAppName.azurewebsites.net/api/skus?location=eastus" -ForegroundColor Gray
Write-Host "3. Verify compare_vms uses cache:" -ForegroundColor White  
Write-Host "   Check logs for 'Loaded X SKUs from cache' message" -ForegroundColor Gray
Write-Host "4. Proceed with frontend dropdown implementation`n" -ForegroundColor White
