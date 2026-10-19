// Dependency-free tests run the production Browse contract and pricing helpers.
// Run: node scripts\test_browse_filters_ui.js
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '..');
const app = fs.readFileSync(path.join(root, 'web-app', 'src', 'app.js'), 'utf8');
const browse = fs.readFileSync(path.join(root, 'web-app', 'src', 'browse-filters.js'), 'utf8');
const nodes = new Map();
function node(id) {
    if (!nodes.has(id)) nodes.set(id, {
        value: '', checked: false, selectedOptions: [], options: [], innerHTML: '', textContent: '',
        validity: { badInput: false }, attributes: {}, disabled: false,
        setAttribute(key, value) { this.attributes[key] = value; },
        getAttribute(key) { return this.attributes[key] || ''; },
        querySelectorAll: () => [], addEventListener() {}, focus() {},
        classList: { add() {}, remove() {}, toggle() {} }
    });
    return nodes.get(id);
}
const storage = new Map();
let replaced = '', warning = '';
const context = vm.createContext({
    document: { getElementById: node, querySelectorAll: () => [] },
    window: { location: { href: 'https://example.test/?unrelated=keep' },
        history: { replaceState(a, b, url) { replaced = String(url); } } },
    localStorage: { getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value) },
    console: { warn: message => { warning = message; } }, URL,
    ALL_REGIONS: [{ value: 'eastus' }, { value: 'westus' }],
    gridSelectedFamilies: new Set(), gridRows: [], gridPage: 3, gridSort: { col: 'vCPUs', dir: 'asc' },
    gridCurrencyLoaded: 'USD', gridRegionLoaded: 'eastus',
    currentMode: 'browse', currentPricingOS: 'linux', currentPricingModel: 'payg',
    renderGridFacets() {}, renderGrid() {}, showError: message => { throw Error(message); },
    locationChoices: { setChoiceByValue: value => { node('location').value = value; } },
    updateOnboardingState() {}, loadSkusForRegion() {}, switchMode: mode => { context.currentMode = mode; },
    setPricingOS: os => { context.currentPricingOS = os; },
    setPricingModel: model => { context.currentPricingModel = model; }
});
vm.runInContext(browse, context);
for (const name of ['isSavingsPlan', 'getDiscountMultiplier', 'getHourlyPrice', 'getMonthlyPrice',
    'gridRowPricing', 'gridPriceValue', 'getGridFilters', 'applyGridFilters', 'sortGridRows',
    'buildGridExportModel', 'savingsPlanExportValues', 'resetGridFilters', 'escapeHtml']) {
    const match = app.match(new RegExp(`^function ${name}\\b[\\s\\S]*?^}`, 'm'));
    assert.ok(match, `Missing production helper ${name}`);
    vm.runInContext(match[0], context);
}
for (const pattern of [/^const SAVINGS_PLAN_ASSUMPTIONS = .*;$/m, /^const SAVINGS_PLAN_EXPORT_COLUMNS = \[[\s\S]*?^\];/m]) {
    vm.runInContext(app.match(pattern)[0], context);
}
const fields = vm.runInContext('BROWSE_FIELDS', context);
for (const field of fields) {
    const id = context.browseId(field);
    if (field.type === 'range') {
        node(id + 'Min').attributes['aria-label'] = field.label + ' minimum';
        node(id + 'Max').attributes['aria-label'] = field.label + ' maximum';
    } else node(id).attributes['aria-label'] = field.label;
}
node('currencyCode').value = 'USD';
node('currencyCode').options = [{ value: 'USD' }, { value: 'EUR' }];
node('location').value = 'eastus';
node('discountPct').value = '0';
context.writeBrowseFilters(context.browseEmptyFilters());

const row = {
    name: 'Standard_D4s_v5', family: 'Dsv5', vCPUs: 4, memoryGB: 16,
    hourlyLinux: 1, monthlyLinux: 730, hourlyWindows: 2, monthlyWindows: 1460,
    sp1YearHourlyLinux: 0.6, sp1YearMonthlyLinux: 438,
    spotHourlyLinux: 0.2, spotMonthlyLinux: 146,
    browseCapabilities: { schemaVersion: 1, vCPUs: 4, memoryGB: 16, memoryPerVcpu: 4,
        architecture: 'x64', premiumIO: false, maxDataDisks: 0, gpuCount: 0,
        hyperVGenerations: ['V1', 'V2'], cpuVendor: 'Intel', availabilityZones: ['1', '2'] }
};
const match = filters => context.browseMatch(row, { ...context.browseEmptyFilters(), ...filters });
assert.equal(match({}).matches, true, 'inactive controls preserve rows');
assert.equal(match({ values: { vCPUs: { min: 4, max: 4 }, memoryGB: { min: 16, max: 16 } } }).matches, true);
assert.equal(match({ values: { vCPUs: { min: 5 } } }).matches, false);
assert.equal(match({ values: { premiumIO: 'false', maxDataDisks: { max: 0 } } }).matches, true, 'false and zero are known');
assert.equal(match({ values: { premiumIO: 'true' }, includeUnknown: true }).matches, false, 'known mismatch is never bypassed');
assert.equal(match({ values: { rdmaEnabled: 'true' } }).matches, false);
assert.equal(match({ values: { rdmaEnabled: 'true' }, includeUnknown: true }).unverified, true);
assert.equal(match({ values: { hyperVGenerations: ['V2', 'V3'], availabilityZones: ['2'] } }).matches, true, 'OR within / AND across');
assert.equal(match({ search: 'missing', includeUnknown: true }).matches, false);
assert.equal(match({ vendors: ['AMD'], includeUnknown: true }).matches, false);
assert.equal(match({ gpuOnly: true }).matches, false);

const legacy = { ...row, browseCapabilities: undefined, nvme: false, gpuCount: 0 };
assert.equal(context.browseCapability(legacy, 'nvme'), null);
assert.equal(context.browseCapability(legacy, 'vCPUs'), 4);
assert.equal(context.browseCapability(legacy, 'memoryPerVcpu'), 4);
const fractional = { ...row, browseCapabilities: { schemaVersion: 1, gpuCount: 1, gpuAllocation: 0.125, gpuMemoryGB: 2 } };
assert.equal(context.browseMatch(fractional, { ...context.browseEmptyFilters(), gpuOnly: true,
    values: { gpuAllocation: { min: 0.125, max: 0.125 } } }).matches, true);

node('discountPct').value = '50';
assert.equal(context.browseDisplayedPrice(row, 'monthly'), 365);
assert.equal(match({ values: { price: { max: 365 } } }).matches, true);
context.currentPricingOS = 'windows';
assert.equal(context.browseDisplayedPrice(row, 'monthly'), 730);
context.currentPricingOS = 'linux';
context.currentPricingModel = 'sp1year';
assert.equal(context.browseDisplayedPrice(row, 'monthly'), 438, 'SP ignores Extra Discount');
context.currentPricingModel = 'spot';
assert.equal(context.browseDisplayedPrice(row, 'hourly'), 0.1);
assert.equal(context.browseDisplayedPrice({ ...row, spotHourlyLinux: 0 }, 'hourly'), 0);
assert.equal(context.browseMatch({ ...row, spotMonthlyLinux: null }, {
    ...context.browseEmptyFilters(), requirePrice: true, includeUnknown: true
}).matches, false);
context.currentPricingModel = 'payg';

node('gridVcpuMin').value = '8';
node('gridVcpuMax').value = '4';
context.readBrowseFilters();
assert.ok(vm.runInContext('browseValidationErrors.length', context) > 0);
assert.equal(node('gridVcpuMin').attributes['aria-invalid'], 'true');
context.gridRows = [row];
assert.equal(context.applyGridFilters([row]).length, 0, 'invalid drafts are not applied');
context.writeBrowseFilters(context.browseEmptyFilters());
node('gridRamMin').validity.badInput = true;
context.readBrowseFilters();
assert.ok(vm.runInContext('browseValidationErrors.length', context) > 0);
node('gridRamMin').validity.badInput = false;
context.readBrowseFilters();
assert.equal(vm.runInContext('browseValidationErrors.length', context), 0);

const state = context.captureBrowseState(context.browseEmptyFilters());
const decoded = context.normalizeBrowseState(JSON.parse(JSON.stringify(state)));
assert.equal(decoded.region, 'eastus');
assert.equal(decoded.filters.priceUnit, 'monthly');
for (const invalid of [
    { ...state, v: 99 }, { ...state, discount: Infinity }, { ...state, region: 'bad' },
    { ...state, filters: { ...state.filters, vendors: ['bad'] } },
    { ...state, filters: { ...state.filters, values: { maxNics: { min: 2, max: 1 } } } },
    { ...state, filters: { ...state.filters, values: { maxNics: { min: -1 } } } },
    { ...state, filters: { ...state.filters, search: 'a'.repeat(121) } }
]) assert.throws(() => context.normalizeBrowseState(invalid));
assert.equal(context.normalizeBrowseState({ ...state, filters: {
    ...state.filters, values: { unknownField: '<script>' } } }).filters.values.unknownField, undefined);
vm.runInContext('browseReady = true', context);
context.persistBrowseState(state.filters);
assert.equal(new URL(replaced).searchParams.get('unrelated'), 'keep');
assert.equal(storage.size, 1);
node('gridVcpuMin').value = '8';
node('gridVcpuMax').value = '1';
context.readBrowseFilters();
const validUrl = replaced;
context.persistBrowseState(context.browseEmptyFilters());
assert.equal(replaced, validUrl, 'invalid drafts never update shared state');
context.resetGridFilters();
context.readBrowseFilters();
assert.equal(context.gridPage, 1);
assert.equal(node('gridVcpuMin').value, '');
assert.equal(node('location').value, 'eastus', 'reset retains context');
context.persistBrowseState(context.readBrowseFilters());
assert.equal(JSON.parse(new URL(replaced).searchParams.get('browse')).filters.search, '');

context.gridRows = [row, { ...row, name: 'Other', browseCapabilities: { ...row.browseCapabilities, vCPUs: 8 } }];
node('gridVcpuMax').value = '4';
const exported = context.buildGridExportModel();
assert.equal(exported.rowCount, 1);
assert.equal(exported.dataRows[0][0], row.name);
assert.equal(exported.headers.length, exported.dataRows[0].length);
assert.ok(exported.headers.includes('Verified: Maximum data disks'));
assert.equal(exported.dataRows[0][exported.headers.indexOf('Verified: Maximum data disks')], '0');
assert.equal(exported.dataRows[0][exported.headers.indexOf('Verified: RDMA')], 'N/A');
assert.ok(exported.meta.some(([key, value]) => key === 'Active Browse filters' && value.includes('Active vCPUs')));
const stored = { ...state, filters: { ...state.filters, search: 'saved search' } };
storage.set('vmsku_browse_v1', JSON.stringify(stored));
context.window.location.href = 'https://example.test/';
context.initializeBrowseFilters();
assert.equal(node('gridSearch').value, 'saved search', 'device state restores without a shared URL');
context.window.location.href = 'https://example.test/?browse=' + encodeURIComponent(JSON.stringify(state));
context.initializeBrowseFilters();
assert.equal(node('gridSearch').value, '', 'explicit empty URL wins over saved filters');
context.window.location.href = 'https://example.test/?browse=bad';
context.initializeBrowseFilters();
assert.equal(node('gridSearch').value, '', 'invalid URL never falls back to saved filters');
assert.ok(node('browseStateNotice').textContent.includes('could not be restored'));
context.window.location.href = 'https://example.test/';
context.localStorage.getItem = () => { throw Error('blocked'); };
context.initializeBrowseFilters();
assert.ok(node('browseStateNotice').textContent.includes('could not be restored'));
context.localStorage.setItem = () => { throw Error('blocked'); };
context.readBrowseFilters();
context.persistBrowseState(context.readBrowseFilters());
assert.ok(warning.includes('save Browse'));
assert.ok(node('browseStateNotice').textContent.includes('unavailable'));
(async () => {
    let copied;
    context.navigator = { clipboard: { writeText: async text => { copied = text; } } };
    await context.copyBrowseLink();
    assert.equal(node('browseStateNotice').textContent, 'Browse link copied.');
    assert.equal(JSON.parse(new URL(copied).searchParams.get('browse')).region, 'eastus');
    context.navigator.clipboard.writeText = async () => { throw Error('permission denied'); };
    await context.copyBrowseLink();
    assert.ok(node('browseStateNotice').textContent.includes('clipboard permissions'));
    console.log('PASS: Browse matching, nullable capabilities, inclusive bounds, fractional GPUs, pricing, validation, state safety, reset, persistence, clipboard feedback and export parity.');
})().catch(error => { console.error(error); process.exitCode = 1; });
