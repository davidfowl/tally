// spending_report.js - JavaScript for spending report interactivity
// This file is embedded into the HTML at build time by analyzer.py

// ============================================================
// CONFIGURATION
// ============================================================

// Table configuration - declarative column mapping for each table type
const TABLE_CONFIG = {
    'monthly-table': {
        section: 'monthly-section',
        columns: { merchant: 0, months: 1, count: 2, type: 3, monthly: 4, ytd: 5, pct: 6 },
        hasMonthlyColumn: true,
        totalColumn: 'ytd'
    },
    'variable-table': {
        section: 'variable-section',
        columns: { merchant: 0, category: 1, months: 2, count: 3, monthly: 4, ytd: 5, pct: 6 },
        hasMonthlyColumn: true,
        totalColumn: 'ytd'
    },
    'annual-table': {
        section: 'annual-section',
        columns: { merchant: 0, category: 1, count: 2, total: 3, pct: 4 },
        hasMonthlyColumn: false,
        totalColumn: 'total'
    },
    'periodic-table': {
        section: 'periodic-section',
        columns: { merchant: 0, category: 1, count: 2, total: 3, pct: 4 },
        hasMonthlyColumn: false,
        totalColumn: 'total'
    },
    'travel-table': {
        section: 'travel-section',
        columns: { merchant: 0, category: 1, count: 2, total: 3, pct: 4 },
        hasMonthlyColumn: false,
        totalColumn: 'total'
    },
    'oneoff-table': {
        section: 'oneoff-section',
        columns: { merchant: 0, category: 1, count: 2, total: 3, pct: 4 },
        hasMonthlyColumn: false,
        totalColumn: 'total'
    }
};

// Month helpers
const monthNames = {Jan:'01', Feb:'02', Mar:'03', Apr:'04', May:'05', Jun:'06',
                    Jul:'07', Aug:'08', Sep:'09', Oct:'10', Nov:'11', Dec:'12'};
const monthLabels = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

// ============================================================
// UTILITY FUNCTIONS
// ============================================================

function formatCurrency(amount) {
    const formatted = amount.toLocaleString('en-US', { maximumFractionDigits: 0 });
    return window.currencyFormat.replace('{amount}', formatted);
}

function monthLabelToKey(label) {
    // "Dec 2025" -> "2025-12"
    const [mon, year] = label.split(' ');
    return `${year}-${monthNames[mon]}`;
}

function monthKeyToLabel(key) {
    // "2025-12" -> "Dec 2025"
    // "2025-01..2025-03" -> "Jan–Mar 2025"
    if (key.includes('..')) {
        const [start, end] = key.split('..');
        const [sy, sm] = start.split('-');
        const [ey, em] = end.split('-');
        const startMon = monthLabels[parseInt(sm) - 1];
        const endMon = monthLabels[parseInt(em) - 1];
        if (sy === ey) {
            return `${startMon}–${endMon} ${sy}`;
        }
        return `${startMon} ${sy}–${endMon} ${ey}`;
    }
    const [year, month] = key.split('-');
    return `${monthLabels[parseInt(month) - 1]} ${year}`;
}

function monthMatchesFilter(txnMonth, filterText) {
    // Check for range separator (supports both '..' and ':')
    const rangeMatch = filterText.match(/^(\d{4}-\d{2})(?:\.\.|:)(\d{4}-\d{2})$/);
    if (rangeMatch) {
        const [, start, end] = rangeMatch;
        return txnMonth >= start && txnMonth <= end;
    }
    // Single month: "2025-12"
    return txnMonth === filterText;
}

// Normalize merchant ID (same logic as Python make_merchant_id)
function normalizeMerchantId(name) {
    return name.replace(/['"]/g, '').replace(/ /g, '_');
}

// Look up display name for a merchant ID from sectionData (case-insensitive)
function getMerchantDisplayName(merchantId) {
    if (!window.sectionData) return null;
    const normalizedId = normalizeMerchantId(merchantId).toLowerCase();
    for (const section of Object.values(window.sectionData.sections)) {
        for (const [key, merchant] of Object.entries(section.merchants)) {
            if (key.toLowerCase() === normalizedId && merchant.displayName) {
                return merchant.displayName;
            }
        }
    }
    return null;
}

// ============================================================
// FILTER MANAGEMENT
// ============================================================

function addFilter(text, type, displayText = null) {
    // Don't add duplicate filters
    if (window.activeFilters.some(f => f.text === text && f.type === type)) return;
    const filter = { text, type, mode: 'include' };
    if (displayText) filter.displayText = displayText;
    window.activeFilters.push(filter);
    renderFilters();
    applyFilters();
}

function removeFilter(index) {
    window.activeFilters.splice(index, 1);
    renderFilters();
    applyFilters();
}

function toggleFilter(index) {
    window.activeFilters[index].mode = window.activeFilters[index].mode === 'include' ? 'exclude' : 'include';
    renderFilters();
    applyFilters();
}

function applyDateRange(value) {
    const select = document.getElementById('dateRangeSelect');
    if (value) {
        // Check if this month/range is already in filters
        const exists = window.activeFilters.some(f => f.type === 'month' && f.text === value);
        if (!exists) {
            addFilter(value, 'month');
        }
    }
    // Reset dropdown to "All Dates" after selection (acts as "add filter" button)
    if (select) {
        select.value = '';
    }
}

function syncDatePickerWithFilters() {
    // Reset dropdown when filters change (multi-select via chips)
    const select = document.getElementById('dateRangeSelect');
    if (select) {
        select.value = '';
    }
}

function renderFilters() {
    const container = document.getElementById('filterChips');
    let html = window.activeFilters.map((f, i) => {
        // Use displayText if available, otherwise look up or format based on type
        let displayText = f.displayText || f.text;
        if (f.type === 'month') {
            displayText = monthKeyToLabel(f.text);
        } else if (f.type === 'merchant' && !f.displayText) {
            // Look up display name from ID
            const lookupName = getMerchantDisplayName(f.text);
            if (lookupName) displayText = lookupName;
        }
        const typeChar = f.type === 'month' ? 'd' : f.type.charAt(0);
        return `
            <div class="filter-chip ${f.type} ${f.mode}" data-index="${i}">
                <span class="chip-type">${typeChar}</span>
                <span class="chip-text">${displayText}</span>
                <span class="chip-remove" data-action="remove">×</span>
            </div>
        `;
    }).join('');

    // Add "Clear all" button if there are multiple filters
    if (window.activeFilters.length > 1) {
        html += '<button class="clear-all-btn" onclick="clearAllFilters()">Clear all</button>';
    }

    container.innerHTML = html;

    // Add click handlers
    container.querySelectorAll('.filter-chip').forEach(chip => {
        chip.addEventListener('click', (e) => {
            const idx = parseInt(chip.dataset.index);
            if (e.target.dataset.action === 'remove') {
                removeFilter(idx);
            } else {
                toggleFilter(idx);
            }
        });
    });

    // Sync date picker with current filters
    syncDatePickerWithFilters();
}

function clearAllFilters() {
    window.activeFilters = [];
    renderFilters();
    applyFilters();
}

// ============================================================
// FILTER ENGINE (from sectionData - single source of truth)
// ============================================================

/**
 * Filter sectionData based on active filters.
 * Returns computed state for rendering - no DOM mutations.
 */
function filterSectionData(filters) {
    const includeFilters = filters.filter(f => f.mode === 'include');
    const excludeFilters = filters.filter(f => f.mode === 'exclude');

    const result = {
        sections: {},           // Filtered merchants per section
        sectionTotals: {},      // Total per section
        merchantTotals: {},     // Per-merchant totals
        merchantMonths: {},     // Unique months per merchant
        merchantCounts: {},     // Transaction counts
        totalAmount: 0,
        numFilteredMonths: window.sectionData.numMonths,
        aggregations: {
            byMonth: {},
            byCategory: {},
            byCategoryByMonth: {}
        }
    };

    // Calculate filtered month count if month filters present
    const monthFilters = filters.filter(f => f.type === 'month' && f.mode === 'include');
    if (monthFilters.length > 0) {
        const allMonths = new Set();
        monthFilters.forEach(f => {
            if (f.text.includes('..')) {
                // Range: "2025-01..2025-03"
                expandMonthRange(f.text).forEach(m => allMonths.add(m));
            } else {
                allMonths.add(f.text);
            }
        });
        result.numFilteredMonths = allMonths.size || 1;
    }

    // Process each section
    for (const [sectionId, section] of Object.entries(window.sectionData.sections)) {
        result.sections[sectionId] = {};
        result.sectionTotals[sectionId] = 0;

        for (const [merchantId, merchant] of Object.entries(section.merchants)) {
            // Filter transactions
            const matchingTxns = merchant.transactions.filter(txn =>
                txnPassesFilters(txn, merchant, sectionId, includeFilters, excludeFilters)
            );

            if (matchingTxns.length === 0) continue;

            // Store filtered merchant data
            const filteredTotal = matchingTxns.reduce((sum, t) => sum + t.amount, 0);
            const filteredMonths = new Set(matchingTxns.map(t => t.month));

            result.sections[sectionId][merchantId] = {
                ...merchant,
                filteredTxns: matchingTxns,
                filteredTotal,
                filteredMonths: filteredMonths.size,
                filteredCount: matchingTxns.length
            };

            result.sectionTotals[sectionId] += filteredTotal;
            result.merchantTotals[merchantId] = filteredTotal;
            result.merchantMonths[merchantId] = filteredMonths.size;
            result.merchantCounts[merchantId] = matchingTxns.length;
            result.totalAmount += filteredTotal;

            // Aggregate for charts
            matchingTxns.forEach(txn => {
                // By month
                result.aggregations.byMonth[txn.month] = (result.aggregations.byMonth[txn.month] || 0) + txn.amount;

                // By category (use categoryPath for consistency with pie chart)
                const catKey = merchant.categoryPath || 'unknown';
                result.aggregations.byCategory[catKey] = (result.aggregations.byCategory[catKey] || 0) + txn.amount;

                // By category by month (use main category)
                const mainCat = merchant.category || 'Unknown';
                if (!result.aggregations.byCategoryByMonth[mainCat]) {
                    result.aggregations.byCategoryByMonth[mainCat] = {};
                }
                result.aggregations.byCategoryByMonth[mainCat][txn.month] =
                    (result.aggregations.byCategoryByMonth[mainCat][txn.month] || 0) + txn.amount;
            });
        }
    }

    return result;
}

/**
 * Check if a transaction passes all filters.
 */
function txnPassesFilters(txn, merchant, sectionId, includeFilters, excludeFilters) {
    // Group filters by type
    const groupByType = (filters) => {
        const groups = {};
        filters.forEach(f => {
            if (!groups[f.type]) groups[f.type] = [];
            groups[f.type].push(f);
        });
        return groups;
    };

    const includeGroups = groupByType(includeFilters);
    const excludeGroups = groupByType(excludeFilters);

    // Check exclude filters first (any match = excluded)
    for (const [type, filters] of Object.entries(excludeGroups)) {
        if (filters.some(f => matchesFilter(txn, merchant, sectionId, f))) {
            return false;
        }
    }

    // Check include filters (AND across types, OR within type)
    for (const [type, filters] of Object.entries(includeGroups)) {
        const anyMatch = filters.some(f => matchesFilter(txn, merchant, sectionId, f));
        if (!anyMatch) return false;
    }

    return true;
}

/**
 * Check if a single filter matches a transaction.
 */
function matchesFilter(txn, merchant, sectionId, filter) {
    const normalizedFilterText = filter.text.toLowerCase();

    switch (filter.type) {
        case 'merchant':
            // Match by merchant ID (normalized)
            const merchantId = normalizeMerchantId(merchant.displayName || merchant.id).toLowerCase();
            return merchantId === normalizeMerchantId(filter.text).toLowerCase();

        case 'category':
            // Match against categoryPath (e.g., "food/grocery")
            const catPath = (merchant.categoryPath || '').toLowerCase();
            return catPath.includes(normalizedFilterText);

        case 'location':
            // Match transaction location
            const txnLocation = (txn.location || '').toLowerCase();
            return txnLocation === normalizedFilterText;

        case 'month':
            // Match transaction month (supports ranges)
            return monthMatchesFilter(txn.month, filter.text);

        default:
            return false;
    }
}

/**
 * Expand a month range into individual months.
 * "2025-01..2025-03" -> ["2025-01", "2025-02", "2025-03"]
 */
function expandMonthRange(rangeStr) {
    const [start, end] = rangeStr.split('..');
    const months = [];
    let current = start;
    while (current <= end) {
        months.push(current);
        // Increment month
        const [y, m] = current.split('-').map(Number);
        const nextM = m === 12 ? 1 : m + 1;
        const nextY = m === 12 ? y + 1 : y;
        current = `${nextY}-${String(nextM).padStart(2, '0')}`;
    }
    return months;
}

// ============================================================
// RENDERING FUNCTIONS
// ============================================================

/**
 * Update DOM visibility based on filter state.
 */
function renderVisibility(state) {
    // For each section/table
    for (const [tableId, config] of Object.entries(TABLE_CONFIG)) {
        const table = document.getElementById(tableId);
        if (!table) continue;

        const tbody = table.querySelector('tbody');
        if (!tbody) continue;

        const sectionId = tableId.replace('-table', '-section');
        const filteredSection = state.sections[tableId.replace('-table', '-table')] || {};

        // Show/hide merchant rows
        tbody.querySelectorAll('.merchant-row').forEach(row => {
            const merchantId = row.dataset.merchant;
            const isVisible = merchantId && state.merchantTotals[merchantId] !== undefined;
            row.classList.toggle('hidden', !isVisible);

            // Show/hide transaction rows
            if (isVisible) {
                row.querySelectorAll('.txn-row').forEach(txnRow => {
                    // For now, show all txn rows if merchant is visible
                    // Could filter by month here if needed
                    txnRow.classList.remove('hidden');
                });
            }
        });

        // Show/hide section if no visible merchants
        const section = document.getElementById(config.section);
        if (section) {
            const hasVisibleMerchants = Object.keys(state.sections[tableId] || {}).length > 0;
            section.classList.toggle('hidden', !hasVisibleMerchants);
        }
    }
}

/**
 * Update totals in the DOM based on filter state.
 */
function renderTotals(state) {
    const numMonths = state.numFilteredMonths || 1;

    // Update each table's totals
    for (const [tableId, config] of Object.entries(TABLE_CONFIG)) {
        const table = document.getElementById(tableId);
        if (!table) continue;

        const cols = config.columns;
        const sectionTotal = state.sectionTotals[tableId] || 0;

        // Update merchant row totals
        table.querySelectorAll('.merchant-row').forEach(row => {
            const merchantId = row.dataset.merchant;
            const total = state.merchantTotals[merchantId];
            if (total === undefined) return;

            const count = state.merchantCounts[merchantId] || 0;
            const pct = sectionTotal > 0 ? (total / sectionTotal * 100) : 0;

            // Update cells based on config
            if (cols.count !== undefined) {
                row.cells[cols.count].textContent = count;
            }
            if (cols.total !== undefined) {
                row.cells[cols.total].innerHTML = formatCurrency(total);
            }
            if (cols.ytd !== undefined) {
                row.cells[cols.ytd].innerHTML = formatCurrency(total);
            }
            if (cols.monthly !== undefined && config.hasMonthlyColumn) {
                const monthly = total / numMonths;
                row.cells[cols.monthly].innerHTML = formatCurrency(monthly) + '/mo';
            }
            if (cols.pct !== undefined) {
                row.cells[cols.pct].textContent = pct.toFixed(1) + '%';
            }
        });

        // Update section total row
        const totalRow = table.querySelector('.total-row');
        if (totalRow) {
            if (cols.ytd !== undefined) {
                totalRow.cells[cols.ytd].innerHTML = formatCurrency(sectionTotal);
            }
            if (cols.total !== undefined) {
                totalRow.cells[cols.total].innerHTML = formatCurrency(sectionTotal);
            }
            if (cols.monthly !== undefined && config.hasMonthlyColumn) {
                const monthly = sectionTotal / numMonths;
                totalRow.cells[cols.monthly].innerHTML = formatCurrency(monthly) + '/mo';
            }
        }
    }

    // Update summary cards
    updateSummaryCards(state);
}

/**
 * Update the summary cards at the top of the page.
 */
function updateSummaryCards(state) {
    const numMonths = state.numFilteredMonths || 1;

    // Monthly budget card
    const monthlyTotal = (state.sectionTotals['monthly-table'] || 0);
    const variableTotal = (state.sectionTotals['variable-table'] || 0);
    const monthlyBudget = (monthlyTotal + variableTotal) / numMonths;

    const monthlyCard = document.querySelector('.summary-card:nth-child(1)');
    if (monthlyCard) {
        const amountEl = monthlyCard.querySelector('.amount');
        if (amountEl) amountEl.textContent = formatCurrency(monthlyBudget) + '/mo';
    }

    // Non-recurring card
    const annualTotal = state.sectionTotals['annual-table'] || 0;
    const periodicTotal = state.sectionTotals['periodic-table'] || 0;
    const travelTotal = state.sectionTotals['travel-table'] || 0;
    const oneoffTotal = state.sectionTotals['oneoff-table'] || 0;
    const nonRecurring = annualTotal + periodicTotal + travelTotal + oneoffTotal;

    const nonRecurringCard = document.querySelector('.summary-card:nth-child(2)');
    if (nonRecurringCard) {
        const amountEl = nonRecurringCard.querySelector('.amount');
        if (amountEl) {
            const pct = window.originalTotals.totalYtd > 0
                ? (nonRecurring / window.originalTotals.totalYtd * 100)
                : 0;
            amountEl.textContent = `${formatCurrency(nonRecurring)} (${pct.toFixed(1)}%)`;
        }
    }

    // Total spending card
    const totalCard = document.querySelector('.summary-card:nth-child(3)');
    if (totalCard) {
        const amountEl = totalCard.querySelector('.amount');
        if (amountEl) {
            const pct = window.originalTotals.totalYtd > 0
                ? (state.totalAmount / window.originalTotals.totalYtd * 100)
                : 0;
            amountEl.textContent = `${formatCurrency(state.totalAmount)} (${pct.toFixed(1)}%)`;
        }
    }
}

// ============================================================
// MAIN FILTER APPLICATION
// ============================================================

function applyFilters() {
    if (window.activeFilters.length === 0) {
        // No filters - reset to original state
        resetToOriginalState();
        return;
    }

    const state = filterSectionData(window.activeFilters);
    renderVisibility(state);
    renderTotals(state);

    // Update charts if available
    if (typeof updateChartsFromFilters === 'function') {
        updateChartsFromFilters(state);
    }
}

function resetToOriginalState() {
    // Show all rows
    document.querySelectorAll('.merchant-row, .txn-row').forEach(row => {
        row.classList.remove('hidden');
    });

    // Show all sections
    document.querySelectorAll('[id$="-section"]').forEach(section => {
        section.classList.remove('hidden');
    });

    // Reset totals to original values
    // This would need the original values stored - for now, reload from originalTotals
    // Or we could just re-run filterSectionData with empty filters
    const state = filterSectionData([]);
    renderTotals(state);

    // Reset charts
    if (typeof resetCharts === 'function') {
        resetCharts();
    }
}

// ============================================================
// URL HASH PERSISTENCE
// ============================================================

function filtersToHash() {
    if (window.activeFilters.length === 0) {
        history.replaceState(null, '', window.location.pathname);
        return;
    }

    const typeToChar = {category: 'c', merchant: 'm', location: 'l', month: 'd'};
    const parts = window.activeFilters.map(f => {
        const modePrefix = f.mode === 'exclude' ? '-' : '+';
        const typeChar = typeToChar[f.type] || 'c';
        return `${modePrefix}${typeChar}:${encodeURIComponent(f.text)}`;
    });

    history.replaceState(null, '', '#' + parts.join('&'));
}

function hashToFilters() {
    const hash = window.location.hash.slice(1);
    if (!hash) return;

    const typeMap = {c: 'category', m: 'merchant', l: 'location', d: 'month'};
    const parts = hash.split('&');
    parts.forEach(part => {
        if (part.length < 2) return;

        // Handle both formats: "+c:value" (with mode) and "c:value" (without mode)
        let mode = 'include';
        let startIdx = 0;

        if (part.charAt(0) === '+' || part.charAt(0) === '-') {
            mode = part.charAt(0) === '-' ? 'exclude' : 'include';
            startIdx = 1;
        }

        const typeChar = part.charAt(startIdx);
        const colonIdx = part.indexOf(':', startIdx);
        if (colonIdx === -1) return;

        const type = typeMap[typeChar] || 'category';
        const text = decodeURIComponent(part.slice(colonIdx + 1));
        if (text && !window.activeFilters.some(f => f.text === text && f.type === type)) {
            window.activeFilters.push({ text, type, mode });
        }
    });

    if (window.activeFilters.length > 0) {
        renderFilters();
        applyFilters();
    }
}

// Update hash when filters change (patch applyFilters)
const originalApplyFilters = applyFilters;
applyFilters = function() {
    originalApplyFilters();
    filtersToHash();
};

// Listen for hash changes
window.addEventListener('hashchange', () => {
    window.activeFilters = [];
    hashToFilters();
});

// ============================================================
// AUTOCOMPLETE & SEARCH
// ============================================================

let selectedIndex = -1;

function setupAutocomplete() {
    const searchInput = document.getElementById('searchInput');
    const autocompleteList = document.getElementById('autocomplete-list');

    if (!searchInput || !autocompleteList) return;

    searchInput.addEventListener('input', (e) => {
        const val = e.target.value.toLowerCase().trim();
        autocompleteList.innerHTML = '';
        selectedIndex = -1;

        if (!val) {
            autocompleteList.classList.remove('active');
            return;
        }

        // Filter autocomplete data
        const matches = window.autocompleteData.filter(item =>
            item.text.toLowerCase().includes(val)
        ).slice(0, 10);

        if (matches.length === 0) {
            autocompleteList.classList.remove('active');
            return;
        }

        matches.forEach((item, idx) => {
            const div = document.createElement('div');
            div.className = 'autocomplete-item';
            div.dataset.index = idx;

            const typeSpan = document.createElement('span');
            typeSpan.className = 'autocomplete-type';
            typeSpan.textContent = item.type.charAt(0).toUpperCase();

            const textSpan = document.createElement('span');
            textSpan.className = 'autocomplete-text';
            textSpan.textContent = item.text;

            div.appendChild(typeSpan);
            div.appendChild(textSpan);

            div.addEventListener('click', () => {
                // Use ID for filtering, display text for chip
                const filterText = item.type === 'merchant' ? item.id : item.text;
                addFilter(filterText, item.type, item.text);
                searchInput.value = '';
                autocompleteList.classList.remove('active');
            });

            autocompleteList.appendChild(div);
        });

        autocompleteList.classList.add('active');
    });

    searchInput.addEventListener('keydown', (e) => {
        const items = autocompleteList.querySelectorAll('.autocomplete-item');
        if (!items.length) return;

        if (e.key === 'ArrowDown') {
            e.preventDefault();
            selectedIndex = Math.min(selectedIndex + 1, items.length - 1);
            updateAutocompleteSelection(items);
        } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            selectedIndex = Math.max(selectedIndex - 1, 0);
            updateAutocompleteSelection(items);
        } else if (e.key === 'Enter' && selectedIndex >= 0) {
            e.preventDefault();
            items[selectedIndex].click();
        } else if (e.key === 'Escape') {
            autocompleteList.classList.remove('active');
            selectedIndex = -1;
        }
    });

    // Close on click outside
    document.addEventListener('click', (e) => {
        if (!e.target.closest('.search-container')) {
            autocompleteList.classList.remove('active');
            selectedIndex = -1;
        }
    });
}

function updateAutocompleteSelection(items) {
    items.forEach((item, idx) => {
        item.classList.toggle('selected', idx === selectedIndex);
    });
}

// ============================================================
// TABLE INTERACTIONS
// ============================================================

function setupTableClickHandlers() {
    // Click on category cell to filter
    document.querySelectorAll('td.category').forEach(cell => {
        cell.style.cursor = 'pointer';
        cell.addEventListener('click', (e) => {
            e.stopPropagation();
            const text = cell.textContent.trim();
            if (text) addFilter(text, 'category');
        });
    });

    // Click on merchant name to filter
    document.querySelectorAll('.merchant-row').forEach(row => {
        const merchantCell = row.cells[0];
        if (merchantCell) {
            merchantCell.style.cursor = 'pointer';
            merchantCell.addEventListener('click', (e) => {
                // Don't trigger if clicking expand arrow
                if (e.target.classList.contains('expand-arrow')) return;

                e.stopPropagation();
                const merchantId = row.dataset.merchant;
                const displayName = merchantCell.textContent.replace('▶', '').replace('▼', '').trim();
                if (merchantId) addFilter(merchantId, 'merchant', displayName);
            });
        }
    });

    // Expand/collapse transaction rows
    document.querySelectorAll('.merchant-row').forEach(row => {
        row.addEventListener('click', (e) => {
            // Toggle expanded state
            const isExpanded = row.classList.toggle('expanded');
            const arrow = row.querySelector('.expand-arrow');
            if (arrow) {
                arrow.textContent = isExpanded ? '▼' : '▶';
            }

            // Show/hide transaction rows
            let next = row.nextElementSibling;
            while (next && next.classList.contains('txn-row')) {
                next.classList.toggle('hidden', !isExpanded);
                next = next.nextElementSibling;
            }
        });
    });
}

// ============================================================
// INITIALIZATION
// ============================================================

function initSpendingReport() {
    setupAutocomplete();
    setupTableClickHandlers();

    // Initialize activeFilters if not already set
    if (!window.activeFilters) {
        window.activeFilters = [];
    }

    // Hash loading is done after charts are ready (in the HTML)
}

// Auto-initialize when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initSpendingReport);
} else {
    initSpendingReport();
}
