// spending_report.js - Vue 3 app for spending report
// This file is embedded into the HTML at build time by analyzer.py

const { createApp, ref, reactive, computed, watch, onMounted, nextTick } = Vue;

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
        const spendingData = computed(() => window.spendingData || { sections: {}, year: 2025, numMonths: 12 });

        // Report title and subtitle
        const title = computed(() => `${spendingData.value.year} Spending Report`);
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

        // Totals per section
        const sectionTotals = computed(() => {
            const totals = {};
            for (const [sectionId, section] of Object.entries(filteredSections.value)) {
                totals[sectionId] = Object.values(section.filteredMerchants)
                    .reduce((sum, m) => sum + m.filteredTotal, 0);
            }
            return totals;
        });

        // Grand total
        const grandTotal = computed(() =>
            Object.values(sectionTotals.value).reduce((sum, t) => sum + t, 0)
        );

        // Monthly budget (monthly + variable sections)
        const monthlyBudget = computed(() => {
            const monthly = sectionTotals.value.monthly || 0;
            const variable = sectionTotals.value.variable || 0;
            return (monthly + variable) / numFilteredMonths.value;
        });

        // Non-recurring total
        const nonRecurringTotal = computed(() => {
            const { annual = 0, periodic = 0, travel = 0, oneoff = 0 } = sectionTotals.value;
            return annual + periodic + travel + oneoff;
        });

        // Monthly recurring average
        const monthlyRecurringAvg = computed(() => {
            const monthly = sectionTotals.value.monthly || 0;
            return monthly / numFilteredMonths.value;
        });

        // Variable monthly average
        const variableMonthlyAvg = computed(() => {
            const variable = sectionTotals.value.variable || 0;
            return variable / numFilteredMonths.value;
        });

        // Uncategorized total
        const uncategorizedTotal = computed(() => {
            return sectionTotals.value.unknown || 0;
        });

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

        // Chart data aggregations
        const chartAggregations = computed(() => {
            const byMonth = {};
            const byCategory = {};
            const byCategoryByMonth = {};

            for (const section of Object.values(filteredSections.value)) {
                for (const merchant of Object.values(section.filteredMerchants)) {
                    for (const txn of merchant.filteredTxns) {
                        // By month
                        byMonth[txn.month] = (byMonth[txn.month] || 0) + txn.amount;

                        // By main category
                        const cat = merchant.category;
                        byCategory[cat] = (byCategory[cat] || 0) + txn.amount;

                        // By category by month
                        if (!byCategoryByMonth[cat]) byCategoryByMonth[cat] = {};
                        byCategoryByMonth[cat][txn.month] =
                            (byCategoryByMonth[cat][txn.month] || 0) + txn.amount;
                    }
                }
            }

            return { byMonth, byCategory, byCategoryByMonth };
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

            // Merchants
            for (const section of Object.values(data.sections || {})) {
                for (const [id, merchant] of Object.entries(section.merchants || {})) {
                    items.push({
                        type: 'merchant',
                        filterText: id,
                        displayText: merchant.displayName,
                        id: `m:${id}`
                    });
                }
            }

            // Categories (unique)
            const categories = new Set();
            const subcategories = new Set();
            for (const section of Object.values(data.sections || {})) {
                for (const merchant of Object.values(section.merchants || {})) {
                    categories.add(merchant.category);
                    subcategories.add(merchant.subcategory);
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
            for (const section of Object.values(data.sections || {})) {
                for (const merchant of Object.values(section.merchants || {})) {
                    for (const txn of merchant.transactions || []) {
                        if (txn.location) locations.add(txn.location);
                    }
                }
            }
            locations.forEach(l => items.push({
                type: 'location', filterText: l, displayText: l, id: `l:${l}`
            }));

            return items;
        });

        // Filtered autocomplete based on search
        const filteredAutocomplete = computed(() => {
            const q = searchQuery.value.toLowerCase().trim();
            if (!q) return [];
            return autocompleteItems.value
                .filter(item => item.displayText.toLowerCase().includes(q))
                .slice(0, 10);
        });

        // Available months for date picker
        const availableMonths = computed(() => {
            const months = new Set();
            for (const section of Object.values(spendingData.value.sections || {})) {
                for (const merchant of Object.values(section.merchants || {})) {
                    for (const txn of merchant.transactions || []) {
                        months.add(txn.month);
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

        function sortedMerchants(merchants, sectionId) {
            // Sort by total descending
            return Object.entries(merchants || {})
                .sort((a, b) => b[1].filteredTotal - a[1].filteredTotal)
                .reduce((acc, [id, m]) => { acc[id] = m; return acc; }, {});
        }

        // Formatting helpers
        function formatCurrency(amount) {
            if (amount === undefined || amount === null) return '$0';
            const rounded = Math.round(amount);
            return '$' + rounded.toLocaleString('en-US');
        }

        function formatDate(dateStr) {
            if (!dateStr) return '';
            // Handle MM/DD format from Python
            if (dateStr.match(/^\d{1,2}\/\d{1,2}$/)) {
                const [month, day] = dateStr.split('/');
                const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
                return `${months[parseInt(month)-1]} ${parseInt(day)}`;
            }
            // Handle YYYY-MM-DD format
            const d = new Date(dateStr + 'T12:00:00');
            return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
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
            return { category: 'c', merchant: 'm', location: 'l', month: 'd' }[type] || '?';
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
            const typeChar = { category: 'c', merchant: 'm', location: 'l', month: 'd' };
            const parts = activeFilters.value.map(f => {
                const mode = f.mode === 'exclude' ? '-' : '+';
                return `${mode}${typeChar[f.type]}:${encodeURIComponent(f.text)}`;
            });
            history.replaceState(null, '', '#' + parts.join('&'));
        }

        function hashToFilters() {
            const hash = location.hash.slice(1);
            if (!hash) return;
            const typeMap = { c: 'category', m: 'merchant', l: 'location', d: 'month' };
            hash.split('&').forEach(part => {
                const mode = part[0] === '-' ? 'exclude' : 'include';
                const start = part[0] === '+' || part[0] === '-' ? 1 : 0;
                const type = typeMap[part[start]] || 'category';
                const text = decodeURIComponent(part.slice(part.indexOf(':') + 1));
                if (text && !activeFilters.value.some(f => f.text === text && f.type === type)) {
                    const displayText = type === 'month' ? formatMonthLabel(text) : text;
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
                            label: 'Monthly Spending',
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
                                ticks: {
                                    callback: v => '$' + (v/1000).toFixed(0) + 'k'
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
                                ticks: {
                                    callback: v => '$' + (v/1000).toFixed(0) + 'k'
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
                monthlyChartInstance.data.labels = labels;
                monthlyChartInstance.data.datasets[0].data = data;
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

                categoryMonthChartInstance.data.labels = labels;
                categoryMonthChartInstance.data.datasets = datasets;
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
            hashToFilters();

            // Wait for next tick to ensure refs are ready
            nextTick(() => {
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
            activeFilters, expandedMerchants, collapsedSections, searchQuery,
            showAutocomplete, autocompleteIndex, isScrolled, isDarkTheme, chartsCollapsed, helpCollapsed,
            // Refs
            monthlyChart, categoryPieChart, categoryByMonthChart,
            // Computed
            spendingData, title, subtitle,
            visibleSections, sectionTotals, grandTotal, monthlyBudget, nonRecurringTotal,
            monthlyRecurringAvg, variableMonthlyAvg, uncategorizedTotal,
            numFilteredMonths, filteredAutocomplete, availableMonths,
            // Methods
            addFilter, removeFilter, toggleFilterMode, clearFilters, addMonthFilter,
            toggleExpand, toggleSection, sortedMerchants,
            formatCurrency, formatDate, formatMonthLabel, formatPct, filterTypeChar, getLocationClass,
            onSearchInput, onSearchKeydown, selectAutocompleteItem,
            toggleTheme
        };
    }
}).mount('#app');
