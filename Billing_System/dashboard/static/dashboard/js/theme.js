/**
 * Global Theme & Shell Controller for Billing System / POS
 * Supports Themes: Ash, Forest, Ocean, Plum, Amber
 * Light / Dark Surfaces, Collapsible Wharf Sidebar, Smooth Search, and Cloud Sync
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

    // Animate target panel if present
    const panel = document.querySelector(`[data-toggle-panel="${target}"]`);
    if (panel) {
        // Toggle sibling panels
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
            this.updateAppearancePageButtons();
            window.dispatchEvent(new CustomEvent('themechanged', { detail: { theme: currentTheme, mode: mode } }));
        },

        toggleMode() {
            this.setMode(currentMode === 'dark' ? 'light' : 'dark');
        },

        updateAppearancePageButtons() {
            const btnLight = document.getElementById("btn-mode-light");
            const btnDark = document.getElementById("btn-mode-dark");
            if (btnLight) {
                btnLight.classList.toggle("active", currentMode === "light");
                btnLight.setAttribute("aria-pressed", currentMode === "light" ? "true" : "false");
            }
            if (btnDark) {
                btnDark.classList.toggle("active", currentMode === "dark");
                btnDark.setAttribute("aria-pressed", currentMode === "dark" ? "true" : "false");
            }
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
            // Only mount into designated settings appearance slots, never in headers
            const slots = document.querySelectorAll('.settings-theme-widget-mount');
            slots.forEach(slot => {
                slot.innerHTML = '';
                slot.appendChild(this.createWidget());
            });
        }
    };

    window.ThemeManager = ThemeManager;
})();

/* ── Collapsible Sidebar Controller ─────────────────────────────────────────── */
const SidebarController = {
    STORAGE_KEY: 'sidebar_collapsed',

    init() {
        const sidebar = document.getElementById('global-sidebar');
        const shell = document.getElementById('app-shell') || document.querySelector('.app-shell');
        const backdrop = document.getElementById('sidebar-backdrop');

        if (!sidebar) return;

        // Restore desktop collapse state
        const isDesktop = window.innerWidth > 900;
        const stored = localStorage.getItem(this.STORAGE_KEY) || localStorage.getItem('pos_sidebar_collapsed');
        if (isDesktop && stored === 'true') {
            sidebar.classList.add('is-collapsed');
            if (shell) shell.setAttribute('data-sidebar-collapsed', 'true');
            this.updateAria(true);
        } else {
            sidebar.classList.remove('is-collapsed');
            if (shell) shell.setAttribute('data-sidebar-collapsed', 'false');
            this.updateAria(false);
        }

        // Attach listener to all sidebar toggle buttons
        document.querySelectorAll('.sidebar-toggle-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.preventDefault();
                this.toggle();
            });
        });

        if (backdrop) {
            backdrop.addEventListener('click', () => {
                this.closeMobileDrawer();
            });
        }

        // Close mobile drawer on route click
        sidebar.querySelectorAll('.sidebar-link').forEach(link => {
            link.addEventListener('click', () => {
                if (window.innerWidth <= 900) {
                    this.closeMobileDrawer();
                }
            });
        });

        // Sync active link with current URL
        this.syncActiveRoute();
    },

    updateAria(isCollapsed) {
        document.querySelectorAll('.sidebar-toggle-btn').forEach(btn => {
            btn.setAttribute('aria-expanded', isCollapsed ? 'false' : 'true');
        });
    },

    toggle() {
        const sidebar = document.getElementById('global-sidebar');
        const shell = document.getElementById('app-shell') || document.querySelector('.app-shell');
        const backdrop = document.getElementById('sidebar-backdrop');
        if (!sidebar) return;

        if (window.innerWidth <= 900) {
            const isOpen = sidebar.classList.contains('is-mobile-open');
            if (isOpen) {
                this.closeMobileDrawer();
            } else {
                sidebar.classList.add('is-mobile-open');
                if (backdrop) backdrop.classList.add('is-visible');
            }
        } else {
            const isCurrentlyCollapsed = sidebar.classList.contains('is-collapsed');
            const newCollapsedState = !isCurrentlyCollapsed;

            sidebar.classList.toggle('is-collapsed', newCollapsedState);
            if (shell) {
                shell.setAttribute('data-sidebar-collapsed', newCollapsedState ? 'true' : 'false');
            }
            this.updateAria(newCollapsedState);
            localStorage.setItem(this.STORAGE_KEY, newCollapsedState ? 'true' : 'false');
            localStorage.setItem('pos_sidebar_collapsed', newCollapsedState ? 'true' : 'false');

            // Trigger window resize so responsive layouts / POS recalculate
            window.dispatchEvent(new Event('resize'));
        }
    },

    closeMobileDrawer() {
        const sidebar = document.getElementById('global-sidebar');
        const backdrop = document.getElementById('sidebar-backdrop');
        if (sidebar) sidebar.classList.remove('is-mobile-open');
        if (backdrop) backdrop.classList.remove('is-visible');
    },

    syncActiveRoute() {
        const path = window.location.pathname;
        const links = document.querySelectorAll('.global-sidebar .sidebar-link');
        let matched = false;

        links.forEach(a => {
            const match = a.getAttribute('data-nav-match');
            if (match) {
                if (match === '/dashboard' && (path === '/dashboard/' || path === '/')) {
                    a.classList.add('active');
                    a.setAttribute('aria-current', 'page');
                    matched = true;
                } else if (match !== '/dashboard' && path.startsWith(match)) {
                    a.classList.add('active');
                    a.setAttribute('aria-current', 'page');
                    matched = true;
                } else {
                    a.classList.remove('active');
                    a.removeAttribute('aria-current');
                }
            }
        });
    }
};

/* ── Global Expanding Search Controller (F2) ─────────────────────────────────── */
const GlobalSearchController = {
    init() {
        const panel = document.getElementById('global-search-panel');
        const input = document.getElementById('global-search-input');
        const toggleIcon = document.getElementById('global-search-toggle-icon');
        const closeBtn = document.getElementById('global-search-close-btn');
        const dropdown = document.getElementById('global-search-dropdown');

        if (!panel || !input) return;

        // Open search on panel click
        panel.addEventListener('click', (e) => {
            if (panel.getAttribute('data-open') !== 'true') {
                this.open();
            }
        });

        // Close search button
        if (closeBtn) {
            closeBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                this.close();
            });
        }

        // Live input filtering
        input.addEventListener('input', () => {
            this.handleInput(input.value.trim());
        });

        // Keyboard navigation inside dropdown
        input.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') {
                e.preventDefault();
                e.stopPropagation();
                this.close();
            } else if (e.key === 'ArrowDown') {
                e.preventDefault();
                this.navigateResults(1);
            } else if (e.key === 'ArrowUp') {
                e.preventDefault();
                this.navigateResults(-1);
            } else if (e.key === 'Enter') {
                const selected = dropdown.querySelector('.search-result-item.is-selected');
                if (selected) {
                    e.preventDefault();
                    selected.click();
                }
            }
        });

        // Click outside closes search
        document.addEventListener('click', (e) => {
            if (panel.getAttribute('data-open') === 'true' && !panel.contains(e.target)) {
                this.close();
            }
        });
    },

    open() {
        const panel = document.getElementById('global-search-panel');
        const input = document.getElementById('global-search-input');
        const dropdown = document.getElementById('global-search-dropdown');
        if (!panel || !input) return;

        panel.setAttribute('data-open', 'true');
        if (dropdown) dropdown.style.display = 'block';
        input.focus();
        this.handleInput(input.value.trim());
    },

    close() {
        const panel = document.getElementById('global-search-panel');
        const input = document.getElementById('global-search-input');
        const dropdown = document.getElementById('global-search-dropdown');
        if (!panel) return;

        panel.setAttribute('data-open', 'false');
        if (dropdown) dropdown.style.display = 'none';
        if (input) {
            input.value = '';
            input.blur();
        }
    },

    handleInput(query) {
        const navItems = document.querySelectorAll('#search-nav-items .search-result-item');
        const q = query.toLowerCase();

        navItems.forEach(item => {
            const text = item.textContent.toLowerCase();
            const keywords = (item.getAttribute('data-keywords') || '').toLowerCase();
            const matches = !q || text.includes(q) || keywords.includes(q);
            item.style.display = matches ? 'flex' : 'none';
        });

        // Reset selection
        navItems.forEach(it => it.classList.remove('is-selected'));
        const firstVisible = document.querySelector('#search-nav-items .search-result-item[style*="display: flex"]');
        if (firstVisible) firstVisible.classList.add('is-selected');

        // Optional product query if query has 2+ characters
        this.fetchProducts(q);
    },

    fetchProducts(q) {
        const prodWrap = document.getElementById('search-product-results');
        const prodList = document.getElementById('search-product-list');
        if (!prodWrap || !prodList) return;

        if (!q || q.length < 2) {
            prodWrap.style.display = 'none';
            prodList.innerHTML = '';
            return;
        }

        fetch(`/api/products/?search=${encodeURIComponent(q)}`)
            .then(r => r.json())
            .then(data => {
                const results = data.results || data || [];
                if (Array.isArray(results) && results.length > 0) {
                    prodWrap.style.display = 'block';
                    prodList.innerHTML = results.slice(0, 5).map(p => `
                        <a href="/cart/?add_id=${p.id}" class="search-result-item" style="display:flex;">
                            <i class="bi bi-box-seam"></i>
                            <span>${p.name}</span>
                            <span class="search-result-sub">₹${p.selling_price || '0.00'} · Stock: ${p.current_stock || 0}</span>
                        </a>
                    `).join('');
                } else {
                    prodWrap.style.display = 'none';
                    prodList.innerHTML = '';
                }
            })
            .catch(() => {
                prodWrap.style.display = 'none';
            });
    },

    navigateResults(direction) {
        const items = Array.from(document.querySelectorAll('#global-search-dropdown .search-result-item[style*="display: flex"]'));
        if (!items.length) return;

        let currentIndex = items.findIndex(it => it.classList.contains('is-selected'));
        if (currentIndex === -1) currentIndex = 0;
        else currentIndex = (currentIndex + direction + items.length) % items.length;

        items.forEach(it => it.classList.remove('is-selected'));
        items[currentIndex].classList.add('is-selected');
        items[currentIndex].scrollIntoView({ block: 'nearest' });
    }
};

/* ── Global Keyboard Shortcuts Handler (Safe, non-interfering) ───────────────── */
document.addEventListener("keydown", (event) => {
    const target = event.target;
    const isTyping =
        target instanceof HTMLElement &&
        (
            target.isContentEditable ||
            ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)
        );

    const isPOSRoute = window.location.pathname.startsWith('/cart') || 
                       Boolean(document.getElementById('pos-search-overlay') || document.getElementById('barcode-scanner-input') || document.getElementById('pos-terminal-container'));

    // Escape closes search or mobile sidebar
    if (event.key === "Escape") {
        const searchPanel = document.getElementById('global-search-panel');
        if (searchPanel && searchPanel.getAttribute('data-open') === 'true') {
            event.preventDefault();
            GlobalSearchController.close();
            return;
        }
        SidebarController.closeMobileDrawer();
        return;
    }

    // F2 opens global search (ONLY on non-POS routes, and not while typing)
    if (event.key === "F2" && !event.repeat) {
        if (isPOSRoute) {
            // Strictly yield F2 to POS barcode scanner on the POS terminal
            return;
        }
        if (!isTyping || target.id === 'global-search-input') {
            event.preventDefault();
            GlobalSearchController.open();
        }
    }
});

/* ── Live Cloud Sync Status Polling ───────────────────────────────────────────── */
function initCloudSyncPoll() {
    function updateSyncPill() {
        fetch('/api/sync/status/')
            .then(r => r.json())
            .then(data => {
                if (data && data.sync) {
                    const s = data.sync;
                    const pill = document.getElementById('cloud-sync-nav-pill');
                    const txt = document.getElementById('cloud-sync-nav-text');
                    if (pill && txt) {
                        if (s.is_online && s.pending_count === 0) {
                            pill.style.background = 'rgba(16, 185, 129, 0.12)';
                            pill.style.color = '#34d399';
                            pill.style.borderColor = 'rgba(16, 185, 129, 0.25)';
                            txt.textContent = `Synced: ${s.last_sync_timestamp_display || 'Live'}`;
                        } else if (s.is_online && s.pending_count > 0) {
                            pill.style.background = 'rgba(245, 158, 11, 0.15)';
                            pill.style.color = '#fbbf24';
                            pill.style.borderColor = 'rgba(245, 158, 11, 0.3)';
                            txt.textContent = `Syncing (${s.pending_count} pending)`;
                        } else {
                            pill.style.background = 'rgba(239, 68, 68, 0.15)';
                            pill.style.color = '#f87171';
                            pill.style.borderColor = 'rgba(239, 68, 68, 0.3)';
                            txt.textContent = `Offline (Last: ${s.last_sync_timestamp_display || 'Stale'})`;
                        }
                    }
                }
            })
            .catch(() => {});
    }
    updateSyncPill();
    setInterval(updateSyncPill, 20000);
}

// Document Ready: Wire up shell components
document.addEventListener("DOMContentLoaded", () => {
    const saved = SafeStorage.get("pos_theme", SafeStorage.get("alpha-theme", "forest"));
    applyTheme(saved);

    SidebarController.init();
    GlobalSearchController.init();
    initCloudSyncPoll();
    initSegmentedToggles();

    if (window.ThemeManager) {
        window.ThemeManager.autoMount();
        window.ThemeManager.updateAppearancePageButtons();
    }
});
