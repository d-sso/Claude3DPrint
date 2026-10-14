<#
.SYNOPSIS
    One-time install of the print3d skill. Safe to re-run.
.DESCRIPTION
    1. Finds Python 3.11-3.13
    2. Checks the venv path is safe on Windows (long-path guard)
    3. Creates/updates the venv at %LOCALAPPDATA%\print3d\venv and installs requirements.txt
    4. Writes %LOCALAPPDATA%\print3d\config.toml (parts_dir) if missing and creates the parts folder
    5. Links ~/.claude/skills/print3d to this skill folder (directory junction, no admin needed)
    6. Self-test: creates, builds and deletes a throwaway part
.PARAMETER PartsDir
    Parts library folder (skips the prompt). Default: ~/3DParts
.PARAMETER SkipSelfTest
    Skip step 6.
#>
[CmdletBinding()]
param(
    [string]$PartsDir,
    [switch]$SkipSelfTest
)
$ErrorActionPreference = 'Stop'

$ScriptsDir = $PSScriptRoot
$SkillDir = Split-Path $ScriptsDir -Parent
$AppDir = Join-Path $env:LOCALAPPDATA 'print3d'
$VenvDir = Join-Path $AppDir 'venv'
$VenvPython = Join-Path $VenvDir 'Scripts\python.exe'
$ConfigPath = Join-Path $AppDir 'config.toml'
$SkillLink = Join-Path $HOME '.claude\skills\print3d'
$Cli = Join-Path $ScriptsDir 'print3d.py'

function Step([string]$Message) { Write-Host "==> $Message" -ForegroundColor Cyan }
function Fail([string]$Message) { Write-Host "ERROR: $Message" -ForegroundColor Red; exit 1 }

# 1. Python 3.11-3.13
Step 'Looking for Python 3.11-3.13'
$Python = $null
foreach ($v in '3.13', '3.12', '3.11') {
    try {
        $exe = & py "-$v" -c 'import sys; print(sys.executable)' 2>$null
        if ($LASTEXITCODE -eq 0 -and $exe) { $Python = "$exe".Trim(); break }
    } catch { }
}
if (-not $Python) {
    try {
        $ver = & python -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>$null
        if ($ver -in '3.11', '3.12', '3.13') { $Python = (& python -c 'import sys; print(sys.executable)').Trim() }
    } catch { }
}
if (-not $Python) { Fail 'Python 3.11, 3.12 or 3.13 not found. Install it from python.org, then re-run.' }
Write-Host "    $Python"

# 2. Long-path guard (dependencies contain ~160-character relative paths)
$fs = Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem' -ErrorAction SilentlyContinue
if ($fs.LongPathsEnabled -ne 1 -and ($VenvDir.Length + 160) -gt 260) {
    Fail "The venv path '$VenvDir' is too long while Windows long paths are disabled. Enable them (admin PowerShell: Set-ItemProperty HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem LongPathsEnabled 1) and re-run."
}

# 3. venv and dependencies
if (Test-Path $VenvPython) { Step "Reusing venv at $VenvDir" }
else {
    Step "Creating venv at $VenvDir"
    & $Python -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) { Fail 'Creating the venv failed.' }
}
Step 'Installing dependencies (first run takes about a minute)'
& $VenvPython -m pip install --quiet --upgrade pip
if ($LASTEXITCODE -ne 0) { Fail 'Upgrading pip failed.' }
& $VenvPython -m pip install --quiet -r (Join-Path $SkillDir 'requirements.txt')
if ($LASTEXITCODE -ne 0) { Fail 'Installing requirements failed.' }

# 4. Config and parts folder
if (Test-Path $ConfigPath) { Step "Keeping existing config $ConfigPath" }
else {
    if (-not $PartsDir) {
        $default = Join-Path $HOME '3DParts'
        $answer = Read-Host "Parts library folder [$default]"
        $PartsDir = if ($answer) { $answer } else { $default }
    }
    $full = [System.IO.Path]::GetFullPath($PartsDir) -replace '\\', '/'
    New-Item -ItemType Directory -Force -Path $AppDir | Out-Null
    [System.IO.File]::WriteAllText($ConfigPath, "parts_dir = `"$full`"`n", (New-Object System.Text.UTF8Encoding $false))
    Step "Wrote $ConfigPath"
}
$resolved = & $VenvPython -c "import sys; sys.path.insert(0, r'$ScriptsDir'); import print3d; print(print3d.resolve_parts_dir(print3d.load_config()))"
if ($LASTEXITCODE -ne 0) { Fail "Could not read $ConfigPath" }
New-Item -ItemType Directory -Force -Path "$resolved".Trim() | Out-Null
Write-Host "    parts_dir = $("$resolved".Trim())"

# 5. Skill link
New-Item -ItemType Directory -Force -Path (Split-Path $SkillLink -Parent) | Out-Null
if (Test-Path $SkillLink) {
    $item = Get-Item $SkillLink -Force
    $target = if ($item.LinkType -eq 'Junction') { "$($item.Target)".TrimEnd('\') } else { $null }
    if ($target -and ($target -eq $SkillDir.TrimEnd('\'))) { Step "Skill link already points to $SkillDir" }
    else { Fail "$SkillLink already exists and does not link to $SkillDir. Remove or rename it, then re-run." }
}
else {
    New-Item -ItemType Junction -Path $SkillLink -Target $SkillDir | Out-Null
    Step "Linked $SkillLink -> $SkillDir"
}

# 6. Self-test
if (-not $SkipSelfTest) {
    Step 'Self-test'
    $testDir = Join-Path $AppDir 'selftest'
    if (Test-Path $testDir) { Remove-Item -Recurse -Force $testDir }
    $new = (& $VenvPython $Cli new selftest --dir $testDir | Out-String) | ConvertFrom-Json
    if (-not $new.ok) { Fail "Self-test 'new' failed: $($new.error.message)" }
    $run = (& $VenvPython $Cli run $new.model | Out-String) | ConvertFrom-Json
    Remove-Item -Recurse -Force $testDir
    if (-not $run.ok) { Fail "Self-test 'run' failed at stage $($run.error.stage): $($run.error.message)" }
    Write-Host "    built a $($run.parts[0].bbox_mm.size -join ' x ') mm test part"
}

Step 'print3d is installed. In Claude Code, ask for a part, e.g. "model a 40 mm cable clip".'
