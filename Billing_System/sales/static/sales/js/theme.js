/**
 * Global Theme & Personalization Manager for Billing System
 * Supports Themes: Ash, Forest, Ocean, Plum, Amber
 * Light / Dark Surfaces, Segmented Toggle Animations & Week Start Persistence
 */

const SafeStorage = {
    get(key, fallback) {
        try {
            const v = localStorage.getItem(key);
            return (v !== null && v !== undefined) ? v : fallback;
        } catch (e) {
            return fallback;
        }
    },
    set(key, val) {
        try {
            localStorage.setItem(key, val);
        } catch (e) {}
    }
};

function applyTheme(theme) {
    const allowed = ["ash", "forest", "ocean", "plum", "amber"];

    if (!allowed.includes(theme)) {
        theme = "forest";
    }

    document.documentElement.dataset.theme = theme;
    document.documentElement.setAttribute('data-theme', theme);
    SafeStorage.set("alpha-theme", theme);
    SafeStorage.set("pos_theme", theme);

    document.querySelectorAll("[data-theme-option]").forEach(el => {
        el.classList.toggle(
            "active",
            el.dataset.themeOption === theme
        );
    });

    if (window.ThemeManager) {
        window.ThemeManager.updateWidgetUI();
    }

    window.dispatchEvent(new CustomEvent('themechanged', { detail: { theme: theme } }));
}

function setToggle(toggle, target) {
    if (!toggle) return;
    toggle.dataset.active = target;

    const panel = document.querySelector(`[data-toggle-panel="${target}"]`);
    if (panel) {
        const parent = panel.parentElement;
        if (parent) {
            parent.querySelectorAll('[data-toggle-panel]').forEach(p => {
                if (p === panel) {
                    p.style.display = 'block';
                    p.classList.add('active');
                } else {
                    p.style.display = 'none';
                    p.classList.remove('active');
                }
            });
        }

        panel.animate(
            [
                { opacity: 0, transform: "translateY(8px) scale(.985)" },
                { opacity: 1, transform: "translateY(0) scale(1)" }
            ],
            {
                duration: 260,
                easing: "cubic-bezier(.2,.8,.2,1)"
            }
        );
    }
}

function initSegmentedToggles() {
    document.querySelectorAll('.segmented-toggle').forEach(toggle => {
        let indicator = toggle.querySelector('.indicator');
        if (!indicator) {
            indicator = document.createElement('div');
            indicator.className = 'indicator';
            toggle.insertBefore(indicator, toggle.firstChild);
        }

        const buttons = toggle.querySelectorAll('button');
        if (!buttons.length) return;

        function updateIndicator(btn) {
            if (!btn) return;
            const toggleRect = toggle.getBoundingClientRect();
            const btnRect = btn.getBoundingClientRect();
            const leftOffset = btnRect.left - toggleRect.left - 4;
            indicator.style.transform = `translateX(${leftOffset}px)`;
            indicator.style.width = `${btnRect.width}px`;
        }

        let activeBtn = toggle.querySelector('button.active');
        if (!activeBtn && buttons.length) {
            activeBtn = buttons[0];
            activeBtn.classList.add('active');
        }

        if (activeBtn) {
            setTimeout(() => updateIndicator(activeBtn), 30);
        }

        buttons.forEach(btn => {
            btn.addEventListener('click', () => {
                buttons.forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                updateIndicator(btn);

                const target = btn.dataset.target || btn.dataset.toggleTarget || btn.dataset.tab;
                if (target) {
                    setToggle(toggle, target);
                }
            });
        });
    });
}

function getWeekStart() {
    return localStorage.getItem('pos_week_start') || 'sunday';
}

function setWeekStart(day) {
    const val = (day === 'monday') ? 'monday' : 'sunday';
    localStorage.setItem('pos_week_start', val);
    window.dispatchEvent(new CustomEvent('weekstartchanged', { detail: { weekStart: val } }));
}

// Global Theme Manager instance
(function () {
    'use strict';

    const STORAGE_KEY_THEME = 'pos_theme';
    const STORAGE_KEY_MODE = 'pos_mode';

    const THEMES = [
        { id: 'forest', name: 'Forest', color: '#16a34a' },
        { id: 'ash',    name: 'Ash',    color: '#64748b' },
        { id: 'ocean',  name: 'Ocean',  color: '#0284c7' },
        { id: 'plum',   name: 'Plum',   color: '#9333ea' },
        { id: 'amber',  name: 'Amber',  color: '#d97706' },
    ];

    let currentTheme = SafeStorage.get(STORAGE_KEY_THEME, SafeStorage.get('alpha-theme', 'forest'));
    let currentMode = SafeStorage.get(STORAGE_KEY_MODE, 'light');
    if (currentMode !== 'dark') currentMode = 'light';

    document.documentElement.dataset.theme = currentTheme;
    document.documentElement.setAttribute('data-theme', currentTheme);
    document.documentElement.setAttribute('data-mode', currentMode);

    const ThemeManager = {
        getTheme() {
            return currentTheme;
        },

        getMode() {
            return currentMode;
        },

        setTheme(themeId) {
            currentTheme = themeId;
            applyTheme(themeId);
        },

        setMode(mode) {
            if (mode !== 'dark') mode = 'light';
            currentMode = mode;
            document.documentElement.setAttribute('data-mode', mode);
            document.documentElement.dataset.mode = mode;
            SafeStorage.set(STORAGE_KEY_MODE, mode);
            this.updateWidgetUI();
            window.dispatchEvent(new CustomEvent('themechanged', { detail: { theme: currentTheme, mode: mode } }));
        },

        toggleMode() {
            this.setMode(currentMode === 'dark' ? 'light' : 'dark');
        },

        createWidget() {
            const wrap = document.createElement('div');
            wrap.className = 'theme-picker-widget';
            wrap.setAttribute('aria-label', 'Theme Selector');

            const dotsContainer = document.createElement('div');
            dotsContainer.className = 'theme-palette-dots';

            THEMES.forEach(t => {
                const btn = document.createElement('button');
                btn.type = 'button';
                btn.className = `theme-dot dot-${t.id}${t.id === currentTheme ? ' active' : ''}`;
                btn.title = `Theme: ${t.name}`;
                btn.setAttribute('aria-label', t.name);
                btn.onclick = (e) => {
                    e.preventDefault();
                    this.setTheme(t.id);
                };
                dotsContainer.appendChild(btn);
            });

            const modeBtn = document.createElement('button');
            modeBtn.type = 'button';
            modeBtn.className = 'mode-toggle-btn';
            modeBtn.title = currentMode === 'dark' ? 'Switch to Light Mode' : 'Switch to Dark Mode';
            modeBtn.innerHTML = currentMode === 'dark' ? '<i class="bi bi-sun-fill"></i>' : '<i class="bi bi-moon-fill"></i>';
            modeBtn.onclick = (e) => {
                e.preventDefault();
                this.toggleMode();
            };

            wrap.appendChild(dotsContainer);
            wrap.appendChild(modeBtn);
            return wrap;
        },

        updateWidgetUI() {
            document.querySelectorAll('.theme-picker-widget').forEach(w => {
                w.querySelectorAll('.theme-dot').forEach(dot => {
                    const isTarget = dot.classList.contains(`dot-${currentTheme}`);
                    dot.classList.toggle('active', isTarget);
                });
                const modeBtn = w.querySelector('.mode-toggle-btn');
                if (modeBtn) {
                    modeBtn.title = currentMode === 'dark' ? 'Switch to Light Mode' : 'Switch to Dark Mode';
                    modeBtn.innerHTML = currentMode === 'dark' ? '<i class="bi bi-sun-fill"></i>' : '<i class="bi bi-moon-fill"></i>';
                }
            });
        },

        autoMount() {
            const slots = document.querySelectorAll('#theme-widget-slot, .theme-picker-mount');
            if (slots.length > 0) {
                slots.forEach(slot => {
                    slot.innerHTML = '';
                    slot.appendChild(this.createWidget());
                });
                return;
            }

            const posHeaderRight = document.querySelector('.pos-header .header-right');
            if (posHeaderRight && !posHeaderRight.querySelector('.theme-picker-widget')) {
                posHeaderRight.insertBefore(this.createWidget(), posHeaderRight.firstChild);
                return;
            }

            const navBar = document.querySelector('.nav-bar');
            if (navBar && !navBar.querySelector('.theme-picker-widget')) {
                const logoutBtn = navBar.querySelector('a[href*="logout"]');
                if (logoutBtn) {
                    navBar.insertBefore(this.createWidget(), logoutBtn);
                } else {
                    navBar.appendChild(this.createWidget());
                }
            }
        }
    };

    window.ThemeManager = ThemeManager;
})();

// Document Ready: Wire up appearance buttons and animation
document.addEventListener("DOMContentLoaded", () => {
    const saved = localStorage.getItem("alpha-theme") || localStorage.getItem("pos_theme") || "forest";
    applyTheme(saved);

    document.querySelectorAll("[data-theme-option]").forEach(el => {
        el.addEventListener("click", () => {
            applyTheme(el.dataset.themeOption);

            el.animate(
                [
                    { transform: "scale(.94)" },
                    { transform: "scale(1.06)" },
                    { transform: "scale(1)" }
                ],
                {
                    duration: 240,
                    easing: "cubic-bezier(.2,.8,.2,1)"
                }
            );
        });
    });

    initSegmentedToggles();

    if (window.ThemeManager) {
        window.ThemeManager.autoMount();
    }
});
