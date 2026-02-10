# Monitor Deployment Script

Write-Host "Monitoring Azure Static Web App deployment..." -ForegroundColor Cyan
Write-Host "GitHub Actions: https://github.com/powersshell/AzureVMSkuAlternatives/actions" -ForegroundColor Yellow
Write-Host ""

$startTime = Get-Date
$timeout = 300 # 5 minutes

Write-Host "Waiting for deployment to complete (max 5 minutes)..." -ForegroundColor Cyan

while (((Get-Date) - $startTime).TotalSeconds -lt $timeout) {
    $elapsed = [math]::Round(((Get-Date) - $startTime).TotalSeconds)
    Write-Host "`r[$elapsed seconds] Checking..." -NoNewline -ForegroundColor Gray

    # Check if Functions are deployed
    $functions = az staticwebapp functions show `
        --name vmsku-alternatives-webapp `
        --resource-group rg-vmsku-alternatives `
        --output json 2>$null | ConvertFrom-Json

    if ($functions -and $functions.Count -gt 0) {
        Write-Host "`n"
        Write-Host "✅ SUCCESS! Functions are now deployed!" -ForegroundColor Green
        Write-Host ""
        Write-Host "Deployed Functions:" -ForegroundColor Cyan
        $functions | ForEach-Object { Write-Host "  - $($_.functionName)" -ForegroundColor White }
        Write-Host ""
        Write-Host "Testing health endpoint..." -ForegroundColor Cyan

        try {
            $response = Invoke-WebRequest -Uri "https://black-sea-0784c5d0f.1.azurestaticapps.net/api/health" -UseBasicParsing
            Write-Host "✅ Health check passed! Status: $($response.StatusCode)" -ForegroundColor Green
            Write-Host "Response: $($response.Content)" -ForegroundColor White
        } catch {
            Write-Host "⚠️  Health check failed: $($_.Exception.Message)" -ForegroundColor Yellow
        }

        Write-Host ""
        Write-Host "Test the website: https://black-sea-0784c5d0f.1.azurestaticapps.net" -ForegroundColor Cyan
        exit 0
    }

    Start-Sleep -Seconds 15
}

Write-Host "`n"
Write-Host "⏱️  Timeout reached. Functions still not deployed." -ForegroundColor Yellow
Write-Host ""
Write-Host "Troubleshooting steps:" -ForegroundColor Cyan
Write-Host "1. Check GitHub Actions: https://github.com/powersshell/AzureVMSkuAlternatives/actions" -ForegroundColor White
Write-Host "2. Look for errors in the 'Build And Deploy' step" -ForegroundColor White
Write-Host "3. Check if the workflow completed successfully" -ForegroundColor White
Write-Host ""
Write-Host "Current Functions status:" -ForegroundColor Cyan
az staticwebapp functions show --name vmsku-alternatives-webapp --resource-group rg-vmsku-alternatives
