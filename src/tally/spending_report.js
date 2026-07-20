// spending_report.js - Vue 3 app for spending report
// This file is embedded into the HTML at build time by analyzer.py

const { createApp, ref, reactive, computed, watch, onMounted, onUnmounted, nextTick, defineComponent } = Vue;

function setAppInitializingState() {
    document.body.classList.add('app-initializing');
    document.body.classList.remove('app-ready');
}

function setAppReadyState() {
    document.body.classList.remove('app-initializing');
    document.body.classList.add('app-ready');
}

setAppInitializingState();

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
        isIncludeFilterActive: { type: Function, required: true },
        toggleIncludeFilter: { type: Function, required: true },
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
                                    <td class="merchant">
                                        <div class="merchant-cell">
                                            <span class="chevron">{{ isExpanded(item.id || idx) ? '▼' : '▶' }}</span>
                                            <div class="merchant-body">
                                                <span class="merchant-name">
                                                    {{ item.displayName || item.merchant }}
                                                </span>
                                                <span class="merchant-actions" v-if="categoryMode">
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
                                                            <div class="popup-section-header">{{ item.matchInfo.ruleName ? 'Rule: [' + item.matchInfo.ruleName + ']' : 'Tag Rules Matched' }}</div>
                                                            <div v-if="item.matchInfo.ruleName || (item.matchInfo.assignedCategory && item.matchInfo.assignedCategory !== 'Unknown')" class="popup-row">
                                                                <span class="popup-label">Merchant:</span>
                                                                <span class="popup-value">{{ item.matchInfo.assignedMerchant }}</span>
                                                            </div>
                                                            <div v-if="item.matchInfo.ruleName || (item.matchInfo.assignedCategory && item.matchInfo.assignedCategory !== 'Unknown')" class="popup-row">
                                                                <span class="popup-label">Category:</span>
                                                                <span class="popup-value">{{ item.matchInfo.assignedCategory }} / {{ item.matchInfo.assignedSubcategory }}</span>
                                                            </div>
                                                            <div v-for="(tag, tagIndex) in getTags(item)" :key="tag" class="popup-row popup-tag-row">
                                                                <span class="popup-label">{{ tagIndex === 0 ? 'Tags:' : '' }}</span>
                                                                <span class="popup-value">
                                                                    <span class="tag-badge popup-tag-badge" :style="{ borderColor: tagColor(tag), color: tagColor(tag) }">{{ tag }}</span>
                                                                    <span v-if="item.matchInfo.tagSources && item.matchInfo.tagSources[tag] && (!item.matchInfo.ruleName || item.matchInfo.tagSources[tag].rule !== item.matchInfo.ruleName)" class="popup-tag-source">
                                                                        from [{{ item.matchInfo.tagSources[tag].rule }}]
                                                                    </span>
                                                                </span>
                                                            </div>
                                                        </div>
                                                    </template>
                                                    <template v-if="item.viewInfo && item.viewInfo.filterExpr">
                                                        <div class="popup-section popup-view-section">
                                                            <div class="popup-section-header">View Filter ({{ item.viewInfo.viewName }})</div>
                                                            <div v-if="item.viewInfo.explanation" class="popup-explanation" style="margin-top: 0.3em;">{{ item.viewInfo.explanation }}</div>
                                                            <div class="popup-code">{{ item.viewInfo.filterExpr }}</div>
                                                        </div>
                                                    </template>
                                                    <div v-if="item.matchInfo" class="popup-source">From: {{ item.matchInfo.source === 'user' ? 'merchants.rules' : item.matchInfo.source }}</div>
                                                </span>
                                                    </span>
                                                    <span v-if="item.matchInfo || item.viewInfo" class="merchant-action-sep">&middot;</span>
                                                    <button type="button" class="merchant-filter-trigger" @click.stop="toggleMerchantFilter(item)">
                                                        {{ isMerchantFiltered(item) ? 'clear' : 'filter' }}
                                                    </button>
                                                </span>
                                            </div>
                                        </div>
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
                                            <span class="txn-date">{{ formatDate(txn.date, txn.month) }}</span>
                                            <span class="txn-account">
                                                <span v-if="txn.source" class="txn-source" :class="txn.source.toLowerCase()">{{ txn.source }}</span>
                                            </span>
                                            <span class="txn-desc"><span v-html="highlightDescription(txn.description)"></span></span>
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
        getFilterDescriptor(item) {
            const type = this.subcategoryMode ? 'subcategory' : 'merchant';
            const text = this.subcategoryMode ? (item.subcategory || item.displayName || item.id) : (item.displayName || item.id);
            const displayText = item.displayName || item.merchant || text;
            return { text, type, displayText };
        },
        isMerchantFiltered(item) {
            const { text, type } = this.getFilterDescriptor(item);
            return this.isIncludeFilterActive(text, type);
        },
        toggleMerchantFilter(item) {
            const { text, type, displayText } = this.getFilterDescriptor(item);
            this.toggleIncludeFilter(text, type, displayText);
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

const UI_STATE_KEY = 'spending-report-ui-state-v1';

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
        const currentView = ref('category'); // 'category' or 'section'
        const groupByMode = ref('merchant'); // 'merchant' or 'subcategory'
        const sortConfig = reactive({}); // { 'cat:Food': { column: 'total', dir: 'desc' } }
        const includeNegativeTotals = ref(false); // show categories with negative filteredTotal
        const txColumnProfiles = reactive({ merchant: null, subcategory: null, section: null });
        let isHydratingUiState = true;
        let txMeasureHost = null;
        let txMeasureDateEl = null;
        let txMeasureAmountEl = null;
        let txMeasureAccountEl = null;
        let txResizeDebounceHandle = null;
        let chartResizeDebounceHandle = null;
        let chartLayoutObserver = null;
        let suppressChartAnimationForResize = false;

        // Chart refs
        const kpiGrid = ref(null);
        const categoryTrendChart = ref(null);
        const cashFlowTrendChart = ref(null);
        const fixedVariableChart = ref(null);
        const volatilityChart = ref(null);

        // Chart instances
        const chartInstances = {
            category: null,
            cashFlow: null,
            fixedVariable: null,
            volatility: null,
        };

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

        const allPersistableSectionKeys = computed(() => {
            const keys = new Set();
            Object.keys(spendingData.value.categoryView || {}).forEach(name => keys.add('cat:' + name));
            Object.keys(spendingData.value.sections || {}).forEach(id => keys.add('sec:' + id));
            return keys;
        });

        const allPersistableItemIds = computed(() => {
            const ids = new Set();

            for (const category of Object.values(spendingData.value.categoryView || {})) {
                for (const [subName, subcat] of Object.entries(category.subcategories || {})) {
                    ids.add(subName);
                    for (const merchantId of Object.keys(subcat.merchants || {})) {
                        ids.add(String(merchantId));
                    }
                }
            }

            for (const section of Object.values(spendingData.value.sections || {})) {
                for (const merchantId of Object.keys(section.merchants || {})) {
                    ids.add(String(merchantId));
                }
            }

            return ids;
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

        // Chart data aggregations - always sourced from filtered category view.
        // Includes all flow buckets and category/month spending for chart builders.
        const chartAggregations = computed(() => {
            const spendingByMonth = {};
            const incomeByMonth = {};
            const investmentByMonth = {};
            const creditsByMonth = {};
            const transfersByMonth = {};
            const txnCountByMonth = {};
            const byCategory = {};
            const byCategoryByMonth = {};
            const recurringMerchants = [];
            const seenRecurring = new Set();
            const recurringSpendingByMonth = {};

            const categoryView = filteredCategoryView.value;
            for (const [catName, category] of Object.entries(categoryView)) {
                for (const subcat of Object.values(category.filteredSubcategories || {})) {
                    for (const [merchantId, merchant] of Object.entries(subcat.filteredMerchants || {})) {
                        const recurrence = merchant.recurrence || null;
                        const recurringMonthlyCost = Number(merchant.recurringMonthlyCost || 0);
                        const isRecurringMerchant = recurrence && recurringMonthlyCost > 0;
                        let recurringSpendingForMerchant = 0;

                        for (const txn of merchant.filteredTxns || []) {
                            const c = categorizeAmount(txn.amount, txn.tags || []);
                            txnCountByMonth[txn.month] = (txnCountByMonth[txn.month] || 0) + 1;

                            if (c.spending > 0) {
                                spendingByMonth[txn.month] = (spendingByMonth[txn.month] || 0) + c.spending;
                                byCategory[catName] = (byCategory[catName] || 0) + c.spending;
                                if (!byCategoryByMonth[catName]) byCategoryByMonth[catName] = {};
                                byCategoryByMonth[catName][txn.month] =
                                    (byCategoryByMonth[catName][txn.month] || 0) + c.spending;
                                if (isRecurringMerchant) {
                                    recurringSpendingForMerchant += c.spending;
                                    recurringSpendingByMonth[txn.month] =
                                        (recurringSpendingByMonth[txn.month] || 0) + c.spending;
                                }
                            }
                            if (c.income > 0) {
                                incomeByMonth[txn.month] = (incomeByMonth[txn.month] || 0) + c.income;
                            }
                            if (c.investment > 0) {
                                investmentByMonth[txn.month] = (investmentByMonth[txn.month] || 0) + c.investment;
                            }
                            if (c.credits > 0) {
                                creditsByMonth[txn.month] = (creditsByMonth[txn.month] || 0) + c.credits;
                            }
                            if (c.transferIn > 0 || c.transferOut > 0) {
                                transfersByMonth[txn.month] =
                                    (transfersByMonth[txn.month] || 0) + c.transferIn - c.transferOut;
                            }
                        }

                        // Keep fixed outputs aligned with spending logic by excluding
                        // recurring merchants that have only transfer/income/investment txns.
                        if (isRecurringMerchant && recurringSpendingForMerchant > 0 && !seenRecurring.has(merchantId)) {
                            seenRecurring.add(merchantId);
                            recurringMerchants.push({
                                id: merchantId,
                                merchant: merchant.displayName,
                                category: merchant.category,
                                cadence: recurrence,
                                recurringMonthlyCost,
                            });
                        }
                    }
                }
            }

            return {
                spendingByMonth,
                incomeByMonth,
                investmentByMonth,
                creditsByMonth,
                transfersByMonth,
                txnCountByMonth,
                byCategory,
                byCategoryByMonth,
                recurringMerchants,
                recurringSpendingByMonth,
            };
        });

        // Assign category colors from a stable global ranking so filtering/regrouping
        // never repaints surviving categories.
        const stableCategoryRanking = computed(() => {
            const totals = {};
            const categoryView = spendingData.value.categoryView || {};
            for (const [catName, category] of Object.entries(categoryView)) {
                for (const subcat of Object.values(category.subcategories || {})) {
                    for (const merchant of Object.values(subcat.merchants || {})) {
                        for (const txn of merchant.transactions || []) {
                            const c = categorizeAmount(txn.amount || 0, txn.tags || []);
                            if (c.spending > 0) totals[catName] = (totals[catName] || 0) + c.spending;
                        }
                    }
                }
            }
            return Object.entries(totals)
                .filter(([, total]) => total > 0)
                .sort((a, b) => b[1] - a[1])
                .map(([name]) => name);
        });

        const categoryColorMap = computed(() => {
            const colorMap = {};
            stableCategoryRanking.value.forEach((name, idx) => {
                colorMap[name] = CATEGORY_COLORS[idx % CATEGORY_COLORS.length];
            });

            // Backfill any category not in the ranking (e.g., zero-spend categories).
            let offset = stableCategoryRanking.value.length;
            for (const catName of Object.keys(spendingData.value.categoryView || {})) {
                if (!colorMap[catName]) {
                    colorMap[catName] = CATEGORY_COLORS[offset % CATEGORY_COLORS.length];
                    offset += 1;
                }
            }

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

        function toggleIncludeFilter(text, type, displayText = null) {
            const index = activeFilters.value.findIndex(f =>
                f.mode === 'include' && f.text === text && f.type === type
            );
            if (index >= 0) {
                removeFilter(index);
            } else {
                addFilter(text, type, displayText);
            }
        }

        function isIncludeFilterActive(text, type) {
            return activeFilters.value.some(f =>
                f.mode === 'include' && f.text === text && f.type === type
            );
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

        function formatCurrencyDecimalValue(amount) {
            const formatted = Math.abs(amount).toLocaleString('en-US', {
                minimumFractionDigits: 2,
                maximumFractionDigits: 2,
            });
            const text = currencyFormat.replace('{amount}', formatted);
            return amount < 0 ? '-' + text : text;
        }

        function formatDate(dateStr, monthStr) {
            if (!dateStr) return '';
            const yearSuffix = monthStr ? `, ${monthStr.slice(0, 4)}` : '';
            // Handle MM/DD format from Python
            if (dateStr.match(/^\d{1,2}\/\d{1,2}$/)) {
                const [month, day] = dateStr.split('/');
                const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
                return `${months[parseInt(month)-1]} ${parseInt(day)}${yearSuffix}`;
            }
            // Handle YYYY-MM-DD format
            const d = new Date(dateStr + 'T12:00:00');
            return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
        }

        function getTxnModeKey() {
            if (currentView.value === 'section' && hasSections.value) return 'section';
            return groupByMode.value === 'subcategory' ? 'subcategory' : 'merchant';
        }

        function getTxnItemsForMode(mode) {
            const items = [];
            if (mode === 'section') {
                const sections = spendingData.value.sections || {};
                for (const section of Object.values(sections)) {
                    items.push(...Object.values(section.merchants || {}));
                }
                return items;
            }
            const categoryView = spendingData.value.categoryView || {};
            for (const category of Object.values(categoryView)) {
                for (const subcat of Object.values(category.subcategories || {})) {
                    items.push(...Object.values(subcat.merchants || {}));
                }
            }
            return items;
        }

        function ensureTxnMeasureElements() {
            if (txMeasureHost) return;
            txMeasureHost = document.createElement('div');
            txMeasureHost.style.position = 'fixed';
            txMeasureHost.style.left = '-10000px';
            txMeasureHost.style.top = '-10000px';
            txMeasureHost.style.visibility = 'hidden';
            txMeasureHost.style.pointerEvents = 'none';
            txMeasureHost.style.whiteSpace = 'nowrap';
            txMeasureHost.style.fontSize = '0.85rem';

            txMeasureDateEl = document.createElement('span');
            txMeasureDateEl.className = 'txn-date';
            txMeasureAmountEl = document.createElement('span');
            txMeasureAmountEl.className = 'txn-amount';
            txMeasureAccountEl = document.createElement('span');
            txMeasureAccountEl.className = 'txn-source';

            txMeasureHost.appendChild(txMeasureDateEl);
            txMeasureHost.appendChild(txMeasureAmountEl);
            txMeasureHost.appendChild(txMeasureAccountEl);
            document.body.appendChild(txMeasureHost);
        }

        function measureTxnDatePx(label) {
            txMeasureDateEl.textContent = label || '';
            return Math.ceil(txMeasureDateEl.getBoundingClientRect().width);
        }

        function measureTxnAmountPx(label) {
            txMeasureAmountEl.textContent = label || '';
            return Math.ceil(txMeasureAmountEl.getBoundingClientRect().width);
        }

        function measureTxnAccountPx(source) {
            if (!source) return 0;
            txMeasureAccountEl.className = 'txn-source ' + source.toLowerCase();
            txMeasureAccountEl.textContent = source;
            return Math.ceil(txMeasureAccountEl.getBoundingClientRect().width);
        }

        function formatTxnAmountLabel(txn) {
            const tags = txn.tags || [];
            if (isIncome(tags)) {
                return '+' + formatCurrency(Math.abs(txn.amount));
            }
            return formatCurrency(txn.amount);
        }

        function clampPx(value, min, max) {
            return Math.max(min, Math.min(max, value));
        }

        function measureTxnColumnsForMode(mode) {
            ensureTxnMeasureElements();
            const items = getTxnItemsForMode(mode);
            let maxDate = 0;
            let maxAccount = 0;
            let maxAmount = 0;

            for (const item of items) {
                const txns = item.filteredTxns || item.transactions || [];

                for (const txn of txns) {
                    if (txn.date) {
                        const dateLabel = formatDate(txn.date, txn.month);
                        maxDate = Math.max(maxDate, measureTxnDatePx(dateLabel));
                    }
                    maxAccount = Math.max(maxAccount, measureTxnAccountPx(txn.source));
                    maxAmount = Math.max(maxAmount, measureTxnAmountPx(formatTxnAmountLabel(txn)));
                }
            }

            // Add small padding guardrails and clamp to desktop-appropriate bounds.
            return {
                date: clampPx(maxDate + 1, 70, 112),
                account: clampPx(maxAccount + 10, 110, 260),
                amount: clampPx(maxAmount + 4, 56, 160)
            };
        }

        function applyTxnColumnProfile(mode = getTxnModeKey()) {
            const profile = txColumnProfiles[mode];
            if (!profile) return;
            const rootStyle = document.documentElement.style;
            rootStyle.setProperty('--txn-date-col', profile.date + 'px');
            rootStyle.setProperty('--txn-account-col', profile.account + 'px');
            rootStyle.setProperty('--txn-amount-col', profile.amount + 'px');
        }

        function recomputeTxnColumnProfiles() {
            txColumnProfiles.merchant = measureTxnColumnsForMode('merchant');
            txColumnProfiles.subcategory = measureTxnColumnsForMode('subcategory');
            txColumnProfiles.section = hasSections.value ? measureTxnColumnsForMode('section') : txColumnProfiles.merchant;
            applyTxnColumnProfile();
        }

        function recomputeTxnColumnsDebounced() {
            if (txResizeDebounceHandle) clearTimeout(txResizeDebounceHandle);
            txResizeDebounceHandle = setTimeout(() => {
                recomputeTxnColumnProfiles();
            }, 200);
        }

        function rerenderChartsDebounced() {
            if (chartResizeDebounceHandle) clearTimeout(chartResizeDebounceHandle);
            chartResizeDebounceHandle = setTimeout(() => {
                if (!chartsInitialized) return;
                requestAnimationFrame(() => {
                    requestAnimationFrame(() => {
                        suppressChartAnimationForResize = true;
                        renderAllCharts();
                        suppressChartAnimationForResize = false;
                    });
                });
            }, 180);
        }

        function initChartLayoutObserver() {
            if (typeof ResizeObserver === 'undefined') return;
            if (chartLayoutObserver) chartLayoutObserver.disconnect();

            chartLayoutObserver = new ResizeObserver(() => {
                rerenderChartsDebounced();
            });

            const targets = [
                document.getElementById('app'),
                document.querySelector('.chart-section'),
                categoryTrendChart.value?.closest('.chart-wrapper'),
                cashFlowTrendChart.value?.closest('.chart-wrapper'),
                fixedVariableChart.value?.closest('.chart-wrapper'),
                volatilityChart.value?.closest('.chart-wrapper'),
            ].filter(Boolean);

            for (const target of targets) {
                chartLayoutObserver.observe(target);
            }
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
            applyChartDefaults();
            renderAllCharts();
        }

        function initTheme() {
            const saved = localStorage.getItem('theme');
            if (saved === 'light') {
                isDarkTheme.value = false;
                document.documentElement.setAttribute('data-theme', 'light');
            }
        }

        function toSortedArray(values) {
            return [...values].map(v => String(v)).sort((a, b) => a.localeCompare(b));
        }

        function parseDetailsViewMode(currentViewMode, groupMode) {
            if (currentViewMode === 'section') return 'section';
            return groupMode === 'subcategory' ? 'subcategory' : 'merchant';
        }

        function applyDetailsViewMode(mode) {
            if (mode === 'section' && hasSections.value) {
                currentView.value = 'section';
                return;
            }
            currentView.value = 'category';
            groupByMode.value = mode === 'subcategory' ? 'subcategory' : 'merchant';
        }

        function resetChartUiStateDefaults() {
            categoryState.grouping = 'month';
            categoryState.compare = false;
            categoryState.comparePage = 0;
            categoryState.unstack = false;
            categoryState.focused = null;
            categoryState.page = 0;

            cashState.grouping = 'month';
            cashState.compare = false;
            cashState.comparePage = 0;
            cashState.page = 0;

            fixedState.grouping = 'month';
            fixedState.compare = false;
            fixedState.comparePage = 0;
            fixedState.page = 0;

            heatmapState.page = 0;

            categoryHidden.clear();
            cashHidden.clear();
            fixedHidden.clear();

            chartPanels.category = false;
            chartPanels.seasonality = false;
            chartPanels.volatility = false;
            chartPanels.audit = false;
            chartPanels.cash = false;
            chartPanels.fixed = false;
        }

        function saveUiState() {
            if (isHydratingUiState) return;
            try {
                const state = {
                    version: 2,
                    detailsViewMode: parseDetailsViewMode(currentView.value, groupByMode.value),
                    includeNegativeTotals: !!includeNegativeTotals.value,
                    chartsCollapsed: !!chartsCollapsed.value,
                    detailsCollapsed: !!detailsCollapsed.value,
                    chartPanels: {
                        category: !!chartPanels.category,
                        seasonality: !!chartPanels.seasonality,
                        volatility: !!chartPanels.volatility,
                        audit: !!chartPanels.audit,
                        cash: !!chartPanels.cash,
                        fixed: !!chartPanels.fixed,
                    },
                    chartPreferences: {
                        category: {
                            grouping: categoryState.grouping,
                            compare: !!categoryState.compare,
                            comparePage: categoryState.comparePage,
                            unstack: !!categoryState.unstack,
                            focused: categoryState.focused || null,
                        },
                        cash: {
                            grouping: cashState.grouping,
                            compare: !!cashState.compare,
                            comparePage: cashState.comparePage,
                        },
                        fixed: {
                            grouping: fixedState.grouping,
                            compare: !!fixedState.compare,
                            comparePage: fixedState.comparePage,
                        },
                    },
                    knownSectionKeys: toSortedArray(allPersistableSectionKeys.value),
                    collapsedSectionKeys: toSortedArray(collapsedSections),
                    knownItemIds: toSortedArray(allPersistableItemIds.value),
                    expandedItemIds: toSortedArray(expandedMerchants),
                    sortConfig: Object.fromEntries(
                        Object.entries(sortConfig).map(([key, cfg]) => [key, {
                            column: cfg?.column || 'total',
                            dir: cfg?.dir === 'asc' ? 'asc' : 'desc'
                        }])
                    )
                };
                localStorage.setItem(UI_STATE_KEY, JSON.stringify(state));
            } catch {
                // Ignore localStorage failures (private mode/quota/security settings).
            }
        }

        function normalizeUiStateForCurrentData() {
            const validSectionKeys = allPersistableSectionKeys.value;
            const validItemIds = allPersistableItemIds.value;

            for (const key of [...collapsedSections]) {
                if (!validSectionKeys.has(String(key))) collapsedSections.delete(key);
            }
            for (const id of [...expandedMerchants]) {
                if (!validItemIds.has(String(id))) expandedMerchants.delete(id);
            }
            for (const key of Object.keys(sortConfig)) {
                if (!validSectionKeys.has(String(key))) delete sortConfig[key];
            }
        }

        function initUiState() {
            const validSectionKeys = allPersistableSectionKeys.value;
            const validItemIds = allPersistableItemIds.value;

            collapsedSections.clear();
            expandedMerchants.clear();
            Object.keys(sortConfig).forEach(key => delete sortConfig[key]);

            let state = null;
            try {
                const raw = localStorage.getItem(UI_STATE_KEY);
                if (raw) state = JSON.parse(raw);
            } catch {
                state = null;
            }

            if (!state || typeof state !== 'object') {
                validSectionKeys.forEach(key => collapsedSections.add(key));
                applyDetailsViewMode('merchant');
                includeNegativeTotals.value = false;
                chartsCollapsed.value = false;
                detailsCollapsed.value = false;
                resetChartUiStateDefaults();
                return;
            }

            const knownSectionKeys = new Set((state.knownSectionKeys || []).map(k => String(k)));
            const savedCollapsedSectionKeys = new Set((state.collapsedSectionKeys || []).map(k => String(k)));

            for (const key of validSectionKeys) {
                if (!knownSectionKeys.has(key) || savedCollapsedSectionKeys.has(key)) {
                    collapsedSections.add(key);
                }
            }

            const knownItemIds = new Set((state.knownItemIds || []).map(id => String(id)));
            const expandedItemIds = new Set((state.expandedItemIds || []).map(id => String(id)));
            for (const id of validItemIds) {
                if (knownItemIds.has(id) && expandedItemIds.has(id)) {
                    expandedMerchants.add(id);
                }
            }

            if (state.sortConfig && typeof state.sortConfig === 'object') {
                for (const [key, cfg] of Object.entries(state.sortConfig)) {
                    if (!validSectionKeys.has(String(key))) continue;
                    const column = cfg?.column || 'total';
                    const dir = cfg?.dir === 'asc' ? 'asc' : 'desc';
                    sortConfig[key] = { column, dir };
                }
            }

            applyDetailsViewMode(state.detailsViewMode);
            includeNegativeTotals.value = !!state.includeNegativeTotals;
            chartsCollapsed.value = !!state.chartsCollapsed;
            detailsCollapsed.value = !!state.detailsCollapsed;

            resetChartUiStateDefaults();
            const validGroupings = new Set(['year', 'quarter', 'month', 'none']);
            const chartPrefs = state.chartPreferences && typeof state.chartPreferences === 'object'
                ? state.chartPreferences
                : {};

            const catPrefs = chartPrefs.category || {};
            if (validGroupings.has(catPrefs.grouping)) categoryState.grouping = catPrefs.grouping;
            categoryState.compare = !!catPrefs.compare;
            categoryState.comparePage = Number.isInteger(catPrefs.comparePage) && catPrefs.comparePage >= 0
                ? catPrefs.comparePage
                : 0;
            categoryState.unstack = !!catPrefs.unstack;
            categoryState.focused = typeof catPrefs.focused === 'string' && catPrefs.focused.trim()
                ? catPrefs.focused.trim()
                : null;

            const cashPrefs = chartPrefs.cash || {};
            if (validGroupings.has(cashPrefs.grouping)) cashState.grouping = cashPrefs.grouping;
            cashState.compare = !!cashPrefs.compare;
            cashState.comparePage = Number.isInteger(cashPrefs.comparePage) && cashPrefs.comparePage >= 0
                ? cashPrefs.comparePage
                : 0;

            const fixedPrefs = chartPrefs.fixed || {};
            if (validGroupings.has(fixedPrefs.grouping)) fixedState.grouping = fixedPrefs.grouping;
            fixedState.compare = !!fixedPrefs.compare;
            fixedState.comparePage = Number.isInteger(fixedPrefs.comparePage) && fixedPrefs.comparePage >= 0
                ? fixedPrefs.comparePage
                : 0;

            const savedPanels = state.chartPanels && typeof state.chartPanels === 'object'
                ? state.chartPanels
                : {};
            chartPanels.category = !!savedPanels.category;
            chartPanels.seasonality = !!savedPanels.seasonality;
            chartPanels.volatility = !!savedPanels.volatility;
            chartPanels.audit = !!savedPanels.audit;
            chartPanels.cash = !!savedPanels.cash;
            chartPanels.fixed = !!savedPanels.fixed;

            normalizeUiStateForCurrentData();
        }

        function resetUiSettings() {
            try {
                localStorage.removeItem(UI_STATE_KEY);
            } catch {
                // Ignore localStorage failures.
            }
            initUiState();
            saveUiState();
            if (chartsInitialized) renderAllCharts();
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

        const CHART_PAGE_SIZE = 24;
        const COMPARE_YEARS_MAX = 3;
        const HEATMAP_PAGE_SIZE = 24;
        const HEATMAP_ALPHAS = [0.06, 0.22, 0.4, 0.58, 0.76, 0.95];
        const CASH_FLOW_SERIES = [
            { label: 'Spending', byMonthKey: 'spendingByMonth', color: '#4facfe' },
            { label: 'Income', byMonthKey: 'incomeByMonth', color: '#00c9a7' },
            { label: 'Credits', byMonthKey: 'creditsByMonth', color: '#ffa94d' },
            { label: 'Investment', byMonthKey: 'investmentByMonth', color: '#7c3aed' },
        ];

        const categoryState = reactive({ grouping: 'month', compare: false, comparePage: 0, unstack: false, focused: null, page: 0 });
        const cashState = reactive({ grouping: 'month', compare: false, comparePage: 0, page: 0 });
        const fixedState = reactive({ grouping: 'month', compare: false, comparePage: 0, page: 0 });
        const heatmapState = reactive({ page: 0 });
        const chartPanels = reactive({
            category: false,
            seasonality: false,
            volatility: false,
            audit: false,
            cash: false,
            fixed: false,
        });

        const categoryHidden = new Set();
        const cashHidden = new Set();
        const fixedHidden = new Set();
        let categoryFocusedHasAnimated = false;

        let chartsInitialized = false;
        let tooltipEl = null;
        let tooltipAnchorKey = null;

        function toggleChartPanel(panelKey) {
            if (!(panelKey in chartPanels)) return;
            chartPanels[panelKey] = !chartPanels[panelKey];
            saveUiState();
            if (chartsInitialized) {
                nextTick(() => renderAllCharts());
            }
        }

        function cssVar(name) {
            return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
        }

        function withAlpha(hex, alpha) {
            if (!hex || !hex.startsWith('#') || hex.length < 7) return hex;
            const r = parseInt(hex.slice(1, 3), 16);
            const g = parseInt(hex.slice(3, 5), 16);
            const b = parseInt(hex.slice(5, 7), 16);
            return `rgba(${r}, ${g}, ${b}, ${alpha})`;
        }

        function surfaceColor() {
            return cssVar('--bg-gradient-start') || '#1a1a2e';
        }

        function monthLabel(mk) {
            const [year, month] = mk.split('-');
            return `${MONTH_NAMES_SHORT[parseInt(month, 10) - 1]} '${year.slice(2)}`;
        }

        function getMonthKeys() {
            return filteredMonthsForCharts.value.map(m => m.key);
        }

        function getGroupingOptions(monthKeys) {
            const options = [];
            const years = [...new Set(monthKeys.map(mk => mk.split('-')[0]))];
            if (years.length > 1) options.push({ value: 'year', label: 'Years' });
            if (buildBuckets('quarter', monthKeys).length > 1) options.push({ value: 'quarter', label: 'Quarters' });
            options.push({ value: 'month', label: 'Months' });
            options.push({ value: 'none', label: 'None' });
            return options;
        }

        function ensureGroupingState(state, options) {
            const allowed = new Set(options.map(opt => opt.value));
            if (!allowed.has(state.grouping)) state.grouping = 'month';
        }

        function getSpanYears(monthKeys) {
            return [...new Set(monthKeys.map(mk => mk.split('-')[0]))].sort();
        }

        function getCompareYears(monthKeys, state) {
            const spanYears = getSpanYears(monthKeys);
            return pagedCompareYears(spanYears, state).years;
        }

        function pagedCompareYears(spanYears, state, pageSize = COMPARE_YEARS_MAX) {
            const pagerState = state || { comparePage: 0 };
            const total = spanYears.length;
            if (total <= pageSize) {
                if (state && pagerState.comparePage !== 0) pagerState.comparePage = 0;
                return {
                    years: spanYears,
                    total,
                    page: 0,
                    maxPage: 0,
                };
            }

            const maxPage = Math.max(0, total - pageSize);
            if (pagerState.comparePage > maxPage) pagerState.comparePage = maxPage;
            if (pagerState.comparePage < 0) pagerState.comparePage = 0;

            const startIdx = Math.max(0, total - pageSize - pagerState.comparePage);
            const endIdx = Math.min(total, startIdx + pageSize);

            return {
                years: spanYears.slice(startIdx, endIdx),
                total,
                page: pagerState.comparePage,
                maxPage,
            };
        }

        function yearAlpha(compareYears, year) {
            const idx = compareYears.length - 1 - compareYears.indexOf(year);
            return idx % 2 === 0 ? 1 : 0.55;
        }

        function buildBuckets(grouping, monthKeys) {
            const map = new Map();
            for (const mk of monthKeys) {
                const [y, m] = mk.split('-');
                let key;
                let label;
                if (grouping === 'year') {
                    key = y;
                    label = y;
                } else if (grouping === 'quarter') {
                    const q = Math.floor((parseInt(m, 10) - 1) / 3) + 1;
                    key = `${y}-Q${q}`;
                    label = `Q${q} '${y.slice(2)}`;
                } else {
                    key = mk;
                    label = monthLabel(mk);
                }
                if (!map.has(key)) map.set(key, { key, label, months: [] });
                map.get(key).months.push(mk);
            }
            return [...map.values()];
        }

        function sumFor(byMonth, bucket) {
            return bucket.months.reduce((sum, mk) => sum + (byMonth?.[mk] || 0), 0);
        }

        function comparePeriods(grouping) {
            if (grouping === 'quarter') {
                return [
                    { label: 'Q1', months: [1, 2, 3] },
                    { label: 'Q2', months: [4, 5, 6] },
                    { label: 'Q3', months: [7, 8, 9] },
                    { label: 'Q4', months: [10, 11, 12] },
                ];
            }
            return MONTH_NAMES_SHORT.map((name, idx) => ({ label: name, months: [idx + 1] }));
        }

        function xTickOptions(labels, canvasRef, compareActive = false) {
            const width = Math.max(
                canvasRef?.value?.clientWidth || canvasRef?.value?.parentElement?.clientWidth || 0,
                320
            );
            const count = Math.max(labels?.length || 0, 1);
            const pixelsPerTick = width / count;

            let rotation = 0;
            if (pixelsPerTick < 22) rotation = 90;
            else if (pixelsPerTick < 40) rotation = 45;

            const shouldAutoSkip = pixelsPerTick < (compareActive ? 34 : 26);
            const maxTicksLimit = Math.max(3, Math.floor(width / (compareActive ? 56 : 64)));

            return {
                minRotation: rotation,
                maxRotation: rotation,
                autoSkip: shouldAutoSkip,
                maxTicksLimit,
            };
        }

        function sumForPeriod(byMonth, year, period, monthSet) {
            let total = 0;
            for (const monthNum of period.months) {
                const mk = `${year}-${String(monthNum).padStart(2, '0')}`;
                if (!monthSet.has(mk)) continue;
                total += byMonth?.[mk] || 0;
            }
            return total;
        }

        function stripEmptyAxisSlots(labels, datasets) {
            if (!labels?.length || !datasets?.length) return { labels, datasets };

            const keepIndexes = [];
            for (let i = 0; i < labels.length; i += 1) {
                const hasValue = datasets.some(ds => Number(ds?.data?.[i] || 0) > 0);
                if (hasValue) keepIndexes.push(i);
            }

            if (keepIndexes.length === labels.length) return { labels, datasets };

            return {
                labels: keepIndexes.map(i => labels[i]),
                datasets: datasets.map(ds => ({
                    ...ds,
                    data: keepIndexes.map(i => Number(ds?.data?.[i] || 0)),
                })),
            };
        }

        function pagedBuckets(buckets, state, pageSize = CHART_PAGE_SIZE) {
            const total = buckets.length;
            const maxPage = Math.max(0, Math.ceil(total / pageSize) - 1);
            if (state.page > maxPage) state.page = maxPage;
            const end = total - state.page * pageSize;
            const start = Math.max(0, end - pageSize);
            return {
                all: buckets,
                page: buckets.slice(start, end),
                start,
                end,
                total,
                maxPage,
            };
        }

        function resetChartPages() {
            categoryState.page = 0;
            categoryState.comparePage = 0;
            cashState.page = 0;
            cashState.comparePage = 0;
            fixedState.page = 0;
            fixedState.comparePage = 0;
            heatmapState.page = 0;
        }

        function ensureTooltip() {
            if (!tooltipEl) tooltipEl = document.getElementById('ext-tooltip');
            return tooltipEl;
        }

        function extTooltipHandler(context) {
            const el = ensureTooltip();
            if (!el) return;

            const { chart, tooltip } = context;
            if (!tooltip || tooltip.opacity === 0 || !tooltip.dataPoints || !tooltip.dataPoints.length) {
                el.style.opacity = 0;
                tooltipAnchorKey = null;
                return;
            }

            const dp = tooltip.dataPoints[0];
            const dataIndex = dp.dataIndex;
            const hoveredDatasetIndex = dp.datasetIndex;
            const hoveredDataset = chart.data.datasets[hoveredDatasetIndex];
            const hoveredStack = hoveredDataset.stack;

            const rows = [];
            chart.data.datasets.forEach((ds, i) => {
                if (!chart.isDatasetVisible(i)) return;
                if (ds.ttSkip) return;
                if (hoveredStack !== undefined && ds.stack !== undefined && ds.stack !== hoveredStack) return;
                const value = ds.data[dataIndex];
                if (value === null || value === undefined) return;
                if (chart.options.ttHideZero && Math.abs(Number(value) || 0) < 1e-9) return;
                rows.push({
                    name: ds.label,
                    color: ds.baseColor || (Array.isArray(ds.backgroundColor) ? ds.backgroundColor[dataIndex] : ds.backgroundColor),
                    value,
                    active: chart.options.ttBoldLabel ? ds.label === chart.options.ttBoldLabel : i === hoveredDatasetIndex,
                });
            });
            if (chart.options.ttSortByValueDesc) {
                rows.sort((a, b) => (Number(b.value) || 0) - (Number(a.value) || 0));
            }

            let title = chart.data.labels[dataIndex];
            if (hoveredDataset.ttYear) title = `${title} ${hoveredDataset.ttYear}`;

            const totalLabel = chart.options.ttNet ? 'Net' : 'Total';
            const total = chart.options.ttNet
                ? rows.reduce((sum, row) => sum + ((row.name === 'Income' || row.name === 'Credits') ? row.value : -row.value), 0)
                : rows.reduce((sum, row) => sum + row.value, 0);

            el.textContent = '';
            const titleEl = document.createElement('div');
            titleEl.className = 'tt-title';
            titleEl.textContent = title;
            el.appendChild(titleEl);

            for (const rowData of rows) {
                const row = document.createElement('div');
                row.className = `tt-row${rowData.active ? ' active' : ''}`;
                const sw = document.createElement('span');
                sw.className = 'swatch';
                sw.style.background = rowData.color;
                const name = document.createElement('span');
                name.className = 'tt-name';
                name.textContent = rowData.name;
                const value = document.createElement('span');
                value.className = 'tt-val';
                value.textContent = formatCurrency(rowData.value);
                row.append(sw, name, value);
                el.appendChild(row);
            }

            const totalRow = document.createElement('div');
            totalRow.className = 'tt-total';
            const tn = document.createElement('span');
            tn.textContent = totalLabel;
            const tv = document.createElement('span');
            tv.textContent = formatCurrency(total);
            totalRow.append(tn, tv);
            el.appendChild(totalRow);

            el.style.opacity = 1;
            const anchorKey = `${chart.canvas.id}:${dataIndex}:${hoveredStack || ''}`;
            if (anchorKey !== tooltipAnchorKey) {
                tooltipAnchorKey = anchorKey;
                const rect = chart.canvas.getBoundingClientRect();
                let x = rect.left + window.scrollX + tooltip.caretX + 14;
                let y = rect.top + window.scrollY + tooltip.caretY - el.offsetHeight / 2;
                if (x + el.offsetWidth > window.scrollX + document.documentElement.clientWidth - 8) {
                    x = rect.left + window.scrollX + tooltip.caretX - el.offsetWidth - 14;
                }
                y = Math.max(window.scrollY + 8, y);
                el.style.left = `${x}px`;
                el.style.top = `${y}px`;
            }
        }

        function showHeatmapTooltip(cell, category, mk, amount, min, max) {
            const el = ensureTooltip();
            if (!el) return;

            tooltipAnchorKey = null;
            el.textContent = '';

            const title = document.createElement('div');
            title.className = 'tt-title';
            title.textContent = `${category} - ${monthLabel(mk)}`;
            el.appendChild(title);

            const row1 = document.createElement('div');
            row1.className = 'tt-row';
            row1.innerHTML = `<span class="tt-name">Spent</span><span class="tt-val">${formatCurrency(amount)}</span>`;
            const row2 = document.createElement('div');
            row2.className = 'tt-row';
            row2.innerHTML = `<span class="tt-name">Row range</span><span class="tt-val">${formatCurrency(min)} - ${formatCurrency(max)}</span>`;
            el.append(row1, row2);

            const rect = cell.getBoundingClientRect();
            el.style.opacity = 1;
            let x = rect.left + window.scrollX + rect.width / 2 + 12;
            if (x + el.offsetWidth > window.scrollX + document.documentElement.clientWidth - 8) {
                x = rect.left + window.scrollX - el.offsetWidth - 12;
            }
            el.style.left = `${x}px`;
            el.style.top = `${rect.top + window.scrollY - el.offsetHeight - 8}px`;
        }

        function externalTooltipConfig() {
            return { enabled: false, external: extTooltipHandler };
        }

        function ensureChartPlugins() {
            if (window.__tallyChartsReimaginedPlugins) return;

            const yearSubLabelsPlugin = {
                id: 'yearSubLabels',
                afterDraw(chart) {
                    if (!chart.options.yearSubLabels) return;
                    const xScale = chart.scales.x;
                    if (!xScale) return;

                    const ctx = chart.ctx;
                    ctx.save();
                    const tickFont = Chart.helpers.toFont(
                        xScale.options?.ticks?.font,
                        Chart.defaults.font
                    );
                    ctx.font = tickFont.string;
                    ctx.fillStyle = cssVar('--text-muted') || '#666';
                    ctx.textAlign = 'center';
                    ctx.textBaseline = 'middle';

                    const tickCount = chart.data?.labels?.length || 0;
                    const pixelsPerTick = tickCount ? (xScale.width / tickCount) : xScale.width;
                    let angle = 0;
                    if (pixelsPerTick < 22) angle = -Math.PI / 2;
                    else if (pixelsPerTick < 40) angle = -Math.PI / 4;

                    let drawEvery = 1;
                    if (pixelsPerTick < 14) drawEvery = 4;
                    else if (pixelsPerTick < 20) drawEvery = 3;
                    else if (pixelsPerTick < 28) drawEvery = 2;

                    const y = xScale.bottom - 4;

                    const byYear = new Map();
                    chart.data.datasets.forEach((ds, i) => {
                        if (!ds.ttYear || !chart.isDatasetVisible(i)) return;
                        if (!byYear.has(ds.ttYear)) byYear.set(ds.ttYear, []);
                        byYear.get(ds.ttYear).push(i);
                    });

                    for (const [year, indexes] of byYear.entries()) {
                        for (let di = 0; di < chart.data.labels.length; di += drawEvery) {
                            let sumX = 0;
                            let count = 0;
                            let hasValue = false;
                            for (const datasetIndex of indexes) {
                                const point = chart.getDatasetMeta(datasetIndex).data[di];
                                if (!point) continue;
                                sumX += point.x;
                                count += 1;
                                if ((chart.data.datasets[datasetIndex].data[di] || 0) > 0) hasValue = true;
                            }
                            if (count && hasValue) {
                                const shortYear = String(year).slice(-2);
                                const x = sumX / count;
                                if (angle === 0) {
                                    ctx.fillText(`'${shortYear}`, x, y);
                                } else {
                                    ctx.save();
                                    ctx.translate(x, y);
                                    ctx.rotate(angle);
                                    ctx.fillText(`'${shortYear}`, 0, 0);
                                    ctx.restore();
                                }
                            }
                        }
                    }

                    ctx.restore();
                },
            };

            const crosshairPlugin = {
                id: 'crosshair',
                afterDraw(chart) {
                    if (!chart.options.crosshair || !chart.tooltip) return;
                    const active = chart.tooltip.getActiveElements();
                    if (!active.length) return;

                    const x = active[0].element.x;
                    const { top, bottom } = chart.chartArea;
                    const ctx = chart.ctx;
                    ctx.save();
                    ctx.strokeStyle = cssVar('--border-medium') || 'rgba(255,255,255,0.2)';
                    ctx.lineWidth = 1;
                    ctx.beginPath();
                    ctx.moveTo(x, top);
                    ctx.lineTo(x, bottom);
                    ctx.stroke();
                    ctx.restore();
                },
            };

            Chart.register(yearSubLabelsPlugin, crosshairPlugin);
            window.__tallyChartsReimaginedPlugins = true;
        }

        function applyChartDefaults() {
            Chart.defaults.color = cssVar('--text-secondary') || '#888';
            Chart.defaults.borderColor = cssVar('--border-light') || 'rgba(255,255,255,0.1)';
            Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
            Chart.defaults.maintainAspectRatio = false;
            Chart.defaults.responsive = true;
        }

        function destroyChart(key) {
            if (chartInstances[key]) {
                chartInstances[key].destroy();
                chartInstances[key] = null;
            }
        }

        function renderChart(key, canvasRef, config) {
            if (!canvasRef?.value) return null;
            destroyChart(key);
            let finalConfig = config;
            if (suppressChartAnimationForResize) {
                finalConfig = {
                    ...config,
                    options: {
                        ...(config.options || {}),
                        animation: false,
                    },
                };
            }
            chartInstances[key] = new Chart(canvasRef.value, finalConfig);
            return chartInstances[key];
        }

        function pillGroup(el, options, current, onChange) {
            if (!el) return;
            el.textContent = '';
            for (const option of options) {
                const b = document.createElement('button');
                b.className = `proto-pill${option.value === current ? ' active' : ''}`;
                b.textContent = option.label;
                b.setAttribute('aria-pressed', option.value === current ? 'true' : 'false');
                b.addEventListener('click', () => onChange(option.value));
                el.appendChild(b);
            }
        }

        function chipLegend(el, items) {
            if (!el) return;
            el.textContent = '';
            for (const item of items) {
                const b = document.createElement('button');
                b.className = `legend-chip${item.active ? ' active' : ''}`;
                if (item.active) b.style.borderColor = item.color;
                const dot = document.createElement('span');
                dot.className = 'chip-dot';
                dot.style.background = item.active ? item.color : 'rgba(128,134,150,0.6)';
                b.append(dot, document.createTextNode(item.label));
                b.addEventListener('click', item.onClick);
                el.appendChild(b);
            }
        }

        function sparklineSVG(values, dotColor) {
            const w = 260;
            const h = 28;
            const pad = 4;
            const min = Math.min(...values);
            const max = Math.max(...values);
            const span = max - min || 1;
            const points = values.map((value, idx) => [
                pad + idx * (w - pad * 2) / Math.max(values.length - 1, 1),
                h - pad - (value - min) / span * (h - pad * 2),
            ]);

            const svgNs = 'http://www.w3.org/2000/svg';
            const svg = document.createElementNS(svgNs, 'svg');
            svg.setAttribute('viewBox', `0 0 ${w} ${h}`);
            svg.setAttribute('preserveAspectRatio', 'none');

            const polyline = document.createElementNS(svgNs, 'polyline');
            polyline.setAttribute('points', points.map(p => `${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(' '));
            polyline.setAttribute('fill', 'none');
            polyline.setAttribute('stroke', cssVar('--text-muted') || '#666');
            polyline.setAttribute('stroke-width', '1.5');
            polyline.setAttribute('stroke-linejoin', 'round');
            polyline.setAttribute('stroke-linecap', 'round');

            const last = points[points.length - 1];
            const dot = document.createElementNS(svgNs, 'circle');
            dot.setAttribute('cx', last[0].toFixed(1));
            dot.setAttribute('cy', last[1].toFixed(1));
            dot.setAttribute('r', '3');
            dot.setAttribute('fill', dotColor);

            svg.append(polyline, dot);
            return svg;
        }

        function trimTrailingZeroMonths(series, monthKeys) {
            let lastNonZero = -1;
            for (let i = series.length - 1; i >= 0; i--) {
                if (Math.abs(series[i] || 0) > 0.0001) {
                    lastNonZero = i;
                    break;
                }
            }

            if (lastNonZero < 0) {
                return { series, monthKeys };
            }

            return {
                series: series.slice(0, lastNonZero + 1),
                monthKeys: monthKeys.slice(0, lastNonZero + 1),
            };
        }

        function trendStatement(series, monthKeys, upIsGood) {
            const len = series.length;
            const n = Math.min(12, len - 1);
            if (n < 1) return null;

            const last = series[len - 1] || 0;
            const baseline = series.slice(len - 1 - n, len - 1).reduce((sum, v) => sum + v, 0) / n;
            if (!baseline) return null;

            const delta = last - baseline;
            const pct = Math.abs(delta / baseline * 100);
            return {
                arrow: delta >= 0 ? '↑' : '↓',
                value: `${delta >= 0 ? '+' : '-'}${pct.toFixed(1)}%`,
                sentence: ` vs prior ${n} month${n === 1 ? '' : 's'}`,
                cls: (delta >= 0) === upIsGood ? 'pos' : 'neg',
            };
        }

        function renderKpis(monthKeys, agg, fixedMonthlyBaseline) {
            const grid = kpiGrid.value;
            if (!grid) return;
            if (!monthKeys.length) {
                grid.textContent = '';
                return;
            }

            const orderedMonthKeys = [...monthKeys].sort();

            const sum = byMonth => orderedMonthKeys.reduce((total, mk) => total + (byMonth?.[mk] || 0), 0);
            const income = sum(agg.incomeByMonth);
            const credits = sum(agg.creditsByMonth);
            const spending = sum(agg.spendingByMonth);
            const investment = sum(agg.investmentByMonth);
            const transfers = sum(agg.transfersByMonth);
            const txCount = orderedMonthKeys.reduce((total, mk) => total + (agg.txnCountByMonth?.[mk] || 0), 0);
            const cashFlowTotal = calculateCashFlow(income, spending, credits);

            const incomeOnlySeries = orderedMonthKeys.map(mk => agg.incomeByMonth[mk] || 0);
            const creditsSeries = orderedMonthKeys.map(mk => agg.creditsByMonth[mk] || 0);
            const incomeSeries = orderedMonthKeys.map(mk => (agg.incomeByMonth[mk] || 0) + (agg.creditsByMonth[mk] || 0));
            const spendingSeries = orderedMonthKeys.map(mk => agg.spendingByMonth[mk] || 0);
            const recurringSeries = orderedMonthKeys.map(mk => agg.recurringSpendingByMonth?.[mk] || 0);
            const cashFlowSeries = orderedMonthKeys.map(mk =>
                calculateCashFlow(
                    agg.incomeByMonth[mk] || 0,
                    agg.spendingByMonth[mk] || 0,
                    agg.creditsByMonth[mk] || 0,
                )
            );

            grid.textContent = '';

            function createCard(className, title, headline, details, spark, subtitle, options = {}) {
                const card = document.createElement('div');
                card.className = `kpi-card ${className}`;
                if (options.cardTestId) card.setAttribute('data-testid', options.cardTestId);

                const topWrap = document.createElement('div');
                topWrap.className = 'kpi-top';

                const h = document.createElement('h4');
                h.textContent = title;

                const value = document.createElement('div');
                value.className = 'kpi-value';
                if (options.amountTestId) value.setAttribute('data-testid', options.amountTestId);

                const primary = document.createElement('span');
                primary.className = 'kpi-value-primary';
                primary.textContent = headline;
                value.appendChild(primary);

                if (options.headlineSecondary) {
                    const secondary = document.createElement('span');
                    secondary.className = 'kpi-value-secondary';
                    secondary.textContent = options.headlineSecondary;
                    value.appendChild(secondary);
                }

                topWrap.append(h, value);

                if (subtitle) {
                    const sub = document.createElement('div');
                    sub.className = 'kpi-sub';
                    sub.textContent = subtitle;
                    topWrap.appendChild(sub);
                }

                if (spark) {
                    const sparkWrap = document.createElement('div');
                    sparkWrap.className = 'kpi-spark';
                    sparkWrap.appendChild(sparklineSVG(spark.values, spark.dotColor));
                    topWrap.appendChild(sparkWrap);

                    if (spark.trend) {
                        const trend = document.createElement('div');
                        trend.className = 'kpi-trend';
                        const val = document.createElement('span');
                        val.className = `val ${spark.trend.cls}`;
                        val.textContent = `${spark.trend.arrow} ${spark.trend.value}`;
                        trend.append(val, document.createTextNode(spark.trend.sentence));
                        topWrap.appendChild(trend);
                    }
                }

                card.appendChild(topWrap);

                const detailWrap = document.createElement('div');
                detailWrap.className = 'kpi-details';
                if (options.detailsClass) detailWrap.classList.add(options.detailsClass);
                for (const detail of details) {
                    if (detail.dividerBefore) {
                        const divider = document.createElement('div');
                        divider.className = 'kpi-detail-divider';
                        detailWrap.appendChild(divider);
                    }
                    const row = document.createElement('div');
                    row.className = 'kpi-detail';
                    if (detail.breakdown) row.classList.add('breakdown-item');
                    const name = document.createElement('span');
                    name.className = 'name';
                    if (detail.nameClass) name.classList.add(detail.nameClass);
                    name.textContent = detail.name;
                    if (detail.onClick) {
                        name.style.cursor = 'pointer';
                        name.addEventListener('click', detail.onClick);
                    }
                    const val = document.createElement('span');
                    val.className = 'value';
                    if (detail.valueTone) val.classList.add(detail.valueTone);
                    val.textContent = detail.value;
                    if (detail.secondaryValue) {
                        const detailSecondary = document.createElement('span');
                        detailSecondary.className = 'kpi-detail-secondary';
                        detailSecondary.textContent = ` / ${detail.secondaryValue}`;
                        val.appendChild(detailSecondary);
                    }
                    if (detail.perMonth) {
                        const perMo = document.createElement('span');
                        perMo.className = 'permo';
                        perMo.textContent = `· ${detail.perMonth}`;
                        val.appendChild(perMo);
                    }
                    row.append(name, val);
                    detailWrap.appendChild(row);
                }
                card.appendChild(detailWrap);
                grid.appendChild(card);
            }

            function lastActiveIndex(series) {
                for (let i = series.length - 1; i >= 0; i--) {
                    if (Math.abs(series[i] || 0) > 0.0001) return i;
                }
                return Math.max(0, series.length - 1);
            }

            function rangeSum(series, startIdx, endIdx) {
                if (!series.length) return 0;
                let total = 0;
                for (let i = Math.max(0, startIdx); i <= Math.min(endIdx, series.length - 1); i++) {
                    total += series[i] || 0;
                }
                return total;
            }

            function lastNSeriesTotal(series, endIdx, count) {
                return rangeSum(series, endIdx - count + 1, endIdx);
            }

            function parseMonthKey(mk) {
                const [yearStr, monthStr] = mk.split('-');
                const year = parseInt(yearStr, 10);
                const month = parseInt(monthStr, 10);
                return {
                    year,
                    month,
                    serial: year * 12 + month,
                };
            }

            function sumWithPredicate(series, predicate) {
                let total = 0;
                for (let i = 0; i < orderedMonthKeys.length; i++) {
                    const info = parseMonthKey(orderedMonthKeys[i]);
                    if (predicate(info)) total += series[i] || 0;
                }
                return total;
            }

            function countWithPredicate(predicate) {
                let count = 0;
                for (let i = 0; i < orderedMonthKeys.length; i++) {
                    const info = parseMonthKey(orderedMonthKeys[i]);
                    if (predicate(info)) count += 1;
                }
                return count;
            }

            function monthLabelComma(mk) {
                return monthLabel(mk).replace(" '", ", '");
            }

            function periodMetrics(primarySeries, secondarySeries, anchorIdx) {
                const anchorKey = orderedMonthKeys[anchorIdx];
                const anchorInfo = parseMonthKey(anchorKey);
                const quarter = Math.floor((anchorInfo.month - 1) / 3) + 1;
                const quarterStart = (quarter - 1) * 3 + 1;

                const inQuarter = info =>
                    info.year === anchorInfo.year && info.month >= quarterStart && info.month <= anchorInfo.month;
                const inYtd = info =>
                    info.year === anchorInfo.year && info.month <= anchorInfo.month;
                const inLast12 = info =>
                    info.serial >= (anchorInfo.serial - 11) && info.serial <= anchorInfo.serial;

                // Use the same baseline as trend: average of prior up-to-12 months,
                // excluding the anchor/current month.
                const priorCount = Math.min(12, anchorIdx);
                const quarterPrimary = sumWithPredicate(primarySeries, inQuarter);
                const ytdPrimary = sumWithPredicate(primarySeries, inYtd);
                const last12Primary = priorCount > 0
                    ? rangeSum(primarySeries, anchorIdx - priorCount, anchorIdx - 1)
                    : (primarySeries[anchorIdx] || 0);
                const allTimePrimary = primarySeries.reduce((sum, value) => sum + (value || 0), 0);

                const quarterSecondary = secondarySeries ? sumWithPredicate(secondarySeries, inQuarter) : null;
                const ytdSecondary = secondarySeries ? sumWithPredicate(secondarySeries, inYtd) : null;
                const last12Secondary = secondarySeries
                    ? (priorCount > 0
                        ? rangeSum(secondarySeries, anchorIdx - priorCount, anchorIdx - 1)
                        : (secondarySeries[anchorIdx] || 0))
                    : null;
                const allTimeSecondary = secondarySeries
                    ? secondarySeries.reduce((sum, value) => sum + (value || 0), 0)
                    : null;

                return {
                    anchorLabel: monthLabelComma(anchorKey),
                    quarterLabel: `Q${quarter} ${anchorInfo.year}`,
                    ytdLabel: `${anchorInfo.year} To Date`,
                    anchorPrimary: primarySeries[anchorIdx] || 0,
                    anchorSecondary: secondarySeries ? (secondarySeries[anchorIdx] || 0) : null,
                    quarterPrimary,
                    quarterSecondary,
                    ytdPrimary,
                    ytdSecondary,
                    last12Primary,
                    last12Secondary,
                    last12AvgPrimary: priorCount > 0 ? (last12Primary / priorCount) : last12Primary,
                    last12AvgSecondary: secondarySeries
                        ? (priorCount > 0 ? (last12Secondary / priorCount) : last12Secondary)
                        : null,
                    allTimePrimary,
                    allTimeSecondary,
                };
            }

            const incomeIdx = lastActiveIndex(incomeSeries);
            const spendingIdx = lastActiveIndex(spendingSeries);
            const cashFlowIdx = lastActiveIndex(cashFlowSeries);

            const incomeMonthKey = orderedMonthKeys[incomeIdx];
            const spendingMonthKey = orderedMonthKeys[spendingIdx];
            const cashFlowMonthKey = orderedMonthKeys[cashFlowIdx];

            const incomeMonth = incomeSeries[incomeIdx] || 0;
            const creditsMonth = creditsSeries[incomeIdx] || 0;
            const incomeLast3 = lastNSeriesTotal(incomeOnlySeries, incomeIdx, 3);
            const creditsLast3 = lastNSeriesTotal(creditsSeries, incomeIdx, 3);

            const spendingMonth = spendingSeries[spendingIdx] || 0;
            const recurringMonth = recurringSeries[spendingIdx] || 0;

            const cashFlowMonth = cashFlowSeries[cashFlowIdx] || 0;

            const incomePeriods = periodMetrics(incomeSeries, creditsSeries, incomeIdx);
            const spendingPeriods = periodMetrics(spendingSeries, recurringSeries, spendingIdx);
            const cashFlowPeriods = periodMetrics(cashFlowSeries, null, cashFlowIdx);

            function spark(series, upIsGood) {
                const trimmed = trimTrailingZeroMonths(series, orderedMonthKeys);
                const trend = trendStatement(trimmed.series, trimmed.monthKeys, upIsGood);
                const good = trend && trend.cls === 'pos';
                return {
                    values: trimmed.series,
                    trend,
                    dotColor: good ? (cssVar('--accent-green') || '#4ade80') : (cssVar('--accent-red') || '#ff6b6b'),
                };
            }

            createCard('income', `${incomePeriods.anchorLabel} Income`, formatCurrency(incomeMonth), [
                {
                    name: incomePeriods.quarterLabel,
                    value: formatCurrency(incomePeriods.quarterPrimary),
                    secondaryValue: formatCurrency(incomePeriods.quarterSecondary),
                },
                {
                    name: incomePeriods.ytdLabel,
                    value: formatCurrency(incomePeriods.ytdPrimary),
                    secondaryValue: formatCurrency(incomePeriods.ytdSecondary),
                },
                {
                    name: '12 Month Avg',
                    value: formatCurrency(incomePeriods.last12AvgPrimary),
                    secondaryValue: formatCurrency(incomePeriods.last12AvgSecondary),
                },
                {
                    name: 'All Time',
                    value: formatCurrency(incomePeriods.allTimePrimary),
                    secondaryValue: formatCurrency(incomePeriods.allTimeSecondary),
                },
            ], spark(incomeSeries, true), null, {
                headlineSecondary: `${formatCurrency(creditsMonth)} in credits`,
            });

            createCard('spending', `${spendingPeriods.anchorLabel} Spending`, formatCurrency(spendingMonth), [
                {
                    name: spendingPeriods.quarterLabel,
                    value: formatCurrency(spendingPeriods.quarterPrimary),
                    secondaryValue: formatCurrency(spendingPeriods.quarterSecondary),
                },
                {
                    name: spendingPeriods.ytdLabel,
                    value: formatCurrency(spendingPeriods.ytdPrimary),
                    secondaryValue: formatCurrency(spendingPeriods.ytdSecondary),
                },
                {
                    name: '12 Month Avg',
                    value: formatCurrency(spendingPeriods.last12AvgPrimary),
                    secondaryValue: formatCurrency(spendingPeriods.last12AvgSecondary),
                },
                {
                    name: 'All Time',
                    value: formatCurrency(spendingPeriods.allTimePrimary),
                    secondaryValue: formatCurrency(spendingPeriods.allTimeSecondary),
                },
            ], spark(spendingSeries, false), null, {
                headlineSecondary: `${formatCurrency(recurringMonth)} are fixed`,
                cardTestId: 'filtered-spending-card',
                amountTestId: 'filtered-amount',
            });

            createCard(
                `cashflow ${cashFlowMonth >= 0 ? 'positive' : 'negative'}`,
                `${cashFlowPeriods.anchorLabel} Cash Flow`,
                formatCurrency(cashFlowMonth),
                [
                    {
                        name: cashFlowPeriods.quarterLabel,
                        value: formatCurrency(cashFlowPeriods.quarterPrimary),
                        valueTone: cashFlowPeriods.quarterPrimary >= 0 ? 'pos' : 'neg',
                    },
                    {
                        name: cashFlowPeriods.ytdLabel,
                        value: formatCurrency(cashFlowPeriods.ytdPrimary),
                        valueTone: cashFlowPeriods.ytdPrimary >= 0 ? 'pos' : 'neg',
                    },
                    {
                        name: '12 Month Avg',
                        value: formatCurrency(cashFlowPeriods.last12AvgPrimary),
                        valueTone: cashFlowPeriods.last12AvgPrimary >= 0 ? 'pos' : 'neg',
                    },
                    {
                        name: 'All Time',
                        value: formatCurrency(cashFlowPeriods.allTimePrimary),
                        valueTone: cashFlowPeriods.allTimePrimary >= 0 ? 'pos' : 'neg',
                    },
                ],
                spark(cashFlowSeries, true),
                null,
                {
                    detailsClass: 'cashflow-details',
                    cardTestId: 'cashflow-card',
                    amountTestId: 'cashflow-amount',
                }
            );

            createCard('details', 'Details', txCount.toLocaleString('en-US'), [
                { name: 'Transfers', value: formatCurrency(transfers) },
                { name: 'Investments', value: formatCurrency(investment) },
            ], null, null, {
                headlineSecondary: 'transactions',
            });
        }

        function buildCategoryBreakdown(agg) {
            const ranked = Object.entries(agg.byCategory)
                .filter(([, total]) => total > 0)
                .sort((a, b) => b[1] - a[1])
                .map(([name]) => name);
            const topCategories = ranked.slice(0, TOP_CATEGORY_COUNT);
            const otherCategories = ranked.slice(TOP_CATEGORY_COUNT);
            return { ranked, topCategories, otherCategories };
        }

        function renderBucketPager(elId, allBuckets, state, grouping, onChange) {
            const el = document.getElementById(elId);
            if (!el) return;
            el.textContent = '';

            if (allBuckets.length <= CHART_PAGE_SIZE || grouping === 'none') return;

            const noun = grouping === 'year' ? 'years' : grouping === 'quarter' ? 'quarters' : 'months';
            const windowed = pagedBuckets(allBuckets, state, CHART_PAGE_SIZE);

            const prev = document.createElement('button');
            prev.textContent = `‹ Prev ${CHART_PAGE_SIZE} ${noun}`;
            prev.disabled = windowed.start === 0;
            prev.addEventListener('click', () => {
                state.page += 1;
                onChange();
            });

            const range = document.createElement('span');
            range.className = 'pager-range';
            const startLabel = windowed.page[0]?.label || '';
            const endLabel = windowed.page[windowed.page.length - 1]?.label || '';
            range.textContent = `${startLabel} - ${endLabel} · showing ${windowed.page.length} of ${windowed.total} ${noun}`;

            const next = document.createElement('button');
            next.textContent = `Next ${CHART_PAGE_SIZE} ${noun} ›`;
            next.disabled = state.page === 0;
            next.addEventListener('click', () => {
                state.page -= 1;
                onChange();
            });

            el.append(prev, range, next);
        }

        function renderCompareYearPager(elId, comparePageInfo, onChange) {
            const el = document.getElementById(elId);
            if (!el) return;
            el.textContent = '';

            if (!comparePageInfo || comparePageInfo.total <= COMPARE_YEARS_MAX) return;

            const prev = document.createElement('button');
            prev.textContent = `‹ Prev ${COMPARE_YEARS_MAX} years`;
            prev.disabled = comparePageInfo.page >= comparePageInfo.maxPage;
            prev.addEventListener('click', () => onChange('older'));

            const range = document.createElement('span');
            range.className = 'pager-range';
            const firstYear = comparePageInfo.years[0] || '';
            const lastYear = comparePageInfo.years[comparePageInfo.years.length - 1] || '';
            range.textContent = `${firstYear} - ${lastYear} · showing ${comparePageInfo.years.length} of ${comparePageInfo.total} years`;

            const next = document.createElement('button');
            next.textContent = `Next ${COMPARE_YEARS_MAX} years ›`;
            next.disabled = comparePageInfo.page === 0;
            next.addEventListener('click', () => onChange('newer'));

            el.append(prev, range, next);
        }

        function renderHeatmap(matrix, keys, categories) {
            const el = document.getElementById('seasonality-heatmap');
            if (!el) return;

            const red = cssVar('--accent-red') || '#ff6b6b';
            el.textContent = '';
            el.style.gridTemplateColumns = `140px repeat(${keys.length}, 1fr)`;
            el.appendChild(document.createElement('div'));

            keys.forEach((mk, idx) => {
                const label = document.createElement('div');
                label.className = 'hm-collabel';
                const [year, month] = mk.split('-');
                label.textContent = MONTH_NAMES_SHORT[parseInt(month, 10) - 1];
                if (idx === 0 || month === '01') {
                    const yearEl = document.createElement('span');
                    yearEl.className = 'yy';
                    yearEl.textContent = `'${year.slice(2)}`;
                    label.appendChild(yearEl);
                }
                el.appendChild(label);
            });

            for (const category of categories) {
                const rowLabel = document.createElement('div');
                rowLabel.className = 'hm-rowlabel';
                rowLabel.textContent = category;
                el.appendChild(rowLabel);

                const values = keys.map(mk => matrix[category]?.[mk] || 0);
                const min = Math.min(...values);
                const max = Math.max(...values);

                keys.forEach((mk, idx) => {
                    const value = values[idx];
                    const t = max > min ? (value - min) / (max - min) : 0.5;
                    const colorIdx = Math.round(t * (HEATMAP_ALPHAS.length - 1));
                    const cell = document.createElement('div');
                    cell.className = 'hm-cell';
                    cell.style.background = withAlpha(red, HEATMAP_ALPHAS[colorIdx]);
                    cell.addEventListener('mouseenter', () => showHeatmapTooltip(cell, category, mk, value, min, max));
                    cell.addEventListener('mouseleave', () => {
                        const tip = ensureTooltip();
                        if (tip) tip.style.opacity = 0;
                        tooltipAnchorKey = null;
                    });
                    el.appendChild(cell);
                });
            }
        }

        function renderPagedHeatmap(agg, monthKeys, categories) {
            const pager = document.getElementById('seasonality-heatmap-pager');
            const caption = document.getElementById('seasonality-heatmap-caption');
            if (pager) pager.textContent = '';
            if (caption) {
                caption.textContent = '';
                const red = cssVar('--accent-red') || '#ff6b6b';
                caption.appendChild(document.createTextNode('Low'));
                const ramp = document.createElement('span');
                ramp.className = 'hm-ramp';
                for (const alpha of HEATMAP_ALPHAS) {
                    const cell = document.createElement('span');
                    cell.className = 'hm-ramp-cell';
                    cell.style.background = withAlpha(red, alpha);
                    ramp.appendChild(cell);
                }
                caption.appendChild(ramp);
                caption.appendChild(document.createTextNode('High'));
                const detail = document.createElement('div');
                detail.className = 'chart-caption-break';
                detail.textContent = 'Each row relative to its own range.';
                caption.appendChild(detail);
            }

            const total = monthKeys.length;
            const maxPage = Math.max(0, Math.ceil(total / HEATMAP_PAGE_SIZE) - 1);
            if (heatmapState.page > maxPage) heatmapState.page = maxPage;
            const end = total - heatmapState.page * HEATMAP_PAGE_SIZE;
            const start = Math.max(0, end - HEATMAP_PAGE_SIZE);
            const pageKeys = monthKeys.slice(start, end);

            renderHeatmap(agg.byCategoryByMonth, pageKeys, categories);

            if (!pager || total <= HEATMAP_PAGE_SIZE) return;
            const prev = document.createElement('button');
            prev.textContent = `‹ Prev ${HEATMAP_PAGE_SIZE} months`;
            prev.disabled = start === 0;
            prev.addEventListener('click', () => {
                heatmapState.page += 1;
                renderAllCharts();
            });

            const range = document.createElement('span');
            range.className = 'pager-range';
            range.textContent = `${monthLabel(pageKeys[0])} - ${monthLabel(pageKeys[pageKeys.length - 1])} · showing ${pageKeys.length} of ${total} months`;

            const next = document.createElement('button');
            next.textContent = `Next ${HEATMAP_PAGE_SIZE} months ›`;
            next.disabled = heatmapState.page === 0;
            next.addEventListener('click', () => {
                heatmapState.page -= 1;
                renderAllCharts();
            });

            pager.append(prev, range, next);
        }

        function renderShareView(agg, topCategories, otherCategories) {
            const container = document.getElementById('cat-share-view');
            if (!container) return;

            const allBucket = { months: getMonthKeys() };
            const rows = topCategories.map(name => ({
                name,
                total: sumFor(agg.byCategoryByMonth[name], allBucket),
                color: categoryColorMap.value[name],
            }));
            if (otherCategories.length) {
                rows.push({
                    name: OTHER_CATEGORY_LABEL,
                    total: otherCategories.reduce((sum, name) => sum + sumFor(agg.byCategoryByMonth[name], allBucket), 0),
                    color: OTHER_CATEGORY_COLOR,
                });
            }

            const grand = rows.reduce((sum, row) => sum + row.total, 0) || 1;
            const maxTotal = Math.max(...rows.map(r => r.total), 1);

            container.textContent = '';
            const strip = document.createElement('div');
            strip.className = 'share-strip';
            const list = document.createElement('div');
            list.className = 'share-rows';

            for (const rowData of rows) {
                const seg = document.createElement('div');
                seg.className = 'share-seg';
                seg.style.width = `${(rowData.total / grand) * 100}%`;
                seg.style.background = rowData.color;

                const row = document.createElement('div');
                row.className = 'share-row';
                const sw = document.createElement('span');
                sw.className = 'swatch';
                sw.style.background = rowData.color;
                const name = document.createElement('span');
                name.className = 'share-name';
                name.textContent = rowData.name;
                const track = document.createElement('div');
                track.className = 'share-bar-track';
                const fill = document.createElement('div');
                fill.className = 'share-bar-fill';
                fill.style.width = `${(rowData.total / maxTotal) * 100}%`;
                fill.style.background = withAlpha(rowData.color, 0.55);
                track.appendChild(fill);
                const amt = document.createElement('span');
                amt.className = 'share-amt';
                amt.textContent = formatCurrency(rowData.total);
                const pct = document.createElement('span');
                pct.className = 'share-pct';
                pct.textContent = `${((rowData.total / grand) * 100).toFixed(1)}%`;
                row.append(sw, name, track, amt, pct);

                const toggleHighlight = on => {
                    seg.classList.toggle('hl', on);
                    row.classList.toggle('hl', on);
                    strip.classList.toggle('emph', on);
                    list.classList.toggle('emph', on);
                };

                seg.addEventListener('mouseenter', () => toggleHighlight(true));
                seg.addEventListener('mouseleave', () => toggleHighlight(false));
                row.addEventListener('mouseenter', () => toggleHighlight(true));
                row.addEventListener('mouseleave', () => toggleHighlight(false));

                strip.appendChild(seg);
                list.appendChild(row);
            }

            container.append(strip, list);
        }

        function renderCategoryTrend(agg, monthKeys, topCategories, otherCategories) {
            const groupingOptions = getGroupingOptions(monthKeys);
            ensureGroupingState(categoryState, groupingOptions);
            const groupEl = document.getElementById('cat-group-pills');
            pillGroup(groupEl, groupingOptions, categoryState.grouping, value => {
                categoryState.grouping = value;
                categoryState.page = 0;
                categoryState.comparePage = 0;
                saveUiState();
                renderAllCharts();
            });

            const compareWrap = document.getElementById('cat-compare-wrap');
            const compareBox = document.getElementById('cat-compare-checkbox');
            const unstackWrap = document.getElementById('cat-unstack-wrap');
            const unstackBox = document.getElementById('cat-unstack-checkbox');
            const caption = document.getElementById('cat-chart-caption');
            const shareView = document.getElementById('cat-share-view');
            const legend = document.getElementById('cat-legend-chips');
            const canvasWrap = categoryTrendChart.value?.closest('.chart-wrapper');

            const grouped = categoryState.grouping !== 'none';
            const spanYears = getSpanYears(monthKeys);
            const comparePageInfo = pagedCompareYears(spanYears, categoryState);
            const compareYears = comparePageInfo.years;
            const compareAllowed = spanYears.length > 1 && (categoryState.grouping === 'month' || categoryState.grouping === 'quarter');
            const compareActive = compareAllowed && categoryState.compare;

            if (compareWrap) compareWrap.classList.toggle('hidden', !compareAllowed);
            if (compareBox) compareBox.checked = compareActive;
            if (unstackWrap) unstackWrap.classList.toggle('hidden', !grouped);
            if (unstackBox) {
                unstackBox.checked = grouped && categoryState.unstack;
                unstackBox.disabled = compareActive;
            }
            if (compareBox) compareBox.disabled = !!categoryState.unstack;
            if (caption) caption.textContent = '';

            if (!grouped) {
                destroyChart('category');
                if (canvasWrap) canvasWrap.hidden = true;
                if (shareView) shareView.hidden = false;
                if (legend) legend.textContent = '';
                renderShareView(agg, topCategories, otherCategories);
                renderBucketPager('cat-chart-pager', [], categoryState, 'none', renderAllCharts);
                return;
            }

            if (canvasWrap) canvasWrap.hidden = false;
            if (shareView) shareView.hidden = true;

            const allBuckets = buildBuckets(categoryState.grouping, monthKeys);
            const pageInfo = pagedBuckets(allBuckets, categoryState, CHART_PAGE_SIZE);
            if (compareActive) {
                renderCompareYearPager('cat-chart-pager', comparePageInfo, direction => {
                    categoryState.comparePage += direction === 'older' ? 1 : -1;
                    renderAllCharts();
                });
            } else {
                renderBucketPager('cat-chart-pager', allBuckets, categoryState, categoryState.grouping, renderAllCharts);
            }

            if (categoryState.unstack) {
                const labels = pageInfo.page.map(b => b.label);
                const categories = topCategories.concat(otherCategories.length ? [OTHER_CATEGORY_LABEL] : []);
                if (!categoryState.focused || !categories.includes(categoryState.focused)) {
                    categoryState.focused = categories[0] || null;
                }
                const focused = categoryState.focused;
                const animateFocused = !categoryFocusedHasAnimated;
                const dimColor = 'rgba(128,134,150,0.35)';
                const datasets = categories.map(name => {
                    const isOther = name === OTHER_CATEGORY_LABEL;
                    const baseColor = isOther ? OTHER_CATEGORY_COLOR : categoryColorMap.value[name];
                    const isFocused = focused === name;
                    return {
                        label: name,
                        data: pageInfo.page.map(bucket => {
                            if (isOther) {
                                return otherCategories.reduce((sum, cat) => sum + sumFor(agg.byCategoryByMonth[cat], bucket), 0);
                            }
                            return sumFor(agg.byCategoryByMonth[name], bucket);
                        }),
                        borderColor: isFocused ? baseColor : dimColor,
                        backgroundColor: isFocused ? baseColor : dimColor,
                        borderWidth: isFocused ? 3 : 1.5,
                        pointRadius: isFocused ? 2.5 : 0,
                        pointHoverRadius: 4,
                        tension: 0.25,
                        order: isFocused ? 0 : 1,
                        baseColor,
                    };
                });

                renderChart('category', categoryTrendChart, {
                    type: 'line',
                    data: { labels, datasets },
                    options: {
                        animation: animateFocused ? undefined : false,
                        ttSortByValueDesc: true,
                        ttHideZero: true,
                        crosshair: true,
                        ttBoldLabel: focused,
                        interaction: { mode: 'index', intersect: false },
                        plugins: {
                            legend: { display: false },
                            tooltip: externalTooltipConfig(),
                        },
                        scales: {
                            x: { grid: { display: false } },
                            y: { beginAtZero: true, ticks: { callback: v => formatCurrencyShort(v) } },
                        },
                    },
                });
                categoryFocusedHasAnimated = true;

                chipLegend(legend, categories.map(name => ({
                    label: name,
                    color: name === OTHER_CATEGORY_LABEL ? OTHER_CATEGORY_COLOR : categoryColorMap.value[name],
                    active: focused === name,
                    onClick: () => {
                        categoryState.focused = name;
                        saveUiState();
                        renderAllCharts();
                    },
                })));
                return;
            }
            categoryFocusedHasAnimated = false;

            const gap = surfaceColor();
            let labels = [];
            let datasets = [];

            if (compareActive) {
                const periods = comparePeriods(categoryState.grouping);
                const monthSet = new Set(monthKeys);
                labels = periods.map(p => p.label);
                const categories = topCategories.concat(otherCategories.length ? [OTHER_CATEGORY_LABEL] : []);

                for (const year of compareYears) {
                    const alpha = yearAlpha(compareYears, year);
                    for (const name of categories) {
                        const color = name === OTHER_CATEGORY_LABEL ? OTHER_CATEGORY_COLOR : categoryColorMap.value[name];
                        datasets.push({
                            label: name,
                            data: periods.map(period => {
                                if (name === OTHER_CATEGORY_LABEL) {
                                    return otherCategories.reduce((sum, cat) =>
                                        sum + sumForPeriod(agg.byCategoryByMonth[cat], year, period, monthSet), 0);
                                }
                                return sumForPeriod(agg.byCategoryByMonth[name], year, period, monthSet);
                            }),
                            backgroundColor: withAlpha(color, alpha),
                            borderColor: gap,
                            borderWidth: 1,
                            stack: `y${year}`,
                            ttYear: year,
                            baseColor: color,
                            hidden: categoryHidden.has(name),
                        });
                    }
                }
            } else {
                labels = pageInfo.page.map(b => b.label);
                datasets = topCategories.map(name => ({
                    label: name,
                    data: pageInfo.page.map(bucket => sumFor(agg.byCategoryByMonth[name], bucket)),
                    backgroundColor: categoryColorMap.value[name],
                    borderColor: gap,
                    borderWidth: 1,
                    stack: 'spend',
                    hidden: categoryHidden.has(name),
                }));
                if (otherCategories.length) {
                    datasets.push({
                        label: OTHER_CATEGORY_LABEL,
                        data: pageInfo.page.map(bucket => otherCategories.reduce((sum, cat) => sum + sumFor(agg.byCategoryByMonth[cat], bucket), 0)),
                        backgroundColor: OTHER_CATEGORY_COLOR,
                        borderColor: gap,
                        borderWidth: 1,
                        stack: 'spend',
                        hidden: categoryHidden.has(OTHER_CATEGORY_LABEL),
                    });
                }
            }

            ({ labels, datasets } = stripEmptyAxisSlots(labels, datasets));

            renderChart('category', categoryTrendChart, {
                type: 'bar',
                data: { labels, datasets },
                options: {
                    ttSortByValueDesc: true,
                    ttHideZero: true,
                    yearSubLabels: compareActive,
                    interaction: { mode: 'nearest', intersect: true },
                    plugins: {
                        legend: { display: false },
                        tooltip: externalTooltipConfig(),
                    },
                    scales: {
                        x: compareActive
                            ? {
                                stacked: true,
                                grid: { offset: true },
                                ticks: xTickOptions(labels, categoryTrendChart, true),
                                afterFit: scale => { scale.height += 8; },
                            }
                            : {
                                stacked: true,
                                grid: { display: false },
                                ticks: xTickOptions(labels, categoryTrendChart, false),
                            },
                        y: { stacked: true, ticks: { callback: v => formatCurrencyShort(v) } },
                    },
                },
            });

            const legendCategories = topCategories.concat(otherCategories.length ? [OTHER_CATEGORY_LABEL] : []);
            chipLegend(legend, legendCategories.map(name => ({
                label: name,
                color: name === OTHER_CATEGORY_LABEL ? OTHER_CATEGORY_COLOR : categoryColorMap.value[name],
                active: !categoryHidden.has(name),
                onClick: () => {
                    if (categoryHidden.has(name)) categoryHidden.delete(name);
                    else categoryHidden.add(name);
                    renderAllCharts();
                },
            })));

            if (caption) {
                caption.textContent = spanYears.length > compareYears.length && compareActive ? `showing last ${COMPARE_YEARS_MAX} years` : '';
            }
        }

        function renderCashFlow(agg, monthKeys) {
            const groupingOptions = getGroupingOptions(monthKeys);
            ensureGroupingState(cashState, groupingOptions);
            const groupEl = document.getElementById('cash-group-pills');
            pillGroup(groupEl, groupingOptions, cashState.grouping, value => {
                cashState.grouping = value;
                cashState.page = 0;
                cashState.comparePage = 0;
                saveUiState();
                renderAllCharts();
            });

            const compareWrap = document.getElementById('cash-compare-wrap');
            const compareBox = document.getElementById('cash-compare-checkbox');
            const caption = document.getElementById('cash-chart-caption');
            const legend = document.getElementById('cash-legend-chips');
            const spanYears = getSpanYears(monthKeys);
            const comparePageInfo = pagedCompareYears(spanYears, cashState);
            const compareYears = comparePageInfo.years;
            const compareAllowed = spanYears.length > 1 && (cashState.grouping === 'month' || cashState.grouping === 'quarter');
            const compareActive = compareAllowed && cashState.compare;
            if (compareWrap) compareWrap.classList.toggle('hidden', !compareAllowed);
            if (compareBox) compareBox.checked = compareActive;
            if (caption) caption.textContent = '';

            if (cashState.grouping === 'none') {
                const allBucket = { months: monthKeys };
                renderChart('cashFlow', cashFlowTrendChart, {
                    type: 'bar',
                    data: {
                        labels: CASH_FLOW_SERIES.map(s => s.label),
                        datasets: [{
                            data: CASH_FLOW_SERIES.map(s => sumFor(agg[s.byMonthKey], allBucket)),
                            backgroundColor: CASH_FLOW_SERIES.map(s => s.color),
                            borderRadius: 4,
                        }],
                    },
                    options: {
                        plugins: {
                            legend: { display: false },
                            tooltip: { callbacks: { label: ctx => formatCurrency(ctx.parsed.y) } },
                        },
                        scales: { y: { ticks: { callback: v => formatCurrencyShort(v) } } },
                    },
                });
                if (legend) legend.textContent = '';
                renderBucketPager('cash-chart-pager', [], cashState, 'none', renderAllCharts);
                return;
            }

            const allBuckets = buildBuckets(cashState.grouping, monthKeys);
            const pageInfo = pagedBuckets(allBuckets, cashState, CHART_PAGE_SIZE);
            if (compareActive) {
                renderCompareYearPager('cash-chart-pager', comparePageInfo, direction => {
                    cashState.comparePage += direction === 'older' ? 1 : -1;
                    renderAllCharts();
                });
            } else {
                renderBucketPager('cash-chart-pager', allBuckets, cashState, cashState.grouping, renderAllCharts);
            }

            let labels = [];
            let datasets = [];
            if (compareActive) {
                const periods = comparePeriods(cashState.grouping);
                const monthSet = new Set(monthKeys);
                labels = periods.map(p => p.label);
                for (const year of compareYears) {
                    const alpha = yearAlpha(compareYears, year);
                    for (const series of CASH_FLOW_SERIES) {
                        const data = periods.map(period => sumForPeriod(agg[series.byMonthKey], year, period, monthSet));
                        if (!data.some(v => v > 0)) continue;
                        datasets.push({
                            label: series.label,
                            data,
                            backgroundColor: withAlpha(series.color, alpha),
                            borderRadius: 3,
                            ttYear: year,
                            hidden: cashHidden.has(series.label),
                        });
                    }
                }
            } else {
                labels = pageInfo.page.map(b => b.label);
                datasets = CASH_FLOW_SERIES
                    .map(series => ({
                        label: series.label,
                        data: pageInfo.page.map(bucket => sumFor(agg[series.byMonthKey], bucket)),
                        backgroundColor: series.color,
                        borderRadius: 4,
                        hidden: cashHidden.has(series.label),
                    }))
                    .filter(ds => ds.data.some(v => v > 0));
            }

                    ({ labels, datasets } = stripEmptyAxisSlots(labels, datasets));

            renderChart('cashFlow', cashFlowTrendChart, {
                type: 'bar',
                data: { labels, datasets },
                options: {
                    ttNet: true,
                    yearSubLabels: compareActive,
                    interaction: { mode: 'nearest', intersect: true },
                    plugins: {
                        legend: { display: false },
                        tooltip: externalTooltipConfig(),
                    },
                    scales: {
                        x: compareActive
                            ? {
                                grid: { offset: true },
                                ticks: xTickOptions(labels, cashFlowTrendChart, true),
                                afterFit: scale => { scale.height += 8; },
                            }
                            : {
                                grid: { display: false },
                                ticks: xTickOptions(labels, cashFlowTrendChart, false),
                            },
                        y: { ticks: { callback: v => formatCurrencyShort(v) } },
                    },
                },
            });

            chipLegend(legend, CASH_FLOW_SERIES.map(series => ({
                label: series.label,
                color: series.color,
                active: !cashHidden.has(series.label),
                onClick: () => {
                    if (cashHidden.has(series.label)) cashHidden.delete(series.label);
                    else cashHidden.add(series.label);
                    renderAllCharts();
                },
            })));

            if (caption) {
                caption.textContent = spanYears.length > compareYears.length && compareActive ? `showing last ${COMPARE_YEARS_MAX} years` : '';
            }
        }

        function buildFixedVariableSeries(agg, monthKeys) {
            let fixedTotal = 0;
            const fixedByMonth = {};
            const variableByMonth = {};
            for (const mk of monthKeys) {
                const fixed = agg.recurringSpendingByMonth?.[mk] || 0;
                fixedByMonth[mk] = fixed;
                fixedTotal += fixed;
                variableByMonth[mk] = Math.max((agg.spendingByMonth[mk] || 0) - fixed, 0);
            }
            const fixedMonthly = monthKeys.length ? fixedTotal / monthKeys.length : 0;
            return {
                fixedByMonth,
                variableByMonth,
                fixedMonthly,
                recurringMerchants: agg.recurringMerchants,
            };
        }

        function renderFixedVariable(agg, monthKeys, fvModel) {
            const series = [
                { label: 'Fixed', byMonth: fvModel.fixedByMonth, color: '#4facfe' },
                { label: 'Variable', byMonth: fvModel.variableByMonth, color: '#ffa94d' },
            ];

            const groupingOptions = getGroupingOptions(monthKeys);
            ensureGroupingState(fixedState, groupingOptions);
            const groupEl = document.getElementById('fixed-group-pills');
            pillGroup(groupEl, groupingOptions, fixedState.grouping, value => {
                fixedState.grouping = value;
                fixedState.page = 0;
                fixedState.comparePage = 0;
                saveUiState();
                renderAllCharts();
            });

            const compareWrap = document.getElementById('fixed-compare-wrap');
            const compareBox = document.getElementById('fixed-compare-checkbox');
            const caption = document.getElementById('fixed-chart-caption');
            const legend = document.getElementById('fixed-legend-chips');
            const spanYears = getSpanYears(monthKeys);
            const comparePageInfo = pagedCompareYears(spanYears, fixedState);
            const compareYears = comparePageInfo.years;
            const compareAllowed = spanYears.length > 1 && (fixedState.grouping === 'month' || fixedState.grouping === 'quarter');
            const compareActive = compareAllowed && fixedState.compare;
            if (compareWrap) compareWrap.classList.toggle('hidden', !compareAllowed);
            if (compareBox) compareBox.checked = compareActive;

            const fixedNames = fvModel.recurringMerchants
                .map(row => row.merchant)
                .sort((a, b) => a.localeCompare(b));

            if (fixedState.grouping === 'none') {
                const allBucket = { months: monthKeys };
                renderChart('fixedVariable', fixedVariableChart, {
                    type: 'bar',
                    data: {
                        labels: series.map(s => s.label),
                        datasets: [{
                            data: series.map(s => sumFor(s.byMonth, allBucket)),
                            backgroundColor: series.map(s => s.color),
                            borderRadius: 4,
                        }],
                    },
                    options: {
                        plugins: {
                            legend: { display: false },
                            tooltip: { callbacks: { label: ctx => formatCurrency(ctx.parsed.y) } },
                        },
                        scales: { y: { ticks: { callback: v => formatCurrencyShort(v) } } },
                    },
                });
                if (legend) legend.textContent = '';
                renderBucketPager('fixed-chart-pager', [], fixedState, 'none', renderAllCharts);
            } else {
                const allBuckets = buildBuckets(fixedState.grouping, monthKeys);
                const pageInfo = pagedBuckets(allBuckets, fixedState, CHART_PAGE_SIZE);
                if (compareActive) {
                    renderCompareYearPager('fixed-chart-pager', comparePageInfo, direction => {
                        fixedState.comparePage += direction === 'older' ? 1 : -1;
                        renderAllCharts();
                    });
                } else {
                    renderBucketPager('fixed-chart-pager', allBuckets, fixedState, fixedState.grouping, renderAllCharts);
                }

                const gap = surfaceColor();
                let labels = [];
                let datasets = [];
                if (compareActive) {
                    const periods = comparePeriods(fixedState.grouping);
                    const monthSet = new Set(monthKeys);
                    labels = periods.map(p => p.label);
                    for (const year of compareYears) {
                        const alpha = yearAlpha(compareYears, year);
                        for (const s of series) {
                            datasets.push({
                                label: s.label,
                                data: periods.map(period => sumForPeriod(s.byMonth, year, period, monthSet)),
                                backgroundColor: withAlpha(s.color, alpha),
                                borderColor: gap,
                                borderWidth: 1,
                                stack: `y${year}`,
                                ttYear: year,
                                hidden: fixedHidden.has(s.label),
                            });
                        }
                    }
                } else {
                    labels = pageInfo.page.map(bucket => bucket.label);
                    datasets = series.map(s => ({
                        label: s.label,
                        data: pageInfo.page.map(bucket => sumFor(s.byMonth, bucket)),
                        backgroundColor: s.color,
                        borderColor: gap,
                        borderWidth: 1,
                        stack: 'fv',
                        hidden: fixedHidden.has(s.label),
                    }));
                }

                ({ labels, datasets } = stripEmptyAxisSlots(labels, datasets));

                renderChart('fixedVariable', fixedVariableChart, {
                    type: 'bar',
                    data: { labels, datasets },
                    options: {
                        yearSubLabels: compareActive,
                        interaction: { mode: 'nearest', intersect: true },
                        plugins: {
                            legend: { display: false },
                            tooltip: externalTooltipConfig(),
                        },
                        scales: {
                            x: compareActive
                                ? {
                                    stacked: true,
                                    grid: { offset: true },
                                    ticks: xTickOptions(labels, fixedVariableChart, true),
                                    afterFit: scale => { scale.height += 8; },
                                }
                                : {
                                    stacked: true,
                                    grid: { display: false },
                                    ticks: xTickOptions(labels, fixedVariableChart, false),
                                },
                            y: { stacked: true, ticks: { callback: v => formatCurrencyShort(v) } },
                        },
                    },
                });

                chipLegend(legend, series.map(s => ({
                    label: s.label,
                    color: s.color,
                    active: !fixedHidden.has(s.label),
                    onClick: () => {
                        if (fixedHidden.has(s.label)) fixedHidden.delete(s.label);
                        else fixedHidden.add(s.label);
                        renderAllCharts();
                    },
                })));
            }

            if (caption) {
                const topFixedNames = fixedNames.slice(0, 10);
                const names = topFixedNames.length ? topFixedNames.join(', ') : 'none in current filter';
                const hiddenCount = Math.max(fixedNames.length - topFixedNames.length, 0);
                const more = hiddenCount ? `, + ${hiddenCount} more` : '';
                const truncation = compareActive && spanYears.length > compareYears.length ? ` · showing last ${COMPARE_YEARS_MAX} years` : '';
                caption.textContent = `Top 10 Fixed: ${names}${more}${truncation}`;
            }
        }

        function renderVolatility(agg, monthKeys) {
            const rows = Object.keys(agg.byCategoryByMonth)
                .map(name => {
                    const values = monthKeys.map(mk => agg.byCategoryByMonth[name]?.[mk] || 0);
                    const min = Math.min(...values);
                    const max = Math.max(...values);
                    const avg = values.reduce((sum, v) => sum + v, 0) / Math.max(values.length, 1);
                    return { name, min, max, avg, range: max - min };
                })
                .filter(row => row.max > 0)
                .sort((a, b) => b.range - a.range)
                .slice(0, 10);

            renderChart('volatility', volatilityChart, {
                type: 'bar',
                data: {
                    labels: rows.map(row => row.name),
                    datasets: [
                        {
                            label: 'Monthly range',
                            data: rows.map(row => [row.min, row.max]),
                            backgroundColor: rows.map(row => withAlpha(categoryColorMap.value[row.name] || '#4facfe', 0.45)),
                            borderColor: rows.map(row => categoryColorMap.value[row.name] || '#4facfe'),
                            borderWidth: 1.5,
                            borderRadius: 4,
                            borderSkipped: false,
                            barThickness: 26,
                            maxBarThickness: 26,
                            barPercentage: 0.55,
                        },
                        {
                            label: 'Average',
                            type: 'scatter',
                            data: rows.map(row => ({ x: row.avg, y: row.name })),
                            pointStyle: 'line',
                            rotation: 90,
                            radius: 14,
                            hoverRadius: 14,
                            borderWidth: 2.5,
                            borderColor: cssVar('--text-primary') || '#e8e8e8',
                        },
                    ],
                },
                options: {
                    indexAxis: 'y',
                    layout: {
                        padding: {
                            top: 10,
                            bottom: 10,
                        },
                    },
                    plugins: {
                        legend: { display: false },
                        tooltip: {
                            boxPadding: 6,
                            callbacks: {
                                label: ctx => {
                                    const row = rows[ctx.dataIndex];
                                    return `Min ${formatCurrency(row.min)} · Avg ${formatCurrency(row.avg)} · Max ${formatCurrency(row.max)}`;
                                },
                            },
                        },
                    },
                    scales: {
                        x: { ticks: { callback: v => formatCurrencyShort(v) } },
                        y: {
                            offset: true,
                            grid: { display: false },
                        },
                    },
                },
            });
        }

        function renderAuditTable(recurringMerchants) {
            const container = document.getElementById('recurring-audit');
            if (!container) return;

            container.classList.add('audit-scroll');
            const rows = recurringMerchants
                .map(row => ({
                    merchant: row.merchant,
                    category: row.category,
                    cadence: row.cadence === 'annual' ? 'Annual' : 'Monthly',
                    monthly: row.recurringMonthlyCost,
                    annualized: row.recurringMonthlyCost * 12,
                }))
                .sort((a, b) => b.annualized - a.annualized);

            const visible = rows.slice(0, 10);
            const hidden = rows.slice(10);
            const maxAnnual = Math.max(...rows.map(r => r.annualized), 1);
            const totalAnnual = rows.reduce((sum, row) => sum + row.annualized, 0);
            const totalMonthly = rows.reduce((sum, row) => sum + row.monthly, 0);

            container.textContent = '';
            const table = document.createElement('table');
            table.className = 'audit-table';
            const thead = document.createElement('thead');
            const hr = document.createElement('tr');
            [['Merchant'], ['Category'], ['Cadence'], ['Monthly', 'num'], ['Annualized', 'num']].forEach(([title, cls]) => {
                const th = document.createElement('th');
                th.textContent = title;
                if (cls) th.className = cls;
                hr.appendChild(th);
            });
            thead.appendChild(hr);
            table.appendChild(thead);

            const tbody = document.createElement('tbody');
            for (const rowData of visible) {
                const tr = document.createElement('tr');
                const tdMerchant = document.createElement('td');
                tdMerchant.textContent = rowData.merchant;
                const tdCategory = document.createElement('td');
                tdCategory.className = 'cat';
                tdCategory.textContent = rowData.category;
                const tdCadence = document.createElement('td');
                const badge = document.createElement('span');
                badge.className = 'cadence';
                badge.textContent = rowData.cadence;
                tdCadence.appendChild(badge);
                const tdMonthly = document.createElement('td');
                tdMonthly.className = 'num';
                tdMonthly.textContent = formatCurrencyDecimalValue(rowData.monthly);
                const tdAnnual = document.createElement('td');
                tdAnnual.className = 'num annual';
                const track = document.createElement('div');
                track.className = 'bar-track';
                const fill = document.createElement('div');
                fill.className = 'bar-fill';
                fill.style.width = `${Math.round((rowData.annualized / maxAnnual) * 100)}%`;
                const label = document.createElement('span');
                label.textContent = formatCurrency(rowData.annualized);
                track.append(fill, label);
                tdAnnual.appendChild(track);
                tr.append(tdMerchant, tdCategory, tdCadence, tdMonthly, tdAnnual);
                tbody.appendChild(tr);
            }

            if (hidden.length) {
                const tr = document.createElement('tr');
                const td = document.createElement('td');
                td.colSpan = 3;
                td.className = 'cat';
                td.textContent = `+ ${hidden.length} more`;
                const tdMonthly = document.createElement('td');
                tdMonthly.className = 'num cat';
                tdMonthly.textContent = '';
                const tdAnnual = document.createElement('td');
                tdAnnual.className = 'num cat';
                tdAnnual.textContent = `${formatCurrency(hidden.reduce((sum, row) => sum + row.annualized, 0))} / yr`;
                tr.append(td, tdMonthly, tdAnnual);
                tbody.appendChild(tr);
            }

            const totalRow = document.createElement('tr');
            totalRow.className = 'audit-total-row';
            const label = document.createElement('td');
            label.colSpan = 3;
            label.textContent = `${rows.length} recurring merchants`;
            label.style.whiteSpace = 'nowrap';
            totalRow.appendChild(label);
            const monthly = document.createElement('td');
            monthly.className = 'num';
            monthly.textContent = formatCurrencyDecimalValue(totalMonthly);
            const annual = document.createElement('td');
            annual.className = 'num';
            annual.textContent = `${formatCurrency(totalAnnual)} / yr`;
            totalRow.append(monthly, annual);
            tbody.appendChild(totalRow);

            table.appendChild(tbody);
            container.appendChild(table);
        }

        function renderAllCharts() {
            if (!chartsInitialized) return;

            const monthKeys = getMonthKeys();
            const agg = chartAggregations.value;
            const { topCategories, otherCategories } = buildCategoryBreakdown(agg);
            const fvModel = buildFixedVariableSeries(agg, monthKeys);

            renderKpis(monthKeys, agg, fvModel.fixedMonthly);
            renderCategoryTrend(agg, monthKeys, topCategories, otherCategories);
            renderPagedHeatmap(agg, monthKeys, topCategories);
            renderVolatility(agg, monthKeys);
            renderAuditTable(agg.recurringMerchants);
            renderCashFlow(agg, monthKeys);
            renderFixedVariable(agg, monthKeys, fvModel);
        }

        function wireChartControls() {
            if (chartsInitialized) return;

            const catCompare = document.getElementById('cat-compare-checkbox');
            if (catCompare) {
                catCompare.addEventListener('change', () => {
                    categoryState.compare = catCompare.checked;
                    categoryState.page = 0;
                    categoryState.comparePage = 0;
                    saveUiState();
                    renderAllCharts();
                });
            }

            const catUnstack = document.getElementById('cat-unstack-checkbox');
            if (catUnstack) {
                catUnstack.addEventListener('change', () => {
                    categoryState.unstack = catUnstack.checked;
                    if (categoryState.unstack) categoryState.compare = false;
                    categoryState.page = 0;
                    categoryState.comparePage = 0;
                    saveUiState();
                    renderAllCharts();
                });
            }

            const cashCompare = document.getElementById('cash-compare-checkbox');
            if (cashCompare) {
                cashCompare.addEventListener('change', () => {
                    cashState.compare = cashCompare.checked;
                    cashState.page = 0;
                    cashState.comparePage = 0;
                    saveUiState();
                    renderAllCharts();
                });
            }

            const fixedCompare = document.getElementById('fixed-compare-checkbox');
            if (fixedCompare) {
                fixedCompare.addEventListener('change', () => {
                    fixedState.compare = fixedCompare.checked;
                    fixedState.page = 0;
                    fixedState.comparePage = 0;
                    saveUiState();
                    renderAllCharts();
                });
            }

            chartsInitialized = true;
        }

        function initCharts() {
            ensureChartPlugins();
            applyChartDefaults();
            wireChartControls();
            renderAllCharts();
        }

        // ========== SCROLL HANDLING ==========

        function handleScroll() {
            isScrolled.value = window.scrollY > 50;
        }

        // ========== WATCHERS ==========

        watch(activeFilters, filtersToHash, { deep: true });
        watch(chartAggregations, () => {
            renderAllCharts();
        });
        watch(
            () => filteredMonthsForCharts.value.map(m => m.key).join('|'),
            () => {
                resetChartPages();
                renderAllCharts();
            }
        );
        watch([currentView, groupByMode, hasSections], () => {
            nextTick(() => applyTxnColumnProfile());
        });
        watch([currentView, groupByMode], saveUiState);
        watch([chartsCollapsed, detailsCollapsed, includeNegativeTotals], saveUiState);
        watch(
            () => Array.from(collapsedSections).map(v => String(v)).sort(),
            saveUiState
        );
        watch(
            () => Array.from(expandedMerchants).map(v => String(v)).sort(),
            saveUiState
        );
        watch(
            () => JSON.stringify(sortConfig),
            saveUiState
        );
        watch(
            () => JSON.stringify(chartPanels),
            saveUiState
        );
        watch(allPersistableSectionKeys, () => {
            normalizeUiStateForCurrentData();
            saveUiState();
        });
        watch(allPersistableItemIds, () => {
            normalizeUiStateForCurrentData();
            saveUiState();
        });

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
            initUiState();
            isHydratingUiState = false;
            saveUiState();

            // Wait for next tick to ensure computed properties are ready
            nextTick(() => {
                hashToFilters();
                recomputeTxnColumnProfiles();
                initCharts();
                initChartLayoutObserver();
                requestAnimationFrame(() => {
                    requestAnimationFrame(() => {
                        setAppReadyState();
                    });
                });
            });

            // Scroll handling
            window.addEventListener('scroll', handleScroll);
            window.addEventListener('resize', recomputeTxnColumnsDebounced);
            window.addEventListener('resize', rerenderChartsDebounced);

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

        onUnmounted(() => {
            if (chartLayoutObserver) {
                chartLayoutObserver.disconnect();
                chartLayoutObserver = null;
            }
            if (txResizeDebounceHandle) {
                clearTimeout(txResizeDebounceHandle);
                txResizeDebounceHandle = null;
            }
            if (chartResizeDebounceHandle) {
                clearTimeout(chartResizeDebounceHandle);
                chartResizeDebounceHandle = null;
            }
        });

        // ========== RETURN ==========

        return {
            // State
            activeFilters, expandedMerchants, extraFieldMatches, collapsedSections, searchQuery,
            showAutocomplete, autocompleteIndex, isScrolled, isDarkTheme, chartsCollapsed,
            currentView, groupByMode, sortConfig, includeNegativeTotals, detailsCollapsed, allCollapsed, detailsSummary,
            chartPanels,
            // Refs
            kpiGrid, categoryTrendChart, cashFlowTrendChart, fixedVariableChart, volatilityChart,
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
            addFilter, removeFilter, toggleIncludeFilter, isIncludeFilterActive, toggleFilterMode, clearFilters, addMonthFilter,
            toggleExpand, toggleSection, toggleSort, toggleAllSections, sortedMerchants,
            formatCurrency, formatDate, formatMonthLabel, formatPct, filterTypeChar,
            highlightDescription,
            onSearchInput, onSearchKeydown, selectAutocompleteItem,
            toggleTheme, toggleChartPanel, resetUiSettings
        };
    }
})
.component('merchant-section', MerchantSection)
.component('drill-calendar', DrillCalendar)
.mount('#app');
