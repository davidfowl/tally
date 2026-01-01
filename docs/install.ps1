# Tally installer script for Windows
# Usage: irm https://tallyai.money/install.ps1 | iex
# Usage: iex "& { $(irm https://tallyai.money/install.ps1) } -Prerelease"

param(
    [switch]$Prerelease
)

$ErrorActionPreference = "Stop"

$Repo = "davidfowl/tally"
$InstallDir = "$env:LOCALAPPDATA\tally"

function Write-Info { param($msg) Write-Host "==> " -ForegroundColor Green -NoNewline; Write-Host $msg }
function Write-Warn { param($msg) Write-Host "warning: " -ForegroundColor Yellow -NoNewline; Write-Host $msg }
function Write-Err { param($msg) Write-Host "error: " -ForegroundColor Red -NoNewline; Write-Host $msg; exit 1 }

if ($Prerelease) {
    Write-Info "Installing tally (development build)..."
} else {
    Write-Info "Installing tally..."
}

# Get release version
try {
    if ($Prerelease) {
        # Dev prerelease always uses 'dev' tag - no API call needed
        $Version = "dev"
        Write-Info "Development build: $Version"
    } else {
        $Release = Invoke-RestMethod -Uri "https://api.github.com/repos/$Repo/releases/latest"
        $Version = $Release.tag_name
        Write-Info "Latest version: $Version"
    }
} catch {
    Write-Err "Could not determine latest version. Check https://github.com/$Repo/releases"
}

# Download
$Filename = "tally-windows-amd64.zip"
$Url = "https://github.com/$Repo/releases/download/$Version/$Filename"
# Resolve $env:TEMP to long path (fixes 8.3 short path issues like C:\Users\TERRY~1.ANE)
$TempBase = (Get-Item $env:TEMP).FullName
$TempDir = Join-Path $TempBase "tally-install-$PID"
$ZipPath = Join-Path $TempDir $Filename

Write-Info "Downloading $Url..."

New-Item -ItemType Directory -Force -Path $TempDir | Out-Null
Invoke-WebRequest -Uri $Url -OutFile $ZipPath

# Extract
Write-Info "Extracting..."
Expand-Archive -Path $ZipPath -DestinationPath $TempDir -Force

# Install
Write-Info "Installing to $InstallDir..."
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
Move-Item -Path (Join-Path $TempDir "tally.exe") -Destination (Join-Path $InstallDir "tally.exe") -Force

# Cleanup
Remove-Item -Recurse -Force $TempDir

# Add to PATH
if ($env:GITHUB_ACTIONS) {
    # GitHub Actions: add to GITHUB_PATH
    Write-Info "Adding to GITHUB_PATH for this workflow..."
    $InstallDir | Out-File -FilePath $env:GITHUB_PATH -Append -Encoding utf8
    $env:Path = "$InstallDir;$env:Path"
} else {
    # Regular install: add to user PATH if not already there
    $UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if ($UserPath -notlike "*$InstallDir*") {
        Write-Info "Adding $InstallDir to PATH..."
        [Environment]::SetEnvironmentVariable("Path", "$UserPath;$InstallDir", "User")
        $env:Path = "$env:Path;$InstallDir"
    }
}

# Verify
Write-Info "Successfully installed tally!"
& "$InstallDir\tally.exe" version

Write-Host ""
Write-Host "Restart your terminal or run:" -ForegroundColor Yellow
Write-Host "  `$env:Path = [Environment]::GetEnvironmentVariable('Path', 'User')"
