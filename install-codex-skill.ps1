$ErrorActionPreference = "Stop"

$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$codexRoot = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $HOME ".codex" }
$skillsDir = Join-Path $codexRoot "skills"
$skillNames = @("lampgo-setup", "lampgo-control")

New-Item -ItemType Directory -Force -Path $skillsDir | Out-Null

foreach ($skillName in $skillNames) {
    $sourceDir = Join-Path $projectDir "skills\$skillName"
    $sourceSkill = Join-Path $sourceDir "SKILL.md"
    $targetDir = Join-Path $skillsDir $skillName

    if (-not (Test-Path -LiteralPath $sourceSkill -PathType Leaf)) {
        throw "[LampGo] Skill source not found: $sourceSkill"
    }

    if (Test-Path -LiteralPath $targetDir) {
        $item = Get-Item -LiteralPath $targetDir -Force
        $isLink = ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0
        if (-not $isLink) {
            throw "[LampGo] Target exists and is not a link: $targetDir. Move or inspect it before retrying."
        }
        $currentTarget = @($item.Target)[0]
        if ($currentTarget -eq $sourceDir) {
            Write-Host "[LampGo] Codex skill is already installed: $targetDir"
            continue
        }
        throw "[LampGo] Target is a different link: $targetDir -> $currentTarget. Move or inspect it before retrying."
    }

    try {
        New-Item -ItemType Junction -Path $targetDir -Target $sourceDir | Out-Null
        Write-Host "[LampGo] Installed Codex skill: $targetDir -> $sourceDir"
    } catch {
        New-Item -ItemType Directory -Force -Path $targetDir | Out-Null
        Copy-Item -Path (Join-Path $sourceDir "*") -Destination $targetDir -Recurse -Force
        Write-Host "[LampGo] Junction creation was unavailable; copied the Codex skill to $targetDir"
        Write-Host "[LampGo] Re-run this installer after repository updates to refresh the copied skill."
    }
}

Write-Host '[LampGo] Setup: Use $lampgo-setup to install and configure YareLampGo V2.0.'
Write-Host '[LampGo] Control: Use $lampgo-control to operate LampGo for a real task.'
