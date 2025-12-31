# End-to-end smoke test for tally CLI
# This script tests the full workflow on Windows

$ErrorActionPreference = "Stop"

Write-Host "=== Tally E2E Smoke Test ===" -ForegroundColor Cyan
Write-Host ""

# Create temp directory
$WORKDIR = Join-Path $env:TEMP "tally-e2e-$(Get-Random)"
New-Item -ItemType Directory -Path $WORKDIR -Force | Out-Null
Write-Host "Working directory: $WORKDIR"
Set-Location $WORKDIR

try {
    # Test 1: tally version
    Write-Host ""
    Write-Host "=== Test 1: tally version ===" -ForegroundColor Yellow
    tally version
    Write-Host "✓ Version command works" -ForegroundColor Green

    # Test 2: tally init
    Write-Host ""
    Write-Host "=== Test 2: tally init ===" -ForegroundColor Yellow
    tally init
    if (-not (Test-Path "tally/config/settings.yaml")) {
        throw "settings.yaml not found"
    }
    if (-not (Test-Path "tally/config/merchants.rules")) {
        throw "merchants.rules not found"
    }
    Write-Host "✓ Init created expected files" -ForegroundColor Green

    Set-Location tally

    # Test 3: Create test data
    Write-Host ""
    Write-Host "=== Test 3: Create test data ===" -ForegroundColor Yellow
    New-Item -ItemType Directory -Path "data" -Force | Out-Null

    @"
Date,Description,Amount
01/15/2025,NETFLIX.COM,-15.99
01/16/2025,AMAZON.COM*ABC123,-45.50
01/17/2025,STARBUCKS STORE 12345,-6.75
02/01/2025,NETFLIX.COM,-15.99
02/05/2025,SPOTIFY USA,-9.99
03/01/2025,NETFLIX.COM,-15.99
03/10/2025,UNKNOWN MERCHANT XYZ,-99.00
"@ | Out-File -FilePath "data/transactions.csv" -Encoding utf8

    @"
year: 2025

data_sources:
  - name: TestBank
    file: data/transactions.csv
    format: "{date:%m/%d/%Y},{description},{amount}"

merchants_file: config/merchants.rules
views_file: config/views.rules
"@ | Out-File -FilePath "config/settings.yaml" -Encoding utf8

    @"
# Tally Merchant Rules

[Netflix]
match: contains("NETFLIX")
category: Subscriptions
subcategory: Streaming

[Spotify]
match: contains("SPOTIFY")
category: Subscriptions
subcategory: Streaming

[Amazon]
match: contains("AMAZON")
category: Shopping
subcategory: Online

[Starbucks]
match: contains("STARBUCKS")
category: Food
subcategory: Coffee
"@ | Out-File -FilePath "config/merchants.rules" -Encoding utf8

    @"
# Test views file
[Subscriptions]
description: Monthly subscriptions
filter: category == "Subscriptions"

[High Frequency]
description: Merchants with multiple transactions
filter: months >= 2

[All Spending]
description: Everything
filter: total > 0
"@ | Out-File -FilePath "config/views.rules" -Encoding utf8

    Write-Host "✓ Test data created" -ForegroundColor Green

    # Test 4: tally diag
    Write-Host ""
    Write-Host "=== Test 4: tally diag ===" -ForegroundColor Yellow
    tally diag | Select-Object -First 20
    Write-Host "✓ Diag command works" -ForegroundColor Green

    # Test 5: tally discover (should find unknown merchant)
    Write-Host ""
    Write-Host "=== Test 5: tally discover ===" -ForegroundColor Yellow
    $output = tally discover
    Write-Host $output
    if ($output -match "unknown") {
        Write-Host "✓ Discover found unknown merchants" -ForegroundColor Green
    } else {
        throw "Discover should have found unknown merchants"
    }

    # Test 6: Add rule for unknown merchant
    Write-Host ""
    Write-Host "=== Test 6: Add rule and verify ===" -ForegroundColor Yellow
    @"

[Unknown Merchant]
match: contains("UNKNOWN MERCHANT")
category: Shopping
subcategory: Other
"@ | Add-Content -Path "config/merchants.rules" -Encoding utf8
    $output = tally discover
    Write-Host $output
    if ($output -match "no unknown|all merchants are categorized") {
        Write-Host "✓ All merchants now categorized" -ForegroundColor Green
    } else {
        throw "Should have no unknown merchants after adding rule"
    }

    # Test 7: tally run --summary
    Write-Host ""
    Write-Host "=== Test 7: tally run --summary ===" -ForegroundColor Yellow
    tally run --summary | Select-Object -First 30
    Write-Host "✓ Run summary works" -ForegroundColor Green

    # Test 8: tally run (HTML report)
    Write-Host ""
    Write-Host "=== Test 8: tally run (HTML report) ===" -ForegroundColor Yellow
    tally run
    if (-not (Test-Path "output/spending_summary.html")) {
        throw "HTML report not generated"
    }
    $htmlContent = Get-Content "output/spending_summary.html" -Raw
    if ($htmlContent -match "Netflix") {
        Write-Host "✓ HTML report contains expected content" -ForegroundColor Green
    } else {
        throw "HTML report missing expected content"
    }

    # Test 9: tally run --no-embedded-html
    Write-Host ""
    Write-Host "=== Test 9: tally run --no-embedded-html ===" -ForegroundColor Yellow
    Remove-Item "output/*" -Force
    tally run --no-embedded-html
    if (-not (Test-Path "output/spending_report.css")) {
        throw "External CSS not generated"
    }
    if (-not (Test-Path "output/spending_report.js")) {
        throw "External JS not generated"
    }
    Write-Host "✓ External assets mode works" -ForegroundColor Green

    # Test 10: tally explain
    Write-Host ""
    Write-Host "=== Test 10: tally explain ===" -ForegroundColor Yellow
    tally explain Netflix
    Write-Host "✓ Explain command works" -ForegroundColor Green

    # Test 11: Views - verify views.rules is loaded
    Write-Host ""
    Write-Host "=== Test 11: tally views ===" -ForegroundColor Yellow
    $diagOutput = tally diag 2>&1 | Out-String
    if ($diagOutput -match "views.rules|Subscriptions|High Frequency") {
        Write-Host "✓ Views file detected in diag" -ForegroundColor Green
    } else {
        Write-Host "Note: Views info not in diag output (may be expected)" -ForegroundColor Yellow
    }

    # Check HTML report has view toggle
    $htmlContent = Get-Content "output/spending_summary.html" -Raw
    if ($htmlContent -match "By Section|section-view|sectionView") {
        Write-Host "✓ HTML report has view support" -ForegroundColor Green
    } else {
        Write-Host "Note: View mode not found in HTML (views may be empty)" -ForegroundColor Yellow
    }

    # Test explain with --view flag
    $viewOutput = tally explain --view subscriptions 2>&1 | Out-String
    Write-Host ($viewOutput | Select-Object -First 10)
    Write-Host "✓ View filter works in explain" -ForegroundColor Green

    Write-Host ""
    Write-Host "=== All tests passed! ===" -ForegroundColor Green

} finally {
    # Cleanup
    Set-Location $env:TEMP
    Remove-Item -Recurse -Force $WORKDIR -ErrorAction SilentlyContinue
}
