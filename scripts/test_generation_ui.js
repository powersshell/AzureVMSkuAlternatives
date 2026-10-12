// Offline fixtures use the production rendering/export helpers without Azure or browser dependencies.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'web-app', 'src', 'app.js'), 'utf8');
const html = fs.readFileSync(path.join(root, 'web-app', 'src', 'index.html'), 'utf8');
const panel = { innerHTML: '', classList: { add() {}, remove() {} } };
const context = vm.createContext({
    document: { getElementById: id => id === 'migrationEffortPanel' ? panel : { value: 'eastus' } },
    currentResults: null, regionAvailabilityData: null, currentPricingOS: 'linux', currentPricingModel: 'payg',
    SAVINGS_PLAN_EXPORT_COLUMNS: [], SAVINGS_PLAN_ASSUMPTIONS: 'Fixture pricing assumptions',
    getDiscountMultiplier: () => 1, savingsPlanExportValues: () => [],
    formatCapabilityValue: () => 'N/A',
});
for (const name of ['escapeHtml', 'getMigrationAssessment', 'renderMigrationAssessment', 'renderMigrationEffortPanel', 'buildComparisonExportModel']) {
    const match = source.match(new RegExp(`^function ${name}\\b[\\s\\S]*?^}`, 'm'));
    assert.ok(match, name);
    vm.runInContext(match[0], context);
}
vm.runInContext(source.match(/^const MIGRATION_JOURNEY = \[[\s\S]*?^\];/m)[0], context);
for (const id of ['sourceGen', 'targetGen']) {
    assert.equal((html.match(new RegExp(`id="${id}"`, 'g')) || []).length, 1);
    assert.ok(html.indexOf(`id="${id}"`) < html.indexOf('<!-- Advanced Options -->'));
}
assert.match(html, /A ranking preference, not a strict filter/);
const assessment = {
    sourceGeneration: 1, preferredTargetGeneration: 2, supportedCandidateGenerations: [1],
    destinationGeneration: 1, preferenceSupport: 'unsupported', pathStatus: 'same_generation',
    label: 'Gen1 -> Gen1: same-generation path available', effort: 'Resize assessment',
    reasons: ['Preferred Gen2 unsupported; alternate path only.', '<script>unsafe</script>'],
    checklist: ['Verify actual VM readiness.'], guidanceUrls: [
        'https://learn.microsoft.com/en-us/azure/virtual-machines/trusted-launch-existing-vm-gen-1',
        'javascript:alert(1)'
    ],
};
const alt = { name: 'Standard_D2s_v5', migrationAssessment: assessment };
const rendered = context.renderMigrationAssessment(alt);
assert.match(rendered, /Preferred Gen2 unsupported/);
assert.match(rendered, /alternate, not the requested target/);
assert.match(rendered, /Planning effort: Resize assessment/);
assert.match(rendered, /<details><summary>Migration prerequisites/);
assert.match(rendered, /&lt;script&gt;/);
assert.doesNotMatch(rendered, /<script>|javascript:|Very low/);
assert.match(rendered, /pricing selection does not establish/);
assert.match(context.renderMigrationAssessment({}), /Generation path unassessed/);
assert.match(context.renderMigrationAssessment({}), /Cannot assess effort/);
context.renderMigrationEffortPanel({
    migrationEffort: { level: 'Unassessed', detail: 'Select Source Gen', guidanceUrl: assessment.guidanceUrls[0] },
    generationContext: { warning: 'Dual-generation source.', sourceGeneration: null },
});
assert.match(panel.innerHTML, /Source generation required/);
assert.doesNotMatch(panel.innerHTML, /Open Advanced Options|Very low/);
context.renderMigrationEffortPanel({
    migrationEffort: { level: 'Very low', detail: 'Recent hardware; most moves are a resize.', guidanceUrl: assessment.guidanceUrls[0] },
    generationContext: { sourceGeneration: 1 },
});
assert.doesNotMatch(panel.innerHTML, /Very low|most moves are a resize/);
assert.match(panel.innerHTML, /Updated API metadata is required/);
context.currentResults = {
    targetSku: { name: 'Standard_D2s_v3' }, alternatives: [alt],
    generationContext: { sourceGeneration: 1 }, searchParameters: { targetGen: 'gen2', location: 'eastus' },
};
const exported = context.buildComparisonExportModel();
const cell = name => exported.summaryRows[0][exported.summaryHeaders.indexOf(name)];
assert.equal(cell('Source Hyper-V Generation'), 'Gen1');
assert.equal(cell('Target Generation Preference'), 'gen2');
assert.equal(cell('Preferred Generation Support'), 'unsupported');
assert.equal(cell('Assessed Destination Generation'), 'Gen1');
assert.equal(cell('Migration Path'), assessment.label);
assert.equal(cell('Planning Effort'), assessment.effort);
assert.equal(exported.combinedRows[0].length, exported.headers.length);
assert.ok(exported.meta.some(([key, value]) => key === 'Source Hyper-V Generation' && value === 'Gen1'));
context.currentResults.alternatives = [{ name: 'Unknown' }];
const unknown = context.buildComparisonExportModel();
assert.equal(unknown.summaryRows[0][unknown.summaryHeaders.indexOf('Planning Effort')], 'Cannot assess effort');
assert.match(source, /event\.target\.closest\('\.migration-assessment'\)/);
console.log('PASS: visible generation selectors, candidate/global callouts, prerequisites, escaping, unknowns and comparison exports.');
