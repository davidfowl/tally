# Tally PR installer script for Windows
# Usage: iex "& { $(irm https://raw.githubusercontent.com/davidfowl/tally/main/docs/install-pr.ps1) } <PR_NUMBER>"
#
# Requires: GitHub CLI (gh) installed and authenticated
#   winget install GitHub.cli
#   gh auth login

param(
    [Parameter(Mandatory=$true, Position=0)]
    [int]$PRNumber
)

$ErrorActionPreference = "Stop"

$Repo = "davidfowl/tally"
$InstallDir = "$env:LOCALAPPDATA\tally"

function Write-Info { param($msg) Write-Host "==> " -ForegroundColor Green -NoNewline; Write-Host $msg }
function Write-Err { param($msg) Write-Host "error: " -ForegroundColor Red -NoNewline; Write-Host $msg; exit 1 }

# Check for gh CLI
if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    Write-Err "GitHub CLI (gh) is required but not installed.
Install it with:
  winget install GitHub.cli

Then authenticate with: gh auth login"
}

$authStatus = gh auth status 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Err "GitHub CLI is not authenticated. Run: gh auth login"
}

Write-Info "Installing tally from PR #$PRNumber..."

# Get the head SHA of the PR
$pr = gh api "repos/$Repo/pulls/$PRNumber" | ConvertFrom-Json
if (-not $pr) {
    Write-Err "Could not find PR #$PRNumber"
}

$headSha = $pr.head.sha
Write-Info "PR #$PRNumber head commit: $($headSha.Substring(0, 7))"

# Find the latest successful workflow run for this commit
$runs = gh api "repos/$Repo/actions/workflows/pr-build.yml/runs?head_sha=$headSha&status=success" | ConvertFrom-Json

if (-not $runs.workflow_runs -or $runs.workflow_runs.Count -eq 0) {
    Write-Err "No successful build found for PR #$PRNumber.
Check https://github.com/$Repo/pull/$PRNumber/checks"
}

$RunId = $runs.workflow_runs[0].id
Write-Info "Found workflow run: $RunId"

# Download artifact
$Platform = "windows-amd64"
# Resolve $env:TEMP to long path (fixes 8.3 short path issues like C:\Users\TERRY~1.ANE)
$TempBase = (Get-Item $env:TEMP).FullName
$TempDir = Join-Path $TempBase "tally-pr-$PID"
New-Item -ItemType Directory -Force -Path $TempDir | Out-Null

$ArtifactName = "tally-$Platform"
Write-Info "Downloading $ArtifactName..."

Push-Location $TempDir
try {
    gh run download $RunId -R $Repo --name $ArtifactName
    if ($LASTEXITCODE -ne 0) {
        Write-Err "Failed to download artifact. The build may still be in progress.
Check https://github.com/$Repo/actions/runs/$RunId"
    }
} finally {
    Pop-Location
}

# Extract
Write-Info "Extracting..."
$ZipPath = Join-Path $TempDir "$ArtifactName.zip"
Expand-Archive -Path $ZipPath -DestinationPath $TempDir -Force

# Install
Write-Info "Installing to $InstallDir..."
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
Move-Item -Path (Join-Path $TempDir "tally.exe") -Destination (Join-Path $InstallDir "tally.exe") -Force

# Cleanup
Remove-Item -Recurse -Force $TempDir

# Add to PATH if not already there
$UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($UserPath -notlike "*$InstallDir*") {
    Write-Info "Adding $InstallDir to PATH..."
    [Environment]::SetEnvironmentVariable("Path", "$UserPath;$InstallDir", "User")
    $env:Path = "$env:Path;$InstallDir"
}

# Verify
Write-Info "Successfully installed tally from PR #$PRNumber!"
& "$InstallDir\tally.exe" version

Write-Host ""
Write-Host "Restart your terminal or run:" -ForegroundColor Yellow
Write-Host "  `$env:Path = [Environment]::GetEnvironmentVariable('Path', 'User')"
