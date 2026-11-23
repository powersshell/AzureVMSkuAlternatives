// Offline fixture executes real rendering, filter, refresh and async identity helpers.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync(path.join(__dirname, '..', 'web-app', 'src', 'app.js'), 'utf8');
const html = fs.readFileSync(path.join(__dirname, '..', 'web-app', 'src', 'index.html'), 'utf8');
const elements = new Map();
function element() {
    const classes = new Set();
    return {
        value: '', checked: true, open: false, textContent: '', innerHTML: '', children: [], dataset: {}, style: {},
        classList: {
            add: (...values) => values.forEach(v => classes.add(v)),
            remove: v => classes.delete(v), contains: v => classes.has(v),
            toggle: (v, on) => on ? classes.add(v) : classes.delete(v),
        },
        appendChild(child) { this.children.push(child); },
        setAttribute(k, v) { this[k] = v; }, removeAttribute() {}, addEventListener() {},
        querySelector() { return this.content ||= element(); },
        querySelectorAll() { return []; }
    };
}
const node = id => {
    if (!elements.has(id)) elements.set(id, element());
    return elements.get(id);
};
let liveRow = null, targetRenders = 0, regionRequest;
const context = vm.createContext({
    document: {
        getElementById: node, createElement: element,
        querySelector: () => liveRow,
    },
    renderedAlternatives: {}, currentResults: null, resultsRenderVersion: 0,
    expandAllAbortController: null, expandedDetailsCache: new Map(), detailsInFlight: new Map(),
    resultsTableBody: node('qualified'), noResults: node('noResults'), expandAllBtn: null,
    resultsSection: node('resultsSection'), currentPricingOS: 'linux', currentPricingModel: 'payg',
    regionAvailabilityData: null, currentMode: 'compare', MAX_RESULTS: 50, STRONG_MATCH: 85,
    API_BASE_URL: 'https://fixture.invalid/api', window: {}, console,
    AbortController, setTimeout, clearTimeout, regionCheckAbortController: null,
    PRICE_FRESHNESS_HINT: '', formatFreshness: value => value,
    trackEvent() {}, renderMigrationEffortPanel() {}, hideGettingStarted() {}, initRegionCheckBar() {},
    displayTargetSku() { targetRenders++; },
    getMonthlyPrice: pricing => pricing?.[context.currentPricingModel === 'payg' ? 'monthlyPrice' : 'ri1YearMonthly'],
    formatHourlyPriceSafe: () => 'price', formatMonthlyPriceSafe: () => 'price',
    renderRetirementBadge: () => '', renderGrowthRestrictionBadge: () => '',
    renderMigrationBadges: () => 'migration warning', renderZonesChip: () => '',
    renderRegionAvailCell: name => context.regionAvailabilityData?.[name] ? 'available' : '',
    renderMigrationAssessment: () => 'migration guidance', renderScoreExplanation: () => '',
    prefetchTopDetails() {}, loadSparklines() {}, renderDetailedComparison: details => details.text,
    updatePricingHeaders() {}, syncGridPricingButtons() {}, refreshOpenRegionPricing() {}, renderGrid() {},
    updateDiscountHint() {},
    showLoading() {}, hideLoading() {}, hideError() {}, hideResults() {}, clearRegionCheck() {},
    getPriorityWeight: () => 1,
    showError(message) { context.error = message; },
});
function load(name) {
    const match = source.match(new RegExp(`^(?:async )?function ${name}\\b[\\s\\S]*?^}`, 'm'));
    assert.ok(match, name);
    vm.runInContext(match[0], context);
}
for (const name of [
    'escapeHtml', 'comparisonCandidates', 'capacityEmptyMessage', 'renderCapacityAssessment',
    'filterResultsByVendor', 'filterResultsByGeneration', 'filterResults', 'populateGenFilter',
    'resetExpansionState', 'updateExpandAllButton', 'updateResultsCaption',
    'renderAlternativeGroup', 'displayAlternatives', 'displayResults', 'getCardData',
    'renderDetailsIfLive', 'renderErrorIfLive', 'getDetails', 'handleCompare', 'setPricingOS',
    'refreshComparisonDiscount', 'handleRegionCheck', 'clearRegionCheck', 'renderRegionAvailCell',
    'comparisonDetailsKey',
]) load(name);
node('hideRetiringFilter').checked = false;
node('hideGrowthRestrictedFilter').checked = false;
node('location').value = 'eastus2';
node('skuName').value = 'Source';
node('currencyCode').value = 'USD';
const fit = (name, price, gen = 'v5') => ({
    name, vCPUs: 16, memoryGB: 128, cpuVendor: 'Intel', cpuGeneration: gen,
    recommendationScore: 90, pricing: { monthlyPrice: price, ri1YearMonthly: 200 - price },
    capacityAssessment: { state: 'meets_minimums', reasons: [] },
});
const below = name => ({
    ...fit(name, 1), memoryGB: 64,
    capacityAssessment: { state: 'below_minimums', reasons: [
        { code: 'below_minimum', resource: 'memoryGB', required: 112, actual: 64, deficit: 48, deficitPercent: 48 / 112 * 100 },
        { code: 'unknown', resource: 'vCPUs' },
    ] },
});
function result() {
    return {
        targetSku: { name: 'Source' }, alternatives: [fit('FitA', 100), fit('FitB', 50)],
        downsizingAlternatives: [below('Small')], totalMatches: 101, totalDownsizingMatches: 75,
        capacityRequirements: { schemaVersion: 1, state: 'assessed', vCPUs: 16, memoryGB: 112 },
        capacitySummary: {
            meets_minimums: { assessedCandidates: 101 },
            below_minimums: { assessedCandidates: 75 }, unassessed: { assessedCandidates: 2 },
        },
    };
}
function render(data) {
    context.currentResults = data;
    node('qualified').children = [];
    node('downsizingTableBody').children = [];
    context.displayResults(data);
}
render(result());
assert.equal(node('downsizingSection').open, false);
assert.match(html, /<details id="downsizingSection"[^>]*>/);
assert.doesNotMatch(html.match(/<details id="downsizingSection"[^>]*>/)[0], /\bopen\b/);
assert.equal(targetRenders, 1);
assert.equal(context.getCardData('qualified-0').altSku.name, 'FitA');
assert.equal(context.getCardData('downsizing-0').altSku.name, 'Small');
assert.notEqual(context.comparisonDetailsKey('Source', 'Small', 'eastus2'),
    context.comparisonDetailsKey('Source', 'Small', 'westeurope'));
const usdKey = context.comparisonDetailsKey('Source', 'Small', 'eastus2');
node('currencyCode').value = 'EUR';
assert.notEqual(context.comparisonDetailsKey('Source', 'Small', 'eastus2'), usdKey);
node('currencyCode').value = 'USD';
assert.match(node('qualified').children[0].innerHTML, /Meets source CPU\/RAM/);
assert.match(node('downsizingTableBody').children[0].innerHTML, /48 GB RAM below minimum/);
assert.match(node('downsizingTableBody').children[0].innerHTML, /effective vCPUs: unknown/);
assert.match(node('downsizingTableBody').children[0].innerHTML, /migration warning|migration guidance/);
assert.match(node('resultsCaption').textContent, /2.*101/);
assert.match(node('downsizingCaption').textContent, /1.*75/);
assert.match(node('capacityUnknowns').textContent, /2 candidates/);
node('comparisonSort').value = 'price-asc';
render(result());
assert.equal(context.getCardData('qualified-0').altSku.name, 'FitB');
assert.equal(context.getCardData('downsizing-0').altSku.name, 'Small');
const twoTradeoffs = result();
twoTradeoffs.downsizingAlternatives.push({
    ...below('Smaller'), pricing: { monthlyPrice: 0, ri1YearMonthly: 199 },
});
render(twoTradeoffs);
assert.equal(context.getCardData('qualified-0').altSku.name, 'FitB');
assert.equal(context.getCardData('downsizing-0').altSku.name, 'Smaller');
assert.equal(context.getCardData('downsizing-1').altSku.name, 'Small');
context.currentPricingModel = 'ri1year';
render(result());
assert.equal(context.getCardData('qualified-0').altSku.name, 'FitA');
context.currentPricingModel = 'payg';
context.setPricingOS('windows');
assert.equal(context.getCardData('downsizing-0').altSku.name, 'Small');
context.refreshComparisonDiscount();
assert.equal(context.getCardData('qualified-0').altSku.name, 'FitB');
assert.equal(context.getCardData('downsizing-0').altSku.name, 'Small');
node('resultFilterIntel').checked = false;
render(result());
assert.equal(node('resultsCount').textContent, 0);
assert.match(node('noResultsHint').textContent, /local display filtering/);
assert.match(node('downsizingCaption').textContent, /0.*75/);
node('resultFilterIntel').checked = true;
const empty = result();
empty.alternatives = []; empty.totalMatches = 0;
empty.downsizingAlternatives[0].cpuGeneration = 'DownOnlyGen';
empty.capacitySummary.meets_minimums.assessedCandidates = 0;
render(empty);
assert.equal(targetRenders > 3, true);
assert.equal(node('downsizingSection').classList.contains('hidden'), false);
assert.equal(node('exportBtn').disabled, true);
assert.match(node('genFilterOptions').innerHTML, /DownOnlyGen/);
assert.match(node('noResultsHint').textContent, /No assessed candidates meet/);
empty.capacitySummary.meets_minimums.assessedCandidates = 1;
assert.match(context.capacityEmptyMessage(empty), /similarity floor/);
empty.capacityRequirements.state = 'unassessed';
render(empty);
assert.match(node('capacityMinimums').textContent, /source metadata is missing or invalid/);
const noPool = result();
noPool.totalMatches = 0;
Object.values(noPool.capacitySummary).forEach(group => group.assessedCandidates = 0);
assert.match(context.capacityEmptyMessage(noPool), /architecture\/GPU\/NVMe/);
render(result());
const version = context.resultsRenderVersion;
liveRow = element(); liveRow.dataset.skuName = 'Small';
context.renderDetailsIfLive('downsizing-0', below('Small'), {}, { text: 'down detail' }, version);
assert.equal(liveRow.content.innerHTML, 'down detail');
context.renderDetailsIfLive('qualified-0', fit('FitA', 100), {}, { text: 'wrong group' }, version);
assert.equal(liveRow.content.innerHTML, 'down detail');
context.resetExpansionState();
context.renderDetailsIfLive('downsizing-0', below('Small'), {}, { text: 'stale' }, version);
assert.equal(liveRow.content.innerHTML, 'down detail');

async function requests() {
    let detailsCalls = 0, completeDetails;
    context.fetchComparisonDetails = () => {
        detailsCalls++;
        return new Promise(resolve => { completeDetails = resolve; });
    };
    const key = context.comparisonDetailsKey('Source', 'Small', 'eastus2');
    const first = context.getDetails(key, 'Source', 'Small', 'eastus2');
    assert.equal(context.getDetails(key, 'Source', 'Small', 'eastus2'), first);
    assert.equal(detailsCalls, 1);
    completeDetails({ text: 'cached downsizing details' });
    await first;
    assert.equal((await context.getDetails(key, 'Source', 'Small', 'eastus2')).text, 'cached downsizing details');
    assert.equal(detailsCalls, 1);
    context.fetch = async (url, options) => {
        regionRequest = JSON.parse(options.body);
        assert.match(url, /check_region_availability$/);
        return { ok: true, json: async () => ({
            region: 'westus', availability: { FitA: true, FitB: false, Small: true },
            availableCount: 2, totalChecked: 4,
        }) };
    };
    await context.handleRegionCheck({ target: {
        value: 'westus', options: [{ textContent: 'West US' }], selectedIndex: 0,
    } });
    assert.deepEqual(Array.from(regionRequest.skuNames), ['Source', 'FitA', 'FitB', 'Small']);
    assert.match(node('qualified').children.at(-2).innerHTML, /avail-yes/);
    assert.match(node('downsizingTableBody').children.at(-2).innerHTML, /avail-yes/);
    context.clearRegionCheck();
    assert.equal(context.regionAvailabilityData, null);
    context.fetch = async (url, options) => {
        const request = JSON.parse(options.body);
        assert.equal(request.includeDownsizingOptions, true);
        return { ok: true, json: async () => ({ alternatives: [below('Legacy')] }) };
    };
    await context.handleCompare();
    assert.match(context.error, /protection is unavailable/);
    assert.equal(context.currentResults, null);
    context.fetch = async () => ({ ok: true, json: async () => result() });
    await context.handleCompare();
    assert.equal(context.currentResults.alternatives[0].name, 'FitA');
    assert.equal(node('downsizingSection').open, false);
    assert.equal(context.currentResults.downsizingAlternatives[0].name, 'Small');
    console.log('PASS: protected groups, source retention, no-match distinctions, independent sort/filter/counts, refresh, card identity/stale guards, qualified export controls and old-API rejection.');
}
requests().catch(error => { console.error(error); process.exitCode = 1; });
