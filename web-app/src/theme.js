(() => {
    'use strict';

    const storageKey = 'vmsku-theme';
    const preferences = ['system', 'light', 'dark'];
    const deviceTheme = window.matchMedia('(prefers-color-scheme: dark)');
    let preference = 'system';
    try {
        const stored = localStorage.getItem(storageKey);
        if (preferences.includes(stored)) preference = stored;
    } catch (error) {
        console.warn('Unable to read theme preference; using the system theme.', error);
    }

    function applyTheme() {
        document.documentElement.dataset.theme = preference === 'system'
            ? (deviceTheme.matches ? 'dark' : 'light')
            : preference;
    }

    applyTheme();
    deviceTheme.addEventListener('change', () => {
        if (preference === 'system') applyTheme();
    });

    document.addEventListener('DOMContentLoaded', () => {
        const selector = document.getElementById('themePreference');
        selector.value = preference;
        selector.addEventListener('change', () => {
            if (!preferences.includes(selector.value)) {
                console.warn('Ignoring unsupported theme preference.');
                selector.value = preference;
                return;
            }
            preference = selector.value;
            applyTheme();
            try {
                localStorage.setItem(storageKey, preference);
            } catch (error) {
                console.warn('Unable to save theme preference; keeping it for this visit.', error);
            }
        });
    });
})();
