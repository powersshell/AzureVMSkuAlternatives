const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'web-app', 'src', 'theme.js'), 'utf8');
const html = fs.readFileSync(path.join(root, 'web-app', 'src', 'index.html'), 'utf8');
const css = fs.readFileSync(path.join(root, 'web-app', 'src', 'styles.css'), 'utf8');
const app = fs.readFileSync(path.join(root, 'web-app', 'src', 'app.js'), 'utf8');

function fixture({ stored = null, dark = false, readFails = false, writeFails = false } = {}) {
    const warnings = [];
    const saved = new Map(stored === null ? [] : [['vmsku-theme', stored]]);
    const dataset = {};
    const events = {};
    const selectorEvents = {};
    const deviceEvents = {};
    const device = {
        matches: dark,
        addEventListener: (name, callback) => { deviceEvents[name] = callback; },
    };
    const selector = {
        value: 'system',
        addEventListener: (name, callback) => { selectorEvents[name] = callback; },
    };
    vm.runInNewContext(source, {
        window: {
            matchMedia: query => {
                assert.equal(query, '(prefers-color-scheme: dark)');
                return device;
            },
        },
        document: {
            documentElement: { dataset },
            getElementById: id => {
                assert.equal(id, 'themePreference');
                return selector;
            },
            addEventListener: (name, callback) => { events[name] = callback; },
        },
        localStorage: {
            getItem: key => {
                if (readFails) throw new Error('Storage unavailable');
                return saved.get(key) ?? null;
            },
            setItem: (key, value) => {
                if (writeFails) throw new Error('Storage unavailable');
                saved.set(key, value);
            },
        },
        console: { warn: (...args) => warnings.push(args) },
    });
    const initialTheme = dataset.theme;
    events.DOMContentLoaded();
    return {
        dataset, selector, saved, warnings, initialTheme,
        select(value) { selector.value = value; selectorEvents.change(); },
        deviceChange(value) { device.matches = value; deviceEvents.change(); },
    };
}

for (const dark of [false, true]) {
    for (const stored of [null, 'system', 'light', 'dark', 'invalid']) {
        const f = fixture({ dark, stored });
        const effective = stored === 'light' || stored === 'dark' ? stored : dark ? 'dark' : 'light';
        assert.equal(f.initialTheme, effective, 'Theme resolves before DOMContentLoaded');
        assert.equal(f.selector.value, stored === 'light' || stored === 'dark' ? stored : 'system');
        f.deviceChange(!dark);
        assert.equal(f.dataset.theme, stored === 'light' || stored === 'dark' ? stored : dark ? 'light' : 'dark');
        for (const value of ['light', 'dark', 'system']) {
            f.select(value);
            assert.equal(f.saved.get('vmsku-theme'), value);
            const reloaded = fixture({ dark: !dark, stored: f.saved.get('vmsku-theme') });
            assert.equal(reloaded.dataset.theme, value === 'system' ? dark ? 'light' : 'dark' : value);
        }
    }
}
const override = fixture({ dark: true });
override.select('light');
override.deviceChange(false);
override.deviceChange(true);
assert.equal(override.dataset.theme, 'light');
override.select('system');
assert.equal(override.dataset.theme, 'dark');
override.deviceChange(false);
assert.equal(override.dataset.theme, 'light');
override.select('unsupported');
assert.equal(override.selector.value, 'system');
assert.equal(override.warnings.length, 1);
for (const readFails of [false, true]) {
    const f = fixture({ dark: true, readFails, writeFails: true });
    assert.equal(f.initialTheme, 'dark');
    f.select('light');
    f.deviceChange(true);
    assert.equal(f.dataset.theme, 'light');
    assert.equal(f.warnings.length, readFails ? 2 : 1);
}

assert.ok(html.indexOf('<script src="theme.js"></script>') < html.indexOf('<link rel="stylesheet"'));
assert.match(html, /<label for="themePreference">Theme<\/label>/);
const select = html.match(/<select id="themePreference">([\s\S]*?)<\/select>/)[1];
assert.deepEqual([...select.matchAll(/value="([^"]+)"/g)].map(match => match[1]), ['system', 'light', 'dark']);
assert.doesNotMatch(source, /fetch\(|location\.reload|innerHTML|trackEvent|Choices/);
assert.match(css, /:root\[data-theme="dark"\]/);
assert.match(css, /\.grid-pagination:empty\s*\{\s*display:\s*none;/);

const chartContext = vm.createContext({
    fmtUsd: value => value.toFixed(3),
    escapeHtml: value => value,
});
for (const name of ['seriesToPoints', 'renderSparklineInto', 'buildHistoryChart']) {
    const fn = app.match(new RegExp(`^function ${name}\\b[\\s\\S]*?^}`, 'm'));
    assert.ok(fn, name);
    vm.runInContext(fn[0], chartContext);
}
const points = [
    { date: '2026-10-01', hourlyLinux: 0.2, hourlyWindows: 0.4, hourlySpot: 0.1 },
    { date: '2026-10-02', hourlyLinux: 0.1, hourlyWindows: 0.3, hourlySpot: 0.05 },
];
const chart = chartContext.buildHistoryChart(points);
for (const color of ['chart-linux', 'chart-windows', 'chart-spot', 'border']) {
    assert.ok(chart.includes(`var(--${color})`));
}
const sparkline = { innerHTML: '', classList: { add() {}, remove() {} } };
chartContext.renderSparklineInto(sparkline, { points });
assert.match(sparkline.innerHTML, /stroke="var\(--success\)"/);
chartContext.renderSparklineInto(sparkline, { points: [...points].reverse() });
assert.match(sparkline.innerHTML, /stroke="var\(--danger\)"/);
chartContext.renderSparklineInto(sparkline, { points: [points[0], points[0]] });
assert.match(sparkline.innerHTML, /stroke="var\(--text-muted\)"/);

function tokens(block) {
    return Object.fromEntries([...block.matchAll(/(--[\w-]+):\s*([^;]+);/g)].map(match => [match[1], match[2].trim()]));
}
function resolve(palette, key) {
    const color = palette[key];
    assert.ok(color, `Missing token ${key}`);
    return color.startsWith('var(') ? resolve(palette, color.match(/var\((--[\w-]+)\)/)[1]) : color;
}
function luminance(color) {
    assert.match(color, /^#[\da-f]{3,6}$/i);
    const hex = color.length === 4 ? [...color.slice(1)].map(value => value + value).join('') : color.slice(1);
    const values = [0, 2, 4].map(i => parseInt(hex.slice(i, i + 2), 16) / 255)
        .map(value => value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4);
    return values[0] * 0.2126 + values[1] * 0.7152 + values[2] * 0.0722;
}
function contrast(a, b) {
    const values = [luminance(a), luminance(b)].sort((a, b) => b - a);
    return (values[0] + 0.05) / (values[1] + 0.05);
}
const light = tokens(css.match(/:root \{([\s\S]*?)\n\}/)[1]);
const dark = { ...light, ...tokens(css.match(/:root\[data-theme="dark"\] \{([\s\S]*?)\n\}/)[1]) };
for (const [name, palette] of [['light', light], ['dark', dark]]) {
    const pairs = [];
    for (const text of ['--text-primary', '--text-secondary', '--text-muted', '--accent', '--success', '--danger', '--warning-text']) {
        for (const bg of ['--card-bg', '--surface-alt', '--surface-subtle', '--border-light', '--selected-bg']) {
            pairs.push([text, bg, 4.5]);
        }
    }
    for (const status of ['success', 'danger', 'info']) {
        pairs.push([status === 'info' ? '--info-text' : `--${status}`, `--${status}-bg`, 4.5]);
    }
    pairs.push(['--warning-text', '--warning-bg', 4.5]);
    for (const color of ['--chart-linux', '--chart-windows', '--chart-spot', '--control-border', '--accent']) {
        pairs.push([color, '--card-bg', 3]);
    }
    for (const [text, bg, threshold] of pairs) {
        const ratio = contrast(resolve(palette, text), resolve(palette, bg));
        assert.ok(ratio >= threshold, `${name} ${text} on ${bg}: ${ratio.toFixed(2)} < ${threshold}`);
    }
    assert.ok(contrast('#fff', resolve(palette, '--blue-start')) >= 4.5);
    for (const bg of ['--page-start', '--page-end']) assert.ok(contrast('#fff', resolve(palette, bg)) >= 4.5);
}
console.log('PASS: early theme resolution, System/Light/Dark precedence, persistence, live device changes, storage failures, SVG tokens and semantic palette contrast.');
