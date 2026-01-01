// spending_report.js - Vue 3 app for spending report
// This file is embedded into the HTML at build time by analyzer.py

const { createApp, ref, reactive, computed, watch, onMounted, nextTick, defineComponent } = Vue;

// i18n helper - uses window.i18n from i18n.js
const t = (key, params) => window.i18n ? window.i18n.t(key, params) : key;
const getMonthShort = (idx) => window.i18n ? window.i18n.getMonthShort(idx) : ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][idx];
const formatCurrencyI18n = (amount) => window.i18n ? window.i18n.formatCurrency(amount) : '$' + Math.round(amount);
const formatCurrencyAxisI18n = (value) => window.i18n ? window.i18n.formatCurrencyAxis(value) : '$' + value;
const getLanguage = () => (window.spendingData && window.spendingData.language) || 'en';
const getPerMonth = () => getLanguage() === 'tr' ? '/ay' : '/mo';

// ========== REUSABLE COMPONENTS ==========

// Sortable merchant/group section component
// Reusable for Credits, Excluded, and Category sections
const MerchantSection = defineComponent({
    name: 'MerchantSection',
    props: {
        sectionKey: { type: String, required: true },
        title: { type: String, required: true },
        items: { type: Array, required: true },
        totalLabel: { type: String, default: '' },
        showTotal: { type: Boolean, default: false },
        totalAmount: { type: Number, default: 0 },
        subtitle: { type: String, default: '' },
        creditMode: { type: Boolean, default: false },
        // Category mode adds % column and different formatting
        categoryMode: { type: Boolean, default: false },
        categoryTotal: { type: Number, default: 0 },
        grandTotal: { type: Number, default: 0 },
        numMonths: { type: Number, default: 12 },
        headerColor: { type: String, default: '' },
        // Injected from parent
        collapsedSections: { type: Object, required: true },
        sortConfig: { type: Object, required: true },
        expandedItems: { type: Object, required: true },
        toggleSection: { type: Function, required: true },
        toggleSort: { type: Function, required: true },
        formatCurrency: { type: Function, required: true },
        formatDate: { type: Function, required: true },
        formatPct: { type: Function, default: null },
        addFilter: { type: Function, required: true },
        getLocationClass: { type: Function, default: null },
        highlightDescription: { type: Function, default: (d) => d }
    },
    computed: {
        // Label spans first 4 columns in all modes
        colSpan() {
            return 4;
        },
        // Transaction row spans all columns
        totalColSpan() {
            return this.categoryMode ? 6 : 5;
        },
        // i18n computed properties
        merchantLabel() {
            return this.creditMode ? t('source') : t('merchant');
        },
        categoryLabel() {
            return this.categoryMode ? t('subcategory') : t('category');
        },
        totalColumnLabel() {
            return this.creditMode ? t('amount') : t('total');
        },
        computedTotalLabel() {
            return this.totalLabel || t('total');
        },
        perMonth() {
            return getPerMonth();
        }
    },
    template: `
        <section :class="[sectionKey.replace(':', '-') + '-section', 'category-section']">
            <div class="section-header" @click="toggleSection(sectionKey)">
                <h2>
                    <span class="toggle">{{ collapsedSections.has(sectionKey) ? '▶' : '▼' }}</span>
                    <span v-if="headerColor" class="category-dot" :style="{ backgroundColor: headerColor }"></span>
                    {{ title }}
                </h2>
                <span class="section-total">
                    <template v-if="categoryMode">
                        <span class="section-monthly">{{ formatCurrency(totalAmount / numMonths) }}{{ perMonth }}</span> ·
                        <span class="section-ytd">{{ formatCurrency(totalAmount) }}</span>
                        <span class="section-pct">({{ formatPct(totalAmount, grandTotal) }})</span>
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
                                    :class="getSortClass('merchant')">{{ merchantLabel }}</th>
                                <th @click.stop="toggleSort(sectionKey, 'subcategory')"
                                    :class="getSortClass('subcategory')">{{ categoryLabel }}</th>
                                <!-- Category mode: Count then Tags; Other modes: Tags then Count -->
                                <th v-if="categoryMode" @click.stop="toggleSort(sectionKey, 'count')"
                                    :class="getSortClass('count')">{{ t('count') }}</th>
                                <th>{{ t('tags') }}</th>
                                <th v-if="!categoryMode" @click.stop="toggleSort(sectionKey, 'count')"
                                    :class="getSortClass('count')">{{ t('count') }}</th>
                                <th class="money" @click.stop="toggleSort(sectionKey, 'total')"
                                    :class="getSortClass('total')">{{ totalColumnLabel }}</th>
                                <th v-if="categoryMode" @click.stop="toggleSort(sectionKey, 'total')"
                                    :class="getSortClass('total')">%</th>
                            </tr>
                        </thead>
                        <tbody>
                            <template v-for="(item, idx) in items" :key="item.id || idx">
                                <tr class="merchant-row"
                                    :class="{ expanded: isExpanded(item.id || idx) }"
                                    @click="toggleExpand(item.id || idx)">
                                    <td class="merchant" :class="{ clickable: categoryMode }">
                                        <span class="chevron">{{ isExpanded(item.id || idx) ? '▼' : '▶' }}</span>
                                        <span class="merchant-name" @click.stop="categoryMode ? addFilter(item.id, 'merchant', item.displayName) : null">
                                            {{ item.displayName || item.merchant }}
                                        </span>
                                        <span v-if="item.matchInfo || item.viewInfo" class="match-info-trigger"
                                                      @click.stop="togglePopup($event)">info
                                            <span class="match-info-popup" ref="popup">
                                                <button class="popup-close" @click="closePopup($event)">&times;</button>
                                                <div class="popup-header">{{ t('whyMatched') }}</div>
                                                <template v-if="item.matchInfo">
                                                    <div v-if="item.matchInfo.explanation" class="popup-explanation">{{ item.matchInfo.explanation }}</div>
                                                    <div class="popup-section">
                                                        <div class="popup-section-header">{{ t('merchantPattern') }}</div>
                                                        <div class="popup-code">{{ item.matchInfo.pattern }}</div>
                                                    </div>
                                                    <div class="popup-section">
                                                        <div class="popup-section-header">{{ t('assignedTo') }}</div>
                                                        <div class="popup-row">
                                                            <span class="popup-label">{{ t('merchant') }}:</span>
                                                            <span class="popup-value">{{ item.matchInfo.assignedMerchant }}</span>
                                                        </div>
                                                        <div class="popup-row">
                                                            <span class="popup-label">{{ t('category') }}:</span>
                                                            <span class="popup-value">{{ item.matchInfo.assignedCategory }} / {{ item.matchInfo.assignedSubcategory }}</span>
                                                        </div>
                                                        <div v-if="item.matchInfo.assignedTags && item.matchInfo.assignedTags.length" class="popup-row popup-tags-section">
                                                            <span class="popup-label">{{ t('tags') }}:</span>
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
                                                        <div class="popup-section-header">{{ t('viewFilter') }} ({{ item.viewInfo.viewName }})</div>
                                                        <div v-if="item.viewInfo.explanation" class="popup-explanation" style="margin-top: 0.3em;">{{ item.viewInfo.explanation }}</div>
                                                        <div class="popup-code">{{ item.viewInfo.filterExpr }}</div>
                                                    </div>
                                                </template>
                                                <div v-if="item.matchInfo" class="popup-source">{{ t('fromSource') }}: {{ item.matchInfo.source === 'user' ? 'merchants.rules' : item.matchInfo.source }}</div>
                                            </span>
                                        </span>
                                    </td>
                                    <td class="category" :class="{ clickable: categoryMode }"
                                        @click.stop="categoryMode && addFilter(item.subcategory, 'category')">
                                        {{ item.subcategory }}
                                    </td>
                                    <!-- Category mode: Count then Tags; Other modes: Tags then Count -->
                                    <td v-if="categoryMode">{{ item.filteredCount || item.count }}</td>
                                    <td class="tags-cell">
                                        <span v-for="tag in getTags(item)" :key="tag" class="tag-badge"
                                              @click.stop="addFilter(tag, 'tag')">{{ tag }}</span>
                                    </td>
                                    <td v-if="!categoryMode">{{ item.filteredCount || item.count }}</td>
                                    <td class="money" :class="getAmountClass(item)">
                                        {{ formatAmount(item) }}
                                    </td>
                                    <td v-if="categoryMode" class="pct">{{ formatPct(item.filteredTotal || item.total, categoryTotal || totalAmount) }}</td>
                                </tr>
                                <tr v-for="txn in getTransactions(item)"
                                    :key="txn.id"
                                    class="txn-row"
                                    :class="{ hidden: !isExpanded(item.id || idx) }">
                                    <td :colspan="totalColSpan">
                                        <div class="txn-detail">
                                            <span class="txn-date">{{ formatDate(txn.date) }}</span>
                                            <span class="txn-desc"><span v-if="txn.source" class="txn-source" :class="txn.source.toLowerCase()">{{ txn.source }}</span> <span v-html="highlightDescription(txn.description)"></span></span>
                                            <span class="txn-badges">
                                                <span v-if="categoryMode && txn.amount < 0" class="txn-badge refund">{{ t('refund') }}</span>
                                                <span v-if="txn.location && getLocationClass"
                                                      class="txn-location clickable"
                                                      :class="getLocationClass(txn.location)"
                                                      @click.stop="addFilter(txn.location, 'location')">
                                                    {{ txn.location }}
                                                </span>
                                                <span v-for="tag in (txn.tags || [])"
                                                      :key="tag"
                                                      class="tag-badge"
                                                      @click.stop="addFilter(tag, 'tag')">{{ tag }}</span>
                                            </span>
                                            <span class="txn-amount" :class="getTxnAmountClass(txn)">
                                                {{ formatTxnAmount(txn) }}
                                            </span>
                                        </div>
                                    </td>
                                </tr>
                            </template>
                            <tr class="total-row">
                                <td :colspan="colSpan">{{ computedTotalLabel }}</td>
                                <td class="money" :class="{ 'credit-amount': creditMode }">
                                    {{ creditMode ? '+' + formatCurrency(totalAmount) : formatCurrency(totalAmount) }}
                                </td>
                                <td v-if="categoryMode" class="pct">100%</td>
                            </tr>
                        </tbody>
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
            if (item.filteredTxns) {
                return [...new Set(item.filteredTxns.flatMap(t => t.tags || []))];
            }
            return item.tags || [];
        },
        getTransactions(item) {
            return item.filteredTxns || item.transactions || [];
        },
        getAmountClass(item) {
            if (this.creditMode) return 'credit-amount';
            const tags = item.tags || [];
            const total = item.total || item.filteredTotal || 0;
            if (tags.includes('income')) return 'income-amount';
            if (total < 0 && !tags.includes('income')) return 'negative-amount';
            return '';
        },
        getTxnAmountClass(txn) {
            if (this.creditMode) return 'credit-amount';
            const tags = txn.tags || [];
            if (tags.includes('income')) return 'income-amount';
            if (txn.amount < 0 && !tags.includes('income')) return 'negative-amount';
            return '';
        },
        formatAmount(item) {
            if (this.creditMode) {
                return '+' + this.formatCurrency(item.creditAmount || Math.abs(item.filteredTotal || item.total || 0));
            }
            const tags = item.tags || [];
            const total = item.total || item.filteredTotal || 0;
            if (tags.includes('income')) {
                return '+' + this.formatCurrency(Math.abs(total));
            }
            return this.formatCurrency(total);
        },
        formatTxnAmount(txn) {
            if (this.creditMode) {
                return '+' + this.formatCurrency(Math.abs(txn.amount));
            }
            const tags = txn.tags || [];
            if (tags.includes('income')) {
                return '+' + this.formatCurrency(Math.abs(txn.amount));
            }
            return this.formatCurrency(txn.amount);
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

// Category colors for charts
const CATEGORY_COLORS = [
    '#4facfe', '#00f2fe', '#4dffd2', '#ffa94d', '#f5af19',
    '#f093fb', '#fa709a', '#ff6b6b', '#a855f7', '#3b82f6',
    '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#06b6d4'
];

createApp({
    setup() {
        // ========== STATE ==========
        const activeFilters = ref([]);
        const expandedMerchants = reactive(new Set());
        const collapsedSections = reactive(new Set());
        const searchQuery = ref('');
        const showAutocomplete = ref(false);
        const autocompleteIndex = ref(-1);
        const isScrolled = ref(false);
        const isDarkTheme = ref(true);
        const chartsCollapsed = ref(false);
        const helpCollapsed = ref(true);
        const currentView = ref('category'); // 'category' or 'section'
        const sortConfig = reactive({}); // { 'cat:Food': { column: 'total', dir: 'desc' } }

        // Chart refs
        const monthlyChart = ref(null);
        const categoryPieChart = ref(null);
        const categoryByMonthChart = ref(null);

        // Chart instances
        let monthlyChartInstance = null;
        let pieChartInstance = null;
        let categoryMonthChartInstance = null;

        // ========== COMPUTED ==========

        // Shortcut to spending data
        const spendingData = computed(() => window.spendingData || { sections: {}, year: 2025, numMonths: 12, language: 'en' });

        // Language from spending data
        const language = computed(() => spendingData.value.language || 'en');

        // Report title and subtitle
        const title = computed(() => t('reportTitle', { year: spendingData.value.year }));
        const subtitle = computed(() => {
            const data = spendingData.value;
            const sources = data.sources || [];
            return sources.length > 0 ? t('dataFrom', { sources: sources.join(', ') }) : '';
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

        // Positive categories only (for main display, excludes credits/refunds)
        const positiveCategoryView = computed(() => {
            const result = {};
            for (const [catName, category] of Object.entries(filteredCategoryView.value)) {
                if (category.filteredTotal >= 0) {
                    result[catName] = category;
                }
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
        const unsortedCreditMerchants = computed(() => {
            const credits = [];
            for (const [catName, category] of Object.entries(filteredCategoryView.value)) {
                for (const [subName, subcat] of Object.entries(category.filteredSubcategories || {})) {
                    for (const [merchantId, merchant] of Object.entries(subcat.filteredMerchants || {})) {
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
        const grandTotal = computed(() => {
            // Sum totals from category view (unique merchants only)
            return Object.values(filteredCategoryView.value)
                .reduce((sum, cat) => sum + (cat.filteredTotal || 0), 0);
        });

        // Credits total (sum of all credit merchants, shown as positive)
        const creditsTotal = computed(() => {
            return creditMerchants.value.reduce((sum, m) => sum + m.creditAmount, 0);
        });

        // Gross spending (before credits)
        const grossSpending = computed(() => {
            return grandTotal.value + creditsTotal.value;
        });

        // Uncategorized total
        const uncategorizedTotal = computed(() => {
            return sectionTotals.value.unknown || 0;
        });

        // Excluded transactions split into Income and Transfers
        const excludedTransactions = computed(() => {
            return spendingData.value.excludedTransactions || [];
        });

        // Helper to check if excluded transaction passes all active filters
        // Excluded transactions have merchant/category/tags directly on the transaction object
        function passesExcludedFilters(txn) {
            const includes = activeFilters.value.filter(f => f.mode === 'include');
            const excludes = activeFilters.value.filter(f => f.mode === 'exclude');

            // Helper to match a single filter against the transaction
            function matchesExcludedFilter(t, filter) {
                const text = filter.text.toLowerCase();
                switch (filter.type) {
                    case 'merchant':
                        return (t.merchant || '').toLowerCase() === text;
                    case 'category':
                        return (t.category || '').toLowerCase().includes(text) ||
                               (t.subcategory || '').toLowerCase().includes(text);
                    case 'location':
                        return (t.location || '').toLowerCase() === text;
                    case 'month':
                        return monthMatches(t.month, filter.text);
                    case 'tag':
                        return (t.tags || []).some(tag => tag.toLowerCase() === text);
                    case 'text':
                        return (t.description || '').toLowerCase().includes(text);
                    default:
                        return false;
                }
            }

            // Check excludes first
            for (const f of excludes) {
                if (matchesExcludedFilter(txn, f)) return false;
            }

            // Group includes by type
            const byType = {};
            includes.forEach(f => {
                if (!byType[f.type]) byType[f.type] = [];
                byType[f.type].push(f);
            });

            // AND across types, OR within type
            for (const [type, filters] of Object.entries(byType)) {
                const anyMatch = filters.some(f => matchesExcludedFilter(txn, f));
                if (!anyMatch) return false;
            }

            return true;
        }

        // Filtered excluded transactions (respects all filters)
        const filteredExcluded = computed(() => {
            return excludedTransactions.value.filter(t => passesExcludedFilters(t));
        });
        const excludedTotal = computed(() => {
            return filteredExcluded.value.reduce((sum, t) => sum + t.amount, 0);
        });
        const filteredExcludedCount = computed(() => filteredExcluded.value.length);

        // Income and Transfer totals (from excluded transactions, by tag)
        const incomeTransactions = computed(() => {
            return excludedTransactions.value.filter(t =>
                t.tags && t.tags.includes('income')
            );
        });
        const incomeTotal = computed(() => {
            return incomeTransactions.value.reduce((sum, t) => sum + t.amount, 0);
        });
        const incomeCount = computed(() => incomeTransactions.value.length);

        const transferTransactions = computed(() => {
            return excludedTransactions.value.filter(t =>
                t.tags && t.tags.includes('transfer')
            );
        });
        const transfersTotal = computed(() => {
            return transferTransactions.value.reduce((sum, t) => sum + t.amount, 0);
        });
        const transfersCount = computed(() => transferTransactions.value.length);

        // Net cash flow
        const netCashFlow = computed(() => {
            return Math.abs(incomeTotal.value) - grandTotal.value - Math.abs(transfersTotal.value);
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

        const unsortedExcluded = computed(() => groupByMerchant(filteredExcluded.value));
        const groupedExcluded = computed(() => sortGroupedArray(unsortedExcluded.value, 'excluded'));
        const expandedExcluded = reactive(new Set());
        const showExcluded = ref(false);

        // Scroll to excluded section
        function scrollToExcluded() {
            showExcluded.value = true;
            nextTick(() => {
                const section = document.querySelector('.excluded-section');
                if (section) {
                    section.scrollIntoView({ behavior: 'smooth', block: 'start' });
                }
            });
        }

        // Number of months in filter (for monthly averages)
        const numFilteredMonths = computed(() => {
            const monthFilters = activeFilters.value.filter(f =>
                f.type === 'month' && f.mode === 'include'
            );
            if (monthFilters.length === 0) return spendingData.value.numMonths || 12;

            const months = new Set();
            monthFilters.forEach(f => {
                if (f.text.includes('..')) {
                    expandMonthRange(f.text).forEach(m => months.add(m));
                } else {
                    months.add(f.text);
                }
            });
            return months.size || 1;
        });

        // Chart data aggregations - always uses categoryView for consistent data
        const chartAggregations = computed(() => {
            const byMonth = {};
            const byCategory = {};
            const byCategoryByMonth = {};

            // Use categoryView which always has data (doesn't require views_file)
            const categoryView = filteredCategoryView.value;
            for (const [catName, category] of Object.entries(categoryView)) {
                for (const subcat of Object.values(category.filteredSubcategories || {})) {
                    for (const merchant of Object.values(subcat.filteredMerchants || {})) {
                        for (const txn of merchant.filteredTxns || []) {
                            // By month
                            byMonth[txn.month] = (byMonth[txn.month] || 0) + txn.amount;

                            // By main category
                            byCategory[catName] = (byCategory[catName] || 0) + txn.amount;

                            // By category by month
                            if (!byCategoryByMonth[catName]) byCategoryByMonth[catName] = {};
                            byCategoryByMonth[catName][txn.month] =
                                (byCategoryByMonth[catName][txn.month] || 0) + txn.amount;
                        }
                    }
                }
            }

            return { byMonth, byCategory, byCategoryByMonth };
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

        // Filtered months for charts (respects month filters)
        const filteredMonthsForCharts = computed(() => {
            const monthFilters = activeFilters.value.filter(f =>
                f.type === 'month' && f.mode === 'include'
            );
            if (monthFilters.length === 0) return availableMonths.value;

            // Build set of included months
            const includedMonths = new Set();
            monthFilters.forEach(f => {
                if (f.text.includes('..')) {
                    expandMonthRange(f.text).forEach(m => includedMonths.add(m));
                } else {
                    includedMonths.add(f.text);
                }
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

            // Categories (unique)
            const categories = new Set();
            const subcategories = new Set();
            for (const category of Object.values(categoryView)) {
                for (const subcat of Object.values(category.subcategories || {})) {
                    for (const merchant of Object.values(subcat.merchants || {})) {
                        categories.add(merchant.category);
                        subcategories.add(merchant.subcategory);
                    }
                }
            }
            categories.forEach(c => items.push({
                type: 'category', filterText: c, displayText: c, id: `c:${c}`
            }));
            subcategories.forEach(s => {
                if (!categories.has(s)) {
                    items.push({
                        type: 'category', filterText: s, displayText: s, id: `cs:${s}`
                    });
                }
            });

            // Locations (unique)
            const locations = new Set();
            for (const category of Object.values(categoryView)) {
                for (const subcat of Object.values(category.subcategories || {})) {
                    for (const merchant of Object.values(subcat.merchants || {})) {
                        for (const txn of merchant.transactions || []) {
                            if (txn.location) locations.add(txn.location);
                        }
                    }
                }
            }
            locations.forEach(l => items.push({
                type: 'location', filterText: l, displayText: l, id: `l:${l}`
            }));

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
            if (type === 'month') return formatMonthLabel(filterText);
            return displayTextLookup.value[`${type}:${filterText}`] || filterText;
        }

        // Filtered autocomplete based on search
        const filteredAutocomplete = computed(() => {
            const q = searchQuery.value.toLowerCase().trim();
            if (!q) return [];

            // Get matching autocomplete items (merchants, categories, etc.)
            const matches = autocompleteItems.value
                .filter(item => item.displayText.toLowerCase().includes(q))
                .slice(0, 8);

            // Add "Search transactions for: X" option at the end
            if (q.length >= 2) {
                matches.push({
                    type: 'text',
                    filterText: q,
                    displayText: t('searchTransactions', { query: q }),
                    id: `search:${q}`,
                    isTextSearch: true
                });
            }

            return matches;
        });

        // Available months for date picker
        const availableMonths = computed(() => {
            const months = new Set();
            const sections = spendingData.value.sections || {};

            // Use sections if available, otherwise fall back to categoryView
            if (Object.keys(sections).length > 0) {
                for (const section of Object.values(sections)) {
                    for (const merchant of Object.values(section.merchants || {})) {
                        for (const txn of merchant.transactions || []) {
                            months.add(txn.month);
                        }
                    }
                }
            } else {
                // Fall back to categoryView when no views configured
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

            // Group includes by type
            const byType = {};
            includes.forEach(f => {
                if (!byType[f.type]) byType[f.type] = [];
                byType[f.type].push(f);
            });

            // AND across types, OR within type
            for (const [type, filters] of Object.entries(byType)) {
                const anyMatch = filters.some(f => matchesFilter(txn, merchant, f));
                if (!anyMatch) return false;
            }

            return true;
        }

        function matchesFilter(txn, merchant, filter) {
            const text = filter.text.toLowerCase();
            switch (filter.type) {
                case 'merchant':
                    return merchant.id.toLowerCase() === text ||
                           merchant.displayName.toLowerCase() === text;
                case 'category':
                    return merchant.category.toLowerCase().includes(text) ||
                           merchant.subcategory.toLowerCase().includes(text) ||
                           (merchant.categoryPath || '').toLowerCase().includes(text);
                case 'location':
                    return (txn.location || '').toLowerCase() === text;
                case 'month':
                    return monthMatches(txn.month, filter.text);
                case 'tag':
                    return (txn.tags || []).some(t => t.toLowerCase() === text);
                case 'text':
                    // Search transaction description
                    return (txn.description || '').toLowerCase().includes(text);
                default:
                    return false;
            }
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
            activeFilters.value.splice(index, 1);
        }

        function toggleFilterMode(index) {
            const f = activeFilters.value[index];
            f.mode = f.mode === 'include' ? 'exclude' : 'include';
        }

        function clearFilters() {
            activeFilters.value = [];
        }

        function addMonthFilter(month) {
            if (month) addFilter(month, 'month', formatMonthLabel(month));
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
        function formatCurrency(amount) {
            return formatCurrencyI18n(amount);
        }

        function formatDate(dateStr) {
            if (!dateStr) return '';
            // Handle MM/DD format from Python
            if (dateStr.match(/^\d{1,2}\/\d{1,2}$/)) {
                const [month, day] = dateStr.split('/');
                return `${parseInt(day)} ${getMonthShort(parseInt(month)-1)}`;
            }
            // Handle YYYY-MM-DD format
            const d = new Date(dateStr + 'T12:00:00');
            const lang = getLanguage();
            const locale = lang === 'tr' ? 'tr-TR' : 'en-US';
            return d.toLocaleDateString(locale, { month: 'short', day: 'numeric' });
        }

        function formatMonthLabel(key) {
            if (!key) return '';
            const [year, month] = key.split('-');
            return `${getMonthShort(parseInt(month)-1)} ${year}`;
        }

        function formatPct(value, total) {
            if (!total || total === 0) return '0%';
            return ((value / total) * 100).toFixed(1) + '%';
        }

        function filterTypeChar(type) {
            return { category: 'c', merchant: 'm', location: 'l', month: 'd', tag: 't', text: 's' }[type] || '?';
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

        function getLocationClass(location) {
            const home = spendingData.value.homeState || 'WA';
            if (location === home) return 'home';
            if (location && location.length > 2) return 'intl'; // International
            return '';
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
            const typeChar = { category: 'c', merchant: 'm', location: 'l', month: 'd', tag: 't', text: 's' };
            const parts = activeFilters.value.map(f => {
                const mode = f.mode === 'exclude' ? '-' : '+';
                return `${mode}${typeChar[f.type]}:${encodeURIComponent(f.text)}`;
            });
            history.replaceState(null, '', '#' + parts.join('&'));
        }

        function hashToFilters() {
            const hash = location.hash.slice(1);
            if (!hash) return;
            const typeMap = { c: 'category', m: 'merchant', l: 'location', d: 'month', t: 'tag', s: 'text' };
            hash.split('&').forEach(part => {
                const mode = part[0] === '-' ? 'exclude' : 'include';
                const start = part[0] === '+' || part[0] === '-' ? 1 : 0;
                const type = typeMap[part[start]] || 'category';
                const text = decodeURIComponent(part.slice(part.indexOf(':') + 1));
                if (text && !activeFilters.value.some(f => f.text === text && f.type === type)) {
                    const displayText = getDisplayText(type, text);
                    activeFilters.value.push({ text, type, mode, displayText });
                }
            });
        }

        // ========== CHARTS ==========

        function initCharts() {
            // Monthly trend chart
            if (monthlyChart.value) {
                const ctx = monthlyChart.value.getContext('2d');
                const labels = availableMonths.value.map(m => m.label);
                monthlyChartInstance = new Chart(ctx, {
                    type: 'bar',
                    data: {
                        labels,
                        datasets: [{
                            label: t('monthlySpending'),
                            data: [],
                            backgroundColor: '#4facfe',
                            borderRadius: 4
                        }]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        plugins: {
                            legend: { display: false }
                        },
                        scales: {
                            y: {
                                beginAtZero: true,
                                grace: '5%',
                                ticks: {
                                    callback: v => formatCurrencyAxisI18n(v)
                                }
                            }
                        },
                        onClick: (e, elements) => {
                            if (elements.length > 0) {
                                const idx = elements[0].index;
                                const month = availableMonths.value[idx];
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
                            backgroundColor: CATEGORY_COLORS
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
                        },
                        onClick: (e, elements) => {
                            if (elements.length > 0) {
                                const idx = elements[0].index;
                                const label = pieChartInstance.data.labels[idx];
                                if (label) addFilter(label, 'category');
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
                                labels: { boxWidth: 12, padding: 8 },
                                onClick: (e, legendItem, legend) => {
                                    // Add category filter when clicking legend
                                    const category = legendItem.text;
                                    if (category) addFilter(category, 'category');
                                    // Also toggle visibility (default behavior)
                                    const index = legendItem.datasetIndex;
                                    const ci = legend.chart;
                                    const meta = ci.getDatasetMeta(index);
                                    meta.hidden = meta.hidden === null ? !ci.data.datasets[index].hidden : null;
                                    ci.update();
                                }
                            }
                        },
                        scales: {
                            x: { stacked: true },
                            y: {
                                stacked: true,
                                beginAtZero: true,
                                grace: '5%',
                                ticks: {
                                    callback: v => formatCurrencyAxisI18n(v)
                                }
                            }
                        },
                        onClick: (e, elements) => {
                            if (elements.length > 0) {
                                const el = elements[0];
                                const monthIndex = el.index;
                                const datasetIndex = el.datasetIndex;

                                // Get month from filtered months
                                const monthsToShow = filteredMonthsForCharts.value;
                                const month = monthsToShow[monthIndex];

                                // Get category from dataset
                                const category = categoryMonthChartInstance.data.datasets[datasetIndex]?.label;

                                // Add both filters
                                if (month) addFilter(month.key, 'month', month.label);
                                if (category) addFilter(category, 'category');
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

            // Update monthly trend
            if (monthlyChartInstance) {
                const labels = monthsToShow.map(m => m.label);
                const data = monthsToShow.map(m => agg.byMonth[m.key] || 0);
                const maxVal = Math.max(...data, 1); // At least 1 to avoid 0
                monthlyChartInstance.data.labels = labels;
                monthlyChartInstance.data.datasets[0].data = data;
                monthlyChartInstance.options.scales.y.suggestedMax = maxVal * 1.1;
                monthlyChartInstance.update();
            }

            // Update category pie
            if (pieChartInstance) {
                const entries = Object.entries(agg.byCategory)
                    .filter(([_, v]) => v > 0)
                    .sort((a, b) => b[1] - a[1]);
                pieChartInstance.data.labels = entries.map(e => e[0]);
                pieChartInstance.data.datasets[0].data = entries.map(e => e[1]);
                pieChartInstance.update();
            }

            // Update category by month (top 8 categories only)
            if (categoryMonthChartInstance) {
                const labels = monthsToShow.map(m => m.label);
                const categories = Object.keys(agg.byCategoryByMonth).sort((a, b) => {
                    const totalA = Object.values(agg.byCategoryByMonth[a]).reduce((s, v) => s + v, 0);
                    const totalB = Object.values(agg.byCategoryByMonth[b]).reduce((s, v) => s + v, 0);
                    return totalB - totalA;
                }).slice(0, 8); // Top 8 categories

                const datasets = categories.map((cat, i) => ({
                    label: cat,
                    data: monthsToShow.map(m => agg.byCategoryByMonth[cat][m.key] || 0),
                    backgroundColor: CATEGORY_COLORS[i % CATEGORY_COLORS.length]
                }));

                // Calculate max for stacked bar (sum of all categories per month)
                const monthTotals = monthsToShow.map((m, idx) =>
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

        // ========== LIFECYCLE ==========

        onMounted(() => {
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
            });

            // Hash change handler
            window.addEventListener('hashchange', () => {
                activeFilters.value = [];
                hashToFilters();
            });
        });

        // ========== RETURN ==========

        return {
            // i18n
            t, language,
            // State
            activeFilters, expandedMerchants, collapsedSections, searchQuery,
            showAutocomplete, autocompleteIndex, isScrolled, isDarkTheme, chartsCollapsed, helpCollapsed,
            showExcluded, currentView, sortConfig,
            // Refs
            monthlyChart, categoryPieChart, categoryByMonthChart,
            // Computed
            spendingData, title, subtitle,
            visibleSections, filteredCategoryView, positiveCategoryView, creditMerchants, filteredSectionView, hasSections,
            sectionTotals, grandTotal, creditsTotal, uncategorizedTotal,
            numFilteredMonths, filteredAutocomplete, availableMonths,
            categoryColorMap,
            // Excluded transactions
            excludedTotal, filteredExcludedCount, groupedExcluded, expandedExcluded,
            // Cash flow
            incomeTotal, incomeCount, transfersTotal, transfersCount, netCashFlow,
            // Methods
            addFilter, removeFilter, toggleFilterMode, clearFilters, addMonthFilter,
            toggleExpand, toggleSection, toggleSort, sortedMerchants,
            formatCurrency, formatDate, formatMonthLabel, formatPct, filterTypeChar, getLocationClass,
            highlightDescription,
            onSearchInput, onSearchKeydown, selectAutocompleteItem,
            toggleTheme, scrollToExcluded
        };
    }
})
.component('merchant-section', MerchantSection)
.mount('#app');
