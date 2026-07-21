$ErrorActionPreference = "Stop"

$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$sourceDir = Join-Path $projectDir "skills\lampgo-setup"
$sourceSkill = Join-Path $sourceDir "SKILL.md"
$codexRoot = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $HOME ".codex" }
$skillsDir = Join-Path $codexRoot "skills"
$targetDir = Join-Path $skillsDir "lampgo-setup"

if (-not (Test-Path -LiteralPath $sourceSkill -PathType Leaf)) {
    throw "[LampGo] Skill source not found: $sourceSkill"
}

New-Item -ItemType Directory -Force -Path $skillsDir | Out-Null

if (Test-Path -LiteralPath $targetDir) {
    $item = Get-Item -LiteralPath $targetDir -Force
    $isLink = ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0
    if (-not $isLink) {
        throw "[LampGo] Target exists and is not a link: $targetDir. Move or inspect it before retrying."
    }
    $currentTarget = @($item.Target)[0]
    if ($currentTarget -eq $sourceDir) {
        Write-Host "[LampGo] Codex skill is already installed: $targetDir"
        exit 0
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

Write-Host '[LampGo] Start a new Codex task and say: Use $lampgo-setup to install and configure YareLampGo V2.0.'
