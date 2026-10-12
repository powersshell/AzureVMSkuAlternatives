<#
.SYNOPSIS
    Offline fixtures for the production PowerShell generation planning helpers.
#>
$ErrorActionPreference = 'Stop'
$source = Get-Content (Join-Path (Split-Path $PSScriptRoot -Parent) 'powershell-script\Compare-AzureVms.ps1') -Raw
$tokens = $null; $parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseInput($source, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw ($parseErrors | Out-String) }
foreach ($name in @('Get-SupportedBootGenerations', 'Get-BootGenerationContext', 'Get-GenerationPath', 'Get-TempDiskPresence', 'Get-MigrationAssessment', 'Get-RecommendationScore', 'Get-ModernizationScore', 'Get-FamilyAffinity')) {
    $node = $ast.Find({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name }, $true)
    if (-not $node) { throw "Missing production helper: $name" }
    . ([scriptblock]::Create($node.Extent.Text))
}
$count = 0
function Assert-True {
    param([bool]$Value, [string]$Message)
    if (-not $Value) { throw $Message }
    $script:count++
}
foreach ($metadata in @('V1', 'V2', 'V1,V2', '', 'invalid', ' v1 , v2 ')) {
    foreach ($sourceGen in @($null, 1, 2)) {
        foreach ($targetGen in @($null, 1, 2)) {
            $supported = @(Get-SupportedBootGenerations $metadata)
            $a = Get-MigrationAssessment -HyperVGenerations $metadata -SourceGeneration $sourceGen -TargetGeneration $targetGen
            Assert-True (-not $a.EligibilityAssessed) 'SKU data must never claim eligibility.'
            $expectedSupport = if ($null -eq $targetGen) { 'not_requested' } elseif (-not $supported.Count) { 'unknown' } elseif ($supported -contains $targetGen) { 'supported' } else { 'unsupported' }
            Assert-True ($a.PreferenceSupport -eq $expectedSupport) 'Preference compatibility mismatch.'
            if ($null -eq $sourceGen -or -not $supported.Count) {
                Assert-True ($null -eq $a.DestinationGeneration -and $a.Effort -eq 'Cannot assess effort') 'Unknown must be unassessed.'
            } else {
                $expected = if ($supported -contains $targetGen) { $targetGen } elseif ($supported -contains $sourceGen) { $sourceGen } else { $supported[0] }
                Assert-True ($a.DestinationGeneration -eq $expected) 'Destination must be the preferred compatible or alternate path.'
                if ($sourceGen -eq 1 -and $expected -eq 2) {
                    foreach ($word in @('Trusted Launch', 'GPT', 'Enhanced', 'Site Recovery', 'rollback', 'deallocation/downtime')) {
                        Assert-True (($a.Checklist -join ' ') -match [regex]::Escape($word)) "Missing upgrade prerequisite: $word"
                    }
                }
                if ($a.PreferenceSupport -eq 'unsupported') { Assert-True ($a.Reasons[0] -match 'alternate path') 'Unsupported preference must not claim the requested target.' }
            }
        }
    }
}
$dual = Get-BootGenerationContext 'V1,V2' 'auto' 'same'
Assert-True ($null -eq $dual.SourceGeneration -and $null -eq $dual.TargetGeneration) 'Dual source requires selection.'
$single = Get-BootGenerationContext 'V1' 'auto' 'same'
Assert-True ($single.SourceGeneration -eq 1 -and $single.TargetGeneration -eq 1) 'Single source is inferred.'
$rejected = $false
try { Get-BootGenerationContext 'V2' 'gen1' 'same' | Out-Null } catch { $rejected = $_.Exception.Message -match 'does not support' }
Assert-True $rejected 'Impossible source must fail explicitly.'
$risks = Get-MigrationAssessment -HyperVGenerations 'V1,V2' -SourceGeneration 1 -TargetGeneration 1 -ArchitectureChange $true -TempDiskMismatch $true -UsesManaNetworking $true
Assert-True ($risks.Effort -eq 'New VM/image planning' -and $risks.Reasons.Count -eq 4) 'Independent risks must remain visible.'
Assert-True ($true -eq (Get-TempDiskPresence @{ ResourceDiskSizeInMB = '16384'; NvmeDiskSizeInMiB = '0' })) 'SCSI temporary disks count.'
Assert-True ($false -eq (Get-TempDiskPresence @{ ResourceDiskSizeInMB = '0'; NvmeDiskSizeInMiB = '0' })) 'Known no temporary disk.'
Assert-True ($null -eq (Get-TempDiskPresence @{})) 'Missing temporary-disk metadata remains unknown.'

# Fixed scoring subcomponents isolate the newly added boot-preference penalties.
function Get-ModernizationScore { param($Identity, $FamilyGenerations, $CpuPerfScore) 100 }
function Get-FamilyAffinity { param($TargetIdentity, $CandidateIdentity) 100 }
$script:RecommendationModeWeights = @{ balanced = @{ Similarity = 0.5; Modernization = 0.3; FamilyAffinity = 0.2 } }
$script:CostModeSavingBonus = 10; $script:OlderGenerationPenalty = 10
$identity = [pscustomobject]@{ Version = 5 }
foreach ($preference in @($null, 1, 2)) {
    foreach ($metadata in @('V1', 'V2', 'V1,V2', '')) {
        $supported = @(Get-SupportedBootGenerations $metadata)
        $score = Get-RecommendationScore -SimilarityScore 100 -TargetIdentity $identity -CandidateIdentity $identity -FamilyGenerations @{} -TargetGenerationPreference $preference -CandidateGenerations $supported
        $penalty = if ($null -eq $preference) { 0 } elseif (-not $supported.Count) { 10 } elseif ($supported -notcontains $preference) { 30 } else { 0 }
        Assert-True ($score.Score -eq (100 - $penalty)) 'Boot preference scores must match API penalties.'
    }
}
Assert-True ($source -match "\[ValidateSet\('auto', 'gen1', 'gen2'\)\]") 'SourceGen parameter is validated.'
Assert-True ($source -match "\[ValidateSet\('same', 'any', 'gen1', 'gen2'\)\]") 'TargetGen parameter is validated.'
Write-Host "PASS: $count generation path, prerequisite, unknown, independent risk and preference scoring assertions."
