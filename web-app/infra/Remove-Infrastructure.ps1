#Requires -Version 7.0
<#
.SYNOPSIS
    Remove the Azure VM SKU Alternatives web application infrastructure
.DESCRIPTION
    This script removes all Azure resources created for the VM SKU Alternatives web application
.PARAMETER ResourceGroupName
    Name of the resource group to delete
.PARAMETER Force
    Skip confirmation prompt
.EXAMPLE
    .\Remove-Infrastructure.ps1
.EXAMPLE
    .\Remove-Infrastructure.ps1 -ResourceGroupName "my-rg" -Force
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $false)]
    [string]$ResourceGroupName = 'rg-vmsku-alternatives',

    [Parameter(Mandatory = $false)]
    [switch]$Force
)

$ErrorActionPreference = 'Stop'

Write-Host "================================" -ForegroundColor Red
Write-Host "Remove Azure VM SKU Alternatives Infrastructure" -ForegroundColor Red
Write-Host "================================" -ForegroundColor Red
Write-Host ""

# Check Azure login
$accountInfo = az account show 2>$null | ConvertFrom-Json

if (-not $accountInfo) {
    Write-Host "Not logged in to Azure. Please login first." -ForegroundColor Yellow
    az login
    $accountInfo = az account show | ConvertFrom-Json
}

Write-Host "Subscription: $($accountInfo.name) ($($accountInfo.id))" -ForegroundColor White
Write-Host "Resource Group: $ResourceGroupName" -ForegroundColor White
Write-Host ""

# Check if resource group exists
$rgExists = az group exists --name $ResourceGroupName --output tsv

if ($rgExists -eq 'false') {
    Write-Host "Resource group '$ResourceGroupName' does not exist" -ForegroundColor Yellow
    exit 0
}

# List resources in the group
Write-Host "Resources to be deleted:" -ForegroundColor Yellow
az resource list --resource-group $ResourceGroupName --output table

Write-Host ""
Write-Host "WARNING: This will permanently delete all resources in the resource group!" -ForegroundColor Red
Write-Host ""

if (-not $Force) {
    $confirmation = Read-Host "Are you sure you want to delete resource group '$ResourceGroupName'? (yes/no)"
    if ($confirmation -ne 'yes') {
        Write-Host "Deletion cancelled" -ForegroundColor Yellow
        exit 0
    }
}

# Delete resource group
Write-Host "`nDeleting resource group..." -ForegroundColor Cyan
az group delete --name $ResourceGroupName --yes --no-wait

Write-Host "✓ Resource group deletion initiated" -ForegroundColor Green
Write-Host "Note: Deletion happens asynchronously and may take several minutes" -ForegroundColor Yellow
