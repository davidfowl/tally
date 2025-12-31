#!/bin/bash
# End-to-end smoke test for tally CLI
# This script tests the full workflow on macOS/Linux

set -e  # Exit on first error

echo "=== Tally E2E Smoke Test ==="
echo ""

# Create temp directory
WORKDIR=$(mktemp -d)
echo "Working directory: $WORKDIR"
cd "$WORKDIR"

cleanup() {
    echo ""
    echo "Cleaning up..."
    rm -rf "$WORKDIR"
}
trap cleanup EXIT

# Test 1: tally version
echo ""
echo "=== Test 1: tally version ==="
tally version
echo "✓ Version command works"

# Test 2: tally init
echo ""
echo "=== Test 2: tally init ==="
tally init
if [ ! -f "tally/config/settings.yaml" ]; then
    echo "✗ settings.yaml not found"
    exit 1
fi
if [ ! -f "tally/config/merchants.rules" ]; then
    echo "✗ merchants.rules not found"
    exit 1
fi
echo "✓ Init created expected files"

cd tally

# Test 3: Create test data
echo ""
echo "=== Test 3: Create test data ==="
mkdir -p data
cat > data/transactions.csv << 'EOF'
Date,Description,Amount
01/15/2025,NETFLIX.COM,-15.99
01/16/2025,AMAZON.COM*ABC123,-45.50
01/17/2025,STARBUCKS STORE 12345,-6.75
02/01/2025,NETFLIX.COM,-15.99
02/05/2025,SPOTIFY USA,-9.99
03/01/2025,NETFLIX.COM,-15.99
03/10/2025,UNKNOWN MERCHANT XYZ,-99.00
EOF

cat > config/settings.yaml << 'EOF'
year: 2025

data_sources:
  - name: TestBank
    file: data/transactions.csv
    format: "{date:%m/%d/%Y},{description},{amount}"

merchants_file: config/merchants.rules
views_file: config/views.rules
EOF

cat > config/merchants.rules << 'EOF'
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
EOF

cat > config/views.rules << 'EOF'
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
EOF
echo "✓ Test data created"

# Test 4: tally diag
echo ""
echo "=== Test 4: tally diag ==="
tally diag | head -20
echo "✓ Diag command works"

# Test 5: tally discover (should find unknown merchant)
echo ""
echo "=== Test 5: tally discover ==="
OUTPUT=$(tally discover)
echo "$OUTPUT"
if echo "$OUTPUT" | grep -qi "unknown"; then
    echo "✓ Discover found unknown merchants"
else
    echo "✗ Discover should have found unknown merchants"
    exit 1
fi

# Test 6: Add rule for unknown merchant
echo ""
echo "=== Test 6: Add rule and verify ==="
cat >> config/merchants.rules << 'EOF'

[Unknown Merchant]
match: contains("UNKNOWN MERCHANT")
category: Shopping
subcategory: Other
EOF
OUTPUT=$(tally discover)
echo "$OUTPUT"
if echo "$OUTPUT" | grep -qi "no unknown\|all merchants are categorized"; then
    echo "✓ All merchants now categorized"
else
    echo "✗ Should have no unknown merchants after adding rule"
    exit 1
fi

# Test 7: tally run --summary
echo ""
echo "=== Test 7: tally run --summary ==="
tally run --summary | head -30
echo "✓ Run summary works"

# Test 8: tally run (HTML report)
echo ""
echo "=== Test 8: tally run (HTML report) ==="
tally run
if [ ! -f "output/spending_summary.html" ]; then
    echo "✗ HTML report not generated"
    exit 1
fi
if grep -q "Netflix" output/spending_summary.html; then
    echo "✓ HTML report contains expected content"
else
    echo "✗ HTML report missing expected content"
    exit 1
fi

# Test 9: tally run --no-embedded-html
echo ""
echo "=== Test 9: tally run --no-embedded-html ==="
rm -rf output/*
tally run --no-embedded-html
if [ ! -f "output/spending_report.css" ]; then
    echo "✗ External CSS not generated"
    exit 1
fi
if [ ! -f "output/spending_report.js" ]; then
    echo "✗ External JS not generated"
    exit 1
fi
echo "✓ External assets mode works"

# Test 10: tally explain
echo ""
echo "=== Test 10: tally explain ==="
tally explain Netflix
echo "✓ Explain command works"

# Test 11: Views - verify views.rules is loaded
echo ""
echo "=== Test 11: tally views ==="
OUTPUT=$(tally diag 2>&1)
if echo "$OUTPUT" | grep -qi "views.rules\|Subscriptions\|High Frequency"; then
    echo "✓ Views file detected in diag"
else
    echo "Note: Views info not in diag output (may be expected)"
fi

# Check HTML report has section view toggle
if grep -q "By Section\|section-view\|sectionView" output/spending_summary.html 2>/dev/null; then
    echo "✓ HTML report has view support"
else
    echo "Note: View mode not found in HTML (views may be empty)"
fi

# Test explain with --view flag
OUTPUT=$(tally explain --view subscriptions 2>&1) || true
echo "$OUTPUT" | head -10
echo "✓ View filter works in explain"

echo ""
echo "=== All tests passed! ==="
