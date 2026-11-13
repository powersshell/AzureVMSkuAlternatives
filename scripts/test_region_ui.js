// Execute production catalog-loading helpers, including failure/retry behavior.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const app = fs.readFileSync(path.join(__dirname, '..', 'web-app', 'src', 'app.js'), 'utf8');
const nodes = new Map();
function node(id) {
    if (!nodes.has(id)) {
        const classes = new Set();
        nodes.set(id, { value: '', textContent: '', classList: {
            add: value => classes.add(value), remove: value => classes.delete(value),
            contains: value => classes.has(value)
        } });
    }
    return nodes.get(id);
}
let options = [], disabled = true, failed = false, stale = false;
const context = vm.createContext({
    document: { getElementById: node }, API_BASE_URL: 'https://example.invalid/api',
    ALL_REGIONS: [], regionCatalogNotice: '', console: { error() {} },
    locationChoices: {
        disable() { disabled = true; }, enable() { disabled = false; }, clearStore() { options = []; },
        setChoices(rows) { options = rows; }
    },
    fetch: async () => {
        if (failed) throw Error('Simulated outage');
        return { ok: true, json: async () => ({
            cloud: 'AzureCloud', stale, regions: [
                { name: 'belgiumcentral', displayName: 'Belgium Central', lifecycle: 'active', dataStatus: 'pending' },
                { name: 'brandnewwest', displayName: 'Brand New West', lifecycle: 'active', dataStatus: 'ready',
                    pricedSkuCount: 0, restricted: true },
                { name: 'futureeast', displayName: 'Future East', lifecycle: 'announced', dataStatus: 'announced' }
            ]
        }) };
    }
});
for (const name of ['loadRegionCatalog', 'updateRegionHint']) {
    vm.runInContext(app.match(new RegExp(`^(?:async )?function ${name}\\b[\\s\\S]*?^}`, 'm'))[0], context);
}
(async () => {
    assert.equal(await context.loadRegionCatalog(), true);
    assert.equal(disabled, false);
    assert.equal(options.length, 4);
    assert.equal(options.find(row => row.value === 'futureeast').disabled, true);
    assert.ok(context.ALL_REGIONS.some(row => row.value === 'brandnewwest'), 'new region needs no UI code change');
    node('location').value = 'belgiumcentral';
    context.updateRegionHint();
    assert.match(node('regionHint').textContent, /pending/);
    node('location').value = 'brandnewwest';
    context.updateRegionHint();
    assert.match(node('regionHint').textContent, /restricted.*pricing is unavailable/);
    failed = true;
    assert.equal(await context.loadRegionCatalog(), false);
    assert.equal(disabled, true);
    assert.equal(node('retryRegions').classList.contains('hidden'), false);
    assert.match(node('regionHint').textContent, /Could not load regions/);
    failed = false;
    stale = true;
    assert.equal(await context.loadRegionCatalog(), true);
    assert.match(node('regionHint').textContent, /last successful catalog/);
    console.log('Public region UI fixtures passed.');
})().catch(error => { console.error(error); process.exitCode = 1; });
