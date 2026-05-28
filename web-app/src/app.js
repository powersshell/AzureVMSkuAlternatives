// API Configuration
// Direct connection to Flex Consumption Function App (Static Web App rewrite doesn't support POST)
const API_BASE_URL = 'https://vmsku-api-functions-flex.azurewebsites.net/api';
const TELEMETRY_CONFIG_ENDPOINT = `${API_BASE_URL}/telemetry_config`;
const ANALYTICS_USER_KEY = 'vmsku_anonymous_user_id';
const ANALYTICS_USER_KEY_CREATED_AT = 'vmsku_anonymous_user_id_created_at';
const ANALYTICS_USER_TTL_MS = 90 * 24 * 60 * 60 * 1000; // 90 days

// Maps priority dropdown values to numeric weights used by the comparison algorithm
const PRIORITY_VALUES = { low: 0.5, normal: 1.5, high: 3.0 };

let appInsights = null;
let analyticsUserId = null;
let telemetryReady = false;
const pendingTelemetryEvents = [];

function generateAnonymousId(prefix = 'anon') {
    if (window.crypto && window.crypto.randomUUID) {
        return `${prefix}-${window.crypto.randomUUID()}`;
    }
    return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 12)}`;
}

function getOrCreateAnonymousUserId() {
    const now = Date.now();
    const existing = localStorage.getItem(ANALYTICS_USER_KEY);
    const createdAtRaw = localStorage.getItem(ANALYTICS_USER_KEY_CREATED_AT);
    const createdAt = createdAtRaw ? Number(createdAtRaw) : null;
    const isExpired = !createdAt || Number.isNaN(createdAt) || (now - createdAt) > ANALYTICS_USER_TTL_MS;

    if (existing && !isExpired) {
        return existing;
    }

    const nextId = generateAnonymousId('vmsku');
    localStorage.setItem(ANALYTICS_USER_KEY, nextId);
    localStorage.setItem(ANALYTICS_USER_KEY_CREATED_AT, String(now));
    return nextId;
}

function sanitizeTelemetryProperties(properties = {}) {
    const safe = {};
    for (const [key, value] of Object.entries(properties)) {
        if (value === null || value === undefined) continue;
        if (typeof value === 'string') {
            safe[key] = value.slice(0, 120);
            continue;
        }
        if (typeof value === 'number' || typeof value === 'boolean') {
            safe[key] = value;
        }
    }
    return safe;
}

function sanitizeTelemetryMeasurements(measurements = {}) {
    const safe = {};
    for (const [key, value] of Object.entries(measurements)) {
        if (typeof value === 'number' && Number.isFinite(value)) {
            safe[key] = value;
        }
    }
    return safe;
}

function sendTelemetryEvent(name, properties = {}, measurements = {}) {
    if (!appInsights || !telemetryReady) return;
    appInsights.trackEvent(
        { name },
        sanitizeTelemetryProperties({
            anonymousUserId: analyticsUserId || 'unknown',
            ...properties
        }),
        sanitizeTelemetryMeasurements(measurements)
    );
}

function flushPendingTelemetryEvents() {
    while (pendingTelemetryEvents.length > 0) {
        const event = pendingTelemetryEvents.shift();
        sendTelemetryEvent(event.name, event.properties, event.measurements);
    }
}

async function initializeTelemetry() {
    const aiGlobal = window.Microsoft && window.Microsoft.ApplicationInsights;
    if (!aiGlobal || !aiGlobal.ApplicationInsights) {
        console.warn('Application Insights SDK unavailable; telemetry disabled.');
        return;
    }

    let telemetryConfig = null;
    try {
        const response = await fetch(TELEMETRY_CONFIG_ENDPOINT, { cache: 'no-store' });
        if (response.ok) {
            telemetryConfig = await response.json();
        }
    } catch (error) {
        console.warn('Unable to load telemetry config; telemetry disabled.', error);
        return;
    }

    const connectionString = telemetryConfig?.connectionString;
    if (!telemetryConfig?.enabled || !connectionString || !connectionString.includes('InstrumentationKey=')) {
        console.warn('Telemetry config not enabled; telemetry disabled.');
        return;
    }

    analyticsUserId = getOrCreateAnonymousUserId();

    appInsights = new aiGlobal.ApplicationInsights({
        config: {
            connectionString,
            disableAjaxTracking: true,
            disableFetchTracking: true,
            autoTrackPageVisitTime: true,
            enableAutoRouteTracking: false
        }
    });

    appInsights.loadAppInsights();
    appInsights.context.user.id = analyticsUserId;
    appInsights.context.user.accountId = 'public-site';
    telemetryReady = true;
    appInsights.trackPageView({ name: 'home' });
    flushPendingTelemetryEvents();
}

function trackEvent(name, properties = {}, measurements = {}) {
    if (!telemetryReady || !appInsights) {
        if (pendingTelemetryEvents.length < 100) {
            pendingTelemetryEvents.push({ name, properties, measurements });
        }
        return;
    }
    sendTelemetryEvent(name, properties, measurements);
}

function getPriorityWeight(id) {
    return PRIORITY_VALUES[document.getElementById(id).value] ?? 1.5;
}

function getDiscountMultiplier() {
    const val = parseFloat(document.getElementById('discountPct').value);
    if (isNaN(val) || val <= 0) return 1.0;
    if (val >= 100) return 0.0;
    return 1.0 - (val / 100);
}

// DOM Elements
const compareBtn = document.getElementById('compareBtn');
const loadingOverlay = document.getElementById('loadingOverlay');
const resultsSection = document.getElementById('resultsSection');
const errorSection = document.getElementById('errorSection');
const errorMessage = document.getElementById('errorMessage');
const dismissErrorBtn = document.getElementById('dismissErrorBtn');
const resultsTableBody = document.getElementById('resultsTableBody');
const targetSkuInfo = document.getElementById('targetSkuInfo');
const noResults = document.getElementById('noResults');
const exportBtn = document.getElementById('exportBtn');

let currentResults = null;
let currentPricingOS = 'linux';
let currentPricingModel = 'payg'; // 'payg', 'ri1year', 'ri3year'
let locationChoices = null; // Choices.js instance for location
let skuChoices = null; // Choices.js instance for SKU
const expandedDetailsCache = new Map(); // Cache for expanded row details (Phase 2)
let prefetchAbortController = null; // Cancels in-flight prefetch wave on new search (Phase 3)
let regionCheckAbortController = null; // Cancels in-flight region availability check
let regionAvailabilityData = null; // { region, availability: { skuName: bool } }

// Capture region options before Choices.js takes over the <select>
const ALL_REGIONS = Array.from(document.getElementById('location').options)
    .filter(opt => opt.value)
    .map(opt => ({ value: opt.value, label: opt.textContent }));

// Event Listeners
compareBtn.addEventListener('click', handleCompare);
dismissErrorBtn.addEventListener('click', hideError);
exportBtn.addEventListener('click', exportToCSV);

// Initialize dropdown functionality on page load
document.addEventListener('DOMContentLoaded', () => {
    initializeTelemetry();
    trackEvent('page_loaded', {
        page: 'home'
    });

    const reportIssueLink = document.getElementById('reportIssueLink');
    if (reportIssueLink) {
        reportIssueLink.addEventListener('click', () => {
            trackEvent('report_issue_clicked', {
                source: 'footer'
            });
        });
    }

    const locationSelect = document.getElementById('location');
    const skuSelect = document.getElementById('skuName');
    
    // Initialize Choices.js for location dropdown
    locationChoices = new Choices(locationSelect, {
        searchEnabled: true,
        searchPlaceholderValue: 'Search regions...',
        itemSelectText: '',
        shouldSort: false,
        searchResultLimit: 50,
        fuseOptions: {
            includeScore: true,
            threshold: 0.4,
            ignoreLocation: true,
            minMatchCharLength: 1,
            findAllMatches: true
        }
    });
    
    // Initialize Choices.js for SKU dropdown
    // fuseOptions with ignoreLocation and findAllMatches ensures substring-style matching
    // (e.g., typing "D4" matches "Standard_D4s_v5" even though it starts at position 9)
    skuChoices = new Choices(skuSelect, {
        searchEnabled: true,
        searchPlaceholderValue: 'Type to search SKUs...',
        itemSelectText: '',
        shouldSort: false,
        placeholder: true,
        placeholderValue: 'Select a region first...',
        searchResultLimit: 200,
        fuseOptions: {
            includeScore: true,
            threshold: 0.4,
            ignoreLocation: true,
            minMatchCharLength: 1,
            findAllMatches: true
        }
    });
    
    // Auto-focus search input when dropdowns open.
    // Choices.js internally focuses the input via requestAnimationFrame, but
    // containerOuter.focus() can steal focus back on some browsers.
    // We observe the 'is-active' class on the dropdown to reliably detect open state.
    function autoFocusSearch(choicesInstance) {
        const dropdown = choicesInstance.dropdown.element;
        const observer = new MutationObserver(() => {
            if (dropdown.classList.contains('is-active')) {
                setTimeout(() => {
                    if (choicesInstance.input && choicesInstance.input.element) {
                        choicesInstance.input.element.focus();
                    }
                }, 16);
            }
        });
        observer.observe(dropdown, { attributes: true, attributeFilter: ['class'] });
    }
    autoFocusSearch(locationChoices);
    autoFocusSearch(skuChoices);

    // Disable SKU dropdown initially
    skuChoices.disable();
    
    // Listen for region changes - using Choices.js passedElement
    locationChoices.passedElement.element.addEventListener('change', async (e) => {
        const location = e.target.value;
        
        if (!location) {
            // No region selected - disable SKU dropdown
            skuChoices.clearStore();
            skuChoices.setChoices([{ value: '', label: 'Select a region first...', disabled: true }], 'value', 'label', true);
            skuChoices.disable();
            document.getElementById('skuCount').textContent = '';
            return;
        }
        
        // Fetch SKUs for selected region
        await loadSkusForRegion(location);
    });
    
    // Listen for CPU vendor filter changes (dropdown filters)
    document.getElementById('filterIntel').addEventListener('change', updateSkuFilters);
    document.getElementById('filterAMD').addEventListener('change', updateSkuFilters);
    document.getElementById('filterARM').addEventListener('change', updateSkuFilters);
    
    // Listen for CPU vendor filter changes (result filters)
    document.getElementById('resultFilterIntel').addEventListener('change', updateResultsFilters);
    document.getElementById('resultFilterAMD').addEventListener('change', updateResultsFilters);
    document.getElementById('resultFilterARM').addEventListener('change', updateResultsFilters);

    // CPU generation filter buttons
    document.getElementById('genSelectAll').addEventListener('click', genSelectAll);
    document.getElementById('genDeselectAll').addEventListener('click', genDeselectAll);

    // Retirement filter checkbox
    document.getElementById('hideRetiringFilter').addEventListener('change', updateResultsFilters);

    // Update discount hint text as user types
    document.getElementById('discountPct').addEventListener('input', updateDiscountHint);
});

// Store all SKUs for current region (unfiltered)
let allSkusForRegion = [];

// Update SKU count display
function updateSkuCount(filteredCount, totalCount) {
    const skuCount = document.getElementById('skuCount');
    if (filteredCount === totalCount) {
        skuCount.textContent = `${totalCount} SKUs available`;
    } else {
        skuCount.textContent = `${filteredCount} of ${totalCount} SKUs (filtered by vendor)`;
    }
    skuCount.style.color = '#107c10'; // Success green
}

// Update SKU dropdown based on CPU vendor filters
function updateSkuFilters() {
    if (allSkusForRegion.length === 0) return;
    
    const filteredSkus = getFilteredSkus(allSkusForRegion);
    populateSkuChoices(filteredSkus);
    updateSkuCount(filteredSkus.length, allSkusForRegion.length);
}

function updateDiscountHint() {
    const hint = document.getElementById('discountHint');
    const val = parseFloat(document.getElementById('discountPct').value);
    if (isNaN(val) || val <= 0) {
        hint.textContent = 'No discount applied. Enter 0–100 to reduce displayed prices.';
        hint.style.color = '';
    } else if (val > 100) {
        hint.textContent = 'Discount must be between 0 and 100.';
        hint.style.color = '#a80000';
    } else {
        hint.textContent = `Prices shown at ${(100 - val).toFixed(val % 1 === 0 ? 0 : 1)}% of list price (${val}% discount applied).`;
        hint.style.color = '#107c10';
    }
}

// Update comparison results based on CPU vendor filters
function updateResultsFilters() {
    if (!currentResults || !currentResults.alternatives) return;
    trackEvent('result_vendor_filter_changed', {
        intel: document.getElementById('resultFilterIntel').checked,
        amd: document.getElementById('resultFilterAMD').checked,
        arm: document.getElementById('resultFilterARM').checked
    });
    
    // Repopulate generation filter (vendor change affects available generations)
    populateGenFilter(currentResults.alternatives);
    // Re-display with both filters
    displayAlternatives(filterResults(currentResults.alternatives));
}

// Filter SKUs based on checked CPU vendor checkboxes
function getFilteredSkus(skus) {
    const showIntel = document.getElementById('filterIntel').checked;
    const showAMD = document.getElementById('filterAMD').checked;
    const showARM = document.getElementById('filterARM').checked;
    
    return skus.filter(sku => {
        if (sku.cpuVendor === 'Intel' && showIntel) return true;
        if (sku.cpuVendor === 'AMD' && showAMD) return true;
        if (sku.cpuVendor === 'ARM' && showARM) return true;
        return false;
    });
}

// Load SKUs from cache for selected region
async function loadSkusForRegion(location) {
    const skuCount = document.getElementById('skuCount');
    
    try {
        // Show loading state
        skuChoices.clearStore();
        skuChoices.setChoices([{ value: '', label: 'Loading SKUs...', disabled: true }], 'value', 'label', true);
        skuChoices.disable();
        skuCount.textContent = 'Loading...';
        skuCount.style.color = '#666';
        
        // Fetch from cached API
        const response = await fetch(`${API_BASE_URL}/skus?location=${location}`);
        
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }
        
        const data = await response.json();
        const skus = data.skus || []; // Extract skus array from response
        
        if (!skus || skus.length === 0) {
            allSkusForRegion = [];
            skuChoices.setChoices([{ value: '', label: 'No SKUs available for this region', disabled: true }], 'value', 'label', true);
            skuCount.textContent = 'No SKUs found';
            skuCount.style.color = '#d13438';
            return;
        }
        
        // Store all SKUs for filtering
        allSkusForRegion = skus;
        
        // Apply current filters and populate dropdown
        const filteredSkus = getFilteredSkus(skus);
        populateSkuChoices(filteredSkus);
        
        // Update count - show filtered/total
        updateSkuCount(filteredSkus.length, skus.length);
        skuChoices.enable();
        
    } catch (error) {
        console.error('Failed to load SKUs:', error);
        skuChoices.setChoices([{ value: '', label: 'Error loading SKUs - try again', disabled: true }], 'value', 'label', true);
        skuCount.textContent = 'Failed to load SKUs';
        skuCount.style.color = '#d13438'; // Error red
    }
}

// Populate SKU dropdown with Choices.js
function populateSkuChoices(skus) {
    // Backend already sorts by vCPUs then memory, so we trust that order
    
    // Build choices array with displayName constructed on frontend
    const choices = skus.map(sku => {
        const retiring = sku.retirementStatus ? ' ⚠️ Retiring' : '';
        return {
            value: sku.name,
            label: `${sku.name} (${sku.vCPUs} vCPUs, ${sku.memoryGB} GB)${retiring}`,
            customProperties: {
                vCPUs: sku.vCPUs,
                memoryGB: sku.memoryGB
            }
        };
    });
    
    // Clear existing choices and add new ones
    skuChoices.clearStore();
    skuChoices.setChoices(choices, 'value', 'label', true);
    
    // Store valid SKU names for validation
    window.validSkuNames = skus.map(s => s.name);
}

// Handle Compare Button Click
async function handleCompare() {
    const skuName = document.getElementById('skuName').value.trim();
    const location = document.getElementById('location').value;
    const minSimilarityScore = parseInt(document.getElementById('minSimilarityScore').value);
    const currencyCode = document.getElementById('currencyCode').value;

    if (!skuName || !location) {
        trackEvent('compare_validation_failed', {
            reason: 'missing_required_fields'
        });
        showError('Please provide both SKU name and location');
        return;
    }
    
    // Validate that entered SKU exists in the list (optional but recommended)
    if (window.validSkuNames && !window.validSkuNames.includes(skuName)) {
        trackEvent('compare_validation_failed', {
            reason: 'invalid_sku_selected'
        });
        showError(`Invalid SKU: "${skuName}". Please select a valid SKU from the dropdown.`);
        return;
    }

    const params = {
        skuName,
        location,
        minSimilarityScore,
        currencyCode,
        weightCPU: getPriorityWeight('weightCPU'),
        weightMemory: getPriorityWeight('weightMemory'),
        weightGPU: getPriorityWeight('weightGPU'),
        weightStorage: getPriorityWeight('weightStorage'),
        weightNetwork: getPriorityWeight('weightNetwork'),
        weightFeatures: getPriorityWeight('weightFeatures'),
        requireNVMeMatch: document.getElementById('requireNVMeMatch').checked,
        requireGPUMatch: document.getElementById('requireGPUMatch').checked
    };

    showLoading();
    hideError();
    hideResults();
    clearRegionCheck();

    trackEvent('compare_submitted', {
        location,
        currencyCode
    }, {
        minSimilarityScore
    });

    try {
        const response = await fetch(`${API_BASE_URL}/compare_vms`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(params)
        });

        if (!response.ok) {
            // Try to parse as JSON, but handle cases where it's not JSON
            const responseText = await response.text();
            let errorMessage;
            try {
                const errorData = JSON.parse(responseText);
                errorMessage = errorData.error || JSON.stringify(errorData);
            } catch {
                errorMessage = responseText || response.statusText;
            }
            throw new Error(`HTTP ${response.status}: ${errorMessage}`);
        }

        // Parse the response as JSON
        const data = await response.json();

        currentResults = data;
        displayResults(data);

        trackEvent('compare_completed', {
            location,
            currencyCode,
            resultStatus: data.alternatives && data.alternatives.length > 0 ? 'results' : 'no_results'
        }, {
            alternativesCount: data.alternatives ? data.alternatives.length : 0
        });
    } catch (error) {
        console.error('Error comparing VMs:', error);
        trackEvent('compare_failed', {
            location,
            currencyCode,
            errorType: 'request_or_server_error'
        });
        showError(error.message || 'Failed to compare VMs. Please check your input and try again.');
    } finally {
        hideLoading();
    }
}

// OS Pricing toggle
function setPricingOS(os) {
    currentPricingOS = os;
    trackEvent('pricing_os_toggled', { os });
    document.getElementById('btn-linux-pricing')?.classList.toggle('active', os === 'linux');
    document.getElementById('btn-windows-pricing')?.classList.toggle('active', os === 'windows');
    updatePricingHeaders();
    // Re-render results if available
    if (currentResults) {
        displayAlternatives(filterResults(currentResults.alternatives));
        displayTargetSku(currentResults.targetSku);
    }
}

// Pricing model toggle (PAYG / 1-Year RI / 3-Year RI)
function setPricingModel(model) {
    currentPricingModel = model;
    trackEvent('pricing_model_toggled', { model });
    document.getElementById('btn-payg')?.classList.toggle('active', model === 'payg');
    document.getElementById('btn-ri1year')?.classList.toggle('active', model === 'ri1year');
    document.getElementById('btn-ri3year')?.classList.toggle('active', model === 'ri3year');

    // OS toggle stays enabled for all pricing models (RI Windows = RI compute + Windows license)

    updatePricingHeaders();
    if (currentResults) {
        displayAlternatives(filterResults(currentResults.alternatives));
        displayTargetSku(currentResults.targetSku);
    }
}

function updatePricingHeaders() {
    const thHourly = document.getElementById('th-hourly');
    const thMonthly = document.getElementById('th-monthly');
    const osLabel = currentPricingOS === 'windows' ? 'Win' : 'Linux';
    if (currentPricingModel === 'ri1year') {
        thHourly.textContent = `Hourly Cost (1yr RI, ${osLabel})`;
        thMonthly.textContent = `Monthly Cost (1yr RI, ${osLabel})`;
    } else if (currentPricingModel === 'ri3year') {
        thHourly.textContent = `Hourly Cost (3yr RI, ${osLabel})`;
        thMonthly.textContent = `Monthly Cost (3yr RI, ${osLabel})`;
    } else {
        thHourly.textContent = `Hourly Cost (${osLabel})`;
        thMonthly.textContent = `Monthly Cost (${osLabel})`;
    }
}

function getHourlyPrice(pricing) {
    if (!pricing) return null;
    if (currentPricingModel === 'ri1year') {
        return currentPricingOS === 'windows'
            ? (pricing.ri1YearHourlyWindows ?? pricing.ri1YearHourly ?? null)
            : (pricing.ri1YearHourly ?? null);
    }
    if (currentPricingModel === 'ri3year') {
        return currentPricingOS === 'windows'
            ? (pricing.ri3YearHourlyWindows ?? pricing.ri3YearHourly ?? null)
            : (pricing.ri3YearHourly ?? null);
    }
    return currentPricingOS === 'windows'
        ? (pricing.hourlyPriceWindows ?? pricing.hourlyPrice)
        : pricing.hourlyPrice;
}

function getMonthlyPrice(pricing) {
    if (!pricing) return null;
    if (currentPricingModel === 'ri1year') {
        return currentPricingOS === 'windows'
            ? (pricing.ri1YearMonthlyWindows ?? pricing.ri1YearMonthly ?? null)
            : (pricing.ri1YearMonthly ?? null);
    }
    if (currentPricingModel === 'ri3year') {
        return currentPricingOS === 'windows'
            ? (pricing.ri3YearMonthlyWindows ?? pricing.ri3YearMonthly ?? null)
            : (pricing.ri3YearMonthly ?? null);
    }
    return currentPricingOS === 'windows'
        ? (pricing.monthlyPriceWindows ?? pricing.monthlyPrice)
        : pricing.monthlyPrice;
}

// Get PAYG monthly price for savings calculation (OS-aware)
function getPaygMonthlyPrice(pricing) {
    if (!pricing) return null;
    return currentPricingOS === 'windows'
        ? (pricing.monthlyPriceWindows ?? pricing.monthlyPrice)
        : pricing.monthlyPrice;
}

// Render RI savings badge for monthly column
function renderRiSavings(pricing) {
    if (currentPricingModel === 'payg' || !pricing) return '';
    const payg = getPaygMonthlyPrice(pricing);
    const ri = getMonthlyPrice(pricing);
    if (!payg || payg <= 0 || ri == null) return '';
    const pct = ((payg - ri) / payg * 100).toFixed(0);
    if (pct <= 0) return '';
    return ` <span class="ri-savings">(-${pct}%)</span>`;
}

// Null-safe price formatters — return 'N/A' when price is unavailable
function formatHourlyPriceSafe(pricing) {
    if (!pricing) return 'N/A';
    const price = getHourlyPrice(pricing);
    if (price == null) return 'N/A';
    return formatHourlyCurrency(price * getDiscountMultiplier(), pricing.currency);
}

function formatMonthlyPriceSafe(pricing) {
    if (!pricing) return 'N/A';
    const price = getMonthlyPrice(pricing);
    if (price == null) return 'N/A';
    return formatCurrency(price * getDiscountMultiplier(), pricing.currency) + renderRiSavings(pricing);
}

// Display Results
function displayResults(data) {
    if (!data.alternatives || data.alternatives.length === 0) {
        noResults.classList.remove('hidden');
        resultsTableBody.innerHTML = '';
        targetSkuInfo.innerHTML = '';
    } else {
        noResults.classList.add('hidden');
        displayTargetSku(data.targetSku);
        
        // Populate generation filter from full results, then apply all filters
        populateGenFilter(data.alternatives);
        const filteredAlternatives = filterResults(data.alternatives);
        displayAlternatives(filteredAlternatives);
    }
    // Show cache timestamp if available
    const timestampEl = document.getElementById('cacheTimestamp');
    if (data.dataLastUpdated) {
        const d = new Date(data.dataLastUpdated);
        const formatted = d.toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' })
            + ' ' + d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', timeZoneName: 'short' });
        timestampEl.textContent = `Data last refreshed: ${formatted}`;
    } else {
        timestampEl.textContent = '';
    }
    resultsSection.classList.remove('hidden');

    // Show cross-region check bar and populate with regions (excluding current)
    initRegionCheckBar();
}

// Filter results by CPU vendor checkboxes
function filterResultsByVendor(alternatives) {
    const showIntel = document.getElementById('resultFilterIntel').checked;
    const showAMD = document.getElementById('resultFilterAMD').checked;
    const showARM = document.getElementById('resultFilterARM').checked;
    
    return alternatives.filter(alt => {
        if (alt.cpuVendor === 'Intel' && showIntel) return true;
        if (alt.cpuVendor === 'AMD' && showAMD) return true;
        if (alt.cpuVendor === 'ARM' && showARM) return true;
        return false;
    });
}

// Filter results by CPU generation checkboxes
function filterResultsByGeneration(alternatives) {
    const container = document.getElementById('genFilterOptions');
    const checkboxes = container.querySelectorAll('input[type="checkbox"]');
    if (checkboxes.length === 0) return alternatives;
    
    const selectedGens = new Set();
    checkboxes.forEach(cb => {
        if (cb.checked) selectedGens.add(cb.value);
    });
    
    // If all are selected, no filtering needed
    if (selectedGens.size === checkboxes.length) return alternatives;
    
    return alternatives.filter(alt => {
        const gen = alt.cpuGeneration || 'Unknown';
        return selectedGens.has(gen);
    });
}

// Apply vendor, generation, and retirement filters
function filterResults(alternatives) {
    let filtered = filterResultsByGeneration(filterResultsByVendor(alternatives));
    // Apply retirement filter if checkbox exists and is checked
    const hideRetiring = document.getElementById('hideRetiringFilter');
    if (hideRetiring && hideRetiring.checked) {
        filtered = filtered.filter(alt => !alt.retirementStatus);
    }
    return filtered;
}

// Populate generation filter checkboxes from current results
function populateGenFilter(alternatives) {
    const section = document.getElementById('genFilterSection');
    const container = document.getElementById('genFilterOptions');
    
    // Get unique generations from vendor-filtered results
    const vendorFiltered = filterResultsByVendor(alternatives);
    const generations = new Map();
    vendorFiltered.forEach(alt => {
        const gen = alt.cpuGeneration || 'Unknown';
        generations.set(gen, (generations.get(gen) || 0) + 1);
    });
    
    if (generations.size === 0) {
        section.style.display = 'none';
        return;
    }
    section.style.display = '';
    
    // Sort: by vendor grouping (Intel gens, AMD gens, ARM gens, Unknown last)
    const sorted = [...generations.entries()].sort((a, b) => {
        if (a[0] === 'Unknown') return 1;
        if (b[0] === 'Unknown') return -1;
        return a[0].localeCompare(b[0]);
    });
    
    container.innerHTML = sorted.map(([gen, count]) => `
        <label class="checkbox-label gen-checkbox">
            <input type="checkbox" value="${gen}" checked>
            ${gen} <span class="gen-count">(${count})</span>
        </label>
    `).join('');
    
    // Add change listeners to each checkbox
    container.querySelectorAll('input[type="checkbox"]').forEach(cb => {
        cb.addEventListener('change', updateGenFilter);
    });
}

// Handle generation filter change
function updateGenFilter() {
    if (!currentResults || !currentResults.alternatives) return;
    trackEvent('result_generation_filter_changed', {});
    displayAlternatives(filterResults(currentResults.alternatives));
}

// Handle Select All / Deselect All buttons
function genSelectAll() {
    document.getElementById('genFilterOptions').querySelectorAll('input[type="checkbox"]').forEach(cb => cb.checked = true);
    updateGenFilter();
}

function genDeselectAll() {
    document.getElementById('genFilterOptions').querySelectorAll('input[type="checkbox"]').forEach(cb => cb.checked = false);
    updateGenFilter();
}

// Display Target SKU Info
function displayTargetSku(targetSku) {
    const cpuDisplay = targetSku.cpuVendor ? `${targetSku.cpuVendor} (${targetSku.architecture || 'x64'})` : 'N/A';
    const cpuPerfDisplay = targetSku.cpuGeneration && targetSku.cpuPerfScore
        ? `${targetSku.cpuGeneration} — score ${targetSku.cpuPerfScore}`
        : null;
    const pricingLabel = currentPricingModel === 'ri1year' ? '1yr RI'
        : currentPricingModel === 'ri3year' ? '3yr RI'
        : (currentPricingOS === 'windows' ? 'Windows' : 'Linux');
    const html = `
        <h3>Target SKU: ${targetSku.name}</h3>
        <div class="target-sku-grid">
            <div class="target-sku-item">
                <strong>CPU Vendor</strong>
                <span>${cpuDisplay}</span>
            </div>
            ${cpuPerfDisplay ? `
            <div class="target-sku-item">
                <strong>CPU Performance</strong>
                <span>${cpuPerfDisplay}</span>
            </div>
            ` : ''}
            <div class="target-sku-item">
                <strong>vCPUs</strong>
                <span>${targetSku.vCPUs || 'N/A'}</span>
            </div>
            <div class="target-sku-item">
                <strong>Memory</strong>
                <span>${targetSku.memoryGB ? targetSku.memoryGB + ' GB' : 'N/A'}</span>
            </div>
            ${targetSku.gpuCount ? `
            <div class="target-sku-item">
                <strong>GPUs</strong>
                <span>${targetSku.gpuCount} ${targetSku.gpuType || ''}</span>
            </div>
            ` : ''}
            <div class="target-sku-item">
                <strong>Hourly Cost (${pricingLabel})</strong>
                <span>${formatHourlyPriceSafe(targetSku.pricing)}</span>
            </div>
            <div class="target-sku-item">
                <strong>Monthly Cost (${pricingLabel})</strong>
                <span>${formatMonthlyPriceSafe(targetSku.pricing)}</span>
            </div>
            <div class="target-sku-item">
                <strong>Availability Zones</strong>
                <span>${targetSku.zones || 'N/A'}</span>
            </div>
            ${targetSku.networkBandwidthMbps ? `
            <div class="target-sku-item">
                <strong>Network Bandwidth</strong>
                <span>${formatBandwidth(targetSku.networkBandwidthMbps)}</span>
            </div>
            ` : ''}
        </div>
    `;
    targetSkuInfo.innerHTML = html;

    // Show retirement warning banner if target SKU is retiring
    const retirementBanner = document.getElementById('retirementBanner');
    if (retirementBanner) {
        if (targetSku.retirementStatus) {
            const dateStr = targetSku.retirementDate ? new Date(targetSku.retirementDate + 'T00:00:00Z').toLocaleDateString('en-US', { year: 'numeric', month: 'long', day: 'numeric' }) : 'TBD';
            const statusClass = targetSku.retirementStatus === 'Retired' ? 'retirement-retired' : 'retirement-announced';
            const statusLabel = targetSku.retirementStatus === 'Retired' ? '❌ This VM SKU has been retired' : '⚠️ This VM SKU is announced for retirement';
            retirementBanner.className = `retirement-banner ${statusClass}`;
            retirementBanner.innerHTML = `
                <strong>${statusLabel}</strong> on ${dateStr}.
                ${targetSku.migrationGuideUrl ? `<a href="${targetSku.migrationGuideUrl}" target="_blank" rel="noopener">View Migration Guide →</a>` : ''}
            `;
            retirementBanner.classList.remove('hidden');
        } else {
            retirementBanner.classList.add('hidden');
        }
    }
}

// Format CPU performance display for table cells
function formatCpuPerf(sku) {
    const gen = sku.cpuGeneration;
    const score = sku.cpuPerfScore;
    const vendor = sku.cpuVendor || 'Intel';
    const arch = sku.architecture || 'x64';
    
    if (gen && score) {
        return `<span class="cpu-gen" title="${vendor} ${arch}">${gen}</span><span class="cpu-score">${score}</span>`;
    }
    // Fallback for GPU/specialty SKUs without CPU perf data
    return `<span class="cpu-gen">${vendor} (${arch})</span>`;
}

// Render retirement badge for a SKU
function renderRetirementBadge(alt) {
    if (!alt.retirementStatus) return '';
    if (alt.retirementStatus === 'Retired') {
        return '<span class="retirement-badge retired" title="This SKU has been retired">❌ Retired</span>';
    }
    const dateStr = alt.retirementDate ? new Date(alt.retirementDate + 'T00:00:00Z').toLocaleDateString('en-US', { year: 'numeric', month: 'short' }) : '';
    return `<span class="retirement-badge retiring" title="Retirement announced for ${dateStr}">⚠️ Retiring ${dateStr}</span>`;
}

// Get CPU performance direction indicator
function getCpuPerfIndicator(targetScore, altScore) {
    if (targetScore == null || altScore == null) {
        return { direction: 'unknown', icon: '', changed: false };
    }
    if (altScore > targetScore) {
        return { direction: 'faster', icon: '▲', changed: true };
    } else if (altScore < targetScore) {
        return { direction: 'slower', icon: '▼', changed: true };
    }
    return { direction: 'same', icon: '●', changed: false };
}

function renderCpuPerfIndicator(indicator) {
    if (!indicator.changed) {
        if (indicator.direction === 'unknown') return '';
        return `<span class="diff-indicator diff-same" title="CPU Perf: Same as target">●</span>`;
    }
    if (indicator.direction === 'faster') {
        return `<span class="diff-indicator diff-upgrade" title="CPU Perf: Faster than target">▲</span>`;
    }
    return `<span class="diff-indicator diff-downgrade" title="CPU Perf: Slower than target">▼</span>`;
}

// Calculate difference indicators from existing data
function calculateIndicators(targetSku, alternativeSku) {
    return {
        cpuPerf: getCpuPerfIndicator(targetSku.cpuPerfScore, alternativeSku.cpuPerfScore),
        vCPUs: getDirectionIndicator(targetSku.vCPUs, alternativeSku.vCPUs),
        memory: getDirectionIndicator(targetSku.memoryGB, alternativeSku.memoryGB),
        hourlyPrice: getPriceIndicator(
            getHourlyPrice(targetSku.pricing), 
            getHourlyPrice(alternativeSku.pricing)
        ),
        monthlyPrice: getPriceIndicator(
            getMonthlyPrice(targetSku.pricing), 
            getMonthlyPrice(alternativeSku.pricing)
        )
    };
}

function getDirectionIndicator(targetValue, altValue) {
    if (targetValue == null || altValue == null) {
        return { direction: 'unknown', icon: '', changed: false };
    }
    
    if (altValue > targetValue) {
        return { direction: 'upgrade', icon: '▲', changed: true };
    } else if (altValue < targetValue) {
        return { direction: 'downgrade', icon: '▼', changed: true };
    } else {
        return { direction: 'same', icon: '●', changed: false };
    }
}

function getPriceIndicator(targetPrice, altPrice) {
    if (targetPrice == null || altPrice == null) {
        return { direction: 'unknown', icon: '', changed: false };
    }
    
    if (altPrice > targetPrice) {
        // Higher price = bad (RED)
        return { direction: 'higher', icon: '▲', changed: true, negative: true };
    } else if (altPrice < targetPrice) {
        // Lower price = good (GREEN)
        return { direction: 'lower', icon: '▼', changed: true, positive: true };
    } else {
        return { direction: 'same', icon: '●', changed: false };
    }
}

function renderIndicator(indicator, fieldName) {
    if (!indicator.changed) {
        return `<span class="diff-indicator diff-same" title="${fieldName}: Same as target">●</span>`;
    }
    
    // Price has inverted logic (higher = bad, lower = good)
    const isPriceField = fieldName.toLowerCase().includes('price');
    
    if (isPriceField) {
        if (indicator.negative) {
            return `<span class="diff-indicator diff-negative" title="${fieldName}: Higher than target (worse)">▲</span>`;
        } else if (indicator.positive) {
            return `<span class="diff-indicator diff-positive" title="${fieldName}: Lower than target (better)">▼</span>`;
        }
    }
    
    // Normal fields (more = upgrade, less = downgrade)
    if (indicator.direction === 'upgrade') {
        return `<span class="diff-indicator diff-upgrade" title="${fieldName}: More than target">▲</span>`;
    } else if (indicator.direction === 'downgrade') {
        return `<span class="diff-indicator diff-downgrade" title="${fieldName}: Less than target">▼</span>`;
    }
    
    return '';
}

// Display Alternatives as Cards
function displayAlternatives(alternatives) {
    resultsTableBody.innerHTML = '';

    // Update results count badge
    const countBadge = document.getElementById('resultsCount');
    if (countBadge) countBadge.textContent = alternatives.length;
    
    // Get target SKU for comparison
    const targetSku = currentResults?.targetSku;

    alternatives.forEach((alt, index) => {
        const scoreClass = alt.similarityScore >= 90 ? 'high' :
                          alt.similarityScore >= 85 ? 'mid' : 'low';
        const scorePercent = alt.similarityScore.toFixed(1);

        // Calculate price delta
        let deltaHtml = '';
        if (targetSku) {
            const targetPrice = getMonthlyPrice(targetSku.pricing);
            const altPrice = getMonthlyPrice(alt.pricing);
            if (targetPrice && altPrice && targetPrice > 0) {
                const pctDiff = ((altPrice - targetPrice) / targetPrice * 100).toFixed(0);
                if (pctDiff < -1) {
                    deltaHtml = `<span class="card-price-delta delta-save">${pctDiff}% ↓</span>`;
                } else if (pctDiff > 1) {
                    deltaHtml = `<span class="card-price-delta delta-more">+${pctDiff}% ↑</span>`;
                } else {
                    deltaHtml = `<span class="card-price-delta delta-same">same price</span>`;
                }
            }
        }

        // CPU info line
        const cpuInfo = alt.cpuVendor 
            ? `${alt.cpuVendor}${alt.cpuGeneration ? ' ' + alt.cpuGeneration : ''} (${alt.architecture || 'x64'})`
            : '';

        // Region availability
        const regionAvail = renderRegionAvailCell(alt.name);

        // Build card
        const card = document.createElement('div');
        card.classList.add('result-card');
        card.dataset.index = index;
        card.dataset.skuName = alt.name;

        card.innerHTML = `
            <div class="card-sku-info">
                <div class="card-sku-name">${alt.name} ${renderRetirementBadge(alt)}</div>
                <div class="card-sku-cpu">${cpuInfo}</div>
                <div class="card-score-bar">
                    <div class="score-track"><div class="score-fill ${scoreClass}" style="width:${scorePercent}%"></div></div>
                    <span class="card-score-pct ${scoreClass}">${scorePercent}%</span>
                </div>
            </div>
            <div class="card-specs">
                <div class="mini-spec"><div class="mini-spec-val">${alt.vCPUs || '—'}</div><div class="mini-spec-lbl">vCPUs</div></div>
                <div class="mini-spec"><div class="mini-spec-val">${alt.memoryGB ? alt.memoryGB + 'GB' : '—'}</div><div class="mini-spec-lbl">Memory</div></div>
                <div class="mini-spec"><div class="mini-spec-val">${alt.zones || '—'}</div><div class="mini-spec-lbl">AZs</div></div>
                ${regionAvail}
            </div>
            <div class="card-price">
                <div class="card-price-hourly">${formatHourlyPriceSafe(alt.pricing)}</div>
                <div class="card-price-monthly">${formatMonthlyPriceSafe(alt.pricing)}/mo</div>
                ${deltaHtml}
            </div>
        `;
        
        card.addEventListener('click', () => toggleDetails(index, alt, targetSku || alt));
        resultsTableBody.appendChild(card);
        
        // Details expansion div
        const detailsDiv = document.createElement('div');
        detailsDiv.classList.add('details-row', 'hidden');
        detailsDiv.dataset.index = index;
        detailsDiv.innerHTML = `<div class="details-content"></div>`;
        resultsTableBody.appendChild(detailsDiv);
    });

    // Kick off background prefetch for top 10 rows (Phase 3)
    if (targetSku) {
        const location = currentResults?.location || document.getElementById('location').value;
        prefetchTopDetails(alternatives, targetSku, location);
    }
}

// Toggle details row expand/collapse (Phase 2)
async function toggleDetails(index, altSku, targetSku) {
    const detailsRow = document.querySelector(`.details-row[data-index="${index}"]`);
    const detailsContent = detailsRow.querySelector('.details-content');
    
    // Toggle visibility
    if (detailsRow.classList.contains('hidden')) {
        // Show details
        detailsRow.classList.remove('hidden');
        
        // Check cache first
        const cacheKey = `${targetSku.name}_${altSku.name}`;
        if (expandedDetailsCache.has(cacheKey)) {
            detailsContent.innerHTML = renderDetailedComparison(
                expandedDetailsCache.get(cacheKey),
                targetSku,
                altSku
            );
        } else {
            // Show loading state
            detailsContent.innerHTML = '<div class="loading"><div class="spinner"></div><p>Loading detailed comparison...</p></div>';
            
            // Fetch from API
            try {
                const details = await fetchComparisonDetails(
                    targetSku.name,
                    altSku.name,
                    currentResults.location || document.getElementById('location').value
                );
                
                // Cache it
                expandedDetailsCache.set(cacheKey, details);
                
                // Render it
                detailsContent.innerHTML = renderDetailedComparison(details, targetSku, altSku);
            } catch (error) {
                console.error('Failed to load details:', error);
                detailsContent.innerHTML = '<div class="error"><p>❌ Failed to load details. Click row again to retry.</p></div>';
            }
        }
    } else {
        // Hide details
        detailsRow.classList.add('hidden');
    }
}

// Fetch comparison details from API (Phase 2)
async function fetchComparisonDetails(targetName, altName, location) {
    const params = new URLSearchParams({
        target: targetName,
        alternative: altName,
        location: location,
        currency: document.getElementById('currencyCode')?.value || 'USD'
    });
    
    const response = await fetch(`${API_BASE_URL}/compare_details?${params}`);
    
    if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
    }
    
    return await response.json();
}

// Fire a single prefetch for one alternative, storing in cache on success (Phase 3)
function prefetchOne(alt, targetSku, location, signal) {
    const cacheKey = `${targetSku.name}_${alt.name}`;
    if (expandedDetailsCache.has(cacheKey)) return;
    const params = new URLSearchParams({
        target: targetSku.name,
        alternative: alt.name,
        location: location,
        currency: document.getElementById('currencyCode')?.value || 'USD'
    });
    fetch(`${API_BASE_URL}/compare_details?${params}`, { signal })
        .then(r => r.ok ? r.json() : null)
        .then(data => { if (data && !signal.aborted) expandedDetailsCache.set(cacheKey, data); })
        .catch(() => {}); // fire-and-forget — errors silently swallowed
}

// Speculatively prefetch top 10 alternatives after results render (Phase 3)
// Top 3 fire immediately; rows 4-10 stagger in pairs after 200ms to avoid cold-start stampede.
function prefetchTopDetails(alternatives, targetSku, location) {
    if (prefetchAbortController) prefetchAbortController.abort();
    prefetchAbortController = new AbortController();
    const signal = prefetchAbortController.signal;

    const toPrefetch = alternatives.slice(0, 10);
    toPrefetch.slice(0, 3).forEach(alt => prefetchOne(alt, targetSku, location, signal));

    const deferred = toPrefetch.slice(3);
    setTimeout(() => {
        let i = 0;
        function next() {
            if (signal.aborted || i >= deferred.length) return;
            prefetchOne(deferred[i++], targetSku, location, signal);
            if (i < deferred.length) prefetchOne(deferred[i++], targetSku, location, signal);
            setTimeout(next, 50);
        }
        next();
    }, 200);
}

// Render detailed comparison (Phase 2)
function renderDetailedComparison(data, targetSku, altSku) {
    const diff = data.differences;
    
    // Derive CPU performance comparison from the SKU objects (not from backend diff)
    const cpuPerfHtml = renderCpuPerfComparison(targetSku, altSku);

    return `
        <div class="comparison-details">
            <h4>📊 Detailed Comparison: ${altSku.name} vs ${targetSku.name}</h4>
            
            <div class="details-grid">
                <!-- Compute Section -->
                <div class="details-section">
                    <h5>Compute</h5>
                    ${cpuPerfHtml}
                    ${renderNumericDiff('vCPUs', diff.compute.vCPUs)}
                    ${renderNumericDiff('Memory', diff.compute.memory)}
                    ${renderBooleanDiff(diff.compute.hyperVGen2)}
                </div>
                
                <!-- Storage Section -->
                <div class="details-section">
                    <h5>Storage</h5>
                    ${renderNumericDiff('Max Data Disks', diff.storage.maxDataDisks)}
                    ${renderNumericDiff('Uncached IOPS', diff.storage.uncachedIOPS)}
                    ${renderNumericDiff('Uncached Throughput', diff.storage.uncachedThroughput)}
                    ${renderNumericDiff('OS VHD Size', diff.storage.osVhdSizeMB)}
                    ${renderBooleanDiff(diff.storage.premiumIO)}
                    ${renderBooleanDiff(diff.storage.ephemeralOSDisk)}
                    ${renderBooleanDiff(diff.storage.nvmeSupport)}
                </div>
                
                <!-- Network Section -->
                <div class="details-section">
                    <h5>Network</h5>
                    ${diff.network.networkBandwidthMbps && diff.network.networkBandwidthMbps.target != null ? renderNumericDiff('Max Bandwidth', diff.network.networkBandwidthMbps) : ''}
                    ${renderNumericDiff('Max NICs', diff.network.maxNics)}
                    ${renderBooleanDiff(diff.network.acceleratedNetworking)}
                </div>
                
                <!-- Cost Section -->
                <div class="details-section">
                    <h5>Cost Analysis</h5>
                    ${renderPricingSection(diff.pricing)}
                </div>
            </div>
            
            <!-- Features Section -->
            ${renderFeatures(diff.features)}
        </div>
    `;
}

// Render CPU performance comparison for the detailed expand view
function renderCpuPerfComparison(targetSku, altSku) {
    const tScore = targetSku.cpuPerfScore;
    const aScore = altSku.cpuPerfScore;
    const tGen = targetSku.cpuGeneration;
    const aGen = altSku.cpuGeneration;

    if (!tScore && !aScore) return '';

    if (tScore && aScore) {
        const delta = aScore - tScore;
        const pct = tScore > 0 ? ((delta / tScore) * 100).toFixed(1) : '0.0';
        const sign = delta > 0 ? '+' : '';
        let className, icon, label;
        if (delta > 0) {
            className = 'diff-item upgrade';
            icon = '▲';
            label = 'faster';
        } else if (delta < 0) {
            className = 'diff-item downgrade';
            icon = '▼';
            label = 'slower';
        } else {
            className = 'diff-item same';
            icon = '●';
            label = 'same';
        }
        return `<div class="${className}">${icon} CPU Perf: ${tGen || 'Unknown'} (${tScore}) → ${aGen || 'Unknown'} (${aScore}) <span class="diff-pct">${sign}${pct}% ${label}</span></div>`;
    }

    // Only one side has data
    if (aScore) {
        return `<div class="diff-item same">● CPU Perf: ${aGen || 'Unknown'} (score: ${aScore})</div>`;
    }
    return `<div class="diff-item same">● CPU Perf: Target ${tGen || 'Unknown'} (score: ${tScore}) — alternative: N/A</div>`;
}

function renderNumericDiff(label, diff) {
    if (!diff.changed) {
        return `<div class="diff-item same">● ${label}: ${diff.alternative} ${diff.unit} (same)</div>`;
    }
    
    const icon = diff.direction === 'upgrade' ? '▲' : '▼';
    const className = diff.direction === 'upgrade' ? 'diff-item upgrade' : 'diff-item downgrade';
    const sign = diff.delta > 0 ? '+' : '';
    const percent = diff.percentChange ? ` (${sign}${diff.percentChange}%)` : '';
    
    return `
        <div class="${className}">
            ${icon} ${label}: ${diff.target} → ${diff.alternative} ${diff.unit}
            <span class="delta">${sign}${diff.delta} ${diff.unit}${percent}</span>
        </div>
    `;
}

function renderPriceDiff(label, diff) {
    if (!diff) return `<div class="diff-item same">● ${label}: N/A</div>`;
    if (!diff.changed) {
        return `<div class="diff-item same">● ${label}: ${diff.currency} ${diff.alternative} (same)</div>`;
    }
    
    const icon = diff.isNegative ? '⚠️' : '✅';
    const className = diff.isNegative ? 'diff-item negative' : 'diff-item positive';
    const sign = diff.delta > 0 ? '+' : '';
    const percent = diff.percentChange ? ` (${sign}${diff.percentChange}%)` : '';
    
    return `
        <div class="${className}">
            ${icon} ${label}: ${diff.currency} ${diff.target.toFixed(2)} → ${diff.currency} ${diff.alternative.toFixed(2)}
            <span class="delta">${sign}${diff.currency} ${Math.abs(diff.delta).toFixed(2)}${percent}</span>
        </div>
    `;
}

function renderPricingSection(pricing) {
    if (!pricing) return '<div class="diff-item same">● Pricing data unavailable</div>';

    let hourly, monthly, modelLabel;
    const isWindows = currentPricingOS === 'windows';
    const osLabel = isWindows ? 'Windows' : 'Linux';

    if (currentPricingModel === 'ri1year' && pricing.ri1Year) {
        hourly = isWindows ? (pricing.ri1YearWindows?.hourly ?? pricing.ri1Year.hourly) : pricing.ri1Year.hourly;
        monthly = isWindows ? (pricing.ri1YearWindows?.monthly ?? pricing.ri1Year.monthly) : pricing.ri1Year.monthly;
        modelLabel = `1-Year RI (${osLabel})`;
    } else if (currentPricingModel === 'ri3year' && pricing.ri3Year) {
        hourly = isWindows ? (pricing.ri3YearWindows?.hourly ?? pricing.ri3Year.hourly) : pricing.ri3Year.hourly;
        monthly = isWindows ? (pricing.ri3YearWindows?.monthly ?? pricing.ri3Year.monthly) : pricing.ri3Year.monthly;
        modelLabel = `3-Year RI (${osLabel})`;
    } else if (isWindows && pricing.hourlyWindows) {
        hourly = pricing.hourlyWindows;
        monthly = pricing.monthlyWindows;
        modelLabel = `Pay as you go (${osLabel})`;
    } else {
        hourly = pricing.hourly;
        monthly = pricing.monthly;
        modelLabel = `Pay as you go (${osLabel})`;
    }

    let html = `<div class="diff-item same" style="font-size:0.85em;opacity:0.7">Pricing model: ${modelLabel}</div>`;
    html += renderPriceDiff('Hourly Price', hourly);
    html += renderPriceDiff('Monthly Price', monthly);
    html += renderEfficiency(pricing.efficiency);
    return html;
}

function renderBooleanDiff(diff) {
    const icon = diff.changed ? (diff.direction === 'added' ? '✅' : '❌') : '●';
    const className = diff.changed ? 'diff-item' : 'diff-item same';
    const text = diff.changed ? 
        `${diff.target ? 'Yes' : 'No'} → ${diff.alternative ? 'Yes' : 'No'}` :
        `${diff.alternative ? 'Yes' : 'No'} (same)`;
    
    return `<div class="${className}">${icon} ${diff.feature}: ${text}</div>`;
}

function renderEfficiency(efficiency) {
    let html = '';
    
    if (efficiency.costPerVCPU) {
        const icon = efficiency.costPerVCPU.betterEfficiency ? '✅' : '⚠️';
        html += `
            <div class="diff-item">
                ${icon} Cost per vCPU: $${efficiency.costPerVCPU.alternative.toFixed(4)}
                (${efficiency.costPerVCPU.betterEfficiency ? 'better' : 'worse'} efficiency)
            </div>
        `;
    }
    
    if (efficiency.costPerGB) {
        const icon = efficiency.costPerGB.betterEfficiency ? '✅' : '⚠️';
        html += `
            <div class="diff-item">
                ${icon} Cost per GB: $${efficiency.costPerGB.alternative.toFixed(4)}
                (${efficiency.costPerGB.betterEfficiency ? 'better' : 'worse'} efficiency)
            </div>
        `;
    }
    
    return html;
}

function renderFeatures(features) {
    if (features.added.length === 0 && features.removed.length === 0) {
        return '<div class="features-diff"><div class="diff-item same">● All features unchanged</div></div>';
    }
    
    let html = '<div class="features-diff">';
    
    if (features.added.length > 0) {
        html += `<div class="features-added">✅ <strong>Added:</strong> ${features.added.join(', ')}</div>`;
    }
    
    if (features.removed.length > 0) {
        html += `<div class="features-removed">❌ <strong>Removed:</strong> ${features.removed.join(', ')}</div>`;
    }
    
    html += '</div>';
    return html;
}

// Format Currency
function formatCurrency(amount, currency = 'USD') {
    if (amount === null || amount === undefined) return 'N/A';
    return new Intl.NumberFormat('en-US', {
        style: 'currency',
        currency: currency,
        minimumFractionDigits: 2,
        maximumFractionDigits: 2
    }).format(amount);
}

// Use for hourly prices — 4 decimal places to match Azure Pricing Calculator precision
function formatHourlyCurrency(amount, currency = 'USD') {
    if (amount === null || amount === undefined) return 'N/A';
    return new Intl.NumberFormat('en-US', {
        style: 'currency',
        currency: currency,
        minimumFractionDigits: 4,
        maximumFractionDigits: 4
    }).format(amount);
}

function formatBandwidth(mbps) {
    if (mbps === null || mbps === undefined || mbps === 0) return 'N/A';
    if (mbps >= 1000) {
        return `${(mbps / 1000).toLocaleString('en-US', { maximumFractionDigits: 1 })} Gbps`;
    }
    return `${mbps.toLocaleString('en-US')} Mbps`;
}

function escapeCsvCell(value) {
    const stringValue = value === null || value === undefined ? '' : String(value);
    return `"${stringValue.replace(/"/g, '""')}"`;
}

function toNumericOrNull(value) {
    if (value === null || value === undefined || value === '') return null;
    const numeric = Number(value);
    return Number.isFinite(numeric) ? numeric : null;
}

function formatCapabilityValue(value, type = 'string', precision = 0) {
    if (value === null || value === undefined || value === '') return 'N/A';

    if (type === 'boolean') {
        return value ? 'Yes' : 'No';
    }

    if (type === 'number') {
        const numeric = toNumericOrNull(value);
        if (numeric === null) return 'N/A';
        return precision > 0 ? numeric.toFixed(precision) : String(Math.round(numeric));
    }

    return String(value);
}

function formatDeltaValue(targetValue, altValue, precision = 0) {
    const target = toNumericOrNull(targetValue);
    const alternative = toNumericOrNull(altValue);
    if (target === null || alternative === null) return 'N/A';
    const delta = alternative - target;
    return precision > 0 ? delta.toFixed(precision) : String(Math.round(delta));
}

// Export to CSV
function exportToCSV() {
    if (!currentResults || !currentResults.alternatives || currentResults.alternatives.length === 0) {
        trackEvent('export_csv_failed', {
            reason: 'no_data'
        });
        showError('No data to export');
        return;
    }

    const discount = getDiscountMultiplier();
    const discountNote = discount < 1.0 ? ` (${((1 - discount) * 100).toFixed(1)}% discount applied)` : '';

    const targetSku = currentResults.targetSku || {};
    const exportLocation = currentResults.searchParameters?.location || currentResults.location || document.getElementById('location')?.value || 'N/A';

    const capabilityColumns = [
        { key: 'vCPUs', label: 'vCPUs', type: 'number', precision: 0 },
        { key: 'memoryGB', label: 'Memory (GB)', type: 'number', precision: 2 },
        { key: 'gpuCount', label: 'GPU Count', type: 'number', precision: 0 },
        { key: 'gpuType', label: 'GPU Type', type: 'string' },
        { key: 'maxDataDiskCount', label: 'Max Data Disks', type: 'number', precision: 0 },
        { key: 'maxNics', label: 'Max NICs', type: 'number', precision: 0 },
        { key: 'uncachedDiskIOPS', label: 'Uncached Disk IOPS', type: 'number', precision: 0 },
        { key: 'uncachedDiskBytesPerSecond', label: 'Uncached Disk Throughput (Bytes/s)', type: 'number', precision: 0 },
        { key: 'maxWriteAcceleratorDisks', label: 'Max Write Accelerator Disks', type: 'number', precision: 0 },
        { key: 'osVhdSizeMB', label: 'OS VHD Size (MB)', type: 'number', precision: 0 },
        { key: 'hyperVGenerations', label: 'Hyper-V Generations', type: 'string' },
        { key: 'premiumIO', label: 'Premium IO', type: 'boolean' },
        { key: 'ephemeralOSDisk', label: 'Ephemeral OS Disk', type: 'boolean' },
        { key: 'acceleratedNetworking', label: 'Accelerated Networking', type: 'boolean' },
        { key: 'encryptionAtHost', label: 'Encryption at Host', type: 'boolean' },
        { key: 'nvme', label: 'NVMe', type: 'boolean' }
    ];

    const summaryHeaders = [
        'Rank',
        'Location',
        'Target SKU',
        'SKU Name',
        'Similarity Score (%)',
        'CPU Vendor',
        'Architecture',
        'CPU Generation',
        'CPU Perf Score',
        'Target CPU Perf Score',
        'CPU Perf Delta (%)',
        `Hourly Cost Linux${discountNote}`,
        `Monthly Cost Linux${discountNote}`,
        `Hourly Cost Windows${discountNote}`,
        `Monthly Cost Windows${discountNote}`,
        `1yr RI Hourly (Linux)${discountNote}`,
        `1yr RI Monthly (Linux)${discountNote}`,
        `1yr RI Hourly (Windows)${discountNote}`,
        `1yr RI Monthly (Windows)${discountNote}`,
        `3yr RI Hourly (Linux)${discountNote}`,
        `3yr RI Monthly (Linux)${discountNote}`,
        `3yr RI Hourly (Windows)${discountNote}`,
        `3yr RI Monthly (Windows)${discountNote}`,
        'Currency',
        'Availability Zones'
    ];

    // Add region availability column if active
    if (regionAvailabilityData) {
        summaryHeaders.push(`Available in ${regionAvailabilityData.region}`);
    }

    const capabilityHeaders = capabilityColumns.map(col => col.label);

    const headers = [...summaryHeaders, ...capabilityHeaders];

    const rows = currentResults.alternatives.map((alt, index) => {
        const altCaps = alt.capabilities || {};
        const summaryRow = [
            index + 1,
            exportLocation,
            targetSku.name || 'N/A',
            alt.name || 'N/A',
            alt.similarityScore != null ? alt.similarityScore.toFixed(1) : 'N/A',
            alt.cpuVendor || 'N/A',
            alt.architecture || 'N/A',
            alt.cpuGeneration || 'N/A',
            alt.cpuPerfScore != null ? alt.cpuPerfScore : 'N/A',
            targetSku.cpuPerfScore != null ? targetSku.cpuPerfScore : 'N/A',
            (alt.cpuPerfScore != null && targetSku.cpuPerfScore > 0)
                ? (((alt.cpuPerfScore - targetSku.cpuPerfScore) / targetSku.cpuPerfScore) * 100).toFixed(1)
                : 'N/A',
            alt.pricing ? (alt.pricing.hourlyPrice * discount).toFixed(4) : 'N/A',
            alt.pricing ? (alt.pricing.monthlyPrice * discount).toFixed(2) : 'N/A',
            alt.pricing && alt.pricing.hourlyPriceWindows != null ? (alt.pricing.hourlyPriceWindows * discount).toFixed(4) : 'N/A',
            alt.pricing && alt.pricing.monthlyPriceWindows != null ? (alt.pricing.monthlyPriceWindows * discount).toFixed(2) : 'N/A',
            alt.pricing && alt.pricing.ri1YearHourly != null ? (alt.pricing.ri1YearHourly * discount).toFixed(4) : 'N/A',
            alt.pricing && alt.pricing.ri1YearMonthly != null ? (alt.pricing.ri1YearMonthly * discount).toFixed(2) : 'N/A',
            alt.pricing && alt.pricing.ri1YearHourlyWindows != null ? (alt.pricing.ri1YearHourlyWindows * discount).toFixed(4) : 'N/A',
            alt.pricing && alt.pricing.ri1YearMonthlyWindows != null ? (alt.pricing.ri1YearMonthlyWindows * discount).toFixed(2) : 'N/A',
            alt.pricing && alt.pricing.ri3YearHourly != null ? (alt.pricing.ri3YearHourly * discount).toFixed(4) : 'N/A',
            alt.pricing && alt.pricing.ri3YearMonthly != null ? (alt.pricing.ri3YearMonthly * discount).toFixed(2) : 'N/A',
            alt.pricing && alt.pricing.ri3YearHourlyWindows != null ? (alt.pricing.ri3YearHourlyWindows * discount).toFixed(4) : 'N/A',
            alt.pricing && alt.pricing.ri3YearMonthlyWindows != null ? (alt.pricing.ri3YearMonthlyWindows * discount).toFixed(2) : 'N/A',
            alt.pricing?.currency || 'N/A',
            alt.zones || 'N/A'
        ];

        // Add region availability if active
        if (regionAvailabilityData) {
            const avail = regionAvailabilityData.availability[alt.name];
            summaryRow.push(avail === true ? 'Yes' : avail === false ? 'No' : 'N/A');
        }

        const capabilityRow = capabilityColumns.flatMap(col => {
            const altValue = altCaps[col.key];
            return formatCapabilityValue(altValue, col.type, col.precision ?? 0);
        });

        return [...summaryRow, ...capabilityRow];
    });

    const csvContent = [
        headers.map(escapeCsvCell).join(','),
        ...rows.map(row => row.map(escapeCsvCell).join(','))
    ].join('\n');

    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
    const link = document.createElement('a');
    const url = URL.createObjectURL(blob);

    const targetName = targetSku.name || 'target-sku';
    link.setAttribute('href', url);
    link.setAttribute('download', `azure-vm-comparison-${targetName}-${new Date().toISOString().split('T')[0]}.csv`);
    link.style.visibility = 'hidden';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);

    trackEvent('export_csv_clicked', {
        location: exportLocation,
        targetSku: targetName
    }, {
        exportedRows: rows.length
    });
}

// UI Helper Functions
function showLoading() {
    loadingOverlay.classList.remove('hidden');
    compareBtn.disabled = true;
}

function hideLoading() {
    loadingOverlay.classList.add('hidden');
    compareBtn.disabled = false;
}

function showError(message) {
    errorMessage.textContent = message;
    errorSection.classList.remove('hidden');
}

function hideError() {
    errorSection.classList.add('hidden');
}

function hideResults() {
    resultsSection.classList.add('hidden');
}

// ============================================================================
// Cross-Region Availability Check
// ============================================================================

function initRegionCheckBar() {
    const bar = document.getElementById('regionCheckBar');
    const select = document.getElementById('checkRegionSelect');
    const currentRegion = document.getElementById('location').value;

    // Use pre-captured region list (Choices.js modifies original <select> options)
    const options = ALL_REGIONS.filter(r => r.value !== currentRegion);

    select.innerHTML = '<option value="">— Select a region —</option>' +
        options.map(opt => `<option value="${opt.value}">${opt.label}</option>`).join('');

    // Show the bar
    bar.classList.remove('hidden');

    // Wire up change handler (remove old listener by replacing element)
    const newSelect = select.cloneNode(true);
    select.parentNode.replaceChild(newSelect, select);
    newSelect.addEventListener('change', handleRegionCheck);

    // Wire up clear button
    const clearBtn = document.getElementById('regionCheckClear');
    const newClear = clearBtn.cloneNode(true);
    clearBtn.parentNode.replaceChild(newClear, clearBtn);
    newClear.addEventListener('click', clearRegionCheck);
}

function clearRegionCheck() {
    // Abort in-flight request
    if (regionCheckAbortController) {
        regionCheckAbortController.abort();
        regionCheckAbortController = null;
    }
    regionAvailabilityData = null;

    // Reset UI
    const select = document.getElementById('checkRegionSelect');
    if (select) select.value = '';
    const summary = document.getElementById('regionCheckSummary');
    if (summary) summary.textContent = '';
    const clearBtn = document.getElementById('regionCheckClear');
    if (clearBtn) clearBtn.classList.add('hidden');

    // Hide availability column
    const thCol = document.getElementById('thRegionAvail');
    if (thCol) thCol.classList.add('hidden');

    // Re-render results without the column
    if (currentResults && currentResults.alternatives) {
        const filteredAlternatives = filterResults(currentResults.alternatives);
        displayAlternatives(filteredAlternatives);
    }
}

async function handleRegionCheck(e) {
    const region = e.target.value;
    if (!region) {
        clearRegionCheck();
        return;
    }

    // Abort previous in-flight request
    if (regionCheckAbortController) {
        regionCheckAbortController.abort();
    }
    regionCheckAbortController = new AbortController();
    const signal = regionCheckAbortController.signal;

    // Gather SKU names from current results (including target)
    if (!currentResults || !currentResults.alternatives) return;

    const skuNames = currentResults.alternatives.map(a => a.name);
    if (currentResults.targetSku && currentResults.targetSku.name) {
        skuNames.unshift(currentResults.targetSku.name);
    }

    // Show loading state
    const summary = document.getElementById('regionCheckSummary');
    summary.textContent = 'Checking availability...';
    const clearBtn = document.getElementById('regionCheckClear');
    clearBtn.classList.remove('hidden');

    // Show the column header with region name
    const thCol = document.getElementById('thRegionAvail');
    const regionLabel = e.target.options[e.target.selectedIndex].textContent;
    thCol.textContent = regionLabel + '?';
    thCol.classList.remove('hidden');

    // Show loading cells in existing rows
    document.querySelectorAll('.region-avail-cell').forEach(cell => {
        cell.textContent = '…';
        cell.className = 'region-avail-cell avail-loading';
    });

    try {
        // Set a 30-second timeout for the region check
        const timeoutId = setTimeout(() => regionCheckAbortController.abort(), 30000);

        const response = await fetch(`${API_BASE_URL}/check_region_availability`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ skuNames, region }),
            signal
        });

        clearTimeout(timeoutId);

        if (!response.ok) {
            const errData = await response.json().catch(() => ({}));
            throw new Error(errData.error || `HTTP ${response.status}`);
        }

        const data = await response.json();
        regionAvailabilityData = data;

        // Update summary
        summary.textContent = `${data.availableCount} of ${data.totalChecked} available in ${regionLabel}`;

        // Re-render alternatives to include availability column
        const filteredAlternatives = filterResults(currentResults.alternatives);
        displayAlternatives(filteredAlternatives);

        trackEvent('region_check_completed', {
            sourceRegion: document.getElementById('location').value,
            checkRegion: region
        }, {
            availableCount: data.availableCount,
            totalChecked: data.totalChecked
        });

    } catch (err) {
        if (err.name === 'AbortError') {
            // Could be user-initiated or timeout
            if (summary.textContent === 'Checking availability...') {
                summary.textContent = 'Timed out — try again';
            }
            return;
        }
        console.error('Region availability check failed:', err);
        summary.textContent = 'Check failed — try again';
        regionAvailabilityData = null;
    }
}

function renderRegionAvailCell(skuName) {
    if (!regionAvailabilityData) return '';
    const avail = regionAvailabilityData.availability[skuName];
    if (avail === true) {
        return '<div class="mini-spec"><div class="mini-spec-val avail-yes">✅</div><div class="mini-spec-lbl">Region</div></div>';
    } else if (avail === false) {
        return '<div class="mini-spec"><div class="mini-spec-val avail-no">❌</div><div class="mini-spec-lbl">Region</div></div>';
    }
    return '<div class="mini-spec"><div class="mini-spec-val">—</div><div class="mini-spec-lbl">Region</div></div>';
}
