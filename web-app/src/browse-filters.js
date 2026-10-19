// Browse's nullable capability contract is independent of legacy display defaults.
const BROWSE_FIELDS = [
    { key: 'vCPUs', label: 'Active vCPUs', type: 'range', common: 'gridVcpu' },
    { key: 'memoryGB', label: 'Memory (GiB)', type: 'range', common: 'gridRam' },
    { key: 'architecture', label: 'CPU architecture', group: 'Compute', type: 'multi' },
    { key: 'cpuGeneration', label: 'Processor generation', group: 'Compute', type: 'multi' },
    { key: 'cpuModels', label: 'Processor model (series may use several)', group: 'Compute', type: 'multi' },
    { key: 'memoryPerVcpu', label: 'Memory / active vCPU (GiB)', group: 'Compute', type: 'range' },
    { key: 'vCPUsPerCore', label: 'Threads per core', group: 'Compute', type: 'range' },
    { key: 'hasTempDisk', label: 'Local temporary disk', group: 'Storage', type: 'bool' },
    { key: 'nvme', label: 'Local temporary NVMe', group: 'Storage', type: 'bool' },
    { key: 'maxDataDisks', label: 'Maximum data disks', group: 'Storage', type: 'range' },
    { key: 'uncachedDiskIOPS', label: 'Uncached IOPS (VM limit)', group: 'Storage', type: 'range' },
    { key: 'uncachedDiskMBps', label: 'Uncached throughput (MB/s, VM limit)', group: 'Storage', type: 'range' },
    { key: 'premiumIO', label: 'Premium Storage', group: 'Storage', type: 'bool' },
    { key: 'ephemeralOSDisk', label: 'Ephemeral OS disk', group: 'Storage', type: 'bool' },
    { key: 'hyperVGenerations', label: 'Hyper-V boot generation', group: 'Storage', type: 'multi' },
    { key: 'diskControllerTypes', label: 'Remote disk controller', group: 'Storage', type: 'multi' },
    { key: 'maxNics', label: 'Maximum NICs', group: 'Networking', type: 'range' },
    { key: 'networkBandwidthMbps', label: 'Aggregate network bandwidth (Mbps)', group: 'Networking', type: 'range' },
    { key: 'acceleratedNetworking', label: 'Accelerated Networking', group: 'Networking', type: 'bool' },
    { key: 'rdmaEnabled', label: 'RDMA', group: 'Networking', type: 'bool' },
    { key: 'trustedLaunch', label: 'Trusted Launch (size eligibility only)', group: 'Security', type: 'bool' },
    { key: 'encryptionAtHost', label: 'Encryption at host', group: 'Security', type: 'bool' },
    { key: 'confidentialComputingType', label: 'Confidential computing type', group: 'Security', type: 'multi' },
    { key: 'gpuCount', label: 'Reported GPU count', group: 'GPUs', type: 'range' },
    { key: 'gpuAllocation', label: 'Physical GPU allocation (may be fractional)', group: 'GPUs', type: 'range' },
    { key: 'gpuMemoryGB', label: 'Total GPU memory (GB, mapped)', group: 'GPUs', type: 'range' },
    { key: 'gpuType', label: 'GPU model', group: 'GPUs', type: 'multi' },
    { key: 'gpuVendor', label: 'GPU vendor', group: 'GPUs', type: 'multi' },
    { key: 'gpuArchitecture', label: 'GPU architecture', group: 'GPUs', type: 'multi' },
    { key: 'availabilityZones', label: 'Zone support in selected region', group: 'Availability', type: 'multi' },
    { key: 'retirementStatus', label: 'Listed as retiring', group: 'Availability', type: 'bool' },
    { key: 'growthRestricted', label: 'Listed as growth restricted', group: 'Availability', type: 'bool' },
    { key: 'price', label: 'Displayed compute price', group: 'Price', type: 'price' }
];
const BROWSE_STORAGE_KEY = 'vmsku_browse_v1';
let browseReady = false;
let browseRestoring = false;
let browseStorageWarned = false;
let browseValidationErrors = [];

function browseId(field) {
    return field.common || `browse-${field.key}`;
}

function browseCapability(row, key) {
    const caps = row.browseCapabilities;
    if (caps?.schemaVersion === 1) return caps[key] ?? null;
    // Older API payloads cannot establish whether default false/zero was sourced.
    if (key === 'hasTempDisk' && typeof row.hasTempDisk === 'boolean') return row.hasTempDisk;
    if (['vCPUs', 'memoryGB', 'gpuCount', 'maxNics', 'maxDataDisks', 'uncachedDiskIOPS', 'vCPUsPerCore'].includes(key) &&
        Number.isFinite(row[key]) && row[key] > 0) return row[key];
    if (key === 'nvme' && row.nvme === true) return true;
    if (key === 'architecture' && String(row.architecture).toLowerCase() === 'arm64') return 'arm64';
    if (key === 'cpuVendor' && row.cpuGeneration) return row.cpuVendor ?? null;
    if (key === 'memoryPerVcpu' && row.vCPUs > 0 && row.memoryGB > 0) return row.memoryGB / row.vCPUs;
    if (['retirementStatus', 'growthRestricted'].includes(key)) return Boolean(row[key]);
    if (key === 'availabilityZones' && row.availabilityZones?.length) return row.availabilityZones;
    if (['cpuGeneration', 'gpuType', 'gpuVendor', 'gpuArchitecture', 'gpuAllocation', 'gpuMemoryGB'].includes(key)) {
        return row[key] === '' ? null : row[key] ?? null;
    }
    return null;
}

function browseEmptyFilters() {
    return { search: '', families: [], vendors: ['Intel', 'AMD', 'ARM'], gpuOnly: false,
        includeUnknown: false, requirePrice: false, priceUnit: 'monthly', values: {} };
}

function browseInputNumber(id, errors) {
    const el = document.getElementById(id);
    const text = el.value.trim();
    const value = text === '' ? null : Number(text);
    const invalid = el.validity?.badInput || (value !== null && (!Number.isFinite(value) || value < 0));
    el.setAttribute('aria-invalid', String(Boolean(invalid)));
    if (invalid) errors.push(`${el.getAttribute('aria-label') || id}: enter a finite, non-negative number.`);
    return invalid ? null : value;
}

function readBrowseFilters() {
    const filters = browseEmptyFilters();
    const errors = [];
    filters.search = document.getElementById('gridSearch').value.trim().slice(0, 120);
    filters.families = [...gridSelectedFamilies];
    filters.vendors = ['Intel', 'AMD', 'ARM'].filter(v => document.getElementById(`gridVendor${v}`).checked);
    filters.gpuOnly = document.getElementById('gridGpuOnly').checked;
    filters.includeUnknown = document.getElementById('browseIncludeUnknown').checked;
    filters.requirePrice = document.getElementById('browseRequirePrice').checked;
    filters.priceUnit = document.getElementById('browsePriceUnit').value;
    for (const field of BROWSE_FIELDS) {
        const id = browseId(field);
        if (field.type === 'range') {
            const min = browseInputNumber(`${id}Min`, errors);
            const max = browseInputNumber(`${id}Max`, errors);
            const invalid = min !== null && max !== null && min > max;
            const error = document.getElementById(`${id}Error`);
            if (invalid) {
                errors.push(`${field.label}: minimum must not exceed maximum.`);
                document.getElementById(`${id}Min`).setAttribute('aria-invalid', 'true');
                document.getElementById(`${id}Max`).setAttribute('aria-invalid', 'true');
            }
            const fieldError = errors.filter(message => message.startsWith(field.label));
            if (error) error.textContent = fieldError.join(' ');
            if (min !== null || max !== null) filters.values[field.key] = { min, max };
        } else if (field.type === 'price') {
            const max = browseInputNumber(id, errors);
            document.getElementById(`${id}Error`).textContent = errors.filter(m => m.startsWith(field.label)).join(' ');
            if (max !== null) filters.values.price = { max };
        } else if (field.type === 'bool') {
            const value = document.getElementById(id).value;
            if (value) filters.values[field.key] = value;
        } else {
            const values = [...document.getElementById(id).selectedOptions].map(option => option.value);
            if (values.length) filters.values[field.key] = values;
        }
    }
    browseValidationErrors = errors;
    return filters;
}

function browseDisplayedPrice(row, unit) {
    const value = gridPriceValue(row, unit);
    return typeof value === 'number' && Number.isFinite(value) && value >= 0
        ? value * getDiscountMultiplier() : null;
}

function browseMatch(row, filters) {
    let unverified = false;
    if (filters.search && !`${row.name} ${row.family}`.toLowerCase().includes(filters.search.toLowerCase())) return { matches: false };
    if (filters.families.length && !filters.families.includes(row.family)) return { matches: false };
    const checks = Object.entries(filters.values);
    if (filters.vendors.length !== 3) checks.push(['cpuVendor', filters.vendors]);
    if (filters.gpuOnly) checks.push(['gpuCount', { min: Number.MIN_VALUE, max: null }]);
    if (filters.requirePrice && browseDisplayedPrice(row, filters.priceUnit) === null) return { matches: false };
    for (const [key, required] of checks) {
        const value = key === 'price' ? browseDisplayedPrice(row, filters.priceUnit) : browseCapability(row, key);
        const range = required && !Array.isArray(required) && typeof required === 'object';
        const unknown = value === null || value === undefined ||
            (range && (typeof value !== 'number' || !Number.isFinite(value)));
        if (unknown) {
            if (!filters.includeUnknown) return { matches: false };
            unverified = true;
            continue;
        }
        if (range) {
            if (required.min != null && value < required.min) return { matches: false };
            if (required.max != null && value > required.max) return { matches: false };
        } else if (Array.isArray(required)) {
            const values = Array.isArray(value) ? value : [value];
            if (!required.some(option => values.includes(option))) return { matches: false };
        } else if (value !== (required === 'true')) return { matches: false };
    }
    return { matches: true, unverified };
}

function buildBrowseControls() {
    const groups = [...new Set(BROWSE_FIELDS.map(field => field.group).filter(Boolean))];
    document.getElementById('browseFilterGroups').innerHTML = groups.map(group => `<fieldset>
        <legend>${escapeHtml(group)}</legend>${BROWSE_FIELDS.filter(field => field.group === group).map(field => {
            const id = browseId(field), label = escapeHtml(field.label);
            if (field.type === 'range') return `<div class="browse-field"><span id="${id}Label">${label}</span>
                <div class="browse-range" role="group" aria-labelledby="${id}Label">
                <input id="${id}Min" type="number" min="0" step="any" placeholder="min" aria-label="${label} minimum" aria-describedby="${id}Error">
                <span aria-hidden="true">to</span>
                <input id="${id}Max" type="number" min="0" step="any" placeholder="max" aria-label="${label} maximum" aria-describedby="${id}Error"></div>
                <span id="${id}Error" class="browse-field-error"></span><small id="${id}Coverage"></small></div>`;
            if (field.type === 'price') return `<div class="browse-field"><label for="${id}">${label} ceiling (<span id="browseBudgetCurrency">USD</span>)</label>
                <input id="${id}" type="number" min="0" step="any" aria-label="${label}" aria-describedby="${id}Error">
                <span id="${id}Error" class="browse-field-error"></span>
                <label for="browsePriceUnit">Price period</label><select id="browsePriceUnit"><option value="monthly">Monthly (730 hours)</option><option value="hourly">Hourly</option></select>
                <label><input type="checkbox" id="browseRequirePrice"> Require available price</label>
                <small>Matches the displayed OS, purchase option, currency and applicable discount. Compute only; no storage/networking. Changing currency reinterprets the ceiling, not FX-converts it.</small></div>`;
            return `<div class="browse-field"><label for="${id}">${label}</label><select id="${id}"${field.type === 'multi' ? ' multiple size="3"' : ''}>
                ${field.type === 'bool' ? '<option value="">Any</option><option value="true">Supported / Yes</option><option value="false">Not supported / No</option>' : ''}
                </select><small id="${id}Coverage"></small></div>`;
        }).join('')}</fieldset>`).join('');
}

function updateBrowseFacets() {
    for (const field of BROWSE_FIELDS.filter(f => !f.common && f.type !== 'price')) {
        const id = browseId(field);
        const known = gridRows.map(row => browseCapability(row, field.key)).filter(value => value !== null);
        const coverage = document.getElementById(`${id}Coverage`);
        if (coverage) coverage.textContent = `${known.length} of ${gridRows.length} sizes with published/verified data`;
        if (field.type !== 'multi') continue;
        const select = document.getElementById(id);
        const selected = [...select.selectedOptions].map(option => option.value);
        const available = new Set(known.flat());
        const options = [...new Set([...available, ...selected])].sort((a, b) => String(a).localeCompare(String(b)));
        select.innerHTML = options.map(value => `<option value="${escapeHtml(String(value))}"${selected.includes(value) ? ' selected' : ''}>${escapeHtml(String(value))}${available.has(value) ? '' : ' (not in this region)'}</option>`).join('');
    }
    document.getElementById('browseApiNotice').textContent = gridRows.some(row => row.browseCapabilities?.schemaVersion === 1)
        ? 'Unknown fields may need a successful capability-cache refresh. Published limits are not guaranteed workload performance.'
        : 'This API has not published the advanced capability contract yet. Unsupported fields remain unknown; full coverage requires the reviewed API release and cache refresh.';
}

function writeBrowseFilters(filters) {
    document.getElementById('gridSearch').value = filters.search;
    gridSelectedFamilies.clear();
    filters.families.forEach(family => gridSelectedFamilies.add(family));
    ['Intel', 'AMD', 'ARM'].forEach(v => { document.getElementById(`gridVendor${v}`).checked = filters.vendors.includes(v); });
    document.getElementById('gridGpuOnly').checked = filters.gpuOnly;
    document.getElementById('browseIncludeUnknown').checked = filters.includeUnknown;
    document.getElementById('browseRequirePrice').checked = filters.requirePrice;
    document.getElementById('browsePriceUnit').value = filters.priceUnit;
    for (const field of BROWSE_FIELDS) {
        const id = browseId(field), value = filters.values[field.key];
        if (field.type === 'range') {
            document.getElementById(`${id}Min`).value = value?.min ?? '';
            document.getElementById(`${id}Max`).value = value?.max ?? '';
        } else if (field.type === 'price') document.getElementById(id).value = value?.max ?? '';
        else if (field.type === 'bool') document.getElementById(id).value = value || '';
        else {
            const select = document.getElementById(id);
            select.innerHTML = (value || []).map(v => `<option value="${escapeHtml(v)}" selected>${escapeHtml(v)}</option>`).join('');
        }
    }
}

function browseFilterDescriptions(filters) {
    const descriptions = Object.entries(filters.values).map(([key, value]) => {
        const field = BROWSE_FIELDS.find(f => f.key === key);
        const text = Array.isArray(value) ? value.join(', ') : typeof value === 'object'
            ? `${value.min ?? 0} to ${value.max ?? 'any'}${key === 'price' ? ` ${document.getElementById('currencyCode').value}/${filters.priceUnit}` : ''}`
            : value === 'true' ? 'Yes' : 'No';
        return { key, text: `${field.label}: ${text}` };
    });
    if (filters.search) descriptions.push({ key: 'search', text: `Search: ${filters.search}` });
    if (filters.families.length) descriptions.push({ key: 'families', text: `Families: ${filters.families.join(', ')}` });
    if (filters.vendors.length !== 3) descriptions.push({ key: 'vendors', text: `CPU vendors: ${filters.vendors.join(', ') || 'none'}` });
    if (filters.gpuOnly) descriptions.push({ key: 'gpuOnly', text: 'GPU only' });
    if (filters.requirePrice) descriptions.push({ key: 'requirePrice', text: 'Require available price' });
    if (filters.includeUnknown) descriptions.push({ key: 'includeUnknown', text: 'Include unknown values' });
    return descriptions;
}

function refreshBrowseSummary(filters, rows) {
    const descriptions = browseFilterDescriptions(filters);
    document.getElementById('browseActiveCount').textContent = descriptions.length ? ` (${descriptions.length} active)` : '';
    const chips = document.getElementById('browseFilterChips');
    chips.innerHTML = descriptions.map(d => `<button type="button" class="browse-chip" data-filter="${d.key}" aria-label="Remove ${escapeHtml(d.text)}">${escapeHtml(d.text)} <span aria-hidden="true">&times;</span></button>`).join('');
    chips.querySelectorAll('button').forEach(button => button.addEventListener('click', () => {
        const key = button.dataset.filter;
        if (key in filters.values) delete filters.values[key];
        else filters[key] = browseEmptyFilters()[key];
        writeBrowseFilters(filters);
        renderGridFacets();
        gridPage = 1;
        renderGrid();
        document.getElementById('gridResetFilters').focus();
    }));
    document.getElementById('browseFilterErrors').textContent = browseValidationErrors.join(' ');
    const unverified = rows.filter(row => browseMatch(row, filters).unverified).length;
    document.getElementById('browseUnverifiedCount').textContent = unverified ? `${unverified} matches have unverified requirements.` : '';
    document.getElementById('browseBudgetCurrency').textContent = document.getElementById('currencyCode').value || 'USD';
    for (const id of ['gridExportCsvBtn', 'gridExportXlsxBtn', 'browseCopyLink']) {
        document.getElementById(id).disabled = browseValidationErrors.length > 0;
    }
}

function browseFormatValue(value) {
    if (value === null || value === undefined) return 'Unknown';
    if (typeof value === 'boolean') return value ? 'Yes' : 'No';
    if (Array.isArray(value)) return value.length ? value.join(', ') : 'None';
    return typeof value === 'number' ? String(Number(value.toFixed(4))) : String(value);
}

function browseRowDetails(row, filters) {
    const unverified = browseMatch(row, filters).unverified;
    return `<details class="browse-row-details"><summary>Specs${unverified ? ' (unverified match)' : ''}</summary>
        <p>Size capabilities only. Validate OS/image, disk and security prerequisites. Processor models are series possibilities, not a guaranteed host. Zone listings do not establish current capacity or subscription eligibility.</p>
        <dl>${BROWSE_FIELDS.filter(field => field.type !== 'price').map(field => `<div><dt>${escapeHtml(field.label)}</dt><dd>${escapeHtml(browseFormatValue(browseCapability(row, field.key)))}</dd></div>`).join('')}</dl></details>`;
}

function normalizeBrowseState(input) {
    if (!input || input.v !== 1 || !input.filters || typeof input.filters !== 'object') throw Error('Unsupported Browse link or saved state.');
    const f = input.filters, filters = browseEmptyFilters();
    if (Array.isArray(f) || (f.values !== undefined && (!f.values || typeof f.values !== 'object' || Array.isArray(f.values)))) {
        throw Error('Invalid Browse filters.');
    }
    const strings = value => {
        if (!Array.isArray(value) || value.length > 100 || value.some(v => typeof v !== 'string' || !v.length || v.length > 120)) throw Error('Invalid Browse selection.');
        return [...new Set(value)];
    };
    if (f.search !== undefined && (typeof f.search !== 'string' || f.search.length > 120)) throw Error('Invalid Browse search.');
    filters.search = f.search || '';
    filters.families = strings(f.families ?? []);
    filters.vendors = strings(f.vendors ?? filters.vendors);
    if (filters.vendors.some(v => !['Intel', 'AMD', 'ARM'].includes(v))) throw Error('Invalid CPU vendor.');
    for (const key of ['gpuOnly', 'includeUnknown', 'requirePrice']) {
        if (f[key] !== undefined && typeof f[key] !== 'boolean') throw Error('Invalid Browse option.');
        filters[key] = f[key] ?? false;
    }
    filters.priceUnit = f.priceUnit ?? 'monthly';
    if (!['monthly', 'hourly'].includes(filters.priceUnit)) throw Error('Invalid price period.');
    for (const field of BROWSE_FIELDS) {
        const value = f.values?.[field.key];
        if (value === undefined) continue;
        if (field.type === 'range' || field.type === 'price') {
            if (!value || typeof value !== 'object' || Array.isArray(value)) throw Error(`Invalid ${field.label}.`);
            const bounds = field.type === 'price' ? ['max'] : ['min', 'max'];
            const range = {};
            for (const bound of bounds) {
                const n = value[bound] ?? null;
                if (n !== null && (typeof n !== 'number' || !Number.isFinite(n) || n < 0)) throw Error(`Invalid ${field.label}.`);
                range[bound] = n;
            }
            if (range.min != null && range.max != null && range.min > range.max) throw Error(`Invalid ${field.label} range.`);
            if (Object.values(range).some(v => v !== null)) filters.values[field.key] = range;
        } else if (field.type === 'bool') {
            if (!['true', 'false'].includes(value)) throw Error(`Invalid ${field.label}.`);
            filters.values[field.key] = value;
        } else {
            const selections = strings(value);
            if (selections.length) filters.values[field.key] = selections;
        }
    }
    const enumValue = (value, allowed, fallback) => {
        if (value === undefined) return fallback;
        if (!allowed.includes(value)) throw Error('Invalid Browse pricing or region context.');
        return value;
    };
    const discount = input.discount ?? 0;
    if (typeof discount !== 'number' || !Number.isFinite(discount) || discount < 0 || discount > 100) throw Error('Invalid discount.');
    return {
        v: 1, filters,
        region: enumValue(input.region, ['', ...ALL_REGIONS.map(r => r.value)], ''),
        currency: enumValue(input.currency, [...document.getElementById('currencyCode').options].map(o => o.value), 'USD'),
        os: enumValue(input.os, ['linux', 'windows'], 'linux'),
        model: enumValue(input.model, ['payg', 'spot', 'ri1year', 'ri3year', 'sp1year', 'sp3year'], 'payg'),
        discount,
        sort: {
            col: enumValue(input.sort?.col, ['name', 'family', 'vCPUs', 'memoryGB', 'gpuCount', 'gpuPerfScore', 'acu', 'hourly', 'monthly'], 'vCPUs'),
            dir: enumValue(input.sort?.dir, ['asc', 'desc'], 'asc')
        }
    };
}

function captureBrowseState(filters) {
    return normalizeBrowseState({ v: 1, filters,
        region: document.getElementById('location').value,
        currency: document.getElementById('currencyCode').value,
        os: currentPricingOS, model: currentPricingModel,
        discount: Number(document.getElementById('discountPct').value || 0), sort: gridSort });
}

function persistBrowseState(filters) {
    if (!browseReady || browseRestoring || currentMode !== 'browse' || browseValidationErrors.length) return;
    let text;
    try {
        text = JSON.stringify(captureBrowseState(filters));
        const url = new URL(window.location.href);
        url.searchParams.set('browse', text);
        window.history.replaceState(null, '', url);
    } catch (error) {
        console.warn('Could not update Browse link.', error);
        document.getElementById('browseStateNotice').textContent = 'Browse link could not be updated: check pricing and filter values.';
        return;
    }
    try {
        localStorage.setItem(BROWSE_STORAGE_KEY, text);
    } catch (error) {
        if (!browseStorageWarned) console.warn('Could not save Browse filters.', error);
        browseStorageWarned = true;
        document.getElementById('browseStateNotice').textContent = 'Device storage is unavailable. Browse filters can still be shared using the link.';
    }
}

async function copyBrowseLink() {
    const filters = readBrowseFilters();
    if (browseValidationErrors.length) { showError('Correct the filter errors before sharing.'); return; }
    persistBrowseState(filters);
    try {
        const url = new URL(window.location.href);
        url.searchParams.set('browse', JSON.stringify(captureBrowseState(filters)));
        await navigator.clipboard.writeText(url.href);
        document.getElementById('browseStateNotice').textContent = 'Browse link copied.';
    } catch (error) {
        console.warn('Could not copy Browse link.', error);
        document.getElementById('browseStateNotice').textContent = 'Could not copy the link. Check browser clipboard permissions.';
    }
}

function initializeBrowseFilters() {
    buildBrowseControls();
    browseReady = true;
    document.getElementById('browseAdvanced').addEventListener('input', () => { gridPage = 1; renderGrid(); });
    document.getElementById('browseAdvanced').addEventListener('change', () => { gridPage = 1; renderGrid(); });
    document.getElementById('browseCopyLink').addEventListener('click', copyBrowseLink);
    let raw;
    try {
        const url = new URL(window.location.href);
        raw = url.searchParams.has('browse') ? url.searchParams.get('browse') : localStorage.getItem(BROWSE_STORAGE_KEY);
        if (raw === null) return;
        if (raw.length > 20000) throw Error('Browse state is too large.');
        const state = normalizeBrowseState(JSON.parse(raw));
        browseRestoring = true;
        writeBrowseFilters(state.filters);
        document.getElementById('currencyCode').value = state.currency;
        document.getElementById('discountPct').value = state.discount;
        gridSort = state.sort;
        setPricingOS(state.os);
        setPricingModel(state.model);
        if (state.region) {
            locationChoices.setChoiceByValue(state.region);
            updateOnboardingState();
            loadSkusForRegion(state.region);
        }
        switchMode('browse');
    } catch (error) {
        console.warn('Could not restore Browse filters.', error);
        document.getElementById('browseStateNotice').textContent = 'Saved/shared Browse filters could not be restored. Reset filters to start fresh.';
    } finally {
        browseRestoring = false;
    }
}
