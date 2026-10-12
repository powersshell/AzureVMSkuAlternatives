// Dependency-free fixtures execute the production helpers in a minimal DOM harness.
// Run from any directory: node scripts\test_savings_plan_ui.js
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');

const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'web-app', 'src', 'app.js'), 'utf8');
const html = fs.readFileSync(path.join(root, 'web-app', 'src', 'index.html'), 'utf8');
const elements = new Map();
function node(id) {
    if (!elements.has(id)) {
        const classes = new Set();
        elements.set(id, {
            value: '', checked: false, textContent: '', innerHTML: '', style: {}, disabled: false,
            classList: {
                add: value => classes.add(value),
                remove: value => classes.delete(value),
                contains: value => classes.has(value),
                toggle: (value, on) => on ? classes.add(value) : classes.delete(value)
            },
            replaceChildren(text) { this.textContent = text; },
            setAttribute(key, value) { this[key] = value; },
            click() {}
        });
    }
    return elements.get(id);
}

const blobs = [], books = [];
const context = vm.createContext({
    document: {
        getElementById: node, querySelector: node,
        createElement: () => node('download'),
        body: { classList: node('body').classList, appendChild() {}, removeChild() {} }
    },
    currentPricingModel: 'payg', currentPricingOS: 'linux', currentMode: 'compare',
    currentResults: null, regionAvailabilityData: null, regionPriceSku: null,
    regionPriceRequestVersion: 0, gridRows: [], gridSort: { col: 'monthly', dir: 'asc' },
    gridCurrencyLoaded: 'USD', gridRegionLoaded: 'eastus',
    API_BASE_URL: 'https://example.invalid/api',
    trackEvent() {}, showError(message) { throw Error(message); }, renderGrid() {},
    displayAlternatives() {}, displayTargetSku() {},
    filterResultsByVendor: rows => rows, filterResultsByGeneration: rows => rows,
    applyGridFilters: rows => rows, renderEfficiency: () => 'PAYG efficiency',
    Blob: class { constructor(parts) { this.parts = parts; blobs.push(this); } },
    URL: { createObjectURL: () => 'blob:fixture' },
    XLSX: {
        utils: {
            book_new: () => ({}),
            aoa_to_sheet: data => ({ data }),
            book_append_sheet: (book, sheet, name) => { book[name] = sheet; }
        },
        writeFile: book => books.push(book)
    },
    console
});

const helperNames = [
    'isSavingsPlan', 'savingsPlanLabel', 'savingsPlanExportValues',
    'getDiscountMultiplier', 'updateDiscountHint', 'getHourlyPrice', 'getMonthlyPrice',
    'getPaygMonthlyPrice', 'renderRiSavings', 'formatHourlyPriceSafe', 'formatMonthlyPriceSafe',
    'formatCurrency', 'formatHourlyCurrency', 'gridRowPricing', 'gridPriceValue',
    'sortGridRows', 'filterResults', 'setPricingModel', 'setPricingOS', 'applySpotOsLock',
    'updatePricingHeaders', 'syncGridPricingButtons', 'refreshOpenRegionPricing',
    'renderPriceDiff', 'renderPricingSection', 'formatCapabilityValue',
    'buildComparisonExportModel', 'buildGridExportModel', 'coerceExportCell',
    'exportToCSV', 'exportToXLSX', 'exportGridCsv', 'exportGridXlsx', 'escapeCsvCell',
    'escapeHtml', 'showRegionPriceComparison', 'renderRegionPriceComparison', 'closeRegionPriceModal'
];
for (const name of helperNames) {
    const match = source.match(new RegExp(`^(?:async )?function ${name}\\b[\\s\\S]*?^}`, 'm'));
    assert.ok(match, `Production helper ${name} must exist`);
    vm.runInContext(match[0], context, { filename: `app.js:${name}` });
}
for (const pattern of [
    /^const SAVINGS_PLAN_ASSUMPTIONS = .*;$/m,
    /^const SAVINGS_PLAN_EXPORT_COLUMNS = \[[\s\S]*?^\];/m
]) {
    const match = source.match(pattern);
    assert.ok(match, `Production Savings Plan constant must match ${pattern}`);
    vm.runInContext(match[0], context);
}

node('currencyCode').value = 'USD';
node('location').value = 'eastus';
node('discountPct').value = '25';
node('regionPriceModal').classList.add('hidden');
const pricing = {
    currency: 'USD', hourlyPrice: 1, monthlyPrice: 730,
    hourlyPriceWindows: 1.5, monthlyPriceWindows: 1095,
    sp1YearHourly: 0.8, sp1YearMonthly: 584,
    sp3YearHourly: 0.6, sp3YearMonthly: 438,
    sp1YearHourlyWindows: 1.3, sp1YearMonthlyWindows: 949,
    sp3YearHourlyWindows: 1.1, sp3YearMonthlyWindows: 803
};

for (const term of ['sp1year', 'sp3year']) {
    const base = term === 'sp1year' ? 'sp1Year' : 'sp3Year';
    context.setPricingModel(term);
    assert.equal(node('discountPct').disabled, true);
    assert.equal(node('discountPct').value, '25');
    assert.match(node('discountHint').textContent, /not applied/);
    for (const os of ['linux', 'windows']) {
        context.setPricingOS(os);
        const suffix = os === 'windows' ? 'Windows' : '';
        assert.equal(context.getHourlyPrice(pricing), pricing[base + 'Hourly' + suffix]);
        assert.equal(context.getMonthlyPrice(pricing), pricing[base + 'Monthly' + suffix]);
        assert.equal(context.getDiscountMultiplier(), 1);
        const missing = {
            hourlyPrice: 99, monthlyPrice: 99,
            hourlyPriceWindows: 199, monthlyPriceWindows: 199,
            [base + 'Hourly']: os === 'linux' ? null : 10,
            [base + 'Monthly']: os === 'linux' ? null : 7300
        };
        assert.equal(context.getHourlyPrice(missing), null, `${term}/${os}: no PAYG or Linux fallback`);
        assert.equal(context.getMonthlyPrice(missing), null);
        const zero = { currency: 'USD', [base + 'Hourly' + suffix]: 0, [base + 'Monthly' + suffix]: 0 };
        assert.equal(context.getHourlyPrice(zero), 0);
        assert.equal(context.getMonthlyPrice(zero), 0);
        assert.match(context.formatHourlyPriceSafe(zero), /\$0\.0000/);
        assert.match(context.formatMonthlyPriceSafe(zero), /\$0\.00/);
        assert.equal(context.formatMonthlyPriceSafe(missing), 'N/A');
        assert.match(node('th-hourly').textContent, os === 'windows' ? /Windows estimated total/ : /Linux compute/);
        assert.match(node('#gridTable th[data-sort="monthly"]').textContent, /Savings Plan/);
        assert.ok(node(`btn-${term}`).classList.contains('active'));
        assert.ok(node(`btn-grid-${term}`).classList.contains('active'));
        if (os === 'windows') {
            assert.match(context.formatMonthlyPriceSafe(pricing), /SP est\./);
            assert.ok(Math.abs(pricing[base + 'HourlyWindows'] - pricing[base + 'Hourly'] - 0.5) < 1e-10);
        }
        const diff = {
            target: 584, alternative: 0, changed: true, delta: -584,
            percentChange: -100, isNegative: false, currency: 'USD'
        };
        const details = context.renderPricingSection({
            [base + suffix]: { hourly: diff, monthly: diff }, hourly: { alternative: 999 }
        });
        assert.match(details, /584\.00 → USD 0\.00/);
        assert.match(details, /Extra Discount % does not apply/);
        assert.match(details, /monthly = hourly × 730/);
        assert.match(details, /Unused commitment expires; excess usage is PAYG/);
        assert.match(details, /licensing included/);
        assert.doesNotMatch(details, /PAYG efficiency|999/);
        const absentDetails = context.renderPricingSection({ hourly: { alternative: 999 } });
        assert.match(absentDetails, /Hourly Price: N\/A/);
        assert.doesNotMatch(absentDetails, /999/);
    }
}

context.setPricingModel('payg');
assert.equal(node('discountPct').disabled, false);
assert.equal(node('discountPct').value, '25');
assert.equal(context.getDiscountMultiplier(), 0.75);
assert.equal(context.getMonthlyPrice(pricing), 1095);
context.setPricingModel('spot');
assert.equal(context.currentPricingOS, 'linux');
assert.equal(node('btn-windows-pricing').disabled, true);
context.setPricingModel('sp1year');
assert.equal(node('btn-windows-pricing').disabled, false);
node('discountPct').value = '100';
assert.equal(context.getDiscountMultiplier(), 1);
assert.match(context.formatMonthlyPriceSafe(pricing), /\$584\.00/);
context.setPricingModel('payg');
assert.equal(context.getDiscountMultiplier(), 0);
context.setPricingModel('ri1year');
assert.equal(context.getHourlyPrice({ ri1YearHourly: 0.4 }), 0.4);
context.setPricingModel('ri3year');
assert.equal(context.getHourlyPrice({ ri3YearHourly: 0.2 }), 0.2);
context.setPricingModel('spot');
assert.equal(context.getHourlyPrice({ spotHourly: 0.1 }), 0.1);
node('discountPct').value = '25';
context.setPricingModel('sp1year');

const rows = [
    { name: 'missing', monthlyLinux: 1 },
    {
        name: 'priced', sp1YearHourlyLinux: 0.1316736, sp1YearMonthlyLinux: 584,
        sp3YearHourlyLinux: 0.0883584, sp3YearMonthlyLinux: 438,
        sp1YearHourlyWindows: 0.6316736, sp1YearMonthlyWindows: 949,
        sp3YearHourlyWindows: 0.5883584, sp3YearMonthlyWindows: 803
    },
    {
        name: 'zero', sp1YearHourlyLinux: 0, sp1YearMonthlyLinux: 0,
        sp3YearHourlyLinux: 0, sp3YearMonthlyLinux: 0,
        sp1YearHourlyWindows: 0, sp1YearMonthlyWindows: 0,
        sp3YearHourlyWindows: 0, sp3YearMonthlyWindows: 0
    }
];
context.gridRows = rows;
for (const dir of ['asc', 'desc']) {
    context.gridSort.dir = dir;
    const expected = dir === 'asc' ? ['zero', 'priced', 'missing'] : ['priced', 'zero', 'missing'];
    assert.equal(JSON.stringify(context.sortGridRows(rows).map(row => row.name)), JSON.stringify(expected));
    node('comparisonSort').value = `price-${dir}`;
    const alternatives = rows.map(row => ({ name: row.name, pricing: context.gridRowPricing(row) }));
    assert.equal(JSON.stringify(context.filterResults(alternatives).map(row => row.name)), JSON.stringify(expected));
}
node('comparisonSort').value = 'recommendation';
const ranked = [
    { name: 'high-score', recommendationScore: 95, pricing: { ...pricing, sp1YearMonthly: 900 } },
    { name: 'lower-score', recommendationScore: 90, pricing: { ...pricing, sp1YearMonthly: 100 } }
];
for (const term of ['payg', 'sp1year', 'sp3year']) {
    context.currentPricingModel = term;
    assert.equal(JSON.stringify(context.filterResults(ranked).map(row => row.name)), '["high-score","lower-score"]');
    assert.equal(ranked[0].recommendationScore, 95);
}
assert.equal(context.filterResults(rows)[0].name, 'missing');

context.currentResults = {
    targetSku: { name: 'target' },
    alternatives: [
        {
            name: 'priced', similarityScore: 95, recommendationScore: 93,
            pricing: {
                ...pricing, sp1YearHourly: 0.1316736, sp3YearHourly: 0.0883584,
                sp1YearHourlyWindows: 0.6316736, sp3YearHourlyWindows: 0.5883584
            }
        },
        { name: 'missing', similarityScore: 90, recommendationScore: 89, pricing: {} },
        { name: 'zero', similarityScore: 85, recommendationScore: 84, pricing: context.gridRowPricing(rows[2]) }
    ],
    location: 'eastus'
};
const expectedExports = [
    ['1yr SP Hourly (Linux)', '0.1316736'],
    ['1yr SP Monthly (Linux)', '584.00'],
    ['3yr SP Hourly (Linux)', '0.0883584'],
    ['3yr SP Monthly (Linux)', '438.00'],
    ['1yr SP Hourly (Windows estimated total)', '0.6316736'],
    ['1yr SP Monthly (Windows estimated total)', '949.00'],
    ['3yr SP Hourly (Windows estimated total)', '0.5883584'],
    ['3yr SP Monthly (Windows estimated total)', '803.00']
];
for (const model of ['payg', 'ri1year', 'sp1year', 'sp3year']) {
    context.currentPricingModel = model;
    const comparison = context.buildComparisonExportModel();
    const grid = context.buildGridExportModel();
    for (const [headers, data] of [[comparison.summaryHeaders, comparison.summaryRows], [grid.headers, grid.dataRows]]) {
        assert.ok(data.every(row => row.length === headers.length));
        const priced = data.find(row => row.includes('priced'));
        const missing = data.find(row => row.includes('missing'));
        const zero = data.find(row => row.includes('zero'));
        assert.ok(zero, `${model}: export includes zero rates`);
        for (const [label, expected] of expectedExports) {
            const column = headers.indexOf(label);
            assert.notEqual(column, -1, `${model}: export column ${label}`);
            assert.equal(priced[column], expected, `${model}: SP export never discounted`);
            assert.equal(missing[column], 'N/A');
            assert.equal(Number(zero[column]), 0, `${model}: zero remains valid for ${label}`);
        }
        assert.match(priced[headers.indexOf('Savings Plan assumptions (no Extra Discount)')], /Unused commitment expires/);
        assert.equal(zero[headers.indexOf('1yr SP Monthly (Linux)')], '0.00');
    }
    context.exportToCSV();
    context.exportGridCsv();
    context.exportToXLSX();
    context.exportGridXlsx();
    assert.match(blobs.at(-1).parts.join(''), /584\.00/);
    assert.match(blobs.at(-2).parts.join(''), /Windows estimated total/);
    for (const blob of blobs.slice(-2)) {
        const csv = blob.parts.join('');
        for (const [, expected] of expectedExports.filter(([label]) => label.includes('Hourly'))) {
            assert.ok(csv.includes(expected), `${model}: CSV preserves exact hourly rate ${expected}`);
        }
    }
    for (const table of [books.at(-2).Summary.data, books.at(-1)['VM Sizes'].data]) {
        for (const [label, expected] of expectedExports) {
            assert.ok(table.slice(1).some(row => row[table[0].indexOf(label)] === Number(expected)));
        }
    }
}

for (const id of ['btn-sp1year', 'btn-sp3year', 'btn-grid-sp1year', 'btn-grid-sp3year']) {
    const control = html.match(new RegExp(`<button id="${id}"[^>]+>`));
    assert.ok(control, `UI control ${id} exists`);
    assert.match(control[0], /onclick="setPricingModel\('sp[13]year'\)"/);
}
assert.match(html, /id="comparisonSort"/);
const compareStart = source.indexOf('async function handleCompare');
const compareEnd = source.indexOf('// OS Pricing toggle', compareStart);
assert.ok(compareStart >= 0 && compareEnd > compareStart);
assert.doesNotMatch(source.slice(compareStart, compareEnd), /pricingModel|sp1year|sp3year/);

async function regionalTests() {
    context.currentResults = null;
    context.currentPricingOS = 'windows';
    context.currentPricingModel = 'sp1year';
    const requests = [];
    context.fetch = url => {
        let resolve;
        const promise = new Promise(done => { resolve = done; });
        requests.push({ url, resolve });
        return promise;
    };
    const old = context.showRegionPriceComparison('fixture');
    context.setPricingModel('sp3year');
    assert.equal(requests.length, 2);
    assert.match(requests[0].url, /os=windows&pricingModel=sp1year/);
    assert.match(requests[1].url, /pricingModel=sp3year/);
    requests[1].resolve({
        ok: true,
        json: async () => ({
            regionCount: 3, currency: 'USD', regions: [
                { region: 'eastus', location: 'East US', ...pricing },
                { region: 'zero', location: 'Zero Region', sp3YearHourlyWindows: 0, sp3YearMonthlyWindows: 0 },
                { region: 'missing', location: 'Missing Region', hourlyPrice: 999, monthlyPrice: 999 }
            ]
        })
    });
    await new Promise(resolve => setImmediate(resolve));
    const rendered = node('regionPriceBody').innerHTML;
    assert.match(rendered, /3yr Savings Plan \(Windows estimated total\)/);
    assert.match(rendered, /\$803\.00/);
    assert.match(rendered, /\$0\.00/);
    assert.match(rendered, /rp-monthly">N\/A/);
    assert.doesNotMatch(rendered, /999|NaN|Infinity/);
    requests[0].resolve({ ok: false, status: 500 });
    await old;
    assert.equal(node('regionPriceBody').innerHTML, rendered, 'Stale errors cannot replace current regional prices');
    context.setPricingOS('linux');
    assert.match(requests[2].url, /os=linux&pricingModel=sp3year/);
    context.closeRegionPriceModal();
    requests[2].resolve({ ok: true, json: async () => ({ regions: [] }) });
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(node('regionPriceBody').innerHTML.includes('Fetching'), true, 'Closed modal ignores late results');
    context.renderRegionPriceComparison({
        currency: 'USD', regionCount: 1,
        regions: [{ location: 'Missing', region: 'missing', hourlyPrice: 999, monthlyPrice: 999 }]
    }, 'Linux', 'eastus');
    assert.doesNotMatch(node('regionPriceBody').innerHTML, /999|NaN|Infinity/);
    assert.match(node('regionPriceBody').innerHTML, /N\/A/);
    const payg = context.showRegionPriceComparison('fixture');
    context.setPricingModel('payg');
    assert.match(requests[4].url, /pricingModel=payg/);
    context.closeRegionPriceModal();
    requests[3].resolve({ ok: true, json: async () => ({ regions: [] }) });
    requests[4].resolve({ ok: true, json: async () => ({ regions: [] }) });
    await payg;
    await new Promise(resolve => setImmediate(resolve));
}

regionalTests().then(() => {
    console.log('PASS: Savings Plan UI fixtures — 1/3yr × Linux/Windows estimates, N/A/zero, preserved/unapplied discount, controls/headers, Compare/Browse sorting, detailed prices, all CSV/XLSX values, PAYG default ordering/scoring isolation, regional model/OS forwarding and stale-response guards.');
}).catch(error => {
    console.error(error);
    process.exitCode = 1;
});
