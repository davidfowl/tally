// spending_report.js - Vue 3 app for spending report
// This file is embedded into the HTML at build time by analyzer.py

const { createApp, ref, reactive, computed, watch, onMounted, nextTick, defineComponent } = Vue;

// =============================================================================
// TRANSACTION CLASSIFICATION - Mirrors Python classification.py
// =============================================================================

const INCOME_TAG = 'income';
const TRANSFER_TAG = 'transfer';
const INVESTMENT_TAG = 'investment';

const SPECIAL_TAGS = new Set([INCOME_TAG, TRANSFER_TAG, INVESTMENT_TAG]);
const EXCLUDED_FROM_SPENDING = new Set([INCOME_TAG, TRANSFER_TAG, INVESTMENT_TAG]);

function getTagsLower(tags) {
    return new Set((tags || []).map(t => t.toLowerCase()));
}

function isIncome(tags) {
    return getTagsLower(tags).has(INCOME_TAG);
}

function isTransfer(tags) {
    return getTagsLower(tags).has(TRANSFER_TAG);
}

function isInvestment(tags) {
    return getTagsLower(tags).has(INVESTMENT_TAG);
}

function isExcludedFromSpending(tags) {
    const tagsLower = getTagsLower(tags);
    for (const tag of EXCLUDED_FROM_SPENDING) {
        if (tagsLower.has(tag)) return true;
    }
    return false;
}

/**
 * Categorize a transaction amount into appropriate bucket.
 * All returned values are positive (or zero).
 * Mirrors Python classification.categorize_amount()
 */
function categorizeAmount(amount, tags) {
    const result = {
        income: 0,
        investment: 0,
        transferIn: 0,
        transferOut: 0,
        spending: 0,
        credits: 0
    };

    const tagsLower = getTagsLower(tags);

    if (tagsLower.has(INCOME_TAG)) {
        result.income = Math.abs(amount);
    } else if (tagsLower.has(INVESTMENT_TAG)) {
        result.investment = Math.abs(amount);
    } else if (tagsLower.has(TRANSFER_TAG)) {
        if (amount > 0) {
            result.transferIn = amount;
        } else {
            result.transferOut = Math.abs(amount);
        }
    } else {
        // Normal spending/credits
        if (amount > 0) {
            result.spending = amount;
        } else {
            result.credits = Math.abs(amount);
        }
    }

    return result;
}

/**
 * Calculate cash flow from totals.
 * Mirrors Python classification.calculate_cash_flow()
 */
function calculateCashFlow(income, spending, credits) {
    return income - spending + credits;
}

// =============================================================================

// =============================================================================
// DATE FILTER HELPERS (Month / Quarter / Year / Custom range)
// =============================================================================

const MONTH_NAMES_SHORT = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
const MONTH_NAMES_LONG = ['January','February','March','April','May','June','July','August','September','October','November','December'];

function pad2(n) { return String(n).padStart(2, '0'); }
function quarterOf(monthNum) { return Math.ceil(monthNum / 3); }

// 'YYYY-MM' -> 'Mon YYYY'
function monthKeyLabel(key) {
    const parts = key.split('-');
    return MONTH_NAMES_SHORT[parseInt(parts[1], 10) - 1] + ' ' + parts[0];
}
function quarterMonthKeys(year, q) {
    const startM = (q - 1) * 3 + 1;
    return [startM, startM + 1, startM + 2].map(m => year + '-' + pad2(m));
}
function yearMonthKeys(year) {
    const out = [];
    for (let m = 1; m <= 12; m++) out.push(year + '-' + pad2(m));
    return out;
}

// Flatten a preset item ({type:'month'|'quarter'|'year', key}) to its
// constituent calendar-month keys. This is the shared path that lets
// quarter/year presets, individual month clicks, and aggregation all use one
// coverage/toggle mechanism.
function monthsForItem(item) {
    if (item.type === 'month') return [item.key];
    if (item.type === 'quarter') {
        const parts = item.key.split('-Q');
        return quarterMonthKeys(parseInt(parts[0], 10), parseInt(parts[1], 10));
    }
    if (item.type === 'year') return yearMonthKeys(parseInt(item.key, 10));
    return [];
}

// Greedily compress a flat set of month keys into the fewest chips: any full
// year first, then any full quarters remaining in that year, then whatever
// individual months are left. Returns {type, key, label}[].
function aggregateMonthKeys(monthKeys) {
    const remaining = {};
    monthKeys.forEach(k => { remaining[k] = true; });
    const byYear = {};
    monthKeys.forEach(k => { (byYear[k.slice(0, 4)] = byYear[k.slice(0, 4)] || []).push(k); });

    const results = [];
    Object.keys(byYear).sort().forEach(yearStr => {
        const year = parseInt(yearStr, 10);
        if (yearMonthKeys(year).every(k => remaining[k])) {
            results.push({ type: 'year', key: yearStr, label: yearStr });
            yearMonthKeys(year).forEach(k => delete remaining[k]);
            return;
        }
        for (let q = 1; q <= 4; q++) {
            const qMonths = quarterMonthKeys(year, q);
            if (qMonths.every(k => remaining[k])) {
                results.push({ type: 'quarter', key: yearStr + '-Q' + q, label: 'Q' + q + ' ' + yearStr });
                qMonths.forEach(k => delete remaining[k]);
            }
        }
    });
    Object.keys(remaining).sort().forEach(k => {
        results.push({ type: 'month', key: k, label: monthKeyLabel(k) });
    });
    return results;
}

// Chip type -> filter category. month/daterange share the 'date' category so
// they OR together in passesFilters instead of AND-ing as separate types.
function filterCategory(type) {
    return (type === 'month' || type === 'daterange') ? 'date' : type;
}

// Convert an aggregated {type,key,label} entry into an activeFilters chip.
// Quarter/year become 'YYYY-MM..YYYY-MM' range strings under the 'month' type
// (reusing monthMatches), matching the chip data model in the plan.
function aggregateEntryToChip(entry) {
    if (entry.type === 'year') {
        return { text: entry.key + '-01..' + entry.key + '-12', type: 'month', mode: 'include', displayText: entry.label };
    }
    if (entry.type === 'quarter') {
        const parts = entry.key.split('-Q');
        const startM = (parseInt(parts[1], 10) - 1) * 3 + 1;
        return {
            text: parts[0] + '-' + pad2(startM) + '..' + parts[0] + '-' + pad2(startM + 2),
            type: 'month', mode: 'include', displayText: entry.label
        };
    }
    return { text: entry.key, type: 'month', mode: 'include', displayText: monthKeyLabel(entry.key) };
}

// Restore a display label for a 'month'-type chip after a hash reload: plain
// month, a full-year range, a quarter range, or a generic month range.
function monthChipDisplayText(text) {
    if (!text.includes('..')) return monthKeyLabel(text);
    const [start, end] = text.split('..');
    const [sy, sm] = start.split('-');
    const [ey, em] = end.split('-');
    if (sy === ey && sm === '01' && em === '12') return sy;               // Year
    if (sy === ey) {
        const smN = parseInt(sm, 10), emN = parseInt(em, 10);
        if (emN - smN === 2 && (smN - 1) % 3 === 0) {                     // Quarter
            return 'Q' + quarterOf(smN) + ' ' + sy;
        }
    }
    return monthKeyLabel(start) + ' – ' + monthKeyLabel(end);            // Generic range
}

// Cross-year-aware label for a 'daterange' chip ('YYYY-MM-DD..YYYY-MM-DD').
// Year shown on both ends when they differ, once (on end) when they match.
function dateRangeDisplayText(text) {
    const [a, b] = text.split('..');
    const ap = a.split('-'), bp = b.split('-');
    const startPart = MONTH_NAMES_SHORT[parseInt(ap[1], 10) - 1] + ' ' + parseInt(ap[2], 10) +
        (ap[0] !== bp[0] ? ', ' + ap[0] : '');
    return startPart + ' – ' + MONTH_NAMES_SHORT[parseInt(bp[1], 10) - 1] + ' ' + parseInt(bp[2], 10) + ', ' + bp[0];
}

function parseTypedDate(str) {
    str = (str || '').trim();
    if (!str) return null;
    const m1 = str.match(/^(\d{4})-(\d{1,2})-(\d{1,2})$/);
    if (m1) return { y: +m1[1], m: +m1[2], d: +m1[3] };
    const m2 = str.match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})$/);
    if (m2) return { y: +m2[3], m: +m2[1], d: +m2[2] };
    const d = new Date(str);
    if (!isNaN(d.getTime())) return { y: d.getFullYear(), m: d.getMonth() + 1, d: d.getDate() };
    return null;
}
function fmtDrillDate(dt) { return MONTH_NAMES_SHORT[dt.m - 1] + ' ' + dt.d + ', ' + dt.y; }
function dateToKey(dt) { return dt.y + '-' + pad2(dt.m) + '-' + pad2(dt.d); }

// ========== REUSABLE COMPONENTS ==========

// Sortable merchant/group section component
// Reusable for Credits, Excluded, and Category sections
const MerchantSection = defineComponent({
    name: 'MerchantSection',
    props: {
        sectionKey: { type: String, required: true },
        title: { type: String, required: true },
        items: { type: Array, required: true },
        totalLabel: { type: String, default: 'Total' },
        showTotal: { type: Boolean, default: false },
        totalAmount: { type: Number, default: 0 },
        subtitle: { type: String, default: '' },
        creditMode: { type: Boolean, default: false },
        // Category mode adds % column and different formatting
        categoryMode: { type: Boolean, default: false },
        // Subcategory mode: rows are subcategories, not merchants
        subcategoryMode: { type: Boolean, default: false },
        categoryTotal: { type: Number, default: 0 },
        grandTotal: { type: Number, default: 0 },
        grossSpending: { type: Number, default: 0 },
        totalUnfilteredSpending: { type: Number, default: 0 },
        incomeTotal: { type: Number, default: 0 },
        investmentTotal: { type: Number, default: 0 },
        typeTotals: { type: Object, default: null },
        numMonths: { type: Number, default: 12 },
        headerColor: { type: String, default: '' },
        // Injected from parent
        collapsedSections: { type: Object, required: true },
        sortConfig: { type: Object, required: true },
        expandedItems: { type: Object, required: true },
        extraFieldMatches: { type: Object, default: () => new Set() },
        toggleSection: { type: Function, required: true },
        toggleSort: { type: Function, required: true },
        formatCurrency: { type: Function, required: true },
        formatDate: { type: Function, required: true },
        formatPct: { type: Function, default: null },
        addFilter: { type: Function, required: true },
        highlightDescription: { type: Function, default: (d) => d },
        tagColor: { type: Function, default: () => '#888' }
    },
    computed: {
        // Label spans first 4 columns in all modes
        colSpan() {
            return 4;
        },
        // Transaction row spans all columns
        totalColSpan() {
            return this.categoryMode ? 6 : 5;
        }
    },
    template: `
        <section :class="[sectionKey.replace(':', '-') + '-section', 'category-section', { 'is-collapsed': collapsedSections.has(sectionKey) }]" :data-testid="'section-' + sectionKey.replace(':', '-')">
            <div class="section-header" @click="toggleSection(sectionKey)">
                <h2>
                    <span class="toggle">{{ collapsedSections.has(sectionKey) ? '▶' : '▼' }}</span>
                    <span v-if="headerColor" class="category-dot" :style="{ backgroundColor: headerColor, '--dot-color': headerColor }"></span>
                    {{ title }}
                </h2>
                <span class="section-total">
                    <template v-if="categoryMode">
                        <span class="section-monthly" :class="{ 'negative-amount': totalAmount < 0 }">{{ formatCurrency(totalAmount / numMonths) }}/mo</span> ·
                        <span class="section-ytd" :class="{ 'negative-amount': totalAmount < 0 }">{{ formatCurrency(totalAmount) }}</span>
                        <span class="section-pct" v-if="typeTotals">
                            <span v-if="typeTotals.spending > 0 && totalUnfilteredSpending > 0">({{ formatPct(typeTotals.spending, totalUnfilteredSpending) }})</span>
                            <span v-if="typeTotals.income > 0 && incomeTotal > 0" class="income-pct">({{ formatPct(typeTotals.income, incomeTotal) }} income)</span>
                            <span v-if="typeTotals.investment > 0 && investmentTotal > 0" class="investment-pct">({{ formatPct(typeTotals.investment, investmentTotal) }} invest)</span>
                        </span>
                        <span class="section-pct" v-else-if="grossSpending > 0">({{ formatPct(totalAmount, grossSpending) }})</span>
                    </template>
                    <template v-else>
                        <span v-if="showTotal" class="section-ytd credit-amount">+{{ formatCurrency(totalAmount) }}</span>
                        <span class="section-pct">{{ subtitle }}</span>
                    </template>
                </span>
            </div>
            <div class="section-content" :class="{ collapsed: collapsedSections.has(sectionKey) }">
                <div class="table-wrapper">
                    <table>
                        <thead>
                            <tr>
                                <th @click.stop="toggleSort(sectionKey, 'merchant')"
                                    :class="getSortClass('merchant')">{{ creditMode ? 'Source' : (subcategoryMode ? 'Subcategory' : 'Merchant') }}</th>
                                <th @click.stop="toggleSort(sectionKey, 'subcategory')"
                                    :class="getSortClass('subcategory')">{{ subcategoryMode ? 'Merchants' : (categoryMode ? 'Subcategory' : 'Category') }}</th>
                                <!-- Category mode: Count then Tags; Other modes: Tags then Count -->
                                <th v-if="categoryMode" class="count-col" @click.stop="toggleSort(sectionKey, 'count')"
                                    :class="getSortClass('count')">Count</th>
                                <th>Tags</th>
                                <th v-if="!categoryMode" class="count-col" @click.stop="toggleSort(sectionKey, 'count')"
                                    :class="getSortClass('count')">Count</th>
                                <th class="money" @click.stop="toggleSort(sectionKey, 'total')"
                                    :class="getSortClass('total')">{{ creditMode ? 'Amount' : 'Total' }}</th>
                                <th v-if="categoryMode" class="pct" @click.stop="toggleSort(sectionKey, 'total')"
                                    :class="getSortClass('total')">%</th>
                            </tr>
                        </thead>
                        <tbody>
                            <template v-for="(item, idx) in items" :key="item.id || idx">
                                <tr class="merchant-row"
                                    :class="{ expanded: isExpanded(item.id || idx) }"
                                    :data-testid="'merchant-row-' + (item.id || item.displayName || item.merchant || idx)"
                                    @click="toggleExpand(item.id || idx)">
                                    <td class="merchant" :class="{ clickable: categoryMode }">
                                        <span class="chevron">{{ isExpanded(item.id || idx) ? '▼' : '▶' }}</span>
                                        <span class="merchant-name" @click.stop="categoryMode ? addFilter(item.id, subcategoryMode ? 'subcategory' : 'merchant', item.displayName) : null">
                                            {{ item.displayName || item.merchant }}
                                        </span>
                                        <span v-if="item.matchInfo || item.viewInfo" class="match-info-trigger"
                                                      @click.stop="togglePopup($event)">info
                                            <span class="match-info-popup" ref="popup" @click.stop>
                                                <button class="popup-close" @click="closePopup($event)">&times;</button>
                                                <div class="popup-header">Why This Matched</div>
                                                <template v-if="item.matchInfo">
                                                    <div v-if="item.matchInfo.explanation" class="popup-explanation">{{ item.matchInfo.explanation }}</div>
                                                    <div class="popup-section">
                                                        <div class="popup-section-header">Merchant Pattern</div>
                                                        <div class="popup-code">{{ item.matchInfo.pattern }}</div>
                                                    </div>
                                                    <div class="popup-section">
                                                        <div class="popup-section-header">Assigned To</div>
                                                        <div class="popup-row">
                                                            <span class="popup-label">Merchant:</span>
                                                            <span class="popup-value">{{ item.matchInfo.assignedMerchant }}</span>
                                                        </div>
                                                        <div class="popup-row">
                                                            <span class="popup-label">Category:</span>
                                                            <span class="popup-value">{{ item.matchInfo.assignedCategory }} / {{ item.matchInfo.assignedSubcategory }}</span>
                                                        </div>
                                                        <div v-if="item.matchInfo.assignedTags && item.matchInfo.assignedTags.length" class="popup-row popup-tags-section">
                                                            <span class="popup-label">Tags:</span>
                                                            <span class="popup-value">
                                                                <template v-if="item.matchInfo.tagSources && Object.keys(item.matchInfo.tagSources).length">
                                                                    <div v-for="tag in item.matchInfo.assignedTags" :key="tag" class="popup-tag-item">
                                                                        <span class="popup-tag-name">{{ tag }}</span>
                                                                        <span v-if="item.matchInfo.tagSources[tag]" class="popup-tag-source">
                                                                            from [{{ item.matchInfo.tagSources[tag].rule }}]
                                                                        </span>
                                                                    </div>
                                                                </template>
                                                                <template v-else>{{ item.matchInfo.assignedTags.join(', ') }}</template>
                                                            </span>
                                                        </div>
                                                    </div>
                                                </template>
                                                <template v-if="item.viewInfo && item.viewInfo.filterExpr">
                                                    <div class="popup-section">
                                                        <div class="popup-section-header">View Filter ({{ item.viewInfo.viewName }})</div>
                                                        <div v-if="item.viewInfo.explanation" class="popup-explanation" style="margin-top: 0.3em;">{{ item.viewInfo.explanation }}</div>
                                                        <div class="popup-code">{{ item.viewInfo.filterExpr }}</div>
                                                    </div>
                                                </template>
                                                <div v-if="item.matchInfo" class="popup-source">From: {{ item.matchInfo.source === 'user' ? 'merchants.rules' : item.matchInfo.source }}</div>
                                            </span>
                                        </span>
                                    </td>
                                    <td class="category" :class="{ clickable: categoryMode && !subcategoryMode }"
                                        @click.stop="categoryMode && !subcategoryMode && addFilter(item.subcategory, 'subcategory')">
                                        <span v-if="subcategoryMode && item.merchants" class="merchant-list-trigger"
                                              @click.stop="togglePopup($event)">
                                            {{ item.subcategory }}
                                            <span class="match-info-popup" @click.stop>
                                                <button class="popup-close" @click="closePopup($event)">&times;</button>
                                                <div class="popup-header">Merchants</div>
                                                <div v-for="m in item.merchants" :key="m.id" class="popup-row">
                                                    <span class="popup-label">{{ m.displayName }}</span>
                                                    <span class="popup-value">{{ formatCurrency(m.filteredTotal) }}</span>
                                                </div>
                                            </span>
                                        </span>
                                        <span v-else>{{ item.subcategory }}</span>
                                    </td>
                                    <!-- Category mode: Count then Tags; Other modes: Tags then Count -->
                                    <td v-if="categoryMode" class="count-col" data-testid="merchant-count">{{ item.filteredCount || item.count }}</td>
                                    <td class="tags-cell">
                                        <span v-for="tag in getTags(item)" :key="tag" class="tag-badge" data-testid="tag-badge"
                                              :style="{ borderColor: tagColor(tag), color: tagColor(tag) }"
                                              @click.stop="addFilter(tag, 'tag')">{{ tag }}</span>
                                    </td>
                                    <td v-if="!categoryMode" class="count-col" data-testid="merchant-count">{{ item.filteredCount || item.count }}</td>
                                    <td class="money" :class="getAmountClass(item)" data-testid="merchant-total">
                                        {{ formatAmount(item) }}
                                    </td>
                                    <td v-if="categoryMode" class="pct">{{ formatPct(item.filteredTotal || item.total, categoryTotal || totalAmount) }}</td>
                                </tr>
                                <tr v-for="txn in getTransactions(item)"
                                    :key="txn.id"
                                    class="txn-row"
                                    :class="{ hidden: !isExpanded(item.id || idx) }">
                                    <td :colspan="totalColSpan">
                                        <div class="txn-detail" :class="{ 'has-extra': (txn.extra_fields && Object.keys(txn.extra_fields).length) || txn.original_description }">
                                            <span v-if="(txn.extra_fields && Object.keys(txn.extra_fields).length) || txn.original_description"
                                                  class="extra-fields-trigger"
                                                  :class="{ 'match-highlight': extraFieldMatches.has(txn.id) }"
                                                  @click.stop="togglePopup($event)">+{{ (txn.extra_fields ? Object.keys(txn.extra_fields).length : 0) + (txn.original_description ? 1 : 0) }}
                                                <span class="match-info-popup" @click.stop>
                                                    <button class="popup-close" @click="closePopup($event)">&times;</button>
                                                    <div class="popup-header">Transaction Details</div>
                                                    <div v-if="txn.original_description" class="popup-row">
                                                        <span class="popup-label">Original</span>
                                                        <span class="popup-value">{{ txn.original_description }}</span>
                                                    </div>
                                                    <div v-for="(value, key) in txn.extra_fields" :key="key" class="popup-row">
                                                        <span class="popup-label">{{ formatFieldKey(key) }}</span>
                                                        <span v-if="Array.isArray(value)" class="popup-value popup-list">
                                                            <span v-for="(item, i) in value" :key="i" class="popup-list-item">{{ item }}</span>
                                                        </span>
                                                        <span v-else class="popup-value">{{ formatFieldValue(value) }}</span>
                                                    </div>
                                                </span>
                                            </span>
                                            <span class="txn-date">{{ formatDate(txn.date, txn.month, getTransactionYears(item)) }}</span>
                                            <span class="txn-desc"><span v-if="txn.source" class="txn-source" :class="txn.source.toLowerCase()">{{ txn.source }}</span> <span v-html="highlightDescription(txn.description)"></span></span>
                                            <span class="txn-badges">
                                                <span v-for="tag in [...(txn.tags || [])].sort()"
                                                      :key="tag"
                                                      class="tag-badge"
                                                      data-testid="tag-badge"
                                                      :style="{ borderColor: tagColor(tag), color: tagColor(tag) }"
                                                      @click.stop="addFilter(tag, 'tag')">{{ tag }}</span>
                                            </span>
                                            <span class="txn-amount" :class="getTxnAmountClass(txn)">
                                                {{ formatTxnAmount(txn) }}
                                            </span>
                                        </div>
                                    </td>
                                </tr>
                            </template>
                        </tbody>
                        <tfoot>
                            <tr class="total-row">
                                <td :colspan="colSpan">{{ totalLabel }}</td>
                                <td class="money" :class="{ 'credit-amount': creditMode }">
                                    {{ creditMode ? '+' + formatCurrency(totalAmount) : formatCurrency(totalAmount) }}
                                </td>
                                <td v-if="categoryMode" class="pct">100%</td>
                            </tr>
                        </tfoot>
                    </table>
                </div>
            </div>
        </section>
    `,
    created() {
        // Non-reactive cache for getTransactionYears (see method for rationale).
        this._txnYearsCache = new WeakMap();
    },
    methods: {
        getSortClass(column) {
            const cfg = this.sortConfig[this.sectionKey];
            return {
                'sorted-asc': cfg?.column === column && cfg?.dir === 'asc',
                'sorted-desc': cfg?.column === column && cfg?.dir === 'desc'
            };
        },
        toggleExpand(id) {
            if (this.expandedItems.has(id)) {
                this.expandedItems.delete(id);
            } else {
                this.expandedItems.add(id);
            }
        },
        isExpanded(id) {
            return this.expandedItems.has(id);
        },
        togglePopup(event) {
            const icon = event.currentTarget;
            const popup = icon.querySelector('.match-info-popup');
            if (!popup) return;

            // Close any other open popups first
            document.querySelectorAll('.match-info-popup.visible').forEach(p => {
                if (p !== popup) p.classList.remove('visible');
            });

            if (popup.classList.contains('visible')) {
                popup.classList.remove('visible');
            } else {
                // Center in viewport
                popup.style.left = '50%';
                popup.style.top = '50%';
                popup.style.transform = 'translate(-50%, -50%)';
                popup.classList.add('visible');
            }
        },
        closePopup(event) {
            event.stopPropagation();
            const popup = event.currentTarget.closest('.match-info-popup');
            if (popup) popup.classList.remove('visible');
        },
        getTags(item) {
            let tags;
            if (item.filteredTxns) {
                tags = [...new Set(item.filteredTxns.flatMap(t => t.tags || []))];
            } else {
                tags = item.tags || [];
            }
            return [...tags].sort((a, b) => a.localeCompare(b));
        },
        getTransactions(item) {
            const txns = item.filteredTxns || item.transactions || [];
            // Sort by date descending (month YYYY-MM + day from date MM/DD)
            return [...txns].sort((a, b) => {
                const dateA = `${a.month || '0000-00'}-${(a.date || '00/00').slice(3, 5)}`;
                const dateB = `${b.month || '0000-00'}-${(b.date || '00/00').slice(3, 5)}`;
                return dateB.localeCompare(dateA);
            });
        },
        // True when this item's transactions span more than one calendar year,
        // so transaction rows should show ", YYYY". Computed once per item and
        // cached in a WeakMap (keyed by the item object, GC'd when the item is
        // recreated on the next filtered-view recompute) to avoid re-deriving
        // it for every transaction row.
        getTransactionYears(item) {
            if (this._txnYearsCache.has(item)) return this._txnYearsCache.get(item);
            const years = new Set();
            for (const t of (item.filteredTxns || item.transactions || [])) {
                if (t.month) years.add(t.month.slice(0, 4));
            }
            const multi = years.size > 1;
            this._txnYearsCache.set(item, multi);
            return multi;
        },
        getAmountClass(item) {
            if (this.creditMode) return 'credit-amount';
            const tags = item.tags || [];
            const total = item.total || item.filteredTotal || 0;
            if (isIncome(tags)) return 'income-amount';
            if (total < 0 && !isIncome(tags)) return 'negative-amount';
            return '';
        },
        getTxnAmountClass(txn) {
            if (this.creditMode) return 'credit-amount';
            const tags = txn.tags || [];
            if (isIncome(tags)) return 'income-amount';
            if (txn.amount < 0 && !isIncome(tags)) return 'negative-amount';
            return '';
        },
        formatAmount(item) {
            if (this.creditMode) {
                return '+' + this.formatCurrency(item.creditAmount || Math.abs(item.filteredTotal || item.total || 0));
            }
            const tags = item.tags || [];
            const total = item.total || item.filteredTotal || 0;
            if (isIncome(tags)) {
                return '+' + this.formatCurrency(Math.abs(total));
            }
            return this.formatCurrency(total);
        },
        formatTxnAmount(txn) {
            if (this.creditMode) {
                return '+' + this.formatCurrency(Math.abs(txn.amount));
            }
            const tags = txn.tags || [];
            if (isIncome(tags)) {
                return '+' + this.formatCurrency(Math.abs(txn.amount));
            }
            return this.formatCurrency(txn.amount);
        },
        formatFieldKey(key) {
            // Convert snake_case to Title Case
            return key.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
        },
        formatFieldValue(value) {
            if (typeof value === 'number') {
                return Number.isInteger(value) ? value : value.toFixed(2);
            }
            if (Array.isArray(value)) {
                return value.join(', ');
            }
            return String(value);
        },
        getMatchTooltip(item) {
            const matchInfo = item.matchInfo;
            if (!matchInfo) return '';
            const parts = [];
            if (matchInfo.pattern) {
                parts.push(`Pattern: ${matchInfo.pattern}`);
            }
            if (matchInfo.source) {
                parts.push(`Source: ${matchInfo.source}`);
            }
            return parts.join('\n');
        }
    }
});

// Drill-down calendar widget for the custom Start/End date inputs.
// Three views: 'days' (Month + Year drill buttons + day grid), 'months'
// (Year drill + 3x4 month grid), 'years' (paginated 3x4 year grid).
// Ported from createDrillCalendar() in the datefilter.html prototype.
const DrillCalendar = defineComponent({
    name: 'DrillCalendar',
    props: {
        modelValue: { type: Object, default: null }, // { y, m, d } | null
        testidPrefix: { type: String, default: 'cal' },
        alignRight: { type: Boolean, default: false },
        placeholder: { type: String, default: '' }
    },
    emits: ['update:modelValue'],
    data() {
        const now = new Date();
        return {
            view: 'days',
            viewYear: now.getFullYear(),
            viewMonth: now.getMonth() + 1,
            yearPageStart: 1,
            calOpen: false,
            textValue: this.modelValue ? fmtDrillDate(this.modelValue) : '',
            invalid: false
        };
    },
    computed: {
        selected() { return this.modelValue; },
        dowLabels() { return ['S', 'M', 'T', 'W', 'T', 'F', 'S']; },
        monthNamesShort() { return MONTH_NAMES_SHORT; },
        monthNamesLong() { return MONTH_NAMES_LONG; },
        dayCells() {
            const y = this.viewYear, m = this.viewMonth;
            const blanks = new Date(y, m - 1, 1).getDay();
            const total = new Date(y, m, 0).getDate();
            const t = new Date();
            const cells = [];
            for (let i = 0; i < blanks; i++) cells.push({ empty: true, key: 'b' + i });
            for (let d = 1; d <= total; d++) {
                cells.push({
                    empty: false, day: d, key: 'd' + d,
                    selected: this.selected && this.selected.y === y && this.selected.m === m && this.selected.d === d,
                    today: t.getFullYear() === y && (t.getMonth() + 1) === m && t.getDate() === d
                });
            }
            return cells;
        },
        yearPageCells() {
            const cells = [];
            for (let y = this.yearPageStart; y <= this.yearPageStart + 11; y++) cells.push(y);
            return cells;
        },
        yearPageLabel() { return this.yearPageStart + '–' + (this.yearPageStart + 11); }
    },
    watch: {
        modelValue(v) {
            this.textValue = v ? fmtDrillDate(v) : '';
            this.invalid = false;
        }
    },
    methods: {
        alignPage(y) { return Math.floor((y - 1) / 12) * 12 + 1; },
        openCal() {
            const now = new Date();
            const base = this.selected || { y: now.getFullYear(), m: now.getMonth() + 1, d: now.getDate() };
            this.viewYear = base.y;
            this.viewMonth = base.m;
            this.yearPageStart = this.alignPage(this.viewYear);
            this.view = 'days';
            this.calOpen = true;
        },
        toggleCal() { if (this.calOpen) this.calOpen = false; else this.openCal(); },
        onTextChange() {
            const parsed = parseTypedDate(this.textValue);
            if (parsed) {
                this.textValue = fmtDrillDate(parsed);
                this.invalid = false;
                this.$emit('update:modelValue', parsed);
            } else if (this.textValue.trim() === '') {
                this.invalid = false;
                this.$emit('update:modelValue', null);
            } else {
                this.invalid = true;
            }
        },
        prevMonth() { this.viewMonth--; if (this.viewMonth < 1) { this.viewMonth = 12; this.viewYear--; } },
        nextMonth() { this.viewMonth++; if (this.viewMonth > 12) { this.viewMonth = 1; this.viewYear++; } },
        drillToMonths() { this.view = 'months'; },
        drillToYears() { this.yearPageStart = this.alignPage(this.viewYear); this.view = 'years'; },
        pickDay(d) {
            const sel = { y: this.viewYear, m: this.viewMonth, d };
            this.textValue = fmtDrillDate(sel);
            this.invalid = false;
            this.calOpen = false;
            this.$emit('update:modelValue', sel);
        },
        pickMonth(i) { this.viewMonth = i + 1; this.view = 'days'; },
        pickYear(y) { this.viewYear = y; this.view = 'months'; },
        prevMonthsYear() { this.viewYear--; },
        nextMonthsYear() { this.viewYear++; },
        prevYearPage() { this.yearPageStart -= 12; },
        nextYearPage() { this.yearPageStart += 12; },
        onDocClick(e) {
            if (this.calOpen && this.$el && !this.$el.contains(e.target)) this.calOpen = false;
        }
    },
    mounted() { document.addEventListener('click', this.onDocClick); },
    unmounted() { document.removeEventListener('click', this.onDocClick); },
    template: `
        <div class="drill-calendar" :class="{ 'align-right': alignRight }" @click.stop>
            <div class="date-input">
                <input type="text" :class="{ invalid }" v-model="textValue" @change="onTextChange"
                       :data-testid="testidPrefix + '-text'" :placeholder="placeholder">
                <button type="button" class="cal-btn" :data-testid="testidPrefix + '-cal-btn'" @click="toggleCal">📅</button>
            </div>
            <div class="cal-popover" v-if="calOpen" :data-testid="testidPrefix + '-cal'">
                <template v-if="view === 'days'">
                    <div class="cal-header">
                        <button type="button" class="cal-nav" @click="prevMonth">‹</button>
                        <div class="cal-title">
                            <button type="button" class="cal-drill" @click="drillToMonths">{{ monthNamesLong[viewMonth - 1] }}</button>
                            <button type="button" class="cal-drill" @click="drillToYears">{{ viewYear }}</button>
                        </div>
                        <button type="button" class="cal-nav" @click="nextMonth">›</button>
                    </div>
                    <div class="cal-dow"><span v-for="(d, i) in dowLabels" :key="i">{{ d }}</span></div>
                    <div class="cal-days">
                        <template v-for="cell in dayCells" :key="cell.key">
                            <span v-if="cell.empty" class="cal-day empty"></span>
                            <button v-else type="button" class="cal-day"
                                    :class="{ selected: cell.selected, today: cell.today }"
                                    :data-testid="testidPrefix + '-day-' + cell.day"
                                    @click="pickDay(cell.day)">{{ cell.day }}</button>
                        </template>
                    </div>
                </template>
                <template v-else-if="view === 'months'">
                    <div class="cal-header">
                        <button type="button" class="cal-nav" @click="prevMonthsYear">‹</button>
                        <div class="cal-title"><button type="button" class="cal-drill" @click="drillToYears">{{ viewYear }}</button></div>
                        <button type="button" class="cal-nav" @click="nextMonthsYear">›</button>
                    </div>
                    <div class="cal-months">
                        <button v-for="(name, i) in monthNamesShort" :key="i" type="button" class="cal-cell"
                                :class="{ selected: selected && selected.y === viewYear && selected.m === i + 1 }"
                                @click="pickMonth(i)">{{ name }}</button>
                    </div>
                </template>
                <template v-else>
                    <div class="cal-header">
                        <button type="button" class="cal-nav" @click="prevYearPage">‹</button>
                        <div class="cal-title">{{ yearPageLabel }}</div>
                        <button type="button" class="cal-nav" @click="nextYearPage">›</button>
                    </div>
                    <div class="cal-years">
                        <button v-for="y in yearPageCells" :key="y" type="button" class="cal-cell"
                                :class="{ selected: selected && selected.y === y }"
                                @click="pickYear(y)">{{ y }}</button>
                    </div>
                </template>
            </div>
        </div>
    `
});

// Category colors for charts
const CATEGORY_COLORS = [
    '#4facfe', '#00f2fe', '#4dffd2', '#ffa94d', '#f5af19',
    '#f093fb', '#fa709a', '#ff6b6b', '#a855f7', '#3b82f6',
    '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#06b6d4'
];

// Category charts show the top N categories; the rest roll up into "Other"
// so totals still reflect all spending
const TOP_CATEGORY_COUNT = 10;
const OTHER_CATEGORY_LABEL = 'Other';
const OTHER_CATEGORY_COLOR = '#6b7280';

// Tag colors (distinct from category colors, warmer/earthier tones)
const TAG_COLORS = [
    '#e879f9', '#c084fc', '#a78bfa', '#818cf8', '#6366f1',
    '#f472b6', '#fb7185', '#f87171', '#fb923c', '#fbbf24',
    '#a3e635', '#4ade80', '#34d399', '#2dd4bf', '#22d3ee'
];

createApp({
    setup() {
        // ========== STATE ==========
        const activeFilters = ref([]);
        const expandedMerchants = reactive(new Set());
        const extraFieldMatches = reactive(new Set()); // Track transaction IDs that matched via extra_fields
        const collapsedSections = reactive(new Set());
        const searchQuery = ref('');
        const showAutocomplete = ref(false);
        const autocompleteIndex = ref(-1);
        const isScrolled = ref(false);
        const isDarkTheme = ref(true);
        const chartsCollapsed = ref(false);
        const detailsCollapsed = ref(false);
        const helpCollapsed = ref(true);
        const currentView = ref('category'); // 'category' or 'section'
        const groupByMode = ref('merchant'); // 'merchant' or 'subcategory'
        const sortConfig = reactive({}); // { 'cat:Food': { column: 'total', dir: 'desc' } }
        const includeNegativeTotals = ref(false); // show categories with negative filteredTotal

        // Chart refs
        const monthlyChart = ref(null);
        const categoryPieChart = ref(null);
        const categoryByMonthChart = ref(null);

        // Chart instances
        let monthlyChartInstance = null;
        let pieChartInstance = null;
        let categoryMonthChartInstance = null;
        // Months actually plotted on the monthly trend chart; bar clicks index into
        // this, not availableMonths, since empty months and date filters drop months
        let monthlyChartMonths = [];

        // ========== COMPUTED ==========

        // Shortcut to spending data
        const spendingData = computed(() => window.spendingData || { sections: {}, numMonths: 12 });

        // Report title and subtitle
        const title = computed(() => spendingData.value.title || 'Financial Report');
        const subtitle = computed(() => {
            const data = spendingData.value;
            const sources = data.sources || [];
            return sources.length > 0 ? `Data from ${sources.join(', ')}` : '';
        });

        // Core filtering - returns sections with filtered merchants and transactions
        const filteredSections = computed(() => {
            const result = {};
            const data = spendingData.value;

            for (const [sectionId, section] of Object.entries(data.sections || {})) {
                const filteredMerchants = {};

                for (const [merchantId, merchant] of Object.entries(section.merchants || {})) {
                    // Filter transactions
                    const filteredTxns = merchant.transactions.filter(txn =>
                        passesFilters(txn, merchant)
                    );

                    if (filteredTxns.length > 0) {
                        const filteredTotal = filteredTxns.reduce((sum, t) => sum + t.amount, 0);
                        const months = new Set(filteredTxns.map(t => t.month));

                        filteredMerchants[merchantId] = {
                            ...merchant,
                            filteredTxns,
                            filteredTotal,
                            filteredCount: filteredTxns.length,
                            filteredMonths: months.size
                        };
                    }
                }

                if (Object.keys(filteredMerchants).length > 0) {
                    result[sectionId] = {
                        ...section,
                        filteredMerchants
                    };
                }
            }

            return result;
        });

        // Only sections with visible merchants
        const visibleSections = computed(() => filteredSections.value);

        // Category view with filtering applied
        const filteredCategoryView = computed(() => {
            const categoryView = spendingData.value.categoryView || {};
            const result = {};

            for (const [catName, category] of Object.entries(categoryView)) {
                const filteredSubcategories = {};
                let categoryTotal = 0;

                for (const [subcatName, subcat] of Object.entries(category.subcategories || {})) {
                    const filteredMerchants = {};
                    let subcatTotal = 0;

                    for (const [merchantId, merchant] of Object.entries(subcat.merchants || {})) {
                        // Filter transactions
                        const filteredTxns = (merchant.transactions || []).filter(txn =>
                            passesFilters(txn, merchant)
                        );

                        if (filteredTxns.length > 0) {
                            const filteredTotal = filteredTxns.reduce((sum, t) => sum + t.amount, 0);
                            const months = new Set(filteredTxns.map(t => t.month));

                            filteredMerchants[merchantId] = {
                                ...merchant,
                                filteredTxns,
                                filteredTotal,
                                filteredCount: filteredTxns.length,
                                filteredMonths: months.size
                            };
                            subcatTotal += filteredTotal;
                        }
                    }

                    if (Object.keys(filteredMerchants).length > 0) {
                        filteredSubcategories[subcatName] = {
                            ...subcat,
                            filteredMerchants,
                            filteredTotal: subcatTotal
                        };
                        categoryTotal += subcatTotal;
                    }
                }

                if (Object.keys(filteredSubcategories).length > 0) {
                    result[catName] = {
                        ...category,
                        filteredSubcategories,
                        filteredTotal: categoryTotal
                    };
                }
            }

            // Create flattened sorted merchant list for each category
            // Access sortConfig keys to ensure Vue tracks this as a dependency
            const sortKeys = Object.keys(sortConfig);
            for (const [catName, category] of Object.entries(result)) {
                const key = 'cat:' + catName;
                const cfg = sortConfig[key] || { column: 'total', dir: 'desc' };

                // Flatten all merchants from all subcategories into one array
                const allMerchants = [];
                for (const [subName, subcat] of Object.entries(category.filteredSubcategories || {})) {
                    for (const [merchantId, merchant] of Object.entries(subcat.filteredMerchants || {})) {
                        allMerchants.push({
                            id: merchantId,
                            subcategory: subName,
                            ...merchant
                        });
                    }
                }

                // Sort all merchants together
                allMerchants.sort((a, b) => {
                    let vA, vB;
                    switch (cfg.column) {
                        case 'merchant':
                            vA = a.displayName.toLowerCase();
                            vB = b.displayName.toLowerCase();
                            break;
                        case 'subcategory':
                            vA = a.subcategory.toLowerCase();
                            vB = b.subcategory.toLowerCase();
                            break;
                        case 'count':
                            vA = a.filteredCount;
                            vB = b.filteredCount;
                            break;
                        default:
                            vA = a.filteredTotal;
                            vB = b.filteredTotal;
                    }
                    if (typeof vA === 'string') {
                        return cfg.dir === 'asc' ? vA.localeCompare(vB) : vB.localeCompare(vA);
                    }
                    return cfg.dir === 'asc' ? vA - vB : vB - vA;
                });

                category.sortedMerchants = allMerchants;
            }

            // Sort categories by total descending
            return Object.fromEntries(
                Object.entries(result).sort((a, b) => b[1].filteredTotal - a[1].filteredTotal)
            );
        });

        // Categories to display - show all with non-negative totals
        // Negative totals (credits/refunds) are shown in the Credits section
        // When includeNegativeTotals is enabled, show all categories regardless of total sign
        const positiveCategoryView = computed(() => {
            const result = {};
            for (const [catName, category] of Object.entries(filteredCategoryView.value)) {
                if (includeNegativeTotals.value || category.filteredTotal >= 0) {
                    result[catName] = category;
                }
            }
            return result;
        });

        // Subcategory-grouped view: same data but grouped by subcategory instead of merchant
        const subcategoryGroupedView = computed(() => {
            const result = {};
            const sortKeys = Object.keys(sortConfig);

            for (const [catName, category] of Object.entries(positiveCategoryView.value)) {
                const key = 'cat:' + catName;
                const cfg = sortConfig[key] || { column: 'total', dir: 'desc' };

                // Build subcategory array from filteredSubcategories
                const allSubcategories = [];
                for (const [subName, subcat] of Object.entries(category.filteredSubcategories || {})) {
                    // Collect all merchants in this subcategory
                    const merchants = [];
                    for (const [merchantId, merchant] of Object.entries(subcat.filteredMerchants || {})) {
                        merchants.push({
                            id: merchantId,
                            ...merchant
                        });
                    }

                    // Sort merchants within subcategory by total
                    merchants.sort((a, b) => b.filteredTotal - a.filteredTotal);

                    allSubcategories.push({
                        id: subName,
                        displayName: subName,
                        subcategory: `${merchants.length} merchant${merchants.length !== 1 ? 's' : ''}`,
                        merchants: merchants,
                        filteredTotal: subcat.filteredTotal,
                        filteredCount: merchants.reduce((sum, m) => sum + (m.filteredCount || 0), 0),
                        // Flatten all transactions for the subcategory
                        filteredTxns: merchants.flatMap(m => m.filteredTxns || []),
                        // Collect all unique tags
                        tags: [...new Set(merchants.flatMap(m => m.tags || []))]
                    });
                }

                // Sort subcategories
                allSubcategories.sort((a, b) => {
                    let vA, vB;
                    switch (cfg.column) {
                        case 'merchant':
                            vA = a.displayName.toLowerCase();
                            vB = b.displayName.toLowerCase();
                            break;
                        case 'count':
                            vA = a.filteredCount;
                            vB = b.filteredCount;
                            break;
                        default:
                            vA = a.filteredTotal;
                            vB = b.filteredTotal;
                    }
                    if (typeof vA === 'string') {
                        return cfg.dir === 'asc' ? vA.localeCompare(vB) : vB.localeCompare(vA);
                    }
                    return cfg.dir === 'asc' ? vA - vB : vB - vA;
                });

                result[catName] = {
                    ...category,
                    sortedSubcategories: allSubcategories
                };
            }

            return result;
        });

        // Sort an array of groups/merchants by configurable column and direction
        // Works with arrays from creditMerchants, groupedExcluded, etc.
        function sortGroupedArray(items, configKey) {
            const cfg = sortConfig[configKey] || { column: 'total', dir: 'desc' };
            return [...items].sort((a, b) => {
                let vA, vB;
                switch (cfg.column) {
                    case 'merchant':
                        vA = (a.displayName || a.merchant || '').toLowerCase();
                        vB = (b.displayName || b.merchant || '').toLowerCase();
                        break;
                    case 'subcategory':
                        vA = (a.subcategory || '').toLowerCase();
                        vB = (b.subcategory || '').toLowerCase();
                        break;
                    case 'count':
                        vA = a.filteredCount || a.count || 0;
                        vB = b.filteredCount || b.count || 0;
                        break;
                    default:
                        vA = Math.abs(a.creditAmount || a.filteredTotal || a.total || 0);
                        vB = Math.abs(b.creditAmount || b.filteredTotal || b.total || 0);
                }
                if (typeof vA === 'string') {
                    return cfg.dir === 'asc' ? vA.localeCompare(vB) : vB.localeCompare(vA);
                }
                return cfg.dir === 'asc' ? vA - vB : vB - vA;
            });
        }

        // Credit merchants (negative totals, shown separately)
        // Excludes income and transfer tagged merchants
        //
        // Not rendered right now: the "Credits Applied" section that consumed this was
        // dropped from spending_report.html in ad9477e, though docs/reference.html still
        // documents it (refund tag -> shown in "Credits Applied", nets against spending).
        //
        // KNOWN BUG, fix before re-enabling: isExcludedFromSpending(merchant.tags) below is
        // a whole-merchant decision made from merchant.tags, which analyzer.py builds as the
        // UNION of every tag across that merchant's transactions. A single transfer-tagged
        // txn therefore discards all of the merchant's refunds - on a real dataset Amazon's
        // union is [monthly-bill, refund, transfer], so every Amazon credit vanishes.
        // filteredViewTotals and chartAggregations classify per transaction (txn.tags); this
        // must too. Two ways, and they mean different things:
        //   1. Net-negative merchants - net each merchant's per-txn spending against its
        //      per-txn credits, list it when net < 0. Preserves what the section means today
        //      and stays a short list, but "Total Credits" still won't equal the Credits KPI:
        //      a refund absorbed inside a net-positive merchant stays invisible. Only works
        //      when refunds arrive under their own merchant name (e.g. "Amazon Refund").
        //   2. Any merchant with credits - list it when sum(txn credits) > 0 and show that
        //      sum. Gives the identity sum(creditAmount) === filteredViewTotals.credits under
        //      every filter, so the section reconciles with the Credits KPI and Python's
        //      credits_total. Costs a longer list: a merchant can appear both as spending and
        //      as a credit (Amazon: $6,910 spent, refunds back).
        //
        // grandTotal below has the same merchant-union flaw and is likewise unrendered.
        const unsortedCreditMerchants = computed(() => {
            const credits = [];
            for (const [catName, category] of Object.entries(filteredCategoryView.value)) {
                for (const [subName, subcat] of Object.entries(category.filteredSubcategories || {})) {
                    for (const [merchantId, merchant] of Object.entries(subcat.filteredMerchants || {})) {
                        const tags = merchant.tags || [];
                        // Exclude merchants tagged as income/transfer/investment
                        if (isExcludedFromSpending(tags)) {
                            continue;
                        }
                        if (merchant.filteredTotal < 0) {
                            credits.push({
                                id: merchantId,
                                category: catName,
                                subcategory: subName,
                                ...merchant,
                                creditAmount: Math.abs(merchant.filteredTotal)
                            });
                        }
                    }
                }
            }
            return credits;
        });

        const creditMerchants = computed(() => sortGroupedArray(unsortedCreditMerchants.value, 'credits'));

        // Check if sections are defined
        const hasSections = computed(() => {
            const sections = spendingData.value.sections || {};
            return Object.keys(sections).length > 0;
        });

        // View mode with filtering applied (for By View tab)
        const filteredSectionView = computed(() => {
            const sections = spendingData.value.sections || {};
            const result = {};
			
            for (const [sectionId, section] of Object.entries(sections)) {
                const filteredMerchants = {};
                let sectionTotal = 0;

                for (const [merchantId, merchant] of Object.entries(section.merchants || {})) {
                    // Filter transactions
                    const filteredTxns = (merchant.transactions || []).filter(txn =>
                        passesFilters(txn, merchant)
                    );

                    if (filteredTxns.length > 0) {
                        const filteredTotal = filteredTxns.reduce((sum, t) => sum + t.amount, 0);
                        const months = new Set(filteredTxns.map(t => t.month));

                        filteredMerchants[merchantId] = {
                            ...merchant,
                            filteredTxns,
                            filteredTotal,
                            filteredCount: filteredTxns.length,
                            filteredMonths: months.size
                        };
                        sectionTotal += filteredTotal;
                    }
                }

                if (Object.keys(filteredMerchants).length > 0) {
                    result[sectionId] = {
                        ...section,
                        filteredMerchants,
                        filteredTotal: sectionTotal
                    };
                }
            }

            // Sort merchants within each section based on sortConfig
            // Access sortConfig keys to ensure Vue tracks this as a dependency
            const sortKeys = Object.keys(sortConfig);
            for (const [secId, section] of Object.entries(result)) {
                const key = 'sec:' + secId;
                const cfg = sortConfig[key] || { column: 'total', dir: 'desc' };
                section.filteredMerchants = sortMerchantEntries(section.filteredMerchants, cfg.column, cfg.dir);
            }

            return result;
        });

        // Sections to display - show all with non-negative totals
        // When includeNegativeTotals is enabled, show all sections regardless of total sign
        const positiveSectionView = computed(() => {
            const result = {};
            for (const [sectionId, section] of Object.entries(filteredSectionView.value)) {
                if (includeNegativeTotals.value || section.filteredTotal >= 0) {
                    result[sectionId] = section;
                }
            }
            return result;
        });

        // Count of hidden negative-total items (categories or sections, depending on view)
        // Used for the badge on the Include/Exclude Negatives toggle button
        const negativeTotalsCount = computed(() => {
            const source = currentView.value === 'section' ? filteredSectionView.value : filteredCategoryView.value;
            return Object.values(source).filter(item => item.filteredTotal < 0).length;
        });

        // Totals per section
        const sectionTotals = computed(() => {
            const totals = {};
            for (const [sectionId, section] of Object.entries(filteredSections.value)) {
                totals[sectionId] = Object.values(section.filteredMerchants)
                    .reduce((sum, m) => sum + m.filteredTotal, 0);
            }
            return totals;
        });

        // Grand total (from category view to avoid double-counting across sections)
        // Excludes income, transfer, and investment tagged transactions
        const grandTotal = computed(() => {
            let total = 0;
            for (const cat of Object.values(filteredCategoryView.value)) {
                for (const subcat of Object.values(cat.filteredSubcategories || cat.subcategories || {})) {
                    for (const merchant of Object.values(subcat.filteredMerchants || subcat.merchants || {})) {
                        const tags = merchant.tags || [];
                        // Exclude income/transfer/investment tagged merchants from spending
                        if (!isExcludedFromSpending(tags)) {
                            total += merchant.filteredTotal || merchant.total || 0;
                        }
                    }
                }
            }
            return total;
        });

        // Credits total (sum of all credit merchants, shown as positive)
        const creditsTotal = computed(() => {
            return creditMerchants.value.reduce((sum, m) => sum + m.creditAmount, 0);
        });

        // Filtered view totals - sum of ALL matching transactions
        // Simple: whatever matches the filters gets counted and categorized
        const filteredViewTotals = computed(() => {
            // Accumulate totals using same structure as categorizeAmount()
            const totals = {
                income: 0,
                investment: 0,
                transferIn: 0,
                transferOut: 0,
                spending: 0,
                credits: 0
            };
            let count = 0;

            // Count ALL transactions from ALL visible merchants
            for (const cat of Object.values(filteredCategoryView.value)) {
                for (const subcat of Object.values(cat.filteredSubcategories || cat.subcategories || {})) {
                    for (const merchant of Object.values(subcat.filteredMerchants || subcat.merchants || {})) {
                        const txns = merchant.filteredTxns || merchant.transactions || [];

                        for (const txn of txns) {
                            // Classify with the transaction's OWN tags. merchant.tags is a
                            // union of every tag across the merchant's transactions
                            // (analyzer.py builds it that way), so using it here would let a
                            // single income/transfer-tagged txn re-bucket all the others.
                            const c = categorizeAmount(txn.amount || 0, txn.tags || []);
                            totals.income += c.income;
                            totals.investment += c.investment;
                            totals.transferIn += c.transferIn;
                            totals.transferOut += c.transferOut;
                            totals.spending += c.spending;
                            totals.credits += c.credits;
                            count++;
                        }
                    }
                }
            }

            // Net transfers
            const transfers = totals.transferIn - totals.transferOut;

            // Context-aware net calculation:
            // - With income: cash flow (income - spending + credits)
            // - Without income: net spending (spending - credits)
            let net;
            if (totals.income > 0) {
                // Cash flow view
                net = calculateCashFlow(totals.income, totals.spending, totals.credits);
            } else {
                // Spending view - show net spending as positive
                net = totals.spending - totals.credits;
            }

            return {
                spending: totals.spending,
                credits: totals.credits,
                income: totals.income,
                investment: totals.investment,
                transfers,
                count,
                net,
                hasIncome: totals.income > 0  // For display formatting
            };
        });

        // Gross spending (before credits). categorizeAmount() already separates positive
        // spend from refunds per transaction, so this is the sum of spending-tagged
        // amounts - no need to net merchants out and add their credits back in.
        const grossSpending = computed(() => filteredViewTotals.value.spending);

        // Cash flow totals from data (excludes transfers and investments)
        const incomeTotal = computed(() => spendingData.value.incomeTotal || 0);
        const spendingTotal = computed(() => spendingData.value.spendingTotal || 0);
        const dataCreditsTotal = computed(() => spendingData.value.creditsTotal || 0);
        const cashFlow = computed(() => spendingData.value.cashFlow || 0);
        // Transfer totals (money moving between accounts)
        const transfersIn = computed(() => spendingData.value.transfersIn || 0);
        const transfersOut = computed(() => spendingData.value.transfersOut || 0);
        const transfersNet = computed(() => spendingData.value.transfersNet || 0);
        // Investment total (401K, IRA - excluded from spending)
        const investmentTotal = computed(() => spendingData.value.investmentTotal || 0);

        // Uncategorized total
        const uncategorizedTotal = computed(() => {
            return sectionTotals.value.unknown || 0;
        });

        // Income and Transfer counts from merchants by tag
        const incomeCount = computed(() => {
            let count = 0;
            for (const cat of Object.values(filteredCategoryView.value)) {
                for (const subcat of Object.values(cat.subcategories || {})) {
                    for (const merchant of Object.values(subcat.merchants || {})) {
                        if ((merchant.tags || []).includes('income')) {
                            count += (merchant.filteredTxns || merchant.transactions || []).length;
                        }
                    }
                }
            }
            return count;
        });

        const transfersCount = computed(() => {
            let count = 0;
            for (const cat of Object.values(filteredCategoryView.value)) {
                for (const subcat of Object.values(cat.subcategories || {})) {
                    for (const merchant of Object.values(subcat.merchants || {})) {
                        if ((merchant.tags || []).includes('transfer')) {
                            count += (merchant.filteredTxns || merchant.transactions || []).length;
                        }
                    }
                }
            }
            return count;
        });

        // All transactions grouped by merchant (for the Transactions section)
        const allTransactions = computed(() => {
            const transactions = [];
            for (const cat of Object.values(filteredCategoryView.value)) {
                for (const subcat of Object.values(cat.subcategories || {})) {
                    for (const merchant of Object.values(subcat.merchants || {})) {
                        const txns = merchant.filteredTxns || merchant.transactions || [];
                        for (const txn of txns) {
                            transactions.push({
                                ...txn,
                                merchant: merchant.displayName,
                                category: merchant.category,
                                subcategory: merchant.subcategory,
                                tags: merchant.tags || []
                            });
                        }
                    }
                }
            }
            return transactions;
        });

        // Group transactions by merchant helper (returns unsorted)
        function groupByMerchant(transactions) {
            const groups = {};
            for (const txn of transactions) {
                const key = txn.merchant;
                if (!groups[key]) {
                    groups[key] = {
                        merchant: txn.merchant,
                        category: txn.category,
                        subcategory: txn.subcategory,
                        tags: txn.tags || [],
                        transactions: [],
                        total: 0,
                        count: 0
                    };
                }
                groups[key].transactions.push(txn);
                groups[key].total += txn.amount;
                groups[key].count++;
            }
            return Object.values(groups);
        }

        const unsortedTransactions = computed(() => groupByMerchant(allTransactions.value));
        const groupedTransactions = computed(() => sortGroupedArray(unsortedTransactions.value, 'transactions'));
        const expandedTransactions = reactive(new Set());

        // Number of months in filter (for monthly averages)
        const numFilteredMonths = computed(() => {
            const monthFilters = activeFilters.value.filter(f =>
                filterCategory(f.type) === 'date' && f.mode === 'include'
            );
            if (monthFilters.length === 0) return spendingData.value.numMonths || 12;

            const months = new Set();
            monthFilters.forEach(f => {
                expandFilterMonths(f).forEach(m => months.add(m));
            });
            return months.size || 1;
        });

        // Expand a date filter chip (month, month-range, or daterange) into the
        // set of whole-month keys it touches. daterange chips are approximated
        // to whole months here (fine for averaging/chart bucketing; displayed
        // totals stay day-accurate via passesFilters).
        function expandFilterMonths(f) {
            if (f.type === 'daterange') {
                const [s, e] = f.text.split('..');
                return expandMonthRange(s.slice(0, 7) + '..' + e.slice(0, 7));
            }
            if (f.text.includes('..')) return expandMonthRange(f.text);
            return [f.text];
        }

        // Chart data aggregations - always uses categoryView for consistent data
        // Includes spending, income, credits, and investment; excludes transfers (money moving between accounts)
        const chartAggregations = computed(() => {
            const spendingByMonth = {};
            const incomeByMonth = {};
            const investmentByMonth = {};
            const creditsByMonth = {};
            const byCategory = {};  // Spending only (income doesn't have meaningful categories)
            const byCategoryByMonth = {};

            // Use categoryView which always has data (doesn't require views_file)
            const categoryView = filteredCategoryView.value;
            for (const [catName, category] of Object.entries(categoryView)) {
                for (const subcat of Object.values(category.filteredSubcategories || {})) {
                    for (const merchant of Object.values(subcat.filteredMerchants || {})) {
                        for (const txn of merchant.filteredTxns || []) {
                            // Classify with the transaction's OWN tags, not merchant.tags
                            // (a union across the merchant's txns) - see filteredViewTotals
                            const c = categorizeAmount(txn.amount, txn.tags || []);

                            // Track spending by month and category
                            if (c.spending > 0) {
                                spendingByMonth[txn.month] = (spendingByMonth[txn.month] || 0) + c.spending;
                                byCategory[catName] = (byCategory[catName] || 0) + c.spending;
                                if (!byCategoryByMonth[catName]) byCategoryByMonth[catName] = {};
                                byCategoryByMonth[catName][txn.month] =
                                    (byCategoryByMonth[catName][txn.month] || 0) + c.spending;
                            }

                            // Track income by month (no category breakdown)
                            if (c.income > 0) {
                                incomeByMonth[txn.month] = (incomeByMonth[txn.month] || 0) + c.income;
                            }

                            // Track investment by month
                            if (c.investment > 0) {
                                investmentByMonth[txn.month] = (investmentByMonth[txn.month] || 0) + c.investment;
                            }

                            // Track credits by month (refunds are part of cash flow)
                            if (c.credits > 0) {
                                creditsByMonth[txn.month] = (creditsByMonth[txn.month] || 0) + c.credits;
                            }

                            // Transfers excluded - they're just money moving between accounts
                        }
                    }
                }
            }

            return { spendingByMonth, incomeByMonth, investmentByMonth, creditsByMonth, byCategory, byCategoryByMonth };
        });

        // Map category names to colors (matches pie chart order)
        const categoryColorMap = computed(() => {
            const agg = chartAggregations.value;
            const entries = Object.entries(agg.byCategory)
                .filter(([_, v]) => v > 0)
                .sort((a, b) => b[1] - a[1]);
            const colorMap = {};
            entries.forEach((entry, idx) => {
                colorMap[entry[0]] = CATEGORY_COLORS[idx % CATEGORY_COLORS.length];
            });
            return colorMap;
        });

        // Map tag names to colors (sorted by frequency)
        const tagColorMap = computed(() => {
            const data = spendingData.value;
            const categoryView = data.categoryView || {};
            const tagCounts = {};

            // Count tag usage across all merchants
            for (const category of Object.values(categoryView)) {
                for (const subcat of Object.values(category.subcategories || {})) {
                    for (const merchant of Object.values(subcat.merchants || {})) {
                        for (const tag of (merchant.tags || [])) {
                            tagCounts[tag] = (tagCounts[tag] || 0) + 1;
                        }
                    }
                }
            }
            // Also count from excluded and refund transactions
            for (const txn of (data.excludedTransactions || [])) {
                for (const tag of (txn.tags || [])) {
                    tagCounts[tag] = (tagCounts[tag] || 0) + 1;
                }
            }
            for (const txn of (data.refundTransactions || [])) {
                for (const tag of (txn.tags || [])) {
                    tagCounts[tag] = (tagCounts[tag] || 0) + 1;
                }
            }

            // Sort by count descending and assign colors
            const sorted = Object.entries(tagCounts).sort((a, b) => b[1] - a[1]);
            const colorMap = {};
            sorted.forEach(([tag, _], idx) => {
                colorMap[tag] = TAG_COLORS[idx % TAG_COLORS.length];
            });
            return colorMap;
        });

        function tagColor(tag) {
            return tagColorMap.value[tag] || TAG_COLORS[0];
        }

        // Filtered months for charts (respects month filters)
        const filteredMonthsForCharts = computed(() => {
            const monthFilters = activeFilters.value.filter(f =>
                filterCategory(f.type) === 'date' && f.mode === 'include'
            );
            if (monthFilters.length === 0) return availableMonths.value;

            // Build set of included months
            const includedMonths = new Set();
            monthFilters.forEach(f => {
                expandFilterMonths(f).forEach(m => includedMonths.add(m));
            });

            return availableMonths.value.filter(m => includedMonths.has(m.key));
        });

        // Autocomplete items
        const autocompleteItems = computed(() => {
            const items = [];
            const data = spendingData.value;

            // Use categoryView for unique merchants (avoids duplicates from overlapping sections)
            const categoryView = data.categoryView || {};
            const seenMerchants = new Set();

            for (const category of Object.values(categoryView)) {
                for (const subcat of Object.values(category.subcategories || {})) {
                    for (const [id, merchant] of Object.entries(subcat.merchants || {})) {
                        if (!seenMerchants.has(id)) {
                            seenMerchants.add(id);
                            items.push({
                                type: 'merchant',
                                filterText: id,
                                displayText: merchant.displayName,
                                id: `m:${id}`
                            });
                        }
                    }
                }
            }

            // Categories and subcategories (unique, distinguished)
            const categories = new Set();
            const subcategories = new Map(); // subcategory -> parent category
            for (const category of Object.values(categoryView)) {
                for (const subcat of Object.values(category.subcategories || {})) {
                    for (const merchant of Object.values(subcat.merchants || {})) {
                        categories.add(merchant.category);
                        if (merchant.subcategory && merchant.subcategory !== merchant.category) {
                            subcategories.set(merchant.subcategory, merchant.category);
                        }
                    }
                }
            }
            categories.forEach(c => items.push({
                type: 'category', filterText: c, displayText: c, id: `c:${c}`
            }));
            subcategories.forEach((parentCat, s) => {
                // Only add if not also a top-level category
                if (!categories.has(s)) {
                    items.push({
                        type: 'subcategory',
                        filterText: s,
                        displayText: `${parentCat} > ${s}`,
                        parentCategory: parentCat,
                        id: `cs:${s}`
                    });
                }
            });

            // Tags (unique across all merchants, including excluded and refund transactions)
            const tags = new Set();
            for (const category of Object.values(categoryView)) {
                for (const subcat of Object.values(category.subcategories || {})) {
                    for (const merchant of Object.values(subcat.merchants || {})) {
                        (merchant.tags || []).forEach(t => tags.add(t));
                    }
                }
            }
            // Also collect tags from excluded transactions (income, transfer)
            for (const txn of data.excludedTransactions || []) {
                (txn.tags || []).forEach(t => tags.add(t));
            }
            // And from refund transactions
            for (const txn of data.refundTransactions || []) {
                (txn.tags || []).forEach(t => tags.add(t));
            }
            tags.forEach(t => items.push({
                type: 'tag', filterText: t, displayText: t, id: `t:${t}`
            }));

            return items;
        });

        // Reverse lookup: filterText -> displayText by type
        const displayTextLookup = computed(() => {
            const lookup = {};
            for (const item of autocompleteItems.value) {
                const key = `${item.type}:${item.filterText}`;
                lookup[key] = item.displayText;
            }
            return lookup;
        });

        function getDisplayText(type, filterText) {
            if (type === 'month') return monthChipDisplayText(filterText);
            if (type === 'daterange') return dateRangeDisplayText(filterText);
            return displayTextLookup.value[`${type}:${filterText}`] || filterText;
        }

        // Filtered autocomplete based on search
        const filteredAutocomplete = computed(() => {
            const q = searchQuery.value.toLowerCase().trim();
            if (!q) return [];

            // Priority order for autocomplete types (lower = higher priority)
            const typePriority = { tag: 0, category: 1, subcategory: 2, merchant: 3 };

            // Get matching autocomplete items (merchants, categories, etc.)
            // Sort by type priority so tags/categories appear before merchants
            const matches = autocompleteItems.value
                .filter(item => item.displayText.toLowerCase().includes(q))
                .sort((a, b) => (typePriority[a.type] ?? 5) - (typePriority[b.type] ?? 5))
                .slice(0, 8);

            // Add "Search transactions for: X" option at the end
            if (q.length >= 2) {
                matches.push({
                    type: 'text',
                    filterText: q,
                    displayText: `Search transactions: "${q}"`,
                    id: `search:${q}`,
                    isTextSearch: true
                });
            }

            return matches;
        });

        // Available months for date picker.
        // Sourced exclusively from categoryView, which is built from ALL
        // merchants (report.py build_category_view) and is a strict superset of
        // sections: a merchant matching zero views is absent from `sections` by
        // design, so sourcing from `sections` silently drops its months.
        const availableMonths = computed(() => {
            const months = new Set();
            const categoryView = spendingData.value.categoryView || {};
            for (const category of Object.values(categoryView)) {
                for (const subcat of Object.values(category.subcategories || {})) {
                    for (const merchant of Object.values(subcat.merchants || {})) {
                        for (const txn of merchant.transactions || []) {
                            months.add(txn.month);
                        }
                    }
                }
            }
            return Array.from(months).sort().map(m => ({
                key: m,
                label: formatMonthLabel(m)
            }));
        });

        // ========== METHODS ==========

        function passesFilters(txn, merchant) {
            const includes = activeFilters.value.filter(f => f.mode === 'include');
            const excludes = activeFilters.value.filter(f => f.mode === 'exclude');

            // Check excludes first
            for (const f of excludes) {
                if (matchesFilter(txn, merchant, f)) return false;
            }

            // Group includes by filter category (month + daterange collapse to
            // one 'date' category so they OR together rather than AND).
            const byType = {};
            includes.forEach(f => {
                const cat = filterCategory(f.type);
                if (!byType[cat]) byType[cat] = [];
                byType[cat].push(f);
            });

            // AND across categories, OR within a category
            for (const [cat, filters] of Object.entries(byType)) {
                const anyMatch = filters.some(f => matchesFilter(txn, merchant, f));
                if (!anyMatch) return false;
            }

            return true;
        }

        // Reconstruct a day-precision YYYY-MM-DD for a transaction, mirroring
        // analyzer.py's CSV export: month (YYYY-MM) + day from date (MM/DD).
        function txnFullDate(txn) {
            return `${txn.month}-${(txn.date || '00/00').slice(3, 5)}`;
        }

        function matchesFilter(txn, merchant, filter) {
            const text = filter.text.toLowerCase();
            switch (filter.type) {
                case 'merchant':
                    return merchant.id.toLowerCase() === text ||
                           merchant.displayName.toLowerCase() === text;
                case 'category':
                    return merchant.category.toLowerCase() === text;
                case 'subcategory':
                    return merchant.subcategory.toLowerCase() === text;
                case 'month':
                    return monthMatches(txn.month, filter.text);
                case 'daterange': {
                    const [start, end] = filter.text.split('..');
                    const full = txnFullDate(txn);
                    return full >= start && full <= end;
                }
                case 'tag':
                    return (txn.tags || []).some(t => t.toLowerCase() === text);
                case 'text':
                    // Search transaction description and extra_fields
                    if ((txn.description || '').toLowerCase().includes(text)) return true;
                    return matchesExtraFields(txn, text);
                default:
                    return false;
            }
        }

        function matchesExtraFields(txn, searchText) {
            if (!txn.extra_fields) return false;
            for (const value of Object.values(txn.extra_fields)) {
                if (Array.isArray(value)) {
                    if (value.some(item => String(item).toLowerCase().includes(searchText))) return true;
                } else if (String(value).toLowerCase().includes(searchText)) {
                    return true;
                }
            }
            return false;
        }

        function monthMatches(txnMonth, filterText) {
            if (filterText.includes('..')) {
                const [start, end] = filterText.split('..');
                return txnMonth >= start && txnMonth <= end;
            }
            return txnMonth === filterText;
        }

        function addFilter(text, type, displayText = null) {
            if (activeFilters.value.some(f => f.text === text && f.type === type)) return;
            activeFilters.value.push({ text, type, mode: 'include', displayText: displayText || text });
            searchQuery.value = '';
            showAutocomplete.value = false;
            autocompleteIndex.value = -1;
        }

        function removeFilter(index) {
            const removed = activeFilters.value[index];
            activeFilters.value.splice(index, 1);
            // Removing a custom-range chip must also reset the Start/End widget
            // so reopening the popover doesn't silently re-add it on Apply.
            if (removed && removed.type === 'daterange') clearCustomRange();
        }

        function toggleFilterMode(index) {
            const f = activeFilters.value[index];
            f.mode = f.mode === 'include' ? 'exclude' : 'include';
        }

        function clearFilters() {
            activeFilters.value = [];
            pendingMonths.clear();
            clearCustomRange();
        }

        function addMonthFilter(month) {
            if (month) addFilter(month, 'month', formatMonthLabel(month));
        }

        // ========== DATE FILTER POPOVER (Month / Quarter / Year / Custom) ==========
        const datePopoverOpen = ref(false);
        const pendingMonths = reactive(new Set());   // flat set of 'YYYY-MM' keys
        const activeYearTab = ref(null);
        const customStart = ref(null);               // { y, m, d } | null
        const customEnd = ref(null);

        // Distinct years present in the data, ascending.
        const availableYears = computed(() => {
            const years = new Set();
            availableMonths.value.forEach(m => years.add(parseInt(m.key.slice(0, 4), 10)));
            return Array.from(years).sort((a, b) => a - b);
        });
        // Year tabs: up to 3 most recent data years, oldest -> newest.
        const yearTabs = computed(() => availableYears.value.slice(-3));

        // Real-today info driving the This/Last preset rows.
        const todayInfo = computed(() => {
            const now = new Date();
            const y = now.getFullYear();
            const mo = now.getMonth() + 1;
            return { y, mo, q: quarterOf(mo), monthKey: y + '-' + pad2(mo) };
        });
        const thisLastPresets = computed(() => {
            const t = todayInfo.value;
            let lm = t.mo - 1, lmy = t.y;
            if (lm < 1) { lm = 12; lmy -= 1; }
            let lq = t.q - 1, lqy = t.y;
            if (lq < 1) { lq = 4; lqy -= 1; }
            const ly = t.y - 1;
            return {
                thisRow: [
                    { label: 'This Month', type: 'month', key: t.monthKey },
                    { label: 'This Quarter', type: 'quarter', key: t.y + '-Q' + t.q },
                    { label: 'This Year', type: 'year', key: String(t.y) }
                ],
                lastRow: [
                    { label: 'Last Month', type: 'month', key: lmy + '-' + pad2(lm) },
                    { label: 'Last Quarter', type: 'quarter', key: lqy + '-Q' + lq },
                    { label: 'Last Year', type: 'year', key: String(ly) }
                ]
            };
        });

        // Months (with data) for the active year tab.
        const activeYearMonths = computed(() =>
            availableMonths.value.filter(m => m.key.slice(0, 4) === String(activeYearTab.value))
        );

        // A preset is "active" purely by coverage: every constituent month is
        // currently pending. No separate quarter/year pending state.
        function isDateItemActive(item) {
            const months = monthsForItem(item);
            return months.length > 0 && months.every(k => pendingMonths.has(k));
        }
        function toggleDateItem(item) {
            const months = monthsForItem(item);
            const active = isDateItemActive(item);
            months.forEach(k => { if (active) pendingMonths.delete(k); else pendingMonths.add(k); });
        }
        function yearTabHasPending(year) {
            for (const k of pendingMonths) if (k.slice(0, 4) === String(year)) return true;
            return false;
        }

        function openDatePopover() {
            // Expand currently-applied date chips (possibly aggregated into
            // quarter/year ranges) back into flat pending months for editing.
            pendingMonths.clear();
            for (const f of activeFilters.value) {
                if (f.type === 'month') {
                    if (f.text.includes('..')) expandMonthRange(f.text).forEach(k => pendingMonths.add(k));
                    else pendingMonths.add(f.text);
                }
                // daterange chips stay independent (never expanded to months).
            }
            const tabs = yearTabs.value;
            const ty = todayInfo.value.y;
            activeYearTab.value = tabs.includes(ty) ? ty : (tabs.length ? tabs[tabs.length - 1] : ty);
            datePopoverOpen.value = true;
        }
        function toggleDatePopover() {
            if (datePopoverOpen.value) datePopoverOpen.value = false;
            else openDatePopover();
        }
        function closeDatePopover() { datePopoverOpen.value = false; }
        function clearPendingMonths() { pendingMonths.clear(); }

        function getCustomRangeChip() {
            if (!customStart.value || !customEnd.value) return null;
            let a = customStart.value, b = customEnd.value;
            if (dateToKey(a) > dateToKey(b)) { const t = a; a = b; b = t; }
            const text = dateToKey(a) + '..' + dateToKey(b);
            return { text, type: 'daterange', mode: 'include', displayText: dateRangeDisplayText(text) };
        }
        function clearCustomRange() {
            customStart.value = null;
            customEnd.value = null;
        }

        // Apply: re-aggregate pending months into the fewest chips, then append
        // the (independent) custom range. Existing date chips are replaced.
        function applyDateFilters() {
            activeFilters.value = activeFilters.value.filter(f => filterCategory(f.type) !== 'date');
            aggregateMonthKeys([...pendingMonths]).forEach(entry =>
                activeFilters.value.push(aggregateEntryToChip(entry))
            );
            const custom = getCustomRangeChip();
            if (custom) activeFilters.value.push(custom);
            datePopoverOpen.value = false;
        }

        // Footer "Clear all filters": destructive — wipes every filter, the
        // pending set, and the custom-range widget, then closes.
        function clearAllDateFilters() {
            activeFilters.value = [];
            pendingMonths.clear();
            clearCustomRange();
            datePopoverOpen.value = false;
        }

        function toggleExpand(merchantId) {
            if (expandedMerchants.has(merchantId)) {
                expandedMerchants.delete(merchantId);
            } else {
                expandedMerchants.add(merchantId);
            }
        }

        function toggleSection(sectionId) {
            if (collapsedSections.has(sectionId)) {
                collapsedSections.delete(sectionId);
            } else {
                collapsedSections.add(sectionId);
            }
        }

        // Section keys currently visible in the active view (for collapse-all / expand-all)
        const allSectionKeys = computed(() => {
            if (currentView.value === 'section' && hasSections.value) {
                return Object.keys(positiveSectionView.value).map(id => 'sec:' + id);
            }
            const view = groupByMode.value === 'subcategory' ? subcategoryGroupedView.value : positiveCategoryView.value;
            return Object.keys(view).map(name => 'cat:' + name);
        });
        const allCollapsed = computed(() =>
            allSectionKeys.value.length > 0 && allSectionKeys.value.every(k => collapsedSections.has(k))
        );
        // Collapse all: fold every category AND its open transaction lists ("the details")
        function collapseAll() {
            allSectionKeys.value.forEach(k => collapsedSections.add(k));
            expandedMerchants.clear();
        }
        // Expand all: reopen top-level categories only (leave transaction lists folded)
        function expandAll() {
            collapsedSections.clear();
        }
        function toggleAllSections() {
            if (allCollapsed.value) { expandAll(); } else { collapseAll(); }
        }

        // View-aware summary shown in the Transaction Details header
        const pluralize = (n, one, many) => `${n} ${n === 1 ? one : many}`;
        const detailsSummary = computed(() => {
            if (currentView.value === 'section' && hasSections.value) {
                const views = positiveSectionView.value;
                let merchants = 0;
                Object.values(views).forEach(s => { merchants += Object.keys(s.filteredMerchants || {}).length; });
                return `${pluralize(Object.keys(views).length, 'view', 'views')}, ${pluralize(merchants, 'merchant', 'merchants')}`;
            }
            if (groupByMode.value === 'subcategory') {
                const cats = subcategoryGroupedView.value;
                let subs = 0;
                Object.values(cats).forEach(c => { subs += (c.sortedSubcategories || []).length; });
                return `${pluralize(Object.keys(cats).length, 'category', 'categories')}, ${pluralize(subs, 'subcategory', 'subcategories')}`;
            }
            const cats = positiveCategoryView.value;
            let merchants = 0;
            Object.values(cats).forEach(c => { merchants += (c.sortedMerchants || []).length; });
            return `${pluralize(Object.keys(cats).length, 'category', 'categories')}, ${pluralize(merchants, 'merchant', 'merchants')}`;
        });

        // Sort merchants by configurable column and direction (for object-based sections)
        function sortMerchantEntries(merchants, column, dir) {
            return Object.entries(merchants || {})
                .sort((a, b) => {
                    const [, mA] = a, [, mB] = b;
                    let vA, vB;
                    switch (column) {
                        case 'merchant':
                            vA = mA.displayName.toLowerCase();
                            vB = mB.displayName.toLowerCase();
                            break;
                        case 'subcategory':
                            vA = (mA.subcategory || '').toLowerCase();
                            vB = (mB.subcategory || '').toLowerCase();
                            break;
                        case 'count':
                            vA = mA.filteredCount;
                            vB = mB.filteredCount;
                            break;
                        default:
                            vA = mA.filteredTotal;
                            vB = mB.filteredTotal;
                    }
                    if (typeof vA === 'string') {
                        return dir === 'asc' ? vA.localeCompare(vB) : vB.localeCompare(vA);
                    }
                    return dir === 'asc' ? vA - vB : vB - vA;
                })
                .reduce((acc, [id, m]) => { acc[id] = m; return acc; }, {});
        }

        // Toggle sort column/direction for a section
        function toggleSort(key, column) {
            const current = sortConfig[key] || { column: 'total', dir: 'desc' };
            if (current.column === column) {
                sortConfig[key] = { column, dir: current.dir === 'desc' ? 'asc' : 'desc' };
            } else {
                // String columns default to ascending, numeric columns to descending
                const isStringColumn = column === 'merchant' || column === 'subcategory';
                sortConfig[key] = { column, dir: isStringColumn ? 'asc' : 'desc' };
            }
        }

        function sortedMerchants(merchants, sectionId) {
            // Sort by total descending
            return Object.entries(merchants || {})
                .sort((a, b) => b[1].filteredTotal - a[1].filteredTotal)
                .reduce((acc, [id, m]) => { acc[id] = m; return acc; }, {});
        }

        // Formatting helpers
        // Currency format from data (e.g., "${amount}", "£{amount}", "{amount} zł")
        const currencyFormat = spendingData.value.currencyFormat || '${amount}';

        function formatCurrency(amount) {
            if (amount === undefined || amount === null) return currencyFormat.replace('{amount}', '0');
            const rounded = Math.round(amount);
            const absFormatted = Math.abs(rounded).toLocaleString('en-US');
            const formatted = currencyFormat.replace('{amount}', absFormatted);
            if (rounded < 0) {
                return '-' + formatted;
            }
            return formatted;
        }

        // Short format for chart Y-axis (e.g., $1k, £1k, 1k zł)
        function formatCurrencyShort(amount) {
            if (amount >= 1000) {
                const k = (amount / 1000).toFixed(0);
                return currencyFormat.replace('{amount}', k + 'k');
            }
            return currencyFormat.replace('{amount}', amount.toFixed(0));
        }

        function formatDate(dateStr, monthStr, showYear) {
            if (!dateStr) return '';
            // Year suffix (from monthStr YYYY-MM) only when the caller's list
            // spans multiple calendar years.
            const yearSuffix = showYear && monthStr ? `, ${monthStr.slice(0, 4)}` : '';
            // Handle MM/DD format from Python
            if (dateStr.match(/^\d{1,2}\/\d{1,2}$/)) {
                const [month, day] = dateStr.split('/');
                const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
                return `${months[parseInt(month)-1]} ${parseInt(day)}${yearSuffix}`;
            }
            // Handle YYYY-MM-DD format
            const d = new Date(dateStr + 'T12:00:00');
            return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) + yearSuffix;
        }

        function formatMonthLabel(key) {
            if (!key) return '';
            const [year, month] = key.split('-');
            const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
            return `${months[parseInt(month)-1]} ${year}`;
        }

        function formatPct(value, total) {
            if (!total || total === 0) return '0%';
            return ((value / total) * 100).toFixed(1) + '%';
        }

        function filterTypeChar(type) {
            return { category: 'c', subcategory: 'sc', merchant: 'm', month: 'd', daterange: 'dr', tag: 't', text: 's' }[type] || '?';
        }

        // Highlight search terms in transaction descriptions
        function highlightDescription(description) {
            if (!description) return '';
            const textFilters = activeFilters.value.filter(f => f.type === 'text' && f.mode === 'include');
            if (textFilters.length === 0) return escapeHtml(description);

            let result = escapeHtml(description);
            for (const filter of textFilters) {
                const searchTerm = filter.text;
                const regex = new RegExp(`(${escapeRegex(searchTerm)})`, 'gi');
                result = result.replace(regex, '<span class="search-highlight">$1</span>');
            }
            return result;
        }

        function escapeHtml(text) {
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }

        function escapeRegex(text) {
            return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
        }

        function expandMonthRange(rangeStr) {
            const [start, end] = rangeStr.split('..');
            const months = [];
            let current = start;
            while (current <= end) {
                months.push(current);
                const [y, m] = current.split('-').map(Number);
                const nextM = m === 12 ? 1 : m + 1;
                const nextY = m === 12 ? y + 1 : y;
                current = `${nextY}-${String(nextM).padStart(2, '0')}`;
            }
            return months;
        }

        // ========== SEARCH/AUTOCOMPLETE ==========

        function onSearchInput() {
            showAutocomplete.value = true;
            autocompleteIndex.value = -1;
        }

        function onSearchKeydown(e) {
            const items = filteredAutocomplete.value;
            if (!items.length) return;

            if (e.key === 'ArrowDown') {
                e.preventDefault();
                autocompleteIndex.value = Math.min(autocompleteIndex.value + 1, items.length - 1);
            } else if (e.key === 'ArrowUp') {
                e.preventDefault();
                autocompleteIndex.value = Math.max(autocompleteIndex.value - 1, 0);
            } else if (e.key === 'Enter' && autocompleteIndex.value >= 0) {
                e.preventDefault();
                selectAutocompleteItem(items[autocompleteIndex.value]);
            } else if (e.key === 'Escape') {
                showAutocomplete.value = false;
                autocompleteIndex.value = -1;
            }
        }

        function selectAutocompleteItem(item) {
            addFilter(item.filterText, item.type, item.displayText);
        }

        // ========== THEME ==========

        function toggleTheme() {
            isDarkTheme.value = !isDarkTheme.value;
            document.documentElement.setAttribute('data-theme', isDarkTheme.value ? 'dark' : 'light');
            localStorage.setItem('theme', isDarkTheme.value ? 'dark' : 'light');
        }

        function initTheme() {
            const saved = localStorage.getItem('theme');
            if (saved === 'light') {
                isDarkTheme.value = false;
                document.documentElement.setAttribute('data-theme', 'light');
            }
        }

        // ========== URL HASH ==========

        function filtersToHash() {
            if (activeFilters.value.length === 0) {
                history.replaceState(null, '', location.pathname);
                return;
            }
            const typeChar = { category: 'c', subcategory: 'sc', merchant: 'm', month: 'd', daterange: 'dr', tag: 't', text: 's' };
            const parts = activeFilters.value.map(f => {
                const mode = f.mode === 'exclude' ? '-' : '+';
                return `${mode}${typeChar[f.type]}:${encodeURIComponent(f.text)}`;
            });
            history.replaceState(null, '', '#' + parts.join('&'));
        }

        function hashToFilters() {
            const hash = location.hash.slice(1);
            if (!hash) return;
            const typeMap = { c: 'category', sc: 'subcategory', m: 'merchant', d: 'month', dr: 'daterange', t: 'tag', s: 'text' };
            hash.split('&').forEach(part => {
                const mode = part[0] === '-' ? 'exclude' : 'include';
                const start = part[0] === '+' || part[0] === '-' ? 1 : 0;
                const colonIdx = part.indexOf(':');
                const typeCode = part.slice(start, colonIdx);
                const type = typeMap[typeCode] || 'category';
                const text = decodeURIComponent(part.slice(colonIdx + 1));
                if (text && !activeFilters.value.some(f => f.text === text && f.type === type)) {
                    const displayText = getDisplayText(type, text);
                    activeFilters.value.push({ text, type, mode, displayText });
                }
            });
        }

        // ========== CHARTS ==========

        function initCharts() {
            // Cash flow trend chart (datasets are built per update so empty series drop out)
            if (monthlyChart.value) {
                const ctx = monthlyChart.value.getContext('2d');
                const labels = availableMonths.value.map(m => m.label);
                monthlyChartInstance = new Chart(ctx, {
                    type: 'bar',
                    data: {
                        labels,
                        datasets: []
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        plugins: {
                            legend: { display: true, position: 'top' }
                        },
                        scales: {
                            y: {
                                beginAtZero: true,
                                grace: '5%',
                                ticks: {
                                    callback: v => formatCurrencyShort(v)
                                }
                            }
                        },
                        onClick: (e, elements) => {
                            if (elements.length > 0) {
                                const idx = elements[0].index;
                                const month = monthlyChartMonths[idx];
                                if (month) addFilter(month.key, 'month', month.label);
                            }
                        }
                    }
                });
            }

            // Category pie chart
            if (categoryPieChart.value) {
                const ctx = categoryPieChart.value.getContext('2d');
                pieChartInstance = new Chart(ctx, {
                    type: 'doughnut',
                    data: {
                        labels: [],
                        datasets: [{
                            data: [],
                            backgroundColor: []
                        }]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        plugins: {
                            legend: {
                                position: 'right',
                                labels: { boxWidth: 12, padding: 8 }
                            }
                        }
                    }
                });
            }

            // Category by month chart
            if (categoryByMonthChart.value) {
                const ctx = categoryByMonthChart.value.getContext('2d');
                const labels = availableMonths.value.map(m => m.label);
                categoryMonthChartInstance = new Chart(ctx, {
                    type: 'bar',
                    data: {
                        labels,
                        datasets: []
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        plugins: {
                            legend: {
                                position: 'top',
                                labels: { boxWidth: 12, padding: 8 }
                            }
                        },
                        scales: {
                            x: { stacked: true },
                            y: {
                                stacked: true,
                                beginAtZero: true,
                                grace: '5%',
                                ticks: {
                                    callback: v => formatCurrencyShort(v)
                                }
                            }
                        }
                    }
                });
            }

            updateCharts();
        }

        function updateCharts() {
            const agg = chartAggregations.value;
            const monthsToShow = filteredMonthsForCharts.value;

            // Update cash flow trend. Months with nothing to plot (e.g. only transfers)
            // are dropped so the axis doesn't carry empty columns, and series without
            // data are left out entirely so they don't linger in the legend.
            if (monthlyChartInstance) {
                const CASH_FLOW_SERIES = [
                    { label: 'Spending', byMonth: agg.spendingByMonth, color: '#4facfe' },
                    { label: 'Income', byMonth: agg.incomeByMonth, color: '#00c9a7' },
                    { label: 'Credits', byMonth: agg.creditsByMonth, color: '#ffa94d' },
                    { label: 'Investment', byMonth: agg.investmentByMonth, color: '#7c3aed' }
                ];

                monthlyChartMonths = monthsToShow.filter(m =>
                    CASH_FLOW_SERIES.some(s => (s.byMonth[m.key] || 0) > 0)
                );

                const datasets = [];
                let maxVal = 1;
                for (const series of CASH_FLOW_SERIES) {
                    const data = monthlyChartMonths.map(m => series.byMonth[m.key] || 0);
                    if (!data.some(v => v > 0)) continue;
                    datasets.push({
                        label: series.label,
                        data,
                        backgroundColor: series.color,
                        borderRadius: 4
                    });
                    maxVal = Math.max(maxVal, ...data);
                }

                monthlyChartInstance.data.labels = monthlyChartMonths.map(m => m.label);
                monthlyChartInstance.data.datasets = datasets;
                monthlyChartInstance.options.scales.y.suggestedMax = maxVal * 1.1;
                monthlyChartInstance.update();
            }

            // Both category charts share this ranking so their series and colors line up
            const ranked = Object.entries(agg.byCategory)
                .filter(([_, v]) => v > 0)
                .sort((a, b) => b[1] - a[1])
                .map(([name]) => name);
            const topCategories = ranked.slice(0, TOP_CATEGORY_COUNT);
            const otherCategories = ranked.slice(TOP_CATEGORY_COUNT);
            const categoryColor = i => CATEGORY_COLORS[i % CATEGORY_COLORS.length];

            // Update category pie (top N + "Other")
            if (pieChartInstance) {
                const labels = topCategories.slice();
                const data = topCategories.map(cat => agg.byCategory[cat]);
                const colors = topCategories.map((_, i) => categoryColor(i));

                if (otherCategories.length > 0) {
                    labels.push(OTHER_CATEGORY_LABEL);
                    data.push(otherCategories.reduce((sum, cat) => sum + agg.byCategory[cat], 0));
                    colors.push(OTHER_CATEGORY_COLOR);
                }

                pieChartInstance.data.labels = labels;
                pieChartInstance.data.datasets[0].data = data;
                pieChartInstance.data.datasets[0].backgroundColor = colors;
                pieChartInstance.update();
            }

            // Update category by month (top N + "Other"). Months without spending
            // are dropped so the axis doesn't carry empty columns.
            if (categoryMonthChartInstance) {
                const spendingMonths = monthsToShow.filter(m => (agg.spendingByMonth[m.key] || 0) > 0);
                const labels = spendingMonths.map(m => m.label);
                const datasets = topCategories.map((cat, i) => ({
                    label: cat,
                    data: spendingMonths.map(m => agg.byCategoryByMonth[cat]?.[m.key] || 0),
                    backgroundColor: categoryColor(i)
                }));

                if (otherCategories.length > 0) {
                    datasets.push({
                        label: OTHER_CATEGORY_LABEL,
                        data: spendingMonths.map(m => otherCategories.reduce(
                            (sum, cat) => sum + (agg.byCategoryByMonth[cat]?.[m.key] || 0), 0
                        )),
                        backgroundColor: OTHER_CATEGORY_COLOR
                    });
                }

                // Calculate max for stacked bar (sum of all categories per month)
                const monthTotals = spendingMonths.map((m, idx) =>
                    datasets.reduce((sum, ds) => sum + (ds.data[idx] || 0), 0)
                );
                const maxVal = Math.max(...monthTotals, 1); // At least 1 to avoid 0

                categoryMonthChartInstance.data.labels = labels;
                categoryMonthChartInstance.data.datasets = datasets;
                categoryMonthChartInstance.options.scales.y.suggestedMax = maxVal * 1.1;
                categoryMonthChartInstance.update();
            }
        }

        // ========== SCROLL HANDLING ==========

        function handleScroll() {
            isScrolled.value = window.scrollY > 50;
        }

        // ========== WATCHERS ==========

        watch(activeFilters, filtersToHash, { deep: true });
        watch(chartAggregations, updateCharts);

        // Track extra_field matches and auto-expand merchants
        watch(activeFilters, () => {
            extraFieldMatches.clear();
            const textFilters = activeFilters.value.filter(f => f.type === 'text' && f.mode === 'include');
            if (textFilters.length === 0) return;

            const categoryView = spendingData.value.categoryView || {};
            for (const category of Object.values(categoryView)) {
                for (const subcat of Object.values(category.subcategories || {})) {
                    for (const [merchantId, merchant] of Object.entries(subcat.merchants || {})) {
                        for (const txn of merchant.transactions || []) {
                            for (const filter of textFilters) {
                                const searchText = filter.text.toLowerCase();
                                if (matchesExtraFields(txn, searchText)) {
                                    extraFieldMatches.add(txn.id);
                                    expandedMerchants.add(merchantId);
                                }
                            }
                        }
                    }
                }
            }
        }, { deep: true, immediate: true });

        // ========== LIFECYCLE ==========

        onMounted(() => {
            document.title = title.value;
            initTheme();

            // Wait for next tick to ensure computed properties are ready
            nextTick(() => {
                hashToFilters();
                initCharts();
            });

            // Scroll handling
            window.addEventListener('scroll', handleScroll);

            // Close autocomplete on outside click
            document.addEventListener('click', e => {
                if (!e.target.closest('.autocomplete-container')) {
                    showAutocomplete.value = false;
                    autocompleteIndex.value = -1;
                }
                // Close match-info popups on outside click
                if (!e.target.closest('.match-info-trigger') && !e.target.closest('.match-info-popup')) {
                    document.querySelectorAll('.match-info-popup.visible').forEach(p => {
                        p.classList.remove('visible');
                    });
                }
                // Close the date-filter popover on outside click
                if (!e.target.closest('.date-filter-wrap')) {
                    datePopoverOpen.value = false;
                }
            });

            // Hash change handler
            window.addEventListener('hashchange', () => {
                activeFilters.value = [];
                hashToFilters();
            });
        });

        // ========== RETURN ==========

        return {
            // State
            activeFilters, expandedMerchants, extraFieldMatches, collapsedSections, searchQuery,
            showAutocomplete, autocompleteIndex, isScrolled, isDarkTheme, chartsCollapsed, helpCollapsed,
            currentView, groupByMode, sortConfig, includeNegativeTotals, detailsCollapsed, allCollapsed, detailsSummary,
            // Refs
            monthlyChart, categoryPieChart, categoryByMonthChart,
            // Computed
            spendingData, title, subtitle,
            visibleSections, filteredCategoryView, positiveCategoryView, subcategoryGroupedView, creditMerchants, filteredSectionView, positiveSectionView, hasSections, negativeTotalsCount,
            sectionTotals, grandTotal, grossSpending, creditsTotal, uncategorizedTotal,
            numFilteredMonths, filteredAutocomplete, availableMonths,
            categoryColorMap, tagColor,
            // Date filter popover
            datePopoverOpen, pendingMonths, activeYearTab, customStart, customEnd,
            yearTabs, thisLastPresets, activeYearMonths,
            isDateItemActive, toggleDateItem, yearTabHasPending,
            toggleDatePopover, closeDatePopover, clearPendingMonths,
            applyDateFilters, clearAllDateFilters,
            // Cash flow, transfers, and investments
            incomeTotal, spendingTotal, dataCreditsTotal, cashFlow,
            transfersIn, transfersOut, transfersNet,
            incomeCount, transfersCount,
            investmentTotal,
            // Filtered view card
            filteredViewTotals,
            // All transactions section
            groupedTransactions, expandedTransactions,
            // Methods
            addFilter, removeFilter, toggleFilterMode, clearFilters, addMonthFilter,
            toggleExpand, toggleSection, toggleSort, toggleAllSections, sortedMerchants,
            formatCurrency, formatDate, formatMonthLabel, formatPct, filterTypeChar,
            highlightDescription,
            onSearchInput, onSearchKeydown, selectAutocompleteItem,
            toggleTheme
        };
    }
})
.component('merchant-section', MerchantSection)
.component('drill-calendar', DrillCalendar)
.mount('#app');
