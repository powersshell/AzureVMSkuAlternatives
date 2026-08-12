#requires -Module Az.Compute
<#
.SYNOPSIS
    Compare Azure VM SKUs based on comprehensive hardware specifications and capabilities
.DESCRIPTION
    Compares a given VM SKU with all available SKUs in a region and finds similar alternatives based on ALL capabilities.
    Supports custom weighting for CPU, Memory, and all other capabilities to fine-tune comparison results.
.PARAMETER SkuName
    The VM SKU to compare (e.g., "Standard_D4s_v3")
.PARAMETER Location
    The Azure region to search for SKUs (e.g., "eastus")
.PARAMETER Tolerance
    Percentage tolerance for matching capabilities (default: 20%)
.PARAMETER CurrencyCode
    Currency code for pricing information (default: USD)
.PARAMETER WeightCPU
    Weight for vCPU comparison (default: 2.0). Higher values prioritize CPU matching.
.PARAMETER WeightMemory
    Weight for Memory comparison (default: 2.0). Higher values prioritize memory matching.
.PARAMETER WeightStorage
    Weight for storage-related capabilities like IOPS, throughput, and disk counts (default: 1.0)
.PARAMETER WeightNetwork
    Weight for network capabilities like NICs and bandwidth (default: 1.0)
.PARAMETER WeightFeatures
    Weight for feature flags like PremiumIO, Ephemeral OS Disk, etc. (default: 0.5)
.PARAMETER WeightGPU
    Weight for GPU comparison (default: 2.0). Higher values prioritize GPU matching.
.PARAMETER MinSimilarityScore
    Minimum similarity score (0-100) to include in results (default: 60)
.PARAMETER ShowAllCapabilities
    Display all capabilities in the output table (can be verbose)
.PARAMETER RequireNVMeMatch
    If the target SKU has NVMe support, only show alternatives that also have NVMe support
.PARAMETER RequireGPUMatch
    If the target SKU has GPU support, only show alternatives that also have GPU support
.PARAMETER CpuVendor
    Filter alternatives by CPU vendor. One or more of: Intel, AMD, ARM. Default: all vendors.
.PARAMETER HideRetiring
    Exclude SKUs announced for retirement or already retired (default: $true, matching the website).
    Use -HideRetiring:$false to include retiring SKUs (a similarity-score penalty is applied for ranking).
.PARAMETER HideGrowthRestricted
    Exclude growth-restricted (capacity-limited) SKUs. Default: $false, matching the website --
    these sizes are still supported and are shown with a warning plus a similarity-score penalty.
    Growth-restricted series can't be deployed by new subscriptions and won't be granted
    additional quota. Use -HideGrowthRestricted to drop them entirely.
.PARAMETER PricingModel
    Pricing model to display and use for cost-efficiency: PAYG, RI1Year, or RI3Year (default: PAYG).
    Reserved Instance models show $null/N/A for SKUs without RI pricing.
.PARAMETER OS
    Operating system for pricing: Linux or Windows (default: Linux).
.PARAMETER CheckRegion
    A second Azure region to check each alternative's availability in (adds an AvailableIn<region> column).
.PARAMETER ExportCsv
    Path to export the full result set as a CSV file.
.EXAMPLE
    .\Compare-AzureVms.ps1 -SkuName "Standard_D4s_v3" -Location "eastus"
.EXAMPLE
    .\Compare-AzureVms.ps1 -SkuName "Standard_D4s_v3" -Location "eastus" -WeightCPU 3.0 -WeightMemory 1.5
.EXAMPLE
    .\Compare-AzureVms.ps1 -SkuName "Standard_D4s_v3" -Location "eastus" -WeightStorage 2.0 -MinSimilarityScore 70
.EXAMPLE
    .\Compare-AzureVms.ps1 -SkuName "Standard_L8s_v3" -Location "eastus" -RequireNVMeMatch
.EXAMPLE
    .\Compare-AzureVms.ps1 -SkuName "Standard_NC6s_v3" -Location "eastus" -RequireGPUMatch -WeightGPU 3.0
.EXAMPLE
    .\Compare-AzureVms.ps1 -SkuName "Standard_D4as_v5" -Location "eastus" -CpuVendor AMD,ARM
.EXAMPLE
    .\Compare-AzureVms.ps1 -SkuName "Standard_D4s_v5" -Location "eastus" -PricingModel RI3Year -OS Windows
.EXAMPLE
    .\Compare-AzureVms.ps1 -SkuName "Standard_D4s_v5" -Location "eastus" -CheckRegion "westeurope" -ExportCsv ".\results.csv"
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$SkuName,

    [Parameter(Mandatory = $true)]
    [string]$Location,

    [Parameter(Mandatory = $false)]
    [int]$Tolerance = 20,

    [Parameter(Mandatory = $false)]
    [string]$CurrencyCode = 'USD',

    [Parameter(Mandatory = $false)]
    [double]$WeightCPU = 2.0,

    [Parameter(Mandatory = $false)]
    [double]$WeightMemory = 2.0,

    [Parameter(Mandatory = $false)]
    [double]$WeightStorage = 1.0,

    [Parameter(Mandatory = $false)]
    [double]$WeightNetwork = 1.0,

    [Parameter(Mandatory = $false)]
    [double]$WeightFeatures = 0.5,

    [Parameter(Mandatory = $false)]
    [double]$WeightGPU = 2.0,

    [Parameter(Mandatory = $false)]
    [int]$MinSimilarityScore = 60,

    [Parameter(Mandatory = $false)]
    [switch]$ShowAllCapabilities,

    [Parameter(Mandatory = $false)]
    [switch]$RequireNVMeMatch,

    [Parameter(Mandatory = $false)]
    [switch]$RequireGPUMatch,

    [Parameter(Mandatory = $false)]
    [ValidateSet('Intel', 'AMD', 'ARM')]
    [string[]]$CpuVendor,

    [Parameter(Mandatory = $false)]
    [bool]$HideRetiring = $true,

    [Parameter(Mandatory = $false)]
    [switch]$HideGrowthRestricted,

    [Parameter(Mandatory = $false)]
    [ValidateSet('PAYG', 'RI1Year', 'RI3Year')]
    [string]$PricingModel = 'PAYG',

    [Parameter(Mandatory = $false)]
    [ValidateSet('Linux', 'Windows')]
    [string]$OS = 'Linux',

    [Parameter(Mandatory = $false)]
    [string]$CheckRegion,

    [Parameter(Mandatory = $false)]
    [string]$ExportCsv
)

# Ensure Az.Compute module is available
if (-not (Get-Module -ListAvailable -Name Az.Compute)) {
    Write-Error "Az.Compute module is not installed. Install it with: Install-Module -Name Az.Compute"
    exit 1
}

# ============================================================================
# Static data tables ported from web-app/api/function_app.py (source of truth)
# ============================================================================
$script:CpuPerformanceTable = @{
    'E5-2673 v3' = @{ Score = 96; Generation = 'Haswell'; Year = 2014 }
    'E5-2673 v4' = @{ Score = 89; Generation = 'Broadwell'; Year = 2016 }
    '8171M' = @{ Score = 85; Generation = 'Skylake'; Year = 2017 }
    '8168' = @{ Score = 97; Generation = 'Skylake'; Year = 2017 }
    'E-2288G' = @{ Score = 187; Generation = 'Coffee Lake'; Year = 2019 }
    'E-2176G' = @{ Score = 176; Generation = 'Coffee Lake'; Year = 2018 }
    '8272CL' = @{ Score = 96; Generation = 'Cascade Lake'; Year = 2019 }
    '8280M' = @{ Score = 73; Generation = 'Cascade Lake'; Year = 2019 }
    '6246R' = @{ Score = 108; Generation = 'Cascade Lake'; Year = 2020 }
    '8370C' = @{ Score = 100; Generation = 'Ice Lake'; Year = 2021 }
    '8473C' = @{ Score = 115; Generation = 'Sapphire Rapids'; Year = 2023 }
    '8488C' = @{ Score = 115; Generation = 'Sapphire Rapids'; Year = 2023 }
    '8573C' = @{ Score = 120; Generation = 'Emerald Rapids'; Year = 2024 }
    '8592+' = @{ Score = 120; Generation = 'Emerald Rapids'; Year = 2024 }
    '7551' = @{ Score = 72; Generation = 'Naples (Zen 1)'; Year = 2017 }
    '7452' = @{ Score = 101; Generation = 'Rome (Zen 2)'; Year = 2019 }
    '7V12' = @{ Score = 121; Generation = 'Rome (Zen 2)'; Year = 2020 }
    '7763' = @{ Score = 106; Generation = 'Milan (Zen 3)'; Year = 2021 }
    '7V13' = @{ Score = 136; Generation = 'Milan (Zen 3)'; Year = 2021 }
    '7V73X' = @{ Score = 141; Generation = 'Milan-X (Zen 3)'; Year = 2022 }
    '9004' = @{ Score = 122; Generation = 'Genoa (Zen 4)'; Year = 2023 }
    '9V004' = @{ Score = 122; Generation = 'Genoa (Zen 4)'; Year = 2023 }
    # Custom Azure-exclusive EPYC 9004-series part with HBM3, used by HBv5.
    # https://learn.microsoft.com/azure/virtual-machines/hbv5-series-overview
    '9V64H' = @{ Score = 122; Generation = 'Genoa (Zen 4)'; Year = 2024 }
    '9005' = @{ Score = 135; Generation = 'Turin (Zen 5)'; Year = 2024 }
    '9754' = @{ Score = 95; Generation = 'Bergamo (Zen 4c)'; Year = 2023 }
    'Cobalt 100' = @{ Score = 120; Generation = 'Cobalt 100 (Neoverse N2)'; Year = 2023 }
    'Ampere Altra' = @{ Score = 95; Generation = 'Ampere Altra (Neoverse N1)'; Year = 2022 }
}

$script:SeriesCpuMap = @{
    'Dv3' = @('8272CL', '8171M', 'E5-2673 v4')
    'Dsv3' = @('8272CL', '8171M', 'E5-2673 v4')
    'Dv4' = @('8272CL')
    'Dsv4' = @('8272CL')
    'Ddv4' = @('8272CL')
    'Ddsv4' = @('8272CL')
    'Dv5' = @('8370C')
    'Dsv5' = @('8473C', '8370C', '8573C')
    'Ddv5' = @('8370C')
    'Ddsv5' = @('8370C')
    'Dlsv5' = @('8370C')
    'Dldsv5' = @('8370C')
    'Dsv6' = @('8473C', '8573C')
    'Ddsv6' = @('8473C', '8573C')
    'Dlsv6' = @('8473C', '8573C')
    'Dldsv6' = @('8473C', '8573C')
    'Dsv7' = @('8573C')
    'Ddsv7' = @('8573C')
    'Dlsv7' = @('8573C')
    'Dldsv7' = @('8573C')
    'Dav4' = @('7452')
    'Dasv4' = @('7452')
    'Dasv5' = @('7763')
    'Dadsv5' = @('7763')
    'Dasv6' = @('9004')
    'Dadsv6' = @('9004')
    'Dalsv6' = @('9004')
    'Daldsv6' = @('9004')
    'Dasv7' = @('9005')
    'Dadsv7' = @('9005')
    'Dalsv7' = @('9005')
    'Daldsv7' = @('9005')
    'Dpsv5' = @('Ampere Altra')
    'Dpdsv5' = @('Ampere Altra')
    'Dplsv5' = @('Ampere Altra')
    'Dpldsv5' = @('Ampere Altra')
    'Dpsv6' = @('Cobalt 100')
    'Dpdsv6' = @('Cobalt 100')
    'Dplsv6' = @('Cobalt 100')
    'Dpldsv6' = @('Cobalt 100')
    'Ev3' = @('8272CL', '8171M', 'E5-2673 v4')
    'Esv3' = @('8272CL', '8171M', 'E5-2673 v4')
    'Ev4' = @('8272CL')
    'Esv4' = @('8272CL')
    'Edv4' = @('8272CL')
    'Edsv4' = @('8272CL')
    'Ev5' = @('8370C')
    'Esv5' = @('8473C', '8370C', '8573C')
    'Edv5' = @('8370C')
    'Edsv5' = @('8370C')
    'Esv6' = @('8473C', '8573C')
    'Edsv6' = @('8473C', '8573C')
    'Ensv6' = @('8473C', '8573C')
    'Endsv6' = @('8473C', '8573C')
    'Esv7' = @('8573C')
    'Edsv7' = @('8573C')
    'Eav4' = @('7452')
    'Easv4' = @('7452')
    'Easv5' = @('7763')
    'Eadsv5' = @('7763')
    'Easv6' = @('9004')
    'Eadsv6' = @('9004')
    'Easv7' = @('9005')
    'Eadsv7' = @('9005')
    'Epsv5' = @('Ampere Altra')
    'Epdsv5' = @('Ampere Altra')
    'Epsv6' = @('Cobalt 100')
    'Epdsv6' = @('Cobalt 100')
    'Ebsv5' = @('8370C')
    'Ebdsv5' = @('8370C', '8573C')
    'Ebsv6' = @('8573C')
    'Ebdsv6' = @('8573C')
    'Msv2' = @('8280M')
    'Mdsv2' = @('8280M')
    'Msv3' = @('8473C')
    'Mdsv3' = @('8473C')
    'Mbsv3' = @('8473C')
    'Mbdsv3' = @('8473C')
    'Mv2' = @('8280M')
    'Fsv2' = @('8272CL', '8370C', '8168')
    'FXsv2' = @('8370C')
    'FXmdsv2' = @('8370C')
    'Fasv6' = @('9004')
    'Famsv6' = @('9004')
    'Falsv6' = @('9004')
    'Fasv7' = @('9005')
    'Fadsv7' = @('9005')
    'Falsv7' = @('9005')
    'Faldsv7' = @('9005')
    'Famsv7' = @('9005')
    'Famdsv7' = @('9005')
    'Lsv2' = @('7551')
    'Lasv3' = @('7763')
    'Lsv3' = @('8370C')
    'Lasv4' = @('9004')
    'Lsv4' = @('8473C')
    'DCsv2' = @('E-2288G')
    'DCsv3' = @('8370C')
    'DCdsv3' = @('8370C')
    'DCesv6' = @('8573C')
    'DCedsv6' = @('8573C')
    'DCasv5' = @('7763')
    'DCadsv5' = @('7763')
    'DCasv6' = @('9004')
    'DCadsv6' = @('9004')
    'ECasv5' = @('7763')
    'ECadsv5' = @('7763')
    'ECasv6' = @('9004')
    'ECadsv6' = @('9004')
    'Bsv2' = @('8370C')
    'Basv2' = @('7763')
    'Bpsv2' = @('Ampere Altra')
    'Balsv2' = @('7763')
    'Blsv2' = @('8370C')
    'Batsv2' = @('7763')
    'Btsv2' = @('8370C')
    'Bplsv2' = @('Ampere Altra')
    'Bptsv2' = @('Ampere Altra')
    'HBv3' = @('7V13')
    'HBv4' = @('9V004')
    'HBv5' = @('9V64H')
    'HBv2' = @('7V12')
    'HBrsv3' = @('7V13')
    'HBrsv4' = @('9V004')
    'HBrsv5' = @('9V64H')
    'HBrsv2' = @('7V12')
    'HC' = @('8168')
    'HX' = @('7V13')
    'HXrs' = @('7V13')
    'FXmsv2' = @('8370C')
    'Laosv4' = @('9004')
    'Mmsv2' = @('8280M')
    'Mmsv3' = @('8473C')
    'Ensv7' = @('8573C')
    'Endsv7' = @('8573C')
    'Epsv7' = @('Cobalt 100')
    'Epdsv7' = @('Cobalt 100')
    'Dv2' = @('8272CL', '8171M', 'E5-2673 v4', 'E5-2673 v3')
    'DSv2' = @('8272CL', '8171M', 'E5-2673 v4', 'E5-2673 v3')
    'Av2' = @('8272CL', '8171M', 'E5-2673 v4', 'E5-2673 v3')
    'D' = @('E5-2673 v3')
    'DS' = @('E5-2673 v3')
    'F' = @('E5-2673 v3', 'E5-2673 v4')
    'Fs' = @('E5-2673 v3', 'E5-2673 v4')
    'M' = @('E5-2673 v4')
    'Mms' = @('E5-2673 v4')
    'Ms' = @('E5-2673 v4')
    'G' = @('E5-2673 v3')
    'Gs' = @('E5-2673 v3')
    'L' = @('E5-2673 v3')
    'Ls' = @('E5-2673 v3')
    'B' = @('E5-2673 v4', '8171M')
    'Bms' = @('E5-2673 v4', '8171M')
    'Bs' = @('E5-2673 v4', '8171M')
    'Bls' = @('E5-2673 v4', '8171M')
    'DC' = @('E-2176G')
    'DCs' = @('E-2176G')
    'DCv2' = @('E-2288G')
    'EC' = @('7763')
    'FX' = @('8370C')
    'FXmds' = @('8370C')
    'Amv2' = @('8272CL', '8171M', 'E5-2673 v4', 'E5-2673 v3')
    'HCrs' = @('8168')
}

$script:VmRetirementInfo = @(
    @{ Pattern = '^Standard_D\d+$'; Status = 'Announced'; RetirementDate = '2028-05-01'; MigrationGuideUrl = 'https://learn.microsoft.com/azure/virtual-machines/migration/sizes/d-ds-dv2-dsv2-ls-series-migration-guide' }
    @{ Pattern = '^Standard_DS\d+$'; Status = 'Announced'; RetirementDate = '2028-05-01'; MigrationGuideUrl = 'https://learn.microsoft.com/azure/virtual-machines/migration/sizes/d-ds-dv2-dsv2-ls-series-migration-guide' }
    @{ Pattern = '^Standard_D\d+_v2$'; Status = 'Announced'; RetirementDate = '2028-05-01'; MigrationGuideUrl = 'https://learn.microsoft.com/azure/virtual-machines/migration/sizes/d-ds-dv2-dsv2-ls-series-migration-guide' }
    # Note the (-\d+)? group: constrained-vCPU variants (e.g. Standard_DS11-1_v2)
    # retire with their parent series and must match here too.
    @{ Pattern = '^Standard_DS\d+(-\d+)?_v2$'; Status = 'Announced'; RetirementDate = '2028-05-01'; MigrationGuideUrl = 'https://learn.microsoft.com/azure/virtual-machines/migration/sizes/d-ds-dv2-dsv2-ls-series-migration-guide' }
    @{ Pattern = '^Standard_A\d+m?_v2$'; Status = 'Announced'; RetirementDate = '2028-11-15'; MigrationGuideUrl = 'https://learn.microsoft.com/azure/virtual-machines/migration/sizes/d-ds-dv2-dsv2-ls-series-migration-guide' }
    # Narrower B-series pattern must precede the general one: '[a-z]*' would
    # otherwise absorb 'l' and leave this entry permanently shadowed.
    @{ Pattern = '^Standard_B\d+ls$'; Status = 'Announced'; RetirementDate = '2028-11-15'; MigrationGuideUrl = 'https://learn.microsoft.com/azure/virtual-machines/migration/sizes/d-ds-dv2-dsv2-ls-series-migration-guide' }
    @{ Pattern = '^Standard_B\d+[a-z]*s$'; Status = 'Announced'; RetirementDate = '2028-11-15'; MigrationGuideUrl = 'https://learn.microsoft.com/azure/virtual-machines/migration/sizes/d-ds-dv2-dsv2-ls-series-migration-guide' }
    @{ Pattern = '^Standard_F\d+$'; Status = 'Announced'; RetirementDate = '2028-11-15'; MigrationGuideUrl = 'https://learn.microsoft.com/azure/virtual-machines/migration/sizes/d-ds-dv2-dsv2-ls-series-migration-guide' }
    @{ Pattern = '^Standard_F\d+s$'; Status = 'Announced'; RetirementDate = '2028-11-15'; MigrationGuideUrl = 'https://learn.microsoft.com/azure/virtual-machines/migration/sizes/d-ds-dv2-dsv2-ls-series-migration-guide' }
    @{ Pattern = '^Standard_F\d+s_v2$'; Status = 'Announced'; RetirementDate = '2028-11-15'; MigrationGuideUrl = 'https://learn.microsoft.com/azure/virtual-machines/migration/sizes/d-ds-dv2-dsv2-ls-series-migration-guide' }
    @{ Pattern = '^Standard_G\d+$'; Status = 'Announced'; RetirementDate = '2028-11-15'; MigrationGuideUrl = 'https://learn.microsoft.com/azure/virtual-machines/migration/sizes/d-ds-dv2-dsv2-ls-series-migration-guide' }
    @{ Pattern = '^Standard_GS\d+(-\d+)?$'; Status = 'Announced'; RetirementDate = '2028-11-15'; MigrationGuideUrl = 'https://learn.microsoft.com/azure/virtual-machines/migration/sizes/d-ds-dv2-dsv2-ls-series-migration-guide' }
    @{ Pattern = '^Standard_M192idms_v2$'; Status = 'Announced'; RetirementDate = '2027-03-31'; MigrationGuideUrl = 'https://learn.microsoft.com/azure/virtual-machines/sizes/retirement/msv2-mdsv2-retirement' }
    @{ Pattern = '^Standard_M192ids_v2$'; Status = 'Announced'; RetirementDate = '2027-03-31'; MigrationGuideUrl = 'https://learn.microsoft.com/azure/virtual-machines/sizes/retirement/msv2-mdsv2-retirement' }
    @{ Pattern = '^Standard_M192ims_v2$'; Status = 'Announced'; RetirementDate = '2027-03-31'; MigrationGuideUrl = 'https://learn.microsoft.com/azure/virtual-machines/sizes/retirement/msv2-mdsv2-retirement' }
    @{ Pattern = '^Standard_M192is_v2$'; Status = 'Announced'; RetirementDate = '2027-03-31'; MigrationGuideUrl = 'https://learn.microsoft.com/azure/virtual-machines/sizes/retirement/msv2-mdsv2-retirement' }
    @{ Pattern = '^Standard_L\d+s$'; Status = 'Announced'; RetirementDate = '2028-05-01'; MigrationGuideUrl = 'https://learn.microsoft.com/azure/virtual-machines/migration/sizes/d-ds-dv2-dsv2-ls-series-migration-guide' }
    @{ Pattern = '^Standard_L\d+s_v2$'; Status = 'Announced'; RetirementDate = '2028-11-15'; MigrationGuideUrl = 'https://learn.microsoft.com/azure/virtual-machines/migration/sizes/d-ds-dv2-dsv2-ls-series-migration-guide' }
    @{ Pattern = '^Standard_NC24rs_v3$'; Status = 'Retired'; RetirementDate = '2025-09-30'; MigrationGuideUrl = 'https://learn.microsoft.com/azure/virtual-machines/ncv3-nc24rs-retirement' }
    @{ Pattern = '^Standard_NC\d+s?_v3$'; Status = 'Retired'; RetirementDate = '2025-09-30'; MigrationGuideUrl = 'https://learn.microsoft.com/azure/virtual-machines/ncv3-retirement' }
    @{ Pattern = '^Standard_NV\d+s?_v3$'; Status = 'Announced'; RetirementDate = '2026-09-30'; MigrationGuideUrl = 'https://learn.microsoft.com/azure/virtual-machines/sizes/gpu-accelerated/nvv3-series-retirement' }
    @{ Pattern = '^Standard_NV\d+as_v4$'; Status = 'Announced'; RetirementDate = '2026-09-30'; MigrationGuideUrl = 'https://learn.microsoft.com/azure/virtual-machines/sizes/gpu-accelerated/nvv4-retirement' }
    @{ Pattern = '^Standard_NP\d+s$'; Status = 'Announced'; RetirementDate = '2027-05-31'; MigrationGuideUrl = 'https://learn.microsoft.com/azure/virtual-machines/sizes/retirement/np-series-retirement' }
)

# Growth restriction (capacity limitation) data.
# Source: https://learn.microsoft.com/azure/virtual-machines/migration/sizes/previous-gen-series-capacity-limitations
# Effective July 2026: new subscriptions can't deploy these series, and existing
# subscriptions won't be granted additional quota. This is NOT retirement -- the Dv3/Ev3
# and Dv4/Ev4 families remain fully supported. The two flags are independent.
$script:GrowthRestrictionDocUrl = 'https://learn.microsoft.com/azure/virtual-machines/migration/sizes/previous-gen-series-capacity-limitations'
$script:GrowthRestrictionEffectiveDate = '2026-07-01'
$script:GrowthRestrictionPenalty = 8.0

$script:VmGrowthRestrictionInfo = @(
    # Compute optimized
    @{ Pattern = '^Standard_F\d+$'; Series = 'F'; Category = 'Compute optimized'; RecommendedTargets = @('Fsv6', 'Fasv6') }
    @{ Pattern = '^Standard_F\d+s$'; Series = 'Fs'; Category = 'Compute optimized'; RecommendedTargets = @('Fsv6', 'Fasv6') }
    @{ Pattern = '^Standard_F\d+s_v2$'; Series = 'Fsv2'; Category = 'Compute optimized'; RecommendedTargets = @('Fsv6', 'Fasv6') }
    # General purpose
    @{ Pattern = '^Standard_D\d+$'; Series = 'D'; Category = 'General purpose'; RecommendedTargets = @('Dsv5', 'Dsv6', 'Dsv7') }
    @{ Pattern = '^Standard_DS\d+(-\d+)?$'; Series = 'Ds'; Category = 'General purpose'; RecommendedTargets = @('Dsv5', 'Dsv6', 'Dsv7') }
    @{ Pattern = '^Standard_D\d+_v2$'; Series = 'Dv2'; Category = 'General purpose'; RecommendedTargets = @('Dsv5', 'Dsv6', 'Dsv7') }
    @{ Pattern = '^Standard_DS\d+(-\d+)?_v2$'; Series = 'Dsv2'; Category = 'General purpose'; RecommendedTargets = @('Dsv5', 'Dsv6', 'Dsv7') }
    @{ Pattern = '^Standard_D\d+_v3$'; Series = 'Dv3'; Category = 'General purpose'; RecommendedTargets = @('Dv5', 'Dv6', 'Dv7') }
    @{ Pattern = '^Standard_D\d+(-\d+)?s_v3$'; Series = 'Dsv3'; Category = 'General purpose'; RecommendedTargets = @('Dsv5', 'Dsv6', 'Dsv7') }
    @{ Pattern = '^Standard_D\d+_v4$'; Series = 'Dv4'; Category = 'General purpose'; RecommendedTargets = @('Dv5', 'Dv6', 'Dv7') }
    @{ Pattern = '^Standard_D\d+(-\d+)?s_v4$'; Series = 'Dsv4'; Category = 'General purpose'; RecommendedTargets = @('Dsv5', 'Dsv6', 'Dsv7') }
    @{ Pattern = '^Standard_D\d+d_v4$'; Series = 'Ddv4'; Category = 'General purpose'; RecommendedTargets = @('Ddv5', 'Ddv6', 'Ddv7') }
    @{ Pattern = '^Standard_D\d+(-\d+)?ds_v4$'; Series = 'Ddsv4'; Category = 'General purpose'; RecommendedTargets = @('Ddsv5', 'Ddsv6', 'Ddsv7') }
    @{ Pattern = '^Standard_D\d+a_v4$'; Series = 'Dav4'; Category = 'General purpose'; RecommendedTargets = @('Dasv5', 'Dasv6') }
    @{ Pattern = '^Standard_D\d+(-\d+)?as_v4$'; Series = 'Dasv4'; Category = 'General purpose'; RecommendedTargets = @('Dasv5', 'Dasv6') }
    @{ Pattern = '^Standard_D\d+ad_v4$'; Series = 'Ddav4'; Category = 'General purpose'; RecommendedTargets = @('Dadsv5', 'Dadsv6') }
    @{ Pattern = '^Standard_D\d+(-\d+)?ads_v4$'; Series = 'Ddav4'; Category = 'General purpose'; RecommendedTargets = @('Dadsv5', 'Dadsv6') }
    @{ Pattern = '^Standard_B\d+[a-z]*s$'; Series = 'B / Bs'; Category = 'General purpose'; RecommendedTargets = @('Bsv2', 'Dsv5') }
    @{ Pattern = '^Standard_A\d+m?_v2$'; Series = 'Av2 / Amv2'; Category = 'General purpose'; RecommendedTargets = @('Dsv5', 'Dsv6') }
    # Memory optimized
    @{ Pattern = '^Standard_E\d+i?_v3$'; Series = 'Ev3'; Category = 'Memory optimized'; RecommendedTargets = @('Ev5', 'Esv6', 'Esv7') }
    @{ Pattern = '^Standard_E\d+(-\d+)?i?s_v3$'; Series = 'Esv3'; Category = 'Memory optimized'; RecommendedTargets = @('Esv5', 'Esv6', 'Esv7') }
    @{ Pattern = '^Standard_E\d+i?_v4$'; Series = 'Ev4'; Category = 'Memory optimized'; RecommendedTargets = @('Ev5', 'Esv6', 'Esv7') }
    @{ Pattern = '^Standard_E\d+(-\d+)?i?s_v4$'; Series = 'Esv4'; Category = 'Memory optimized'; RecommendedTargets = @('Esv5', 'Esv6', 'Esv7') }
    @{ Pattern = '^Standard_E\d+i?d_v4$'; Series = 'Edv4'; Category = 'Memory optimized'; RecommendedTargets = @('Edsv5', 'Edsv6') }
    @{ Pattern = '^Standard_E\d+(-\d+)?i?ds_v4$'; Series = 'Edsv4'; Category = 'Memory optimized'; RecommendedTargets = @('Edsv5', 'Edsv6') }
    @{ Pattern = '^Standard_E\d+a_v4$'; Series = 'Eav4'; Category = 'Memory optimized'; RecommendedTargets = @('Easv5', 'Easv6') }
    @{ Pattern = '^Standard_E\d+(-\d+)?as_v4$'; Series = 'Easv4'; Category = 'Memory optimized'; RecommendedTargets = @('Easv5', 'Easv6') }
    @{ Pattern = '^Standard_G\d+$'; Series = 'G'; Category = 'Memory optimized'; RecommendedTargets = @('Esv5', 'Esv6') }
    @{ Pattern = '^Standard_GS\d+(-\d+)?$'; Series = 'Gs'; Category = 'Memory optimized'; RecommendedTargets = @('Esv5', 'Esv6') }
    # Storage optimized
    @{ Pattern = '^Standard_L\d+s$'; Series = 'Ls'; Category = 'Storage optimized'; RecommendedTargets = @('Lsv3', 'Lasv3') }
    @{ Pattern = '^Standard_L\d+s_v2$'; Series = 'Lsv2'; Category = 'Storage optimized'; RecommendedTargets = @('Lsv3', 'Lasv3') }
)

# --- CPU performance, vendor, retirement, and growth restriction helpers ---
# Static data and logic below are ported from web-app/api/function_app.py
# (CPU_PERFORMANCE_TABLE, SERIES_CPU_MAP, VM_RETIREMENT_INFO, VM_GROWTH_RESTRICTION_INFO,
#  detect_cpu_vendor, _get_series_prefix, get_cpu_performance, _get_retirement_info,
#  _retirement_penalty, _get_growth_restriction_info, _growth_restriction_penalty).
# Keep these in sync with the Python source of truth when it changes.

function Get-SeriesPrefix {
    <#
    .SYNOPSIS
        Extract the series prefix from a SKU name for CPU mapping.
        E.g., 'Standard_D2s_v5' -> 'Dsv5', 'Standard_E96-24ads_v6' -> 'Eadsv6'.
        Ported from _get_series_prefix in function_app.py.
    #>
    param([Parameter(Mandatory = $true)][string]$SkuName)

    $name = $SkuName -replace '^Standard_', '' -replace '^Basic_', ''
    # Remove constrained vCPU prefix (e.g., E96-24ads_v6 -> Eads_v6)
    $name = [regex]::Replace($name, '^([A-Za-z]+)\d+-\d+', '$1')

    $ic = [System.Text.RegularExpressions.RegexOptions]::IgnoreCase
    $m = [regex]::Match($name, '^([A-Za-z]+)[0-9]*([a-z]*)_v(\d+)', $ic)
    if ($m.Success) {
        $family = $m.Groups[1].Value
        $modifiers = $m.Groups[2].Value
        $version = $m.Groups[3].Value
        $prefix = '{0}{1}v{2}' -f $family, $modifiers, $version
        if ($script:SeriesCpuMap.ContainsKey($prefix)) { return $prefix }
        # The 'i' additive feature means "isolated" -- a dedicated-host variant of
        # the same silicon (Eisv5 is an isolated Esv5), so it is never mapped
        # separately. Drop it and reuse the base series' CPU.
        if ($modifiers.Contains('i')) {
            $idx = $modifiers.IndexOf('i')
            $baseModifiers = $modifiers.Remove($idx, 1)
            $isolatedBase = '{0}{1}v{2}' -f $family, $baseModifiers, $version
            if ($script:SeriesCpuMap.ContainsKey($isolatedBase)) { return $isolatedBase }
        }
        return $prefix
    }
    # Handle non-versioned series (HC, HB, M, etc.)
    $m2 = [regex]::Match($name, '^([A-Za-z]+)[0-9]*([a-z]*)', $ic)
    if ($m2.Success) {
        $family = $m2.Groups[1].Value
        $modifiers = $m2.Groups[2].Value
        $prefix = "$family$modifiers"
        if ($script:SeriesCpuMap.ContainsKey($prefix)) { return $prefix }
        if ($script:SeriesCpuMap.ContainsKey($family)) { return $family }
    }
    return $null
}

function Get-CpuPerformance {
    <#
    .SYNOPSIS
        Get CPU performance data (score, generation, year) for a SKU.
        Ported from get_cpu_performance in function_app.py.
    #>
    param([Parameter(Mandatory = $true)][string]$SkuName)

    $series = Get-SeriesPrefix -SkuName $SkuName
    if (-not $series -or -not $script:SeriesCpuMap.ContainsKey($series)) { return $null }

    $cpuIds = $script:SeriesCpuMap[$series]
    $scores = New-Object System.Collections.Generic.List[double]
    $generations = New-Object System.Collections.Generic.List[string]
    $firstKnownYear = $null
    foreach ($id in $cpuIds) {
        if ($script:CpuPerformanceTable.ContainsKey($id)) {
            $entry = $script:CpuPerformanceTable[$id]
            $scores.Add([double]$entry.Score)
            $generations.Add([string]$entry.Generation)
            if ($null -eq $firstKnownYear) { $firstKnownYear = $entry.Year }
        }
    }

    if ($scores.Count -eq 0) { return $null }

    $avgScore = [int][Math]::Round(($scores | Measure-Object -Average).Average)
    return [PSCustomObject]@{
        Score      = $avgScore
        Generation = $generations[0]
        Year       = $firstKnownYear
        CpuModels  = $cpuIds
    }
}

function Get-CpuVendor {
    <#
    .SYNOPSIS
        Detect CPU vendor (Intel/AMD/ARM) from SKU name and architecture.
        Ported from detect_cpu_vendor in function_app.py.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$SkuName,
        [Parameter(Mandatory = $false)][string]$Architecture = ''
    )

    # 1. Architecture reported by the Compute API is definitive for ARM.
    if ($Architecture -and $Architecture.ToLower() -in @('arm64', 'arm')) { return 'ARM' }

    # 2. The curated CPU generation mapping is the most reliable signal, because it is
    #    keyed off the actual CPU model Azure documents for the series. It is correct even
    #    for series whose names carry no vendor letter (e.g. HB/HX run AMD EPYC).
    $cpuPerf = Get-CpuPerformance -SkuName $SkuName
    if ($cpuPerf -and $cpuPerf.Generation) {
        $gen = $cpuPerf.Generation.ToLower()
        if ($gen -match 'zen|epyc') { return 'AMD' }
        # An ARM generation with an x64 architecture would be a data conflict; architecture
        # above already settled ARM, so only Intel/AMD verdicts are trusted here.
        if ($gen -notmatch 'neoverse|ampere|cobalt') { return 'Intel' }
    }

    # 3. Fall back to the documented additive-feature letters: 'a' = AMD, 'p' = ARM.
    #    Azure VM names are [Family][#vCPUs][-Constrained][AdditiveFeatures]_[Version];
    #    family letters are uppercase and feature letters lowercase, so match case-sensitively.
    $features = ''
    if ($SkuName -cmatch '^(?:Standard|Basic)_[A-Z]+\d+(?:-\d+)?([a-z]*)') {
        $features = $Matches[1]
    }
    if ($features -clike '*a*') { return 'AMD' }
    if ($features -clike '*p*') { return 'ARM' }

    return 'Intel'
}

function Set-EffectiveVCpus {
    <#
    .SYNOPSIS
        Normalize a capability hashtable so 'vCPUs' reports usable cores.
    .DESCRIPTION
        Azure publishes two capabilities: 'vCPUs' is the physical core count of the
        underlying parent size, while 'vCPUsAvailable' is the number actually usable.
        For constrained-vCPU sizes (Standard_E16-4s_v5, Standard_HB368-48rs_v5) these
        differ -- the point of a constrained size is fewer usable cores with the parent's
        memory and I/O, so per-core software licensing costs less. Reading 'vCPUs' reports
        the parent's count and makes a constrained size look identical to its parent.

        'vCPUsAvailable' is published for every size and equals 'vCPUs' for
        non-constrained sizes, so it is correct to use unconditionally.
        Ported from _effective_vcpus in function_app.py.
    #>
    param([Parameter(Mandatory = $true)][hashtable]$Capabilities)

    if (-not $Capabilities.ContainsKey('vCPUsAvailable')) { return }
    $available = 0
    if ([int]::TryParse([string]$Capabilities['vCPUsAvailable'], [ref]$available) -and $available -gt 0) {
        $Capabilities['vCPUs'] = $available
    }
}

function Get-RetirementInfo {
    <#
    .SYNOPSIS
        Return retirement info for a SKU, or $null if not retiring.
        Ported from _get_retirement_info in function_app.py.
    #>
    param([Parameter(Mandatory = $true)][string]$SkuName)

    foreach ($entry in $script:VmRetirementInfo) {
        if ($SkuName -match $entry.Pattern) {
            return [PSCustomObject]@{
                RetirementStatus  = $entry.Status
                RetirementDate    = $entry.RetirementDate
                MigrationGuideUrl = $entry.MigrationGuideUrl
            }
        }
    }
    return $null
}

function Get-RetirementPenalty {
    <#
    .SYNOPSIS
        Similarity-score penalty for retiring/retired SKUs (used for ranking only).
        Ported from _retirement_penalty in function_app.py.
    #>
    param([Parameter(Mandatory = $true)][string]$SkuName)

    $info = Get-RetirementInfo -SkuName $SkuName
    if (-not $info) { return 0.0 }
    if ($info.RetirementStatus -eq 'Retired') { return 15.0 }

    try {
        $retDate = [datetime]::ParseExact($info.RetirementDate, 'yyyy-MM-dd', [System.Globalization.CultureInfo]::InvariantCulture)
        $monthsRemaining = ($retDate - (Get-Date)).Days / 30.44
        if ($monthsRemaining -le 6) { return 10.0 }
        elseif ($monthsRemaining -le 12) { return 5.0 }
        else { return 2.0 }
    } catch {
        return 2.0
    }
}

function Get-GrowthRestrictionInfo {
    <#
    .SYNOPSIS
        Return growth restriction (capacity limitation) info for a SKU, or $null if unaffected.
        Ported from _get_growth_restriction_info in function_app.py.
    #>
    param([Parameter(Mandatory = $true)][string]$SkuName)

    foreach ($entry in $script:VmGrowthRestrictionInfo) {
        if ($SkuName -match $entry.Pattern) {
            return [PSCustomObject]@{
                GrowthRestricted   = $true
                Series             = $entry.Series
                Category           = $entry.Category
                EffectiveDate      = $script:GrowthRestrictionEffectiveDate
                RecommendedTargets = $entry.RecommendedTargets
                DocumentationUrl   = $script:GrowthRestrictionDocUrl
            }
        }
    }
    return $null
}

function Get-GrowthRestrictionPenalty {
    <#
    .SYNOPSIS
        Similarity-score penalty for growth-restricted SKUs (ranking only). Applied in
        addition to any retirement penalty, since the two conditions are independent.
        Ported from _growth_restriction_penalty in function_app.py.
    #>
    param([Parameter(Mandatory = $true)][string]$SkuName)

    if (Get-GrowthRestrictionInfo -SkuName $SkuName) { return $script:GrowthRestrictionPenalty }
    return 0.0
}

function Get-SelectedPrice {
    <#
    .SYNOPSIS
        Pick hourly/monthly price from a pricing object based on PricingModel and OS.
        RI models are strict: returns $null if the requested term is unavailable.
        PAYG falls back from Windows to Linux when no Windows price exists.
    #>
    param(
        [Parameter(Mandatory = $false)]$Pricing,
        [Parameter(Mandatory = $true)][string]$Model,
        [Parameter(Mandatory = $true)][string]$OS
    )

    if (-not $Pricing) { return $null }
    $wantWindows = ($OS -eq 'Windows')

    switch ($Model) {
        'RI1Year' {
            if ($wantWindows) {
                if ($null -ne $Pricing.Ri1YearMonthlyWindows) {
                    return @{ Hourly = $Pricing.Ri1YearHourlyWindows; Monthly = $Pricing.Ri1YearMonthlyWindows; Source = 'RI 1-Year (Windows)' }
                }
            } elseif ($null -ne $Pricing.Ri1YearMonthly) {
                return @{ Hourly = $Pricing.Ri1YearHourly; Monthly = $Pricing.Ri1YearMonthly; Source = 'RI 1-Year' }
            }
            return $null
        }
        'RI3Year' {
            if ($wantWindows) {
                if ($null -ne $Pricing.Ri3YearMonthlyWindows) {
                    return @{ Hourly = $Pricing.Ri3YearHourlyWindows; Monthly = $Pricing.Ri3YearMonthlyWindows; Source = 'RI 3-Year (Windows)' }
                }
            } elseif ($null -ne $Pricing.Ri3YearMonthly) {
                return @{ Hourly = $Pricing.Ri3YearHourly; Monthly = $Pricing.Ri3YearMonthly; Source = 'RI 3-Year' }
            }
            return $null
        }
        default {
            # PAYG
            if ($wantWindows -and $null -ne $Pricing.MonthlyPriceWindows) {
                return @{ Hourly = $Pricing.HourlyPriceWindows; Monthly = $Pricing.MonthlyPriceWindows; Source = 'PAYG (Windows)' }
            }
            return @{ Hourly = $Pricing.HourlyPrice; Monthly = $Pricing.MonthlyPrice; Source = 'PAYG' }
        }
    }
}

# Function to get pricing information for VM SKUs.
# Fetches both Consumption (PAYG) and Reservation (RI) prices, for Linux and Windows.
# Ported from get_vm_pricing / _compute_windows_ri in web-app/api/function_app.py.
function Get-VmPricingInfo {
    param(
        [Parameter(Mandatory = $true)]
        [string]$SkuName,

        [Parameter(Mandatory = $true)]
        [string]$Location,

        [Parameter(Mandatory = $false)]
        [string]$CurrencyCode = 'USD'
    )

    try {
        # Construct the Azure Retail Prices API URL (Consumption + Reservation)
        $apiUrl = "https://prices.azure.com/api/retail/prices"
        $filter = "serviceName eq 'Virtual Machines' and armSkuName eq '$SkuName' and armRegionName eq '$Location' and (type eq 'Consumption' or type eq 'Reservation')"

        if ($CurrencyCode -ne 'USD') {
            $requestUrl = "$($apiUrl)?currencyCode='$CurrencyCode'&`$filter=$filter"
        } else {
            $requestUrl = "$($apiUrl)?`$filter=$filter"
        }

        Write-Verbose "Fetching pricing data from: $requestUrl"

        # Page through all results; retry each page with exponential backoff
        $items = New-Object System.Collections.Generic.List[object]
        $maxRetries = 2
        while ($requestUrl) {
            $retryCount = 0
            $response = $null
            do {
                try {
                    $response = Invoke-RestMethod -Uri $requestUrl -Method Get -ErrorAction Stop
                    break
                }
                catch {
                    $retryCount++
                    if ($retryCount -ge $maxRetries) {
                        Write-Warning "Failed to fetch pricing data for $SkuName after $maxRetries attempts: $($_.Exception.Message)"
                        return $null
                    }
                    Write-Verbose "Retry $retryCount for pricing data..."
                    Start-Sleep -Seconds (2 * $retryCount)  # Exponential backoff
                }
            } while ($retryCount -lt $maxRetries)

            if ($response.Items) { foreach ($it in $response.Items) { $items.Add($it) } }
            # NextPageLink already carries currencyCode and the filter; use it as-is
            $requestUrl = $response.NextPageLink
        }

        if ($items.Count -eq 0) {
            Write-Verbose "No pricing data found for $SkuName in $Location"
            return $null
        }

        # Linux PAYG: exclude DedicatedHost/Cloud/Windows/Spot/Low Priority
        $linuxItem = $items | Where-Object {
            $_.type -eq 'Consumption' -and $_.productName -and
            $_.productName -notlike '*dedicatedhost*' -and
            $_.productName -notlike '*Cloud*' -and
            $_.productName -notlike '*Windows*' -and
            $_.skuName -notlike '*Spot*' -and
            $_.skuName -notlike '*Low Priority*'
        } | Select-Object -First 1

        # Windows PAYG: require Windows, exclude DedicatedHost/Cloud/Spot/Low Priority
        $windowsItem = $items | Where-Object {
            $_.type -eq 'Consumption' -and $_.productName -and
            $_.productName -notlike '*dedicatedhost*' -and
            $_.productName -notlike '*Cloud*' -and
            $_.productName -like '*Windows*' -and
            $_.skuName -notlike '*Spot*' -and
            $_.skuName -notlike '*Low Priority*'
        } | Select-Object -First 1

        # Fall back to first non-Spot/non-Low Priority Consumption item if no Linux item
        if (-not $linuxItem) {
            $linuxItem = $items | Where-Object {
                $_.type -eq 'Consumption' -and
                $_.skuName -notlike '*Spot*' -and
                $_.skuName -notlike '*Low Priority*'
            } | Select-Object -First 1
        }

        if (-not $linuxItem) {
            Write-Verbose "No usable Consumption pricing for $SkuName in $Location"
            return $null
        }

        $pricing = @{
            HourlyPrice           = [Math]::Round($linuxItem.unitPrice, 4)
            MonthlyPrice          = [Math]::Round($linuxItem.unitPrice * 730, 2)  # 730 hours = ~1 month
            HourlyPriceWindows    = if ($windowsItem) { [Math]::Round($windowsItem.unitPrice, 4) } else { $null }
            MonthlyPriceWindows   = if ($windowsItem) { [Math]::Round($windowsItem.unitPrice * 730, 2) } else { $null }
            Currency              = $linuxItem.currencyCode
            ProductName           = $linuxItem.productName
            Ri1YearMonthly        = $null
            Ri1YearHourly         = $null
            Ri3YearMonthly        = $null
            Ri3YearHourly         = $null
            Ri1YearMonthlyWindows = $null
            Ri1YearHourlyWindows  = $null
            Ri3YearMonthlyWindows = $null
            Ri3YearHourlyWindows  = $null
        }

        # Reserved Instance pricing (unitPrice = full term total)
        $ri1 = $items | Where-Object {
            $_.type -eq 'Reservation' -and $_.reservationTerm -eq '1 Year' -and
            $_.productName -notlike '*dedicatedhost*' -and $_.productName -notlike '*Cloud*'
        } | Select-Object -First 1
        $ri3 = $items | Where-Object {
            $_.type -eq 'Reservation' -and $_.reservationTerm -eq '3 Years' -and
            $_.productName -notlike '*dedicatedhost*' -and $_.productName -notlike '*Cloud*'
        } | Select-Object -First 1

        if ($ri1) {
            $pricing.Ri1YearMonthly = [Math]::Round($ri1.unitPrice / 12, 2)
            $pricing.Ri1YearHourly = [Math]::Round($ri1.unitPrice / (12 * 730), 4)
        }
        if ($ri3) {
            $pricing.Ri3YearMonthly = [Math]::Round($ri3.unitPrice / 36, 2)
            $pricing.Ri3YearHourly = [Math]::Round($ri3.unitPrice / (36 * 730), 4)
        }

        # Windows RI = RI compute + Windows license surcharge (Windows PAYG hourly - Linux PAYG hourly)
        if ($null -ne $pricing.HourlyPriceWindows) {
            $licenseSurcharge = $pricing.HourlyPriceWindows - $pricing.HourlyPrice
            if ($licenseSurcharge -gt 0) {
                if ($null -ne $pricing.Ri1YearHourly) {
                    $pricing.Ri1YearHourlyWindows = [Math]::Round($pricing.Ri1YearHourly + $licenseSurcharge, 4)
                    $pricing.Ri1YearMonthlyWindows = [Math]::Round(($pricing.Ri1YearHourly + $licenseSurcharge) * 730, 2)
                }
                if ($null -ne $pricing.Ri3YearHourly) {
                    $pricing.Ri3YearHourlyWindows = [Math]::Round($pricing.Ri3YearHourly + $licenseSurcharge, 4)
                    $pricing.Ri3YearMonthlyWindows = [Math]::Round(($pricing.Ri3YearHourly + $licenseSurcharge) * 730, 2)
                }
            }
        }

        return $pricing
    }
    catch {
        Write-Warning "Error fetching pricing data for $SkuName`: $($_.Exception.Message)"
        return $null
    }
}

# Get all VM sizes for the specified location
Write-Host "Retrieving VM SKUs for location: $Location..." -ForegroundColor Cyan
$allSkus = Get-AzComputeResourceSku -Location $Location | Where-Object { $_.ResourceType -eq 'virtualMachines' }

# Optionally pre-fetch SKU availability for a second region (-CheckRegion)
$regionAvailableSkuNames = $null
if ($CheckRegion) {
    Write-Host "Retrieving VM SKUs for comparison region: $CheckRegion..." -ForegroundColor Cyan
    $regionAvailableSkuNames = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::OrdinalIgnoreCase)
    foreach ($rsku in (Get-AzComputeResourceSku -Location $CheckRegion | Where-Object { $_.ResourceType -eq 'virtualMachines' })) {
        # Treat a SKU as available only if it has no subscription/location restriction in that region
        $restricted = $false
        if ($rsku.Restrictions) {
            foreach ($r in $rsku.Restrictions) {
                if ($r.ReasonCode -eq 'NotAvailableForSubscription') { $restricted = $true; break }
            }
        }
        if (-not $restricted) { [void]$regionAvailableSkuNames.Add($rsku.Name) }
    }
}

# Find the target SKU
$targetSku = $allSkus | Where-Object { $_.Name -eq $SkuName }

if (-not $targetSku) {
    Write-Error "SKU '$SkuName' not found in location '$Location'"
    exit 1
}

# Extract ALL target SKU capabilities into a hashtable for easy lookup
$targetCapabilities = @{}
foreach ($capability in $targetSku.Capabilities) {
    $targetCapabilities[$capability.Name] = $capability.Value
}
Set-EffectiveVCpus -Capabilities $targetCapabilities

# Get availability zones for target SKU
$targetZones = @()
if ($targetSku.LocationInfo -and $targetSku.LocationInfo.Count -gt 0) {
    foreach ($locationInfo in $targetSku.LocationInfo) {
        if ($locationInfo.Location -eq $Location -and $locationInfo.Zones) {
            $targetZones = $locationInfo.Zones | Sort-Object
            break
        }
    }
}
$targetZonesDisplay = if ($targetZones.Count -gt 0) { ($targetZones -join ', ') } else { 'None' }

Write-Host "`nTarget SKU: $SkuName" -ForegroundColor Green
Write-Host "Availability Zones: $targetZonesDisplay" -ForegroundColor Cyan

# CPU vendor, generation/performance, and retirement status for the target SKU
$targetArch = if ($targetCapabilities.ContainsKey('CpuArchitectureType')) { $targetCapabilities['CpuArchitectureType'] } else { 'x64' }
$targetVendor = Get-CpuVendor -SkuName $SkuName -Architecture $targetArch
$targetCpuPerf = Get-CpuPerformance -SkuName $SkuName
$targetRetirement = Get-RetirementInfo -SkuName $SkuName
$targetGrowthRestriction = Get-GrowthRestrictionInfo -SkuName $SkuName

Write-Host "CPU Vendor: $targetVendor" -ForegroundColor Cyan
if ($targetCpuPerf) {
    Write-Host "CPU Generation: $($targetCpuPerf.Generation) (perf score $($targetCpuPerf.Score))" -ForegroundColor Cyan
}
if ($targetRetirement) {
    Write-Host "[!] Retirement: $($targetRetirement.RetirementStatus) - $($targetRetirement.RetirementDate)" -ForegroundColor Red
    Write-Host "    Migration guide: $($targetRetirement.MigrationGuideUrl)" -ForegroundColor Red
}
if ($targetGrowthRestriction) {
    Write-Host "[!] Capacity limited: $($targetGrowthRestriction.Series) series (effective $($targetGrowthRestriction.EffectiveDate))" -ForegroundColor Yellow
    Write-Host "    New subscriptions can't deploy this series, and additional quota won't be approved." -ForegroundColor Yellow
    Write-Host "    Recommended targets: $($targetGrowthRestriction.RecommendedTargets -join ', ')" -ForegroundColor Yellow
    Write-Host "    Details: $($targetGrowthRestriction.DocumentationUrl)" -ForegroundColor Yellow
}
Write-Host "Capabilities:" -ForegroundColor Cyan

# Display all capabilities organized by category
$capabilityDisplay = @{
    'Compute' = @('vCPUs', 'vCPUsAvailable', 'vCPUsPerCore', 'ACUs', 'HyperVGenerations')
    'Memory' = @('MemoryGB', 'MemoryPreservingMaintenanceSupported')
    'GPU' = @('GPUs', 'vGPUsPerCore')
    'Storage' = @('MaxDataDiskCount', 'UncachedDiskIOPS', 'UncachedDiskBytesPerSecond',
                  'CachedDiskBytes', 'MaxCachedDiskIOPS', 'MaxCachedDiskBytesPerSecond',
                  'EphemeralOSDiskSupported', 'PremiumIO', 'NvmeDiskSizeInMiB',
                  'MaxWriteAcceleratorDisksAllowed')
    'Network' = @('MaxNetworkInterfaces', 'ExpectedNetworkBandwidth',
                  'MaxNetworkBandwidthInMbps', 'AcceleratedNetworkingEnabled',
                  'RdmaEnabled', 'RdmaNetworkInterfaceCount')
    'Features' = @('LowPriorityCapable', 'EncryptionAtHostSupported', 'CapacityReservationSupported',
                   'UltraSSDAvailable', 'ConfidentialComputingType', 'TrustedLaunchDisabled',
                   'vCPUsConstraintEnabled', 'NestedVirtualizationEnabled')
}

foreach ($category in $capabilityDisplay.Keys | Sort-Object) {
    $hasCapabilities = $false
    $categoryOutput = ""

    foreach ($capName in $capabilityDisplay[$category]) {
        if ($targetCapabilities.ContainsKey($capName)) {
            $value = $targetCapabilities[$capName]

            # Format specific values for readability
            if ($capName -eq 'UncachedDiskBytesPerSecond' -or $capName -eq 'MaxCachedDiskBytesPerSecond' -or $capName -eq 'CachedDiskBytes') {
                $value = "$([Math]::Round([double]$value / 1MB, 2)) MB/s"
            }
            elseif ($capName -eq 'NvmeDiskSizeInMiB' -and [double]$value -gt 0) {
                $value = "$([Math]::Round([double]$value / 1024, 2)) GB"
            }
            elseif ($capName -eq 'MaxNetworkBandwidthInMbps' -or $capName -eq 'ExpectedNetworkBandwidth') {
                $value = "$value Mbps"
            }

            $categoryOutput += "`n    $capName`: $value"
            $hasCapabilities = $true
        }
    }

    if ($hasCapabilities) {
        Write-Host "  $category`:" -ForegroundColor Yellow
        Write-Host $categoryOutput
    }
}

# Get pricing for target SKU
Write-Host "`nFetching pricing information for target SKU..." -ForegroundColor Cyan
$targetPricing = Get-VmPricingInfo -SkuName $SkuName -Location $Location -CurrencyCode $CurrencyCode
$targetSelectedPrice = Get-SelectedPrice -Pricing $targetPricing -Model $PricingModel -OS $OS
$targetCostPerVCPU = $null
$targetCostPerGB = $null
if ($targetSelectedPrice) {
    Write-Host "  Pricing model: $PricingModel ($OS) - $($targetSelectedPrice.Source)"
    Write-Host "  Hourly Price: `$$($targetSelectedPrice.Hourly) $($targetPricing.Currency)"
    Write-Host "  Monthly Price: `$$($targetSelectedPrice.Monthly) $($targetPricing.Currency)"
    $tCores = if ($targetCapabilities.ContainsKey('vCPUs')) { [double]$targetCapabilities['vCPUs'] } else { 0 }
    $tMem = if ($targetCapabilities.ContainsKey('MemoryGB')) { [double]$targetCapabilities['MemoryGB'] } else { 0 }
    if ($tCores -gt 0) { $targetCostPerVCPU = [Math]::Round($targetSelectedPrice.Hourly / $tCores, 4) }
    if ($tMem -gt 0) { $targetCostPerGB = [Math]::Round($targetSelectedPrice.Hourly / $tMem, 4) }
    if ($null -ne $targetCostPerVCPU -or $null -ne $targetCostPerGB) {
        $perVcpuText = if ($null -ne $targetCostPerVCPU) { "`$$targetCostPerVCPU/vCPU/hr" } else { 'N/A' }
        $perGbText = if ($null -ne $targetCostPerGB) { "`$$targetCostPerGB/GB/hr" } else { 'N/A' }
        Write-Host "  Cost Efficiency: $perVcpuText, $perGbText"
    }
} elseif ($targetPricing) {
    Write-Host "  No $PricingModel ($OS) price available for target SKU" -ForegroundColor Yellow
} else {
    Write-Host "  Pricing information not available" -ForegroundColor Yellow
}

Write-Host "`nSearching for similar SKUs (±$Tolerance% tolerance)..." -ForegroundColor Cyan
Write-Host "Weighting Configuration:" -ForegroundColor Cyan
Write-Host "  CPU Weight: $WeightCPU"
Write-Host "  Memory Weight: $WeightMemory"
Write-Host "  GPU Weight: $WeightGPU"
Write-Host "  Storage Weight: $WeightStorage"
Write-Host "  Network Weight: $WeightNetwork"
Write-Host "  Features Weight: $WeightFeatures"
Write-Host "  Minimum Similarity Score: $MinSimilarityScore%"

# Define capability categories for weighted scoring
$capabilityWeights = @{
    # Compute capabilities
    'vCPUs' = $WeightCPU
    'vCPUsAvailable' = $WeightCPU * 0.5
    'vCPUsPerCore' = $WeightCPU * 0.3
    'ACUs' = $WeightCPU * 0.8

    # Memory capabilities
    'MemoryGB' = $WeightMemory
    'MemoryPreservingMaintenanceSupported' = $WeightFeatures * 0.3

    # GPU capabilities
    'GPUs' = $WeightGPU  # GPU count is critical for GPU workloads
    'vGPUsPerCore' = $WeightGPU * 0.5

    # Storage capabilities
    'MaxDataDiskCount' = $WeightStorage * 0.8
    'UncachedDiskIOPS' = $WeightStorage * 1.2
    'UncachedDiskBytesPerSecond' = $WeightStorage * 1.2
    'CachedDiskBytes' = $WeightStorage * 0.7
    'MaxCachedDiskIOPS' = $WeightStorage * 0.7
    'MaxCachedDiskBytesPerSecond' = $WeightStorage * 0.7
    'EphemeralOSDiskSupported' = $WeightFeatures * 0.5
    'PremiumIO' = $WeightStorage * 0.6
    'NvmeDiskSizeInMiB' = $WeightStorage * 1.5  # Higher weight for NVMe as it's a critical differentiator
    'MaxWriteAcceleratorDisksAllowed' = $WeightStorage * 0.4

    # Network capabilities
    'MaxNetworkInterfaces' = $WeightNetwork * 0.7
    'ExpectedNetworkBandwidth' = $WeightNetwork * 1.0
    'MaxNetworkBandwidthInMbps' = $WeightNetwork * 1.0
    'AcceleratedNetworkingEnabled' = $WeightNetwork * 0.8
    'RdmaEnabled' = $WeightNetwork * 0.6
    'RdmaNetworkInterfaceCount' = $WeightNetwork * 0.5

    # Feature flags
    'LowPriorityCapable' = $WeightFeatures * 0.3
    'EncryptionAtHostSupported' = $WeightFeatures * 0.6
    'CapacityReservationSupported' = $WeightFeatures * 0.3
    'UltraSSDAvailable' = $WeightFeatures * 0.7
    'ConfidentialComputingType' = $WeightFeatures * 0.5
    'TrustedLaunchDisabled' = $WeightFeatures * 0.4
    'HyperVGenerations' = $WeightFeatures * 0.3
    'vCPUsConstraintEnabled' = $WeightFeatures * 0.2
    'NestedVirtualizationEnabled' = $WeightFeatures * 0.3
}

# Calculate total weight for normalization
$totalWeight = ($capabilityWeights.Values | Measure-Object -Sum).Sum

# Get key capabilities for basic filtering
if (-not $targetCapabilities.ContainsKey('vCPUs') -or -not $targetCapabilities.ContainsKey('MemoryGB')) {
    Write-Error "Target SKU missing required capabilities (vCPUs or MemoryGB)"
    exit 1
}

$targetCores = [double]($targetCapabilities['vCPUs'])
$targetMemoryGB = [double]($targetCapabilities['MemoryGB'])

# Calculate tolerance ranges for basic filtering
$coreMin = $targetCores - ($targetCores * $Tolerance / 100)
$coreMax = $targetCores + ($targetCores * $Tolerance / 100)
$memoryMin = $targetMemoryGB - ($targetMemoryGB * $Tolerance / 100)
$memoryMax = $targetMemoryGB + ($targetMemoryGB * $Tolerance / 100)

# Check if target has NVMe support
$targetHasNVMe = $targetCapabilities.ContainsKey('NvmeDiskSizeInMiB') -and
                 $null -ne $targetCapabilities['NvmeDiskSizeInMiB'] -and
                 $targetCapabilities['NvmeDiskSizeInMiB'] -ne '' -and
                 $targetCapabilities['NvmeDiskSizeInMiB'] -ne '0' -and
                 [double]$targetCapabilities['NvmeDiskSizeInMiB'] -gt 0

if ($targetHasNVMe) {
    $targetNVMeSize = [Math]::Round([double]$targetCapabilities['NvmeDiskSizeInMiB'] / 1024, 2)
    Write-Host "  Target has NVMe: $targetNVMeSize GB" -ForegroundColor Green
    if ($RequireNVMeMatch) {
        Write-Host "  Filtering to only NVMe-enabled SKUs" -ForegroundColor Yellow
    }
}

# Check if target has GPU support
$targetHasGPU = $targetCapabilities.ContainsKey('GPUs') -and
                $null -ne $targetCapabilities['GPUs'] -and
                $targetCapabilities['GPUs'] -ne '' -and
                $targetCapabilities['GPUs'] -ne '0' -and
                [double]$targetCapabilities['GPUs'] -gt 0

if ($targetHasGPU) {
    $targetGPUCount = [double]$targetCapabilities['GPUs']
    Write-Host "  Target has GPUs: $targetGPUCount" -ForegroundColor Green
    if ($RequireGPUMatch) {
        Write-Host "  Filtering to only GPU-enabled SKUs" -ForegroundColor Yellow
    }
}

# Function to calculate capability difference
function Get-CapabilityDifference {
    param(
        [string]$CapabilityName,
        $TargetValue,
        $CompareValue
    )

    # "Higher is better" performance capabilities: a candidate that meets or
    # exceeds the target is not "worse", so overshoot is not penalized (only
    # a shortfall is). Mirrors the web API's asymmetric storage/network scoring.
    $higherIsBetter = @('UncachedDiskIOPS', 'UncachedDiskBytesPerSecond',
                        'MaxNetworkInterfaces', 'ExpectedNetworkBandwidth', 'MaxDataDiskCount')
    if ($CapabilityName -in $higherIsBetter) {
        $t = 0.0; $c = 0.0
        [void][double]::TryParse(('' + $TargetValue), [ref]$t)
        [void][double]::TryParse(('' + $CompareValue), [ref]$c)
        if ($t -le 0) { return 0.0 }      # no target requirement to meet
        if ($c -ge $t) { return 0.0 }      # meets or exceeds target - not worse
        return ($t - $c) / $t              # shortfall penalized at full rate
    }

    # Handle null or missing values
    if ($null -eq $TargetValue -or $TargetValue -eq '' -or $TargetValue -eq '0' -or $null -eq $CompareValue -or $CompareValue -eq '' -or $CompareValue -eq '0') {
        # Special handling for NVMe - if target has NVMe and compare doesn't, it's a major difference
        if ($CapabilityName -eq 'NvmeDiskSizeInMiB') {
            $targetHasNVMe = ($null -ne $TargetValue -and $TargetValue -ne '' -and $TargetValue -ne '0' -and [double]$TargetValue -gt 0)
            $compareHasNVMe = ($null -ne $CompareValue -and $CompareValue -ne '' -and $CompareValue -ne '0' -and [double]$CompareValue -gt 0)

            if ($targetHasNVMe -and -not $compareHasNVMe) {
                return 1.0  # Target has NVMe, compare doesn't - major difference
            }
            elseif (-not $targetHasNVMe -and $compareHasNVMe) {
                return 0.3  # Target doesn't have NVMe but compare does - minor difference (bonus)
            }
            elseif ($targetHasNVMe -and $compareHasNVMe) {
                return 0.0  # Both have NVMe - will be compared by size below
            }
            else {
                return 0.0  # Neither has NVMe - no difference
            }
        }

        # Special handling for GPUs - if target has GPUs and compare doesn't, it's a major difference
        if ($CapabilityName -eq 'GPUs') {
            $targetHasGPU = ($null -ne $TargetValue -and $TargetValue -ne '' -and $TargetValue -ne '0' -and [double]$TargetValue -gt 0)
            $compareHasGPU = ($null -ne $CompareValue -and $CompareValue -ne '' -and $CompareValue -ne '0' -and [double]$CompareValue -gt 0)

            if ($targetHasGPU -and -not $compareHasGPU) {
                return 1.0  # Target has GPU, compare doesn't - major difference
            }
            elseif (-not $targetHasGPU -and $compareHasGPU) {
                return 0.3  # Target doesn't have GPU but compare does - minor difference (bonus)
            }
            elseif ($targetHasGPU -and $compareHasGPU) {
                return 0.0  # Both have GPUs - will be compared by count below
            }
            else {
                return 0.0  # Neither has GPUs - no difference
            }
        }

        # If one has the capability and the other doesn't, it's a significant difference
        if (($null -eq $TargetValue -or $TargetValue -eq '' -or $TargetValue -eq '0') -and ($null -ne $CompareValue -and $CompareValue -ne '' -and $CompareValue -ne '0')) {
            return 1.0
        }
        elseif (($null -ne $TargetValue -and $TargetValue -ne '' -and $TargetValue -ne '0') -and ($null -eq $CompareValue -or $CompareValue -eq '' -or $CompareValue -eq '0')) {
            return 1.0
        }
        else {
            return 0.0  # Both are null/missing/zero - no difference
        }
    }

    # Handle boolean/True-False values — only penalize when the target HAS the
    # feature and the candidate lacks it; an extra capability is not "worse".
    if ($TargetValue -eq 'True' -or $TargetValue -eq 'False') {
        if ($TargetValue -eq $CompareValue) {
            return 0.0
        } elseif ($TargetValue -eq 'True') {
            return 1.0  # target needs it, candidate lacks it
        } else {
            return 0.0  # target doesn't need it, candidate has extra - not worse
        }
    }

    # Handle string values (like HyperVGenerations)
    if ($TargetValue -is [string] -and $TargetValue -notmatch '^\d+\.?\d*$') {
        if ($TargetValue -eq $CompareValue) {
            return 0.0
        } else {
            return 0.5
        }
    }

    # Handle numeric values
    try {
        $targetNum = [double]$TargetValue
        $compareNum = [double]$CompareValue

        if ($targetNum -eq 0) {
            if ($compareNum -eq 0) {
                return 0.0
            } else {
                return 1.0
            }
        }

        # Calculate percentage difference
        return [Math]::Abs($compareNum - $targetNum) / $targetNum
    }
    catch {
        # If conversion fails, treat as string comparison
        if ($TargetValue -eq $CompareValue) {
            return 0.0
        } else {
            return 0.5
        }
    }
}

# Find similar SKUs
Write-Host "`nAnalyzing SKUs..." -ForegroundColor Cyan
$skuCount = 0
$totalSkus = ($allSkus | Where-Object { $_.Name -ne $SkuName }).Count

$similarSkus = $allSkus | Where-Object {
    $_.Name -ne $SkuName
} | ForEach-Object {
    $sku = $_
    $skuCount++

    # Show progress every 50 SKUs
    if ($skuCount % 50 -eq 0) {
        Write-Verbose "Processed $skuCount of $totalSkus SKUs..."
    }

    # Build capabilities hashtable for this SKU
    $skuCapabilities = @{}
    foreach ($capability in $sku.Capabilities) {
        $skuCapabilities[$capability.Name] = $capability.Value
    }
    Set-EffectiveVCpus -Capabilities $skuCapabilities

    # Get availability zones for this SKU
    $skuZones = @()
    if ($sku.LocationInfo -and $sku.LocationInfo.Count -gt 0) {
        foreach ($locationInfo in $sku.LocationInfo) {
            if ($locationInfo.Location -eq $Location -and $locationInfo.Zones) {
                $skuZones = $locationInfo.Zones | Sort-Object
                break
            }
        }
    }
    $skuZonesDisplay = if ($skuZones.Count -gt 0) { ($skuZones -join ', ') } else { 'None' }

    # Get basic specs for filtering
    $cores = if ($skuCapabilities.ContainsKey('vCPUs')) { [double]$skuCapabilities['vCPUs'] } else { 0 }
    $memoryGB = if ($skuCapabilities.ContainsKey('MemoryGB')) { [double]$skuCapabilities['MemoryGB'] } else { 0 }

    # Check if this SKU has NVMe
    $skuHasNVMe = $skuCapabilities.ContainsKey('NvmeDiskSizeInMiB') -and
                  $null -ne $skuCapabilities['NvmeDiskSizeInMiB'] -and
                  $skuCapabilities['NvmeDiskSizeInMiB'] -ne '' -and
                  $skuCapabilities['NvmeDiskSizeInMiB'] -ne '0' -and
                  [double]$skuCapabilities['NvmeDiskSizeInMiB'] -gt 0

    # If RequireNVMeMatch is set and target has NVMe, only consider SKUs with NVMe
    $nvmeFilterPass = $true
    if ($RequireNVMeMatch -and $targetHasNVMe -and -not $skuHasNVMe) {
        $nvmeFilterPass = $false
    }

    # Check if this SKU has GPU
    $skuHasGPU = $skuCapabilities.ContainsKey('GPUs') -and
                 $null -ne $skuCapabilities['GPUs'] -and
                 $skuCapabilities['GPUs'] -ne '' -and
                 $skuCapabilities['GPUs'] -ne '0' -and
                 [double]$skuCapabilities['GPUs'] -gt 0

    # If RequireGPUMatch is set and target has GPU, only consider SKUs with GPU
    $gpuFilterPass = $true
    if ($RequireGPUMatch -and $targetHasGPU -and -not $skuHasGPU) {
        $gpuFilterPass = $false
    }

    # CPU vendor filter
    $skuArch = if ($skuCapabilities.ContainsKey('CpuArchitectureType')) { $skuCapabilities['CpuArchitectureType'] } else { 'x64' }
    $skuVendor = Get-CpuVendor -SkuName $sku.Name -Architecture $skuArch
    $vendorFilterPass = $true
    if ($CpuVendor -and $CpuVendor.Count -gt 0 -and ($skuVendor -notin $CpuVendor)) {
        $vendorFilterPass = $false
    }

    # Retirement filter (hidden by default to match the website)
    $skuRetirement = Get-RetirementInfo -SkuName $sku.Name
    $retirementFilterPass = $true
    if ($HideRetiring -and $skuRetirement) {
        $retirementFilterPass = $false
    }

    # Growth restriction filter (shown by default; opt-in hide, matching the website)
    $skuGrowthRestriction = Get-GrowthRestrictionInfo -SkuName $sku.Name
    $growthFilterPass = $true
    if ($HideGrowthRestricted -and $skuGrowthRestriction) {
        $growthFilterPass = $false
    }

    # Apply basic tolerance filter on CPU, Memory, NVMe, and GPU (if required)
    if ($cores -ge $coreMin -and $cores -le $coreMax -and
        $memoryGB -ge $memoryMin -and $memoryGB -le $memoryMax -and
        $nvmeFilterPass -and $gpuFilterPass -and $vendorFilterPass -and $retirementFilterPass -and $growthFilterPass) {

        # Calculate weighted similarity score across ALL capabilities
        $weightedScore = 0
        $applicableWeight = 0
        $capabilityScores = @{}

        foreach ($capName in $targetCapabilities.Keys) {
            $targetValue = $targetCapabilities[$capName]
            $skuValue = if ($skuCapabilities.ContainsKey($capName)) { $skuCapabilities[$capName] } else { $null }

            # Get the weight for this capability (default to 0.5 if not specified)
            $weight = if ($capabilityWeights.ContainsKey($capName)) { $capabilityWeights[$capName] } else { $WeightFeatures * 0.5 }

            # Calculate difference (0 = identical, 1 = completely different)
            $difference = Get-CapabilityDifference -CapabilityName $capName -TargetValue $targetValue -CompareValue $skuValue

            # Calculate similarity (1 = identical, 0 = completely different)
            $similarity = 1 - [Math]::Min($difference, 1.0)

            # Add to weighted score
            $weightedScore += ($similarity * $weight)
            $applicableWeight += $weight

            # Store for detailed output if needed
            $capabilityScores[$capName] = [Math]::Round($similarity * 100, 1)
        }

        # Normalize to 0-100 scale
        $similarityScore = if ($applicableWeight -gt 0) {
            [Math]::Round(($weightedScore / $applicableWeight) * 100, 2)
        } else {
            0
        }

        # Only include SKUs above minimum similarity threshold (unpenalized score,
        # matching the website which thresholds first then penalizes for ranking)
        if ($similarityScore -ge $MinSimilarityScore) {
            # Apply retirement + growth restriction penalties for ranking only (after threshold)
            $originalSimilarityScore = $similarityScore
            $penalty = (Get-RetirementPenalty -SkuName $sku.Name) + (Get-GrowthRestrictionPenalty -SkuName $sku.Name)
            if ($penalty -gt 0) {
                $similarityScore = [Math]::Round([Math]::Max(0, $similarityScore - $penalty), 2)
            }

            # Get pricing information for this SKU
            Write-Verbose "Fetching pricing for $($sku.Name)..."
            $pricingInfo = Get-VmPricingInfo -SkuName $sku.Name -Location $Location -CurrencyCode $CurrencyCode
            $selectedPrice = Get-SelectedPrice -Pricing $pricingInfo -Model $PricingModel -OS $OS

            # CPU performance / generation
            $cpuPerf = Get-CpuPerformance -SkuName $sku.Name

            # Cost-efficiency metrics based on the selected price
            $costPerVCPU = 'N/A'
            $costPerGB = 'N/A'
            if ($selectedPrice) {
                if ($cores -gt 0) { $costPerVCPU = [Math]::Round($selectedPrice.Hourly / $cores, 4) }
                if ($memoryGB -gt 0) { $costPerGB = [Math]::Round($selectedPrice.Hourly / $memoryGB, 4) }
            }

            # Build result object with key capabilities
            $resultObject = [PSCustomObject]@{
                SkuName                           = $sku.Name
                SimilarityScore                   = $similarityScore
                CpuVendor                         = $skuVendor
                CpuGeneration                     = if ($cpuPerf) { $cpuPerf.Generation } else { 'Unknown' }
                CpuPerfScore                      = if ($cpuPerf) { $cpuPerf.Score } else { 'N/A' }
                vCPUs                             = $cores
                MemoryGB                          = $memoryGB
                AvailabilityZones                 = $skuZonesDisplay
                RetirementStatus                  = if ($skuRetirement) { $skuRetirement.RetirementStatus } else { 'Active' }
                GrowthRestricted                  = if ($skuGrowthRestriction) { 'Yes' } else { 'No' }
                GrowthRestrictionSeries           = if ($skuGrowthRestriction) { $skuGrowthRestriction.Series } else { $null }
                GrowthRestrictionTargets          = if ($skuGrowthRestriction) { $skuGrowthRestriction.RecommendedTargets -join ', ' } else { $null }
                "HourlyPrice($CurrencyCode)"      = if ($selectedPrice) { $selectedPrice.Hourly } else { 'N/A' }
                "MonthlyPrice($CurrencyCode)"     = if ($selectedPrice) { $selectedPrice.Monthly } else { 'N/A' }
                "CostPerVCPU($CurrencyCode)"      = $costPerVCPU
                "CostPerGB($CurrencyCode)"        = $costPerGB
            }

            # Add cross-region availability column when -CheckRegion is supplied
            if ($CheckRegion) {
                $availInRegion = if ($regionAvailableSkuNames -and $regionAvailableSkuNames.Contains($sku.Name)) { 'Yes' } else { 'No' }
                $resultObject | Add-Member -NotePropertyName "AvailableIn_$CheckRegion" -NotePropertyValue $availInRegion -Force
            }

            # Keep original (pre-penalty) score available for reference
            if ($penalty -gt 0) {
                $resultObject | Add-Member -NotePropertyName 'OriginalSimilarityScore' -NotePropertyValue $originalSimilarityScore -Force
            }

            # Add all other capabilities if ShowAllCapabilities is specified
            if ($ShowAllCapabilities) {
                foreach ($capName in ($targetCapabilities.Keys | Sort-Object)) {
                    if ($capName -notin @('vCPUs', 'MemoryGB')) {
                        $value = if ($skuCapabilities.ContainsKey($capName)) { $skuCapabilities[$capName] } else { 'N/A' }

                        # Format certain values
                        if ($capName -match 'BytesPerSecond' -and $value -ne 'N/A' -and $null -ne $value -and $value -ne '') {
                            try {
                                $numValue = [double]$value
                                $value = "$([Math]::Round($numValue / 1MB, 2)) MB/s"
                            } catch {
                                # Keep original value if conversion fails
                            }
                        }
                        elseif ($capName -eq 'NvmeDiskSizeInMiB' -and $value -ne 'N/A' -and $null -ne $value -and $value -ne '') {
                            try {
                                $numValue = [double]$value
                                if ($numValue -gt 0) {
                                    $value = "$([Math]::Round($numValue / 1024, 2)) GB"
                                }
                            } catch {
                                # Keep original value if conversion fails
                            }
                        }

                        $resultObject | Add-Member -NotePropertyName $capName -NotePropertyValue $value -Force
                    }
                }
            }
            else {
                # Add selected important capabilities
                $importantCaps = @(
                    'GPUs', 'MaxDataDiskCount', 'UncachedDiskIOPS', 'UncachedDiskBytesPerSecond',
                    'MaxNetworkInterfaces', 'PremiumIO', 'AcceleratedNetworkingEnabled',
                    'EphemeralOSDiskSupported', 'NvmeDiskSizeInMiB', 'HyperVGenerations'
                )

                foreach ($capName in $importantCaps) {
                    if ($skuCapabilities.ContainsKey($capName)) {
                        $value = $skuCapabilities[$capName]
                        $propName = $capName

                        # Format certain values
                        if ($capName -eq 'UncachedDiskBytesPerSecond') {
                            if ($null -ne $value -and $value -ne '' -and $value -ne '0') {
                                try {
                                    $value = "$([Math]::Round([double]$value / 1MB, 2)) MB/s"
                                } catch {
                                    # Keep original value if conversion fails
                                }
                            }
                            $propName = 'MaxDiskThroughput'
                        }
                        elseif ($capName -eq 'NvmeDiskSizeInMiB') {
                            if ($null -ne $value -and $value -ne '') {
                                try {
                                    if ([double]$value -gt 0) {
                                        $value = "$([Math]::Round([double]$value / 1024, 2)) GB"
                                    } else {
                                        $value = '0'
                                    }
                                } catch {
                                    $value = '0'
                                }
                            } else {
                                $value = '0'
                            }
                            $propName = 'NVMeSize'
                        }

                        $resultObject | Add-Member -NotePropertyName $propName -NotePropertyValue $value -Force
                    }
                }
            }

            $resultObject
        }
    }
} | Sort-Object -Property SimilarityScore -Descending

# Remove any SKUs with N/A pricing
$similarSkus = $similarSkus | Where-Object { $_."MonthlyPrice($($CurrencyCode))" -ne 'N/A' }

# Display results
if ($similarSkus.Count -gt 0) {
    Write-Host "`nFound $($similarSkus.Count) similar SKUs (similarity >= $MinSimilarityScore%):" -ForegroundColor Green

    # Display top 20 results
    $displaySkus = $similarSkus | Select-Object -First 20

    if ($ShowAllCapabilities) {
        $displaySkus | Format-Table -AutoSize
    }
    else {
        # Show condensed view with key metrics - include GPUs if target has them
        $baseProps = @('SkuName', 'SimilarityScore', 'CpuVendor', 'CpuGeneration', 'vCPUs', 'MemoryGB')
        if ($targetHasGPU) { $baseProps += 'GPUs' }
        $baseProps += @('AvailabilityZones', 'RetirementStatus', @{ Name = 'Limited'; Expression = { $_.GrowthRestricted } }, "MonthlyPrice($CurrencyCode)", "CostPerVCPU($CurrencyCode)")
        if ($CheckRegion) { $baseProps += "AvailableIn_$CheckRegion" }
        $displaySkus | Format-Table -Property $baseProps -AutoSize
    }

    $restrictedCount = ($similarSkus | Where-Object { $_.GrowthRestricted -eq 'Yes' }).Count
    if ($restrictedCount -gt 0) {
        Write-Host "`n[!] $restrictedCount of these alternatives are growth-restricted (capacity limited)." -ForegroundColor Yellow
        Write-Host "    New subscriptions can't deploy them and additional quota won't be approved." -ForegroundColor Yellow
        Write-Host "    See GrowthRestrictionTargets for recommended replacements, or use -HideGrowthRestricted to exclude them." -ForegroundColor Yellow
        Write-Host "    Details: $script:GrowthRestrictionDocUrl" -ForegroundColor Yellow
    }

    if ($similarSkus.Count -gt 20) {
        Write-Host "`nShowing top 20 of $($similarSkus.Count) results. Access `$similarSkus variable for all results." -ForegroundColor Yellow
    }

    # Show summary statistics
    Write-Host "`nSummary Statistics:" -ForegroundColor Cyan
    Write-Host "  Average Similarity Score: $([Math]::Round(($similarSkus | Measure-Object -Property SimilarityScore -Average).Average, 2))%"
    Write-Host "  Highest Similarity Score: $(($similarSkus | Measure-Object -Property SimilarityScore -Maximum).Maximum)%"

    # Price comparison if available
    $priceField = "MonthlyPrice($CurrencyCode)"
    $validPrices = $similarSkus | Where-Object { $_.$priceField -ne 'N/A' } | Select-Object -ExpandProperty $priceField
    if ($validPrices.Count -gt 0) {
        $avgPrice = [Math]::Round(($validPrices | Measure-Object -Average).Average, 2)
        $minPrice = ($validPrices | Measure-Object -Minimum).Minimum
        $maxPrice = ($validPrices | Measure-Object -Maximum).Maximum

        Write-Host "  Average Monthly Price: `$$avgPrice $CurrencyCode"
        Write-Host "  Price Range: `$$minPrice - `$$maxPrice $CurrencyCode"

        if ($targetSelectedPrice) {
            $targetMonthly = $targetSelectedPrice.Monthly
            $cheaperCount = ($similarSkus | Where-Object { $_.$priceField -ne 'N/A' -and $_.$priceField -lt $targetMonthly }).Count
            Write-Host "  SKUs cheaper than target ($PricingModel/$OS): $cheaperCount of $($similarSkus.Count)"
        }
    }
} else {
    Write-Host "`nNo similar SKUs found within the specified criteria." -ForegroundColor Yellow
    Write-Host "Try adjusting the following parameters:" -ForegroundColor Yellow
    Write-Host "  - Increase -Tolerance (current: $Tolerance%)"
    Write-Host "  - Decrease -MinSimilarityScore (current: $MinSimilarityScore)"
    Write-Host "  - Adjust weights to prioritize different capabilities"
    if ($CpuVendor) { Write-Host "  - Broaden -CpuVendor (current: $($CpuVendor -join ', '))" }
    if ($HideRetiring) { Write-Host "  - Use -HideRetiring:`$false to include retiring SKUs" }
    if ($HideGrowthRestricted) { Write-Host "  - Drop -HideGrowthRestricted to include capacity-limited SKUs" }
}

# Optional CSV export of the full result set
if ($ExportCsv -and $similarSkus.Count -gt 0) {
    try {
        $similarSkus | Export-Csv -Path $ExportCsv -NoTypeInformation -Encoding UTF8
        Write-Host "`nExported $($similarSkus.Count) results to: $ExportCsv" -ForegroundColor Green
    } catch {
        Write-Warning "Failed to export CSV to '$ExportCsv': $($_.Exception.Message)"
    }
}

# Return results for further analysis
return $similarSkus