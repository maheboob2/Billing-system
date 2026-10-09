/**
 * Alpha Hypermarket POS Terminal Controller
 * Supports Role-Based Cashier Workflow:
 * - Cashier: Focused strictly on Barcode Scanning & Product Preview -> Add to Cart
 * - Manager/Admin: Full Product Catalog, Category Filters, and Live Search
 */

(function () {
    'use strict';

    function uuidv4() {
        return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function (c) {
            var r = Math.random() * 16 | 0, v = c == 'x' ? r : (r & 0x3 | 0x8);
            return v.toString(16);
        });
    }

    function formatINR(val) {
        const num = parseFloat(val) || 0;
        return '₹' + num.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    }

    function getCategoryIcon(catName) {
        if (!catName) return 'bi-box-seam-fill';
        const c = catName.toLowerCase();
        if (c.includes('beverage') || c.includes('drink')) return 'bi-cup-straw';
        if (c.includes('grocer') || c.includes('grain') || c.includes('rice') || c.includes('food')) return 'bi-basket2-fill';
        if (c.includes('dairy') || c.includes('milk') || c.includes('egg')) return 'bi-egg-fried';
        if (c.includes('station') || c.includes('pen') || c.includes('book')) return 'bi-pen-fill';
        if (c.includes('electr') || c.includes('hardware')) return 'bi-plug-fill';
        if (c.includes('care') || c.includes('pharma') || c.includes('health')) return 'bi-heart-pulse-fill';
        if (c.includes('bakery') || c.includes('bread') || c.includes('cake')) return 'bi-cake2-fill';
        return 'bi-box-seam-fill';
    }

    const Cart = {
        items: [],              // Array of { product_id, product_name, product_code, barcode, unit_price, quantity, available_stock, gst_percentage }
        customer: null,         // { id, name, phone, credit_balance }
        discountType: 'FLAT',
        discountValue: 0,
        paymentMethod: 'CASH',
        idempotencyKey: uuidv4(),
        quoteTimer: null,
        searchTimer: null,
        customerTimer: null,
        currentPreview: null,
        barcodeMode: false,
        lastLookupTime: 0,
        lastLookupCode: '',

        init() {
            this.bindCoreEvents();
            this.initBarcodeWorkflow();
            this.initProductSearchWorkflow();
            this.initProductSearchOverlay();
            this.render();
            this.refreshQuote();
            this.initSyncPoll();
        },

        initSyncPoll() {
            const updateBadge = () => {
                fetch('/api/sync/status/')
                    .then(r => r.json())
                    .then(data => {
                        if (data && data.sync) {
                            const s = data.sync;
                            const badge = document.getElementById('cloud-sync-cart-badge');
                            const txt = document.getElementById('cloud-sync-cart-text');
                            if (badge && txt) {
                                if (s.is_online && s.pending_count === 0) {
                                    badge.style.background = 'rgba(16, 185, 129, 0.12)';
                                    badge.style.color = '#34d399';
                                    badge.style.borderColor = 'rgba(16, 185, 129, 0.3)';
                                    txt.textContent = `Synced: ${s.last_sync_timestamp_display}`;
                                } else if (s.is_online && s.pending_count > 0) {
                                    badge.style.background = 'rgba(245, 158, 11, 0.15)';
                                    badge.style.color = '#fbbf24';
                                    badge.style.borderColor = 'rgba(245, 158, 11, 0.35)';
                                    txt.textContent = `Syncing (${s.pending_count} pending)`;
                                } else {
                                    badge.style.background = 'rgba(239, 68, 68, 0.15)';
                                    badge.style.color = '#f87171';
                                    badge.style.borderColor = 'rgba(239, 68, 68, 0.3)';
                                    txt.textContent = `Offline (Last: ${s.last_sync_timestamp_display})`;
                                }
                            }
                        }
                    })
                    .catch(() => {});
            };
            updateBadge();
            setInterval(updateBadge, 25000);
        },

        /* ── SCANNER SETTINGS & AUDIO SYNTHESIS ────────────────────────────── */
        getScannerSettings() {
            try {
                const s = localStorage.getItem('pos_scanner_settings');
                if (s) return JSON.parse(s);
            } catch (e) {}
            return {
                auto_add_cart: true,
                repeat_increment: true,
                show_confirmation: true,
                beep_feedback: true,
                ask_quantity: false
            };
        },

        playScanBeep() {
            try {
                const AudioCtx = window.AudioContext || window.webkitAudioContext;
                if (!AudioCtx) return;
                const ctx = new AudioCtx();
                const osc = ctx.createOscillator();
                const gain = ctx.createGain();
                osc.type = 'sine';
                osc.frequency.setValueAtTime(1760, ctx.currentTime);
                gain.gain.setValueAtTime(0.12, ctx.currentTime);
                gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.08);
                osc.connect(gain);
                gain.connect(ctx.destination);
                osc.start();
                osc.stop(ctx.currentTime + 0.08);
            } catch (e) {}
        },

        playErrorBeep() {
            try {
                const AudioCtx = window.AudioContext || window.webkitAudioContext;
                if (!AudioCtx) return;
                const ctx = new AudioCtx();
                const osc = ctx.createOscillator();
                const gain = ctx.createGain();
                osc.type = 'sawtooth';
                osc.frequency.setValueAtTime(220, ctx.currentTime);
                gain.gain.setValueAtTime(0.15, ctx.currentTime);
                gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.22);
                osc.connect(gain);
                gain.connect(ctx.destination);
                osc.start();
                osc.stop(ctx.currentTime + 0.22);
            } catch (e) {}
        },

        /* ── SCANNER DIAGNOSTICS & UNKNOWN BARCODE MODALS ──────────────────── */
        openScannerStatusModal() {
            const m = document.getElementById('scanner-status-modal');
            if (m) {
                m.style.display = 'flex';
                const inp = document.getElementById('modal-test-scanner-input');
                if (inp) {
                    inp.value = '';
                    setTimeout(() => inp.focus(), 80);
                }
            }
        },

        closeScannerStatusModal() {
            const m = document.getElementById('scanner-status-modal');
            if (m) m.style.display = 'none';
            const scanInput = document.getElementById('barcode-scanner-input');
            if (scanInput) scanInput.focus();
        },

        testModalScannerInput(forcedVal = null) {
            const inp = document.getElementById('modal-test-scanner-input');
            const fb = document.getElementById('modal-test-scan-feedback');
            const val = (forcedVal || (inp ? inp.value : '')).trim();
            if (!val) return;

            if (inp) inp.value = val;
            if (fb) {
                fb.style.display = 'block';
                fb.innerHTML = `⏳ Testing barcode <strong>${val}</strong>...`;
            }

            fetch(`/api/products/lookup/?code=${encodeURIComponent(val)}`)
                .then(r => r.json())
                .then(d => {
                    if (fb) {
                        if (d.found && d.product) {
                            this.playScanBeep();
                            fb.innerHTML = `<span style="color:#34d399;">✅ Valid product: <strong>${d.product.name}</strong> (Stock: ${d.product.current_stock}, Price: ${formatINR(d.product.selling_price)})</span>`;
                        } else {
                            this.playErrorBeep();
                            fb.innerHTML = `<span style="color:#f87171;">❌ No product found with barcode "${val}".</span>`;
                        }
                    }
                })
                .catch(err => {
                    if (fb) fb.innerHTML = `<span style="color:#f87171;">Error testing barcode.</span>`;
                });
        },

        showUnknownBarcodeModal(code) {
            const m = document.getElementById('unknown-barcode-modal');
            const valEl = document.getElementById('unknown-barcode-val');
            if (valEl) valEl.textContent = code;
            if (m) m.style.display = 'flex';
            this.showScanError(`Barcode "${code}" not registered in store catalog.`);
        },

        closeUnknownBarcodeModal() {
            const m = document.getElementById('unknown-barcode-modal');
            if (m) m.style.display = 'none';
            this.openBarcodeInput();
        },

        onUnknownBarcodeManualEntry() {
            this.closeUnknownBarcodeModal();
            this.openBarcodeInput();
        },

        openProductSearchFromUnknown() {
            this.closeUnknownBarcodeModal();
            this.openProductSearchOverlay();
        },

        /* ── DEDICATED POS F2 PRODUCT & BARCODE SEARCH OVERLAY ────────────── */
        openProductSearchOverlay() {
            const overlay = document.getElementById('pos-search-overlay');
            const input = document.getElementById('pos-search-overlay-input');
            if (overlay) {
                overlay.style.display = 'flex';
                if (input) {
                    input.focus();
                    input.select();
                }
                this.loadOverlayProducts(input ? input.value.trim() : '');
            }
        },

        closeProductSearchOverlay() {
            const overlay = document.getElementById('pos-search-overlay');
            if (overlay) {
                overlay.style.display = 'none';
            }
            const previewWrap = document.getElementById('preview-wrapper');
            if (previewWrap) previewWrap.focus();
        },

        initProductSearchOverlay() {
            const input = document.getElementById('pos-search-overlay-input');
            const submitBtn = document.getElementById('btn-pos-search-submit');
            if (input) {
                let debounceTimer = null;
                input.addEventListener('input', () => {
                    clearTimeout(debounceTimer);
                    debounceTimer = setTimeout(() => {
                        this.loadOverlayProducts(input.value.trim());
                    }, 180);
                });

                input.addEventListener('keydown', (e) => {
                    if (e.key === 'Enter') {
                        e.preventDefault();
                        this.submitOverlaySearch();
                    } else if (e.key === 'Escape') {
                        e.preventDefault();
                        this.closeProductSearchOverlay();
                    }
                });
            }
            if (submitBtn) {
                submitBtn.addEventListener('click', (e) => {
                    e.preventDefault();
                    this.submitOverlaySearch();
                });
            }
        },

        loadOverlayProducts(query) {
            const container = document.getElementById('pos-search-results-container');
            const emptyState = document.getElementById('pos-search-empty-state');
            const emptyText = document.getElementById('pos-search-empty-text');
            const countHint = document.getElementById('pos-search-count-hint');
            if (!container) return;

            let url = POS_CONFIG.productsUrl;
            if (query) {
                url += `?search=${encodeURIComponent(query)}`;
            }

            fetch(url)
                .then(r => r.json())
                .then(data => {
                    const list = data.results || (Array.isArray(data) ? data : []);
                    container.innerHTML = '';

                    if (countHint) {
                        countHint.textContent = list.length > 0 ? `${list.length} item(s) found` : '';
                    }

                    if (list.length === 0) {
                        if (emptyState) {
                            emptyState.style.display = 'block';
                            if (emptyText) emptyText.textContent = query ? `No products matching "${query}". Check SKU, name or barcode.` : 'No products found.';
                        }
                        return;
                    }

                    if (emptyState) emptyState.style.display = 'none';

                    list.forEach(p => {
                        const row = document.createElement('div');
                        row.className = 'pos-search-row';
                        row.style.cssText = 'display: flex; align-items: center; justify-content: space-between; padding: 10px 14px; background: var(--surface); border: 1px solid var(--border); border-radius: 6px; cursor: pointer; transition: background 120ms ease, border-color 120ms ease;';

                        const stockNum = parseFloat(p.current_stock || 0);
                        const isOut = stockNum <= 0;
                        const stockTag = isOut 
                            ? `<span style="font-size: 10px; color: var(--danger); font-weight: 700; background: rgba(239, 68, 68, 0.1); padding: 2px 6px; border-radius: 4px;">Out of Stock</span>`
                            : `<span style="font-size: 10px; color: var(--success); font-weight: 600; background: var(--primary-soft); padding: 2px 6px; border-radius: 4px;">${stockNum} ${p.unit || 'pcs'}</span>`;

                        row.innerHTML = `
                            <div style="flex: 1; min-width: 0; padding-right: 12px;">
                                <div style="font-size: 13px; font-weight: 700; color: var(--text); overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${p.name}</div>
                                <div style="font-size: 11px; color: var(--muted); margin-top: 2px; display: flex; gap: 8px; align-items: center;">
                                    <span>SKU: ${p.product_code || '—'}</span>
                                    ${p.barcode ? `<span>· Barcode: <strong style="font-family: var(--font-mono);">${p.barcode}</strong></span>` : ''}
                                    ${stockTag}
                                </div>
                            </div>
                            <div style="display: flex; align-items: center; gap: 10px;">
                                <span style="font-size: 14px; font-weight: 700; color: var(--primary); font-family: var(--font-mono);">${formatINR(p.selling_price)}</span>
                                <button type="button" class="btn-add-search-row" style="padding: 5px 12px; font-size: 12px; font-weight: 600; background: var(--primary); color: #ffffff; border: none; border-radius: 4px; cursor: pointer;">
                                    Add
                                </button>
                            </div>
                        `;

                        row.addEventListener('mouseenter', () => {
                            row.style.background = 'var(--surface-2)';
                            row.style.borderColor = 'var(--primary)';
                        });
                        row.addEventListener('mouseleave', () => {
                            row.style.background = 'var(--surface)';
                            row.style.borderColor = 'var(--border)';
                        });

                        row.addEventListener('click', () => {
                            if (isOut) {
                                this.playErrorBeep();
                                this.showToast(`Product "${p.name}" is out of stock.`, 'error');
                                return;
                            }
                            this.addItem(p, 1);
                            this.showProductPreview(p);
                            this.closeProductSearchOverlay();
                        });

                        container.appendChild(row);
                    });
                })
                .catch(() => {
                    if (emptyState) {
                        emptyState.style.display = 'block';
                        if (emptyText) emptyText.textContent = 'Error loading products.';
                    }
                });
        },

        submitOverlaySearch() {
            const input = document.getElementById('pos-search-overlay-input');
            const val = input ? input.value.trim() : '';
            if (!val) return;

            // First check if it matches an exact barcode lookup
            fetch(`/api/products/lookup/?code=${encodeURIComponent(val)}`)
                .then(r => r.json())
                .then(d => {
                    if (d.found && d.product) {
                        this.playScanBeep();
                        this.addItem(d.product, 1);
                        this.showProductPreview(d.product);
                        this.closeProductSearchOverlay();
                        if (input) input.value = '';
                    } else {
                        // Query search API
                        fetch(`${POS_CONFIG.productsUrl}?search=${encodeURIComponent(val)}`)
                            .then(r => r.json())
                            .then(data => {
                                const list = data.results || (Array.isArray(data) ? data : []);
                                if (list.length === 1) {
                                    const p = list[0];
                                    const stockNum = parseFloat(p.current_stock || 0);
                                    if (stockNum > 0) {
                                        this.playScanBeep();
                                        this.addItem(p, 1);
                                        this.showProductPreview(p);
                                        this.closeProductSearchOverlay();
                                        if (input) input.value = '';
                                        return;
                                    }
                                }
                                this.loadOverlayProducts(val);
                            })
                            .catch(() => {
                                this.loadOverlayProducts(val);
                            });
                    }
                })
                .catch(() => {
                    this.loadOverlayProducts(val);
                });
        },

        /* ── DEDICATED BARCODE SCANNER WORKFLOW ───────────────────────────── */
        openBarcodeInput() {
            this.openProductSearchOverlay();
        },

        closeBarcodeInput() {
            this.closeProductSearchOverlay();
        },

        initBarcodeToggle() {
            const toggle = document.getElementById('toggle-barcode-input');
            if (toggle) {
                toggle.addEventListener('click', () => {
                    this.openProductSearchOverlay();
                });
            }
        },

        initBarcodeWorkflow() {
            this.initBarcodeToggle();
            const input = document.getElementById('barcode-scanner-input');
            const submitBtn = document.getElementById('btn-submit-scan');
            const addBtn = document.getElementById('btn-add-preview');
            const qtyMinus = document.getElementById('preview-qty-minus');
            const qtyPlus = document.getElementById('preview-qty-plus');
            const qtyInput = document.getElementById('preview-qty-val');
            const modalTestInput = document.getElementById('modal-test-scanner-input');

            if (modalTestInput) {
                modalTestInput.addEventListener('keydown', (e) => {
                    if (e.key === 'Enter') {
                        e.preventDefault();
                        this.testModalScannerInput();
                    }
                });
            }

            if (input) {
                // Barcode input strictly accepts Enter or HID bursts - NO AUTOCOMPLETE / FUZZY SUGGESTIONS
                input.addEventListener('keydown', (e) => {
                    if (e.key === 'Enter') {
                        e.preventDefault();
                        const val = input.value.trim();
                        input.value = '';
                        if (val) {
                            this.lookupBarcode(val);
                        } else if (this.currentPreview) {
                            this.addCurrentPreviewToCart();
                        }
                    }
                });
            }

            if (submitBtn) {
                submitBtn.addEventListener('click', () => {
                    if (input && input.value.trim()) {
                        const val = input.value.trim();
                        input.value = '';
                        this.lookupBarcode(val);
                    }
                });
            }

            if (addBtn) {
                addBtn.addEventListener('click', () => {
                    this.addCurrentPreviewToCart();
                });
            }

            if (qtyMinus && qtyInput) {
                qtyMinus.addEventListener('click', () => {
                    let v = parseInt(qtyInput.value) || 1;
                    if (v > 1) qtyInput.value = v - 1;
                });
            }

            if (qtyPlus && qtyInput) {
                qtyPlus.addEventListener('click', () => {
                    if (!this.currentPreview) return;
                    const avail = parseFloat(this.currentPreview.current_stock !== undefined ? this.currentPreview.current_stock : (this.currentPreview.available_stock || 0));
                    const existing = this.items.find(it => it.product_id === this.currentPreview.id);
                    const inCart = existing ? existing.quantity : 0;
                    const remaining = Math.max(0, avail - inCart);
                    let v = parseInt(qtyInput.value) || 1;
                    if (v >= remaining) {
                        this.playErrorBeep();
                        const msg = `Only ${avail} units available.`;
                        this.showScanError(msg);
                        this.showToast(msg, 'error');
                        return;
                    }
                    qtyInput.value = v + 1;
                });
            }

            if (qtyInput) {
                qtyInput.addEventListener('change', () => {
                    if (!this.currentPreview) return;
                    const avail = parseFloat(this.currentPreview.current_stock !== undefined ? this.currentPreview.current_stock : (this.currentPreview.available_stock || 0));
                    const existing = this.items.find(it => it.product_id === this.currentPreview.id);
                    const inCart = existing ? existing.quantity : 0;
                    const remaining = Math.max(0, avail - inCart);
                    let v = parseInt(qtyInput.value) || 1;
                    if (v > remaining) {
                        this.playErrorBeep();
                        qtyInput.value = remaining;
                        const msg = `Only ${avail} units available.`;
                        this.showScanError(msg);
                        this.showToast(msg, 'error');
                    } else if (v < 1) {
                        qtyInput.value = 1;
                    }
                });

                qtyInput.addEventListener('keydown', (e) => {
                    if (e.key === 'Enter') {
                        e.preventDefault();
                        this.addCurrentPreviewToCart();
                    }
                });
            }
        },

        lookupBarcode(code) {
            if (!code) return;
            const cleanCode = code.trim();
            if (!cleanCode) return;

            // Prevent rapid double triggers within 220ms
            const now = Date.now();
            if (this.lastLookupCode === cleanCode && (now - this.lastLookupTime) < 220) {
                return;
            }
            this.lastLookupCode = cleanCode;
            this.lastLookupTime = now;

            // Update diagnostics display
            const modalLastScanned = document.getElementById('modal-last-scanned-val');
            if (modalLastScanned) modalLastScanned.textContent = cleanCode;

            const feedback = document.getElementById('scan-feedback');
            if (feedback) {
                feedback.className = 'scan-feedback-banner';
                feedback.style.display = 'none';
                feedback.textContent = '';
            }

            const settings = this.getScannerSettings();

            // Authoritative exact lookup by barcode or SKU
            fetch(`/api/products/lookup/?code=${encodeURIComponent(cleanCode)}`)
                .then(r => r.json().then(data => ({ ok: r.ok, status: r.status, data })))
                .then(({ ok, status, data }) => {
                    if (ok && data.found && data.product) {
                        const prod = data.product;
                        if (prod.status === false) {
                            this.playErrorBeep();
                            this.showScanError(`Product '${prod.name}' is inactive and cannot be sold.`);
                            return;
                        }

                        const availableStock = parseFloat(prod.current_stock || 0);
                        if (availableStock <= 0) {
                            this.playErrorBeep();
                            this.showScanError(`Product '${prod.name}' is out of stock (Available: 0).`);
                            this.showProductPreview(prod);
                            return;
                        }

                        // Strict in-cart stock limit enforcement
                        const existing = this.items.find(it => it.product_id === prod.id);
                        const currentInCart = existing ? existing.quantity : 0;
                        if (currentInCart >= availableStock) {
                            this.playErrorBeep();
                            const msg = `Only ${availableStock} units available.`;
                            this.showScanError(msg);
                            this.showToast(msg, 'error');
                            this.showProductPreview(prod);
                            return;
                        }

                        if (settings.beep_feedback) {
                            this.playScanBeep();
                        }

                        if (settings.ask_quantity) {
                            const remaining = Math.max(0, availableStock - currentInCart);
                            const qtyStr = prompt(`Enter quantity for "${prod.name}" (Max: ${remaining}):`, "1");
                            const qty = parseInt(qtyStr) || 1;
                            if (qty > 0) {
                                if (currentInCart + qty > availableStock) {
                                    this.playErrorBeep();
                                    const msg = `Only ${availableStock} units available.`;
                                    this.showScanError(msg);
                                    this.showToast(msg, 'error');
                                    this.showProductPreview(prod);
                                    return;
                                }
                                this.addOrIncrementProduct(prod, qty, settings);
                            }
                        } else {
                            this.addOrIncrementProduct(prod, 1, settings);
                        }
                    } else if (data && data.inactive) {
                        this.playErrorBeep();
                        this.showScanError(data.error || 'Product is inactive and cannot be sold.');
                    } else {
                        // NO FUZZY FALLBACK SEARCH - Exact lookup failed
                        this.playErrorBeep();
                        this.showUnknownBarcodeModal(cleanCode);
                    }
                })
                .catch(err => {
                    console.error('Scan lookup error:', err);
                    this.playErrorBeep();
                    this.showScanError('Error searching barcode. Please retry.');
                });
        },

        addOrIncrementProduct(prod, qty = 1, settings = null) {
            if (!settings) settings = this.getScannerSettings();
            const availableStock = parseFloat(prod.current_stock !== undefined ? prod.current_stock : (prod.available_stock || 0));
            const existing = this.items.find(it => it.product_id === prod.id);
            const currentQty = existing ? existing.quantity : 0;
            const remainingStock = Math.max(0, availableStock - currentQty);

            // Strict Stock Boundary Validation
            if (currentQty >= availableStock) {
                this.playErrorBeep();
                const msg = `Only ${availableStock} units available.`;
                this.showScanError(msg);
                this.showToast(msg, 'error');
                this.showProductPreview(prod);
                return false;
            }

            if (currentQty + qty > availableStock) {
                this.playErrorBeep();
                const msg = `Only ${availableStock} units available.`;
                this.showScanError(msg);
                this.showToast(msg, 'error');
                this.showProductPreview(prod);
                return false;
            }

            if (existing) {
                if (settings.repeat_increment) {
                    existing.quantity += qty;
                    existing.available_stock = availableStock;
                } else {
                    this.showToast(`Product '${prod.name}' is already in cart.`, 'info');
                    return false;
                }
            } else {
                this.items.push({
                    product_id: prod.id,
                    product_name: prod.name,
                    product_code: prod.product_code,
                    barcode: prod.barcode || '',
                    unit_price: parseFloat(prod.selling_price) || 0,
                    quantity: qty,
                    available_stock: availableStock,
                    gst_percentage: parseFloat(prod.gst_percentage) || 0,
                });
            }

            this.render();
            this.refreshQuote();

            // Populate preview card with real-time stock stats
            this.showProductPreview(prod);

            const totalQty = existing ? existing.quantity : qty;
            const totalItemPrice = (parseFloat(prod.selling_price) * totalQty).toFixed(2);
            const remainingAfterAdd = Math.max(0, availableStock - totalQty);

            // Display non-blocking feedback if enabled
            if (settings.show_confirmation) {
                const feedback = document.getElementById('scan-feedback');
                if (feedback) {
                    feedback.className = 'scan-feedback-banner succ';
                    feedback.innerHTML = `<i class="bi bi-check-circle-fill"></i> Added <strong>${prod.name}</strong> × ${totalQty} (Stock: ${availableStock}, Remaining: ${remainingAfterAdd}) &mdash; ${formatINR(totalItemPrice)}`;
                    feedback.style.display = 'block';
                }

                const notice = document.getElementById('preview-added-notice');
                if (notice) {
                    notice.style.display = 'flex';
                    setTimeout(() => { if (notice) notice.style.display = 'none'; }, 2200);
                }
            }

            // Update last-scanned display
            const lastScanned = document.getElementById('last-scanned-display');
            if (lastScanned) {
                lastScanned.className = 'recent-item';
                lastScanned.innerHTML = `<strong>${prod.name}</strong> × ${totalQty} (${formatINR(totalItemPrice)})`;
            }

            // Refocus barcode input immediately for subsequent scans (HID scanner support)
            const scanInput = document.getElementById('barcode-scanner-input');
            if (scanInput) {
                scanInput.value = '';
                scanInput.focus();
            }
            return true;
        },

        showScanError(msg) {
            const feedback = document.getElementById('scan-feedback');
            if (feedback) {
                feedback.className = 'scan-feedback-banner err';
                feedback.textContent = msg;
                feedback.style.display = 'block';
            }
            const input = document.getElementById('barcode-scanner-input');
            if (input) {
                input.select();
                input.focus();
            }
        },

        showProductPreview(prod) {
            this.currentPreview = prod;
            const idle = document.getElementById('preview-idle-box');
            const card = document.getElementById('product-preview-card');

            if (idle) idle.style.display = 'none';
            if (card) card.style.display = 'flex';

            // Populate preview details
            const nameEl = document.getElementById('preview-name');
            const skuEl = document.getElementById('preview-sku');
            const bcEl = document.getElementById('preview-barcode');
            const catEl = document.getElementById('preview-category');
            const priceEl = document.getElementById('preview-price');
            const gstEl = document.getElementById('preview-gst');
            const stockEl = document.getElementById('preview-stock');
            const unitEl = document.getElementById('preview-unit');
            const iconEl = document.getElementById('preview-icon');
            const qtyInput = document.getElementById('preview-qty-val');
            const cartRemEl = document.getElementById('preview-cart-remaining');
            const addBtn = document.getElementById('btn-add-preview');
            const plusBtn = document.getElementById('preview-qty-plus');

            const available = parseFloat(prod.current_stock !== undefined ? prod.current_stock : (prod.available_stock || 0));
            const existing = this.items.find(it => it.product_id === prod.id);
            const inCart = existing ? existing.quantity : 0;
            const remaining = Math.max(0, available - inCart);

            if (nameEl) nameEl.textContent = prod.name;
            if (skuEl) skuEl.textContent = prod.product_code;
            if (bcEl) bcEl.textContent = prod.barcode || '—';
            if (catEl) catEl.textContent = prod.category_name || 'General';
            if (priceEl) priceEl.textContent = formatINR(prod.selling_price);
            if (gstEl) gstEl.textContent = `${parseFloat(prod.gst_percentage || 0).toFixed(2)}%`;
            if (stockEl) stockEl.textContent = `${available.toFixed(0)} ${prod.unit || 'pcs'}`;
            if (unitEl) unitEl.textContent = prod.unit || 'pcs';

            if (cartRemEl) {
                cartRemEl.innerHTML = `<span style="color:${inCart > 0 ? '#38bdf8' : 'var(--text-muted)'}">${inCart} in cart</span> · <span style="font-weight:700;color:${remaining > 0 ? '#34d399' : '#f87171'}">${remaining} rem</span>`;
            }

            if (remaining <= 0) {
                if (qtyInput) {
                    qtyInput.value = 0;
                    qtyInput.disabled = true;
                }
                if (plusBtn) plusBtn.disabled = true;
                if (addBtn) {
                    addBtn.disabled = true;
                    addBtn.innerHTML = '<i class="bi bi-slash-circle"></i> <span>MAX STOCK IN CART</span>';
                }
            } else {
                if (qtyInput) {
                    qtyInput.disabled = false;
                    qtyInput.value = 1;
                    qtyInput.max = remaining;
                }
                if (plusBtn) plusBtn.disabled = false;
                if (addBtn) {
                    addBtn.disabled = false;
                    addBtn.innerHTML = '<i class="bi bi-cart-plus-fill"></i> <span>ADD TO CART</span> <kbd>↵</kbd>';
                }
            }

            if (iconEl) {
                iconEl.className = `bi ${getCategoryIcon(prod.category_name)}`;
            }
        },

        addCurrentPreviewToCart() {
            if (!this.currentPreview) return;
            const prod = this.currentPreview;
            const available = parseFloat(prod.current_stock !== undefined ? prod.current_stock : (prod.available_stock || 0));
            const existing = this.items.find(it => it.product_id === prod.id);
            const inCart = existing ? existing.quantity : 0;
            const remaining = Math.max(0, available - inCart);

            if (remaining <= 0) {
                this.playErrorBeep();
                const msg = `Only ${available} units available.`;
                this.showScanError(msg);
                this.showToast(msg, 'error');
                return;
            }

            const qtyInput = document.getElementById('preview-qty-val');
            let qty = parseInt(qtyInput ? qtyInput.value : 1) || 1;
            if (qty > remaining) {
                qty = remaining;
                if (qtyInput) qtyInput.value = qty;
            }
            if (qty <= 0) return;

            this.addOrIncrementProduct(prod, qty);
        },

        /* ── SEPARATE PRODUCT SEARCH WORKFLOW ──────────────────────────────── */
        initProductSearchWorkflow() {
            const searchInput = document.getElementById('product-search');
            const barcodeBtn = document.getElementById('btn-toggle-barcode');

            if (searchInput) {
                searchInput.addEventListener('input', () => {
                    clearTimeout(this.searchTimer);
                    this.searchTimer = setTimeout(() => {
                        this.searchProducts(searchInput.value.trim());
                    }, 200);
                });

                searchInput.addEventListener('keydown', (e) => {
                    if (e.key === 'Enter') {
                        e.preventDefault();
                        const val = searchInput.value.trim();
                        if (val) {
                            this.searchProducts(val, false);
                        }
                    }
                });
            }

            if (barcodeBtn) {
                barcodeBtn.addEventListener('click', () => {
                    this.barcodeMode = !this.barcodeMode;
                    barcodeBtn.classList.toggle('active', this.barcodeMode);
                    if (searchInput) {
                        searchInput.placeholder = this.barcodeMode
                            ? 'Search by barcode…'
                            : 'Search products by name / SKU… (F2)';
                        searchInput.focus();
                    }
                });
            }

            this.loadCategories();
            this.loadCatalogProducts();
        },

        loadCategories() {
            const container = document.getElementById('category-pills');
            if (!container) return;

            fetch(POS_CONFIG.categoriesUrl)
                .then(r => r.json())
                .then(data => {
                    const list = data.results || (Array.isArray(data) ? data : []);
                    list.forEach(cat => {
                        const btn = document.createElement('button');
                        btn.type = 'button';
                        btn.className = 'cat-chip';
                        btn.textContent = cat.name;
                        btn.dataset.cat = cat.id;
                        btn.addEventListener('click', () => {
                            container.querySelectorAll('.cat-chip').forEach(c => c.classList.remove('active'));
                            btn.classList.add('active');
                            this.loadCatalogProducts(cat.id);
                        });
                        container.appendChild(btn);
                    });
                })
                .catch(() => {});
        },

        loadCatalogProducts(categoryId = null) {
            const grid = document.getElementById('catalog-items-grid');
            if (!grid) return;

            let url = POS_CONFIG.productsUrl;
            if (categoryId) url += `?category=${categoryId}`;

            fetch(url)
                .then(r => r.json())
                .then(data => {
                    const list = data.results || (Array.isArray(data) ? data : []);
                    this.renderCatalogCards(list, grid);
                })
                .catch(() => {});
        },

        searchProducts(query, autoAdd = false) {
            const resultsBox = document.getElementById('search-results');
            const grid = document.getElementById('catalog-items-grid');
            const spinner = document.getElementById('search-spinner');

            if (!query) {
                if (resultsBox) resultsBox.style.display = 'none';
                if (grid) grid.style.display = 'flex';
                return;
            }

            if (spinner) spinner.style.display = 'inline-block';

            let url = this.barcodeMode
                ? `${POS_CONFIG.productsUrl}?barcode=${encodeURIComponent(query)}`
                : `${POS_CONFIG.productsUrl}?search=${encodeURIComponent(query)}`;

            fetch(url)
                .then(r => r.json())
                .then(data => {
                    if (spinner) spinner.style.display = 'none';
                    const list = data.results || (Array.isArray(data) ? data : []);

                    if (resultsBox) {
                        resultsBox.innerHTML = '';
                        if (list.length === 0) {
                            resultsBox.innerHTML = '<div style="padding:12px;text-align:center;color:var(--text-dim);font-size:12px;">No matching products found.</div>';
                        } else {
                            this.renderCatalogCards(list, resultsBox);
                        }
                        resultsBox.style.display = 'flex';
                    }
                    if (grid) grid.style.display = 'none';
                })
                .catch(() => {
                    if (spinner) spinner.style.display = 'none';
                });
        },

        renderCatalogCards(products, targetContainer) {
            targetContainer.innerHTML = '';
            products.forEach(p => {
                const card = document.createElement('div');
                card.className = 'catalog-card';
                const stockNum = parseFloat(p.current_stock || 0);
                const existing = this.items.find(it => it.product_id === p.id);
                const inCart = existing ? existing.quantity : 0;
                const remaining = Math.max(0, stockNum - inCart);
                const isMax = remaining <= 0 || stockNum <= 0;
                const isLow = stockNum <= parseFloat(p.minimum_stock || 0);
                const stockClass = isLow ? 'stock-tag low' : 'stock-tag ok';

                card.innerHTML = `
                    <div class="catalog-card-info">
                        <div class="name">${p.name}</div>
                        <div class="meta">
                            <span>${p.product_code}</span>
                            ${p.barcode ? `<span>· ${p.barcode}</span>` : ''}
                            <span class="${stockClass}">${stockNum.toFixed(0)} ${p.unit || 'pcs'}</span>
                            ${inCart > 0 ? `<span style="color:#38bdf8;font-size:10px;">(${inCart} in cart)</span>` : ''}
                        </div>
                    </div>
                    <div class="catalog-card-pricing">
                        <div class="price">${formatINR(p.selling_price)}</div>
                        <button type="button" class="btn-quick-add" ${isMax ? 'disabled style="opacity:0.4;cursor:not-allowed;"' : ''}>
                            <i class="bi ${isMax ? 'bi-slash-circle' : 'bi-plus-lg'}"></i> ${isMax ? (stockNum <= 0 ? 'Out' : 'Max') : 'Add'}
                        </button>
                    </div>
                `;

                card.addEventListener('click', (e) => {
                    e.stopPropagation();
                    if (isMax) {
                        this.playErrorBeep();
                        this.showToast(stockNum <= 0 ? `Product '${p.name}' is out of stock.` : `Only ${stockNum} units available.`, 'error');
                        this.showProductPreview(p);
                        return;
                    }
                    this.addItem(p, 1);
                });

                targetContainer.appendChild(card);
            });
        },

        /* ── SHARED CART ITEM OPERATIONS ───────────────────────────────────── */
        addItem(product, qty = 1) {
            const availableStock = parseFloat(product.current_stock !== undefined ? product.current_stock : (product.available_stock || 0));
            const existing = this.items.find(it => it.product_id === product.id);
            const currentQty = existing ? existing.quantity : 0;

            if (currentQty >= availableStock) {
                this.playErrorBeep();
                const msg = `Only ${availableStock} units available.`;
                this.showScanError(msg);
                this.showToast(msg, 'error');
                this.showProductPreview(product);
                return false;
            }

            if (currentQty + qty > availableStock) {
                this.playErrorBeep();
                const msg = `Only ${availableStock} units available.`;
                this.showScanError(msg);
                this.showToast(msg, 'error');
                this.showProductPreview(product);
                return false;
            }

            return this.addOrIncrementProduct(product, qty);
        },

        updateQty(productId, newQty) {
            const item = this.items.find(it => it.product_id === productId);
            if (!item) return;

            const q = parseInt(newQty) || 0;
            if (q <= 0) {
                this.removeItem(productId);
                return;
            }

            if (q > item.available_stock) {
                this.playErrorBeep();
                item.quantity = item.available_stock;
                const msg = `Only ${item.available_stock} units available.`;
                this.showScanError(msg);
                this.showToast(msg, 'error');
                this.render();
                this.refreshQuote();
                if (this.currentPreview && this.currentPreview.id === productId) {
                    this.showProductPreview(this.currentPreview);
                }
                return;
            }

            item.quantity = q;
            this.render();
            this.refreshQuote();
            if (this.currentPreview && this.currentPreview.id === productId) {
                this.showProductPreview(this.currentPreview);
            }
        },

        removeItem(productId) {
            this.items = this.items.filter(it => it.product_id !== productId);
            this.render();
            this.refreshQuote();
            if (this.currentPreview && this.currentPreview.id === productId) {
                this.showProductPreview(this.currentPreview);
            }
        },

        clear() {
            this.items = [];
            this.render();
            this.refreshQuote();
            if (this.currentPreview) {
                this.showProductPreview(this.currentPreview);
            }
            const cashierInput = document.getElementById('barcode-scanner-input');
            if (cashierInput) cashierInput.focus();
        },

        /* ── RENDERING ACTIVE CART TABLE ───────────────────────────────────── */
        render() {
            const tbody = document.getElementById('cart-tbody');
            const countBadge = document.getElementById('cart-item-count');

            if (!tbody) return;

            const totalQty = this.items.reduce((s, it) => s + it.quantity, 0);
            if (countBadge) {
                countBadge.textContent = `${totalQty} ${totalQty === 1 ? 'item' : 'items'}`;
            }

            if (this.items.length === 0) {
                tbody.innerHTML = `
                    <tr id="empty-row">
                        <td colspan="7">
                            <div class="empty-cart-view">
                                <div class="empty-cart-icon">
                                    <i class="bi bi-upc-scan"></i>
                                </div>
                                <div class="empty-cart-title">Register is Empty</div>
                                <div class="empty-cart-sub">
                                    Scan barcode or search product by name on the left.
                                </div>
                            </div>
                        </td>
                    </tr>
                `;
                return;
            }

            tbody.innerHTML = '';
            this.items.forEach((it, idx) => {
                const tr = document.createElement('tr');
                const rowTotal = it.unit_price * it.quantity;
                const estTax = rowTotal * (it.gst_percentage / 100);
                const isMax = it.quantity >= it.available_stock;
                const remaining = Math.max(0, it.available_stock - it.quantity);

                tr.innerHTML = `
                    <td class="col-index">${idx + 1}</td>
                    <td class="col-prod">
                        <div class="pname">${it.product_name}</div>
                        <div class="pcode-sub">${it.product_code}${it.barcode ? ' · ' + it.barcode : ''}</div>
                        <div class="qty-stock-hint ${isMax ? 'maxed' : ''}">Stock: ${it.available_stock} · Rem: ${remaining}</div>
                    </td>
                    <td class="col-price">${formatINR(it.unit_price)}</td>
                    <td class="col-qty">
                        <div class="qty-ctrl">
                            <button type="button" class="btn-qty-dec">-</button>
                            <input type="number" class="qty-input" value="${it.quantity}" min="1" max="${it.available_stock}">
                            <button type="button" class="btn-qty-inc" ${isMax ? 'disabled title="Only ' + it.available_stock + ' units available."' : ''}>+</button>
                        </div>
                    </td>
                    <td class="col-tax">${formatINR(estTax)}</td>
                    <td class="col-total">${formatINR(rowTotal)}</td>
                    <td class="col-action">
                        <button type="button" class="btn-remove" title="Remove item">
                            <i class="bi bi-trash3"></i>
                        </button>
                    </td>
                `;

                tr.querySelector('.btn-qty-dec').addEventListener('click', () => {
                    this.updateQty(it.product_id, it.quantity - 1);
                });

                const incBtn = tr.querySelector('.btn-qty-inc');
                if (!isMax) {
                    incBtn.addEventListener('click', () => {
                        this.updateQty(it.product_id, it.quantity + 1);
                    });
                } else {
                    incBtn.addEventListener('click', (e) => {
                        e.preventDefault();
                        this.playErrorBeep();
                        this.showToast(`Only ${it.available_stock} units available.`, 'error');
                    });
                }

                const qInput = tr.querySelector('.qty-input');
                qInput.addEventListener('change', () => {
                    this.updateQty(it.product_id, qInput.value);
                });

                tr.querySelector('.btn-remove').addEventListener('click', () => {
                    this.removeItem(it.product_id);
                });

                tbody.appendChild(tr);
            });
        },

        /* ── AUTHORITATIVE QUOTE ENGINE ────────────────────────────────────── */
        refreshQuote() {
            clearTimeout(this.quoteTimer);
            this.quoteTimer = setTimeout(() => {
                this.executeQuote();
            }, 100);
        },

        executeQuote() {
            const subtotalEl = document.getElementById('tot-subtotal');
            const discountEl = document.getElementById('tot-discount');
            const taxEl = document.getElementById('tot-tax');
            const grandEl = document.getElementById('tot-grand');

            if (this.items.length === 0) {
                if (subtotalEl) subtotalEl.textContent = '₹0.00';
                if (discountEl) discountEl.textContent = '-₹0.00';
                if (taxEl) taxEl.textContent = '₹0.00';
                if (grandEl) grandEl.textContent = '₹0.00';
                this.updateTenderDefaults(0);
                return;
            }

            const discTypeEl = document.getElementById('discount-type');
            const discValEl = document.getElementById('discount-value');
            const discType = discTypeEl ? discTypeEl.value : 'FLAT';
            const discVal = parseFloat(discValEl ? discValEl.value : 0) || 0;

            const payload = {
                items: this.items.map(it => ({
                    product_id: it.product_id,
                    quantity: it.quantity,
                    unit_price: it.unit_price
                })),
                discount_type: discType,
                discount_value: discVal
            };

            fetch(POS_CONFIG.quoteUrl, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': POS_CONFIG.csrfToken
                },
                body: JSON.stringify(payload)
            })
                .then(r => r.json())
                .then(data => {
                    const q = data.quote || data;
                    if (q && q.grand_total !== undefined) {
                        if (subtotalEl) subtotalEl.textContent = formatINR(q.subtotal);
                        if (discountEl) discountEl.textContent = '-' + formatINR(q.discount_amount);
                        if (taxEl) taxEl.textContent = formatINR(q.tax_amount !== undefined ? q.tax_amount : q.total_tax);
                        if (grandEl) grandEl.textContent = formatINR(q.grand_total);
                        this.lastGrandTotal = parseFloat(q.grand_total);
                        this.updateTenderDefaults(this.lastGrandTotal);
                    }
                })
                .catch(err => {
                    console.error('Quote error:', err);
                });
        },

        updateTenderDefaults(grandTotal) {
            const payInput = document.getElementById('pay-amount');
            if (payInput && (this.paymentMethod === 'CASH' || this.paymentMethod === 'CREDIT')) {
                payInput.value = grandTotal > 0 ? grandTotal.toFixed(2) : '';
            }
        },

        /* ── CUSTOMER MANAGEMENT ───────────────────────────────────────────── */
        selectCustomer(cust) {
            this.customer = cust;
            const input = document.getElementById('customer-input');
            const dropdown = document.getElementById('customer-dropdown');
            const card = document.getElementById('selected-customer-card');
            const nameEl = document.getElementById('sc-name');
            const creditEl = document.getElementById('sc-credit');

            if (input) input.value = '';
            if (dropdown) dropdown.style.display = 'none';

            if (card && nameEl && creditEl) {
                nameEl.textContent = `${cust.name} · ${cust.phone}`;
                creditEl.textContent = `Credit: ${formatINR(cust.credit_balance || 0)} (Limit: ${formatINR(cust.credit_limit || 0)})`;
                card.style.display = 'flex';
            }

            const creditBox = document.getElementById('credit-info');
            if (creditBox && this.paymentMethod === 'CREDIT') {
                creditBox.style.display = 'none';
            }
        },

        clearCustomer() {
            this.customer = null;
            const card = document.getElementById('selected-customer-card');
            if (card) card.style.display = 'none';

            const input = document.getElementById('customer-input');
            if (input) input.focus();

            if (this.paymentMethod === 'CREDIT') {
                const creditBox = document.getElementById('credit-info');
                if (creditBox) creditBox.style.display = 'block';
            }
        },

        /* ── PAYMENT & TENDER METHOD ───────────────────────────────────────── */
        selectPayMethod(method) {
            this.paymentMethod = method;
            document.querySelectorAll('.pay-tab').forEach(tab => {
                tab.classList.toggle('active', tab.dataset.method === method);
            });

            const refWrap = document.getElementById('ref-input-wrap');
            const qcChips = document.getElementById('quick-cash-chips');
            const creditInfo = document.getElementById('credit-info');
            const payInput = document.getElementById('pay-amount');
            const upiBox = document.getElementById('upi-verification-box');

            if (refWrap) {
                refWrap.style.display = (method === 'CARD') ? 'block' : 'none';
            }
            if (qcChips) {
                qcChips.style.display = (method === 'CASH') ? 'flex' : 'none';
            }
            if (creditInfo) {
                creditInfo.style.display = (method === 'CREDIT' && !this.customer) ? 'block' : 'none';
            }

            if (upiBox) {
                if (method === 'UPI') {
                    upiBox.style.display = 'block';
                    this.upiConfirmed = false;
                    const grand = this.lastGrandTotal || 0;
                    const upiAmt = document.getElementById('upi-amount-display');
                    if (upiAmt) upiAmt.textContent = formatINR(grand);

                    const upiBadge = document.getElementById('upi-status-badge');
                    if (upiBadge) {
                        upiBadge.style.background = 'rgba(245, 158, 11, 0.15)';
                        upiBadge.style.color = '#f59e0b';
                        upiBadge.textContent = '⏳ AWAITING PAYMENT';
                    }

                    const upiUri = `upi://pay?pa=merchant@upi&pn=RetailPOS&am=${grand.toFixed(2)}&cu=INR&tn=POS-${this.idempotencyKey.slice(0,8)}`;
                    const upiImg = document.getElementById('upi-qr-image');
                    if (upiImg) {
                        upiImg.src = `https://api.qrserver.com/v1/create-qr-code/?size=100x100&data=${encodeURIComponent(upiUri)}`;
                    }
                } else {
                    upiBox.style.display = 'none';
                }
            }

            if (payInput && this.lastGrandTotal) {
                payInput.value = this.lastGrandTotal.toFixed(2);
            }
        },

        markUpiReceived() {
            this.upiConfirmed = true;
            const refInput = document.getElementById('upi-ref-input');
            const ref = (refInput && refInput.value.trim()) ? refInput.value.trim() : ('UPI-' + Date.now().toString().slice(-6));
            
            const payRef = document.getElementById('pay-ref');
            if (payRef) payRef.value = ref;

            const upiBadge = document.getElementById('upi-status-badge');
            if (upiBadge) {
                upiBadge.style.background = 'rgba(16, 185, 129, 0.2)';
                upiBadge.style.color = '#34d399';
                upiBadge.textContent = `✅ VERIFIED (${ref})`;
            }

            this.showToast(`UPI Payment marked received! Ref: ${ref}`, true);
        },

        /* ── PHONE CAMERA SCANNER COMPANION ────────────────────────────────── */
        /* ── PHONE CAMERA SCANNER COMPANION ────────────────────────────────── */
        openPhoneScannerModal() {
            const modal = document.getElementById('phone-scanner-modal');
            if (!modal) return;
            modal.style.display = 'flex';

            const statusText = document.getElementById('scanner-status-text');
            const lifecycleBadge = document.getElementById('scanner-lifecycle-badge');
            const urlDisplay = document.getElementById('scanner-lan-url-display');
            const devInfoBar = document.getElementById('scanner-device-info-bar');

            if (urlDisplay) urlDisplay.textContent = 'Generating LAN URL...';
            if (devInfoBar) devInfoBar.style.display = 'none';

            this.updateScannerLifecycleState('WAITING_FOR_PHONE');

            fetch('/api/pos/scanner-session/create/', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': POS_CONFIG.csrfToken
                }
            })
            .then(r => r.json())
            .then(data => {
                if (data.session_token) {
                    this.currentScannerSession = data.session_token;
                    const fullUrl = data.lan_connect_url || (window.location.origin + data.connect_url);
                    this.currentScannerUrl = fullUrl;
                    this.phoneConnected = false;

                    const qrImg = document.getElementById('scanner-qr-img');
                    if (qrImg) {
                        qrImg.src = `https://api.qrserver.com/v1/create-qr-code/?size=170x170&data=${encodeURIComponent(fullUrl)}`;
                    }

                    if (urlDisplay) {
                        urlDisplay.textContent = fullUrl;
                    }

                    const sourceBadge = document.getElementById('scanner-url-source-badge');
                    if (sourceBadge && data.url_source) {
                        sourceBadge.textContent = data.url_source.toUpperCase();
                    }

                    const openLocalBtn = document.getElementById('btn-open-scanner-local');
                    if (openLocalBtn) {
                        openLocalBtn.href = fullUrl;
                    }

                    if (this.scannerPollTimer) clearInterval(this.scannerPollTimer);
                    this.scannerPollTimer = setInterval(() => this.pollPhoneScanner(), 900);
                }
            })
            .catch(err => {
                console.error('Error creating scanner session:', err);
                this.updateScannerLifecycleState('ERROR', 'Could not initialize session. Check server connectivity.');
            });
        },

        updateScannerLifecycleState(state, customMessage = null) {
            const badge = document.getElementById('scanner-lifecycle-badge');
            const statusText = document.getElementById('scanner-status-text');
            const devInfoBar = document.getElementById('scanner-device-info-bar');

            if (!badge) return;

            switch (state) {
                case 'WAITING_FOR_PHONE':
                    badge.style.background = 'rgba(245, 158, 11, 0.15)';
                    badge.style.color = '#fbbf24';
                    badge.style.border = '1px solid rgba(245, 158, 11, 0.3)';
                    badge.innerHTML = `<span class="pulse-dot" style="width: 6px; height: 6px; border-radius: 50%; background: #f59e0b; display: inline-block;"></span> WAITING FOR PHONE`;
                    if (statusText) statusText.textContent = customMessage || 'Point physical phone camera at the QR code above or open the LAN URL on mobile browser (same Wi-Fi).';
                    break;
                case 'PHONE_CONNECTED':
                    badge.style.background = 'rgba(16, 185, 129, 0.15)';
                    badge.style.color = '#34d399';
                    badge.style.border = '1px solid rgba(16, 185, 129, 0.3)';
                    badge.innerHTML = `<span class="pulse-dot" style="width: 6px; height: 6px; border-radius: 50%; background: #10b981; display: inline-block;"></span> PHONE CONNECTED`;
                    if (statusText) statusText.textContent = customMessage || 'Phone paired successfully! Ready to receive barcode scans.';
                    break;
                case 'SCANNER_READY':
                    badge.style.background = 'rgba(16, 185, 129, 0.2)';
                    badge.style.color = '#10b981';
                    badge.style.border = '1px solid rgba(16, 185, 129, 0.4)';
                    badge.innerHTML = `✓ SCANNER READY`;
                    if (statusText) statusText.textContent = customMessage || 'Companion active. Point phone camera or type barcode to scan.';
                    break;
                case 'BARCODE_RECEIVED':
                    badge.style.background = 'rgba(56, 189, 248, 0.15)';
                    badge.style.color = '#38bdf8';
                    badge.style.border = '1px solid rgba(56, 189, 248, 0.3)';
                    badge.innerHTML = `⚡ BARCODE RECEIVED`;
                    if (statusText) statusText.textContent = customMessage || 'Barcode received from phone companion. Adding to cart...';
                    break;
                case 'PRODUCT_ADDED':
                    badge.style.background = 'rgba(16, 185, 129, 0.25)';
                    badge.style.color = '#10b981';
                    badge.style.border = '1px solid rgba(16, 185, 129, 0.5)';
                    badge.innerHTML = `🛒 PRODUCT ADDED`;
                    if (statusText) statusText.textContent = customMessage || 'Product added to register.';
                    break;
                case 'DISCONNECTED':
                    badge.style.background = 'rgba(239, 68, 68, 0.15)';
                    badge.style.color = '#f87171';
                    badge.style.border = '1px solid rgba(239, 68, 68, 0.3)';
                    badge.innerHTML = `✕ DISCONNECTED`;
                    if (statusText) statusText.textContent = customMessage || 'Phone disconnected or session expired. Click Regenerate QR to restart.';
                    break;
                case 'ERROR':
                    badge.style.background = 'rgba(239, 68, 68, 0.15)';
                    badge.style.color = '#f87171';
                    badge.style.border = '1px solid rgba(239, 68, 68, 0.3)';
                    badge.innerHTML = `! ERROR`;
                    if (statusText) statusText.textContent = customMessage || 'Scanner connection encountered an error.';
                    break;
            }
        },

        pollPhoneScanner() {
            if (!this.currentScannerSession) return;
            fetch(`/api/pos/scanner-session/${encodeURIComponent(this.currentScannerSession)}/poll/`)
                .then(r => r.json())
                .then(data => {
                    const devInfoBar = document.getElementById('scanner-device-info-bar');

                    if (!data.active) {
                        this.updateScannerLifecycleState('DISCONNECTED', 'Session expired or closed by phone. Click Regenerate QR to start a new session.');
                        if (this.scannerPollTimer) clearInterval(this.scannerPollTimer);
                        this.phoneConnected = false;
                        return;
                    }

                    if (data.connected) {
                        if (!this.phoneConnected) {
                            this.phoneConnected = true;
                            const dev = data.device_info ? data.device_info.slice(0, 45) : 'Mobile Device';
                            this.updateScannerLifecycleState('PHONE_CONNECTED', `Connected: ${dev}`);
                            if (devInfoBar) {
                                devInfoBar.textContent = `Paired Device: ${dev}`;
                                devInfoBar.style.display = 'block';
                            }
                            setTimeout(() => {
                                if (this.phoneConnected) {
                                    this.updateScannerLifecycleState('SCANNER_READY');
                                }
                            }, 1200);
                        }
                    } else if (this.phoneConnected) {
                        // Was connected but lost heartbeat
                        this.phoneConnected = false;
                        this.updateScannerLifecycleState('WAITING_FOR_PHONE', 'Phone connection lost (heartbeat timeout). Waiting for reconnect...');
                    }

                    if (data.scans && data.scans.length > 0) {
                        data.scans.forEach(code => {
                            this.updateScannerLifecycleState('BARCODE_RECEIVED', `Scanned code: ${code}`);
                            this.lookupBarcode(code);
                            setTimeout(() => {
                                this.updateScannerLifecycleState('PRODUCT_ADDED', `Processed barcode: ${code}`);
                                setTimeout(() => {
                                    if (this.phoneConnected) {
                                        this.updateScannerLifecycleState('SCANNER_READY');
                                    }
                                }, 1800);
                            }, 500);
                        });
                    }
                })
                .catch(e => console.warn('Poll error:', e));
        },

        copyScannerUrl() {
            if (!this.currentScannerUrl) return;
            const copyBtn = document.getElementById('btn-copy-scanner-url');
            const originalHtml = copyBtn ? copyBtn.innerHTML : '';

            if (navigator.clipboard && navigator.clipboard.writeText) {
                navigator.clipboard.writeText(this.currentScannerUrl).then(() => {
                    if (copyBtn) copyBtn.innerHTML = '<i class="bi bi-check2"></i> <span>Copied!</span>';
                    this.showToast('Scanner LAN URL copied to clipboard!', 'success');
                    setTimeout(() => { if (copyBtn) copyBtn.innerHTML = originalHtml; }, 2000);
                }).catch(() => this.fallbackCopyUrl(this.currentScannerUrl));
            } else {
                this.fallbackCopyUrl(this.currentScannerUrl);
            }
        },

        fallbackCopyUrl(text) {
            const temp = document.createElement('textarea');
            temp.value = text;
            document.body.appendChild(temp);
            temp.select();
            try {
                document.execCommand('copy');
                const copyBtn = document.getElementById('btn-copy-scanner-url');
                if (copyBtn) copyBtn.innerHTML = '<i class="bi bi-check2"></i> <span>Copied!</span>';
                this.showToast('Scanner LAN URL copied!', 'success');
                setTimeout(() => {
                    if (copyBtn) copyBtn.innerHTML = '<i class="bi bi-clipboard"></i> <span>Copy URL</span>';
                }, 2000);
            } catch (err) {
                prompt('Copy scanner URL manually:', text);
            }
            document.body.removeChild(temp);
        },

        regenerateScannerQR() {
            if (this.scannerPollTimer) clearInterval(this.scannerPollTimer);
            if (this.currentScannerSession) {
                fetch(`/api/pos/scanner-session/${encodeURIComponent(this.currentScannerSession)}/disconnect/`, {
                    method: 'POST',
                    headers: { 'X-CSRFToken': POS_CONFIG.csrfToken }
                }).catch(() => {});
                this.currentScannerSession = null;
            }
            this.openPhoneScannerModal();
        },

        closePhoneScannerModal() {
            const modal = document.getElementById('phone-scanner-modal');
            if (modal) modal.style.display = 'none';
        },

        disconnectPhoneScanner() {
            if (this.scannerPollTimer) clearInterval(this.scannerPollTimer);
            if (this.currentScannerSession) {
                fetch(`/api/pos/scanner-session/${encodeURIComponent(this.currentScannerSession)}/disconnect/`, {
                    method: 'POST',
                    headers: { 'X-CSRFToken': POS_CONFIG.csrfToken }
                }).catch(() => {});
                this.currentScannerSession = null;
            }
            this.phoneConnected = false;
            this.updateScannerLifecycleState('DISCONNECTED');
            this.closePhoneScannerModal();
        },

        quickCash(amount) {
            const payInput = document.getElementById('pay-amount');
            if (!payInput) return;

            if (amount === 'exact') {
                payInput.value = (this.lastGrandTotal || 0).toFixed(2);
            } else {
                payInput.value = parseFloat(amount).toFixed(2);
            }
        },

        onPayAmountChange() {
            const payErr = document.getElementById('pay-error');
            if (payErr) payErr.style.display = 'none';
        },

        /* ── CHECKOUT EXECUTION ────────────────────────────────────────────── */
        checkout() {
            const btn = document.getElementById('btn-checkout');
            const errBox = document.getElementById('global-alert');
            const succBox = document.getElementById('global-success');

            if (errBox) errBox.style.display = 'none';
            if (succBox) succBox.style.display = 'none';

            if (this.items.length === 0) {
                this.showError('Register is empty. Add at least one product before checkout.');
                return;
            }

            if (this.paymentMethod === 'CREDIT' && !this.customer) {
                this.showError('CREDIT payment requires a registered customer. Please select a customer first.');
                return;
            }

            if (this.paymentMethod === 'UPI' && !this.upiConfirmed) {
                this.showError('UPI payment requires verification. Please confirm payment receipt before completing checkout.');
                return;
            }

            const payInput = document.getElementById('pay-amount');
            const tenderAmount = parseFloat(payInput ? payInput.value : 0) || 0;
            const refInput = document.getElementById('pay-ref');
            const payRef = refInput ? refInput.value.trim() : '';

            const discTypeEl = document.getElementById('discount-type');
            const discValEl = document.getElementById('discount-value');
            const discType = discTypeEl ? discTypeEl.value : 'FLAT';
            const discVal = parseFloat(discValEl ? discValEl.value : 0) || 0;

            const payload = {
                customer_id: this.customer ? this.customer.id : null,
                items: this.items.map(it => ({
                    product_id: it.product_id,
                    quantity: it.quantity,
                    unit_price: it.unit_price
                })),
                discount_type: discType,
                discount_value: discVal,
                payment: {
                    payment_method: this.paymentMethod,
                    amount: tenderAmount > 0 ? tenderAmount : this.lastGrandTotal,
                    reference_number: payRef
                }
            };

            if (btn) {
                btn.disabled = true;
                btn.classList.add('processing');
                const btnText = document.getElementById('checkout-btn-text');
                if (btnText) btnText.textContent = 'Processing Sale…';
            }

            fetch(POS_CONFIG.checkoutUrl, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': POS_CONFIG.csrfToken,
                    'Idempotency-Key': this.idempotencyKey
                },
                body: JSON.stringify(payload)
            })
                .then(r => r.json().then(data => ({ status: r.status, ok: r.ok, data })))
                .then(res => {
                    if (btn) {
                        btn.disabled = false;
                        btn.classList.remove('processing');
                        const btnText = document.getElementById('checkout-btn-text');
                        if (btnText) btnText.textContent = 'Complete Sale';
                    }

                    if (res.ok && res.status === 201) {
                        const inv = (res.data.invoice && res.data.invoice.invoice_number) ||
                                    (res.data.sale && res.data.sale.sale_number) ||
                                    res.data.invoice_number || res.data.sale_number || '';
                        const grandTotalVal = (res.data.sale && res.data.sale.grand_total) ||
                                              (res.data.invoice && res.data.invoice.grand_total) ||
                                              res.data.grand_total || 0;
                        const grand = formatINR(grandTotalVal);
                        const msg = `✓ Checkout successful! Invoice: ${inv} · Grand Total: ${grand}`;

                        this.showSuccess(msg);
                        this.showToast(msg, 'success');

                        if (inv) {
                            window.open(`/invoice/${inv}/`, '_blank');
                        }

                        // Reset terminal to fresh sale state
                        this.resetAfterCheckout();
                    } else {
                        const err = res.data.detail || res.data.error || JSON.stringify(res.data);
                        this.showError(`Checkout Failed: ${err}`);
                    }
                })
                .catch(err => {
                    if (btn) {
                        btn.disabled = false;
                        btn.classList.remove('processing');
                        const btnText = document.getElementById('checkout-btn-text');
                        if (btnText) btnText.textContent = 'Complete Sale';
                    }
                    console.error('Checkout error:', err);
                    this.showError('Network error during checkout. Please verify and retry.');
                });
        },

        resetAfterCheckout() {
            this.items = [];
            this.customer = null;
            this.idempotencyKey = uuidv4();
            this.currentPreview = null;

            const card = document.getElementById('selected-customer-card');
            if (card) card.style.display = 'none';

            const discValEl = document.getElementById('discount-value');
            if (discValEl) discValEl.value = '0';

            const payRef = document.getElementById('pay-ref');
            if (payRef) payRef.value = '';

            const previewCard = document.getElementById('product-preview-card');
            const idleBox = document.getElementById('preview-idle-box');
            if (previewCard) previewCard.style.display = 'none';
            if (idleBox) idleBox.style.display = 'flex';

            this.render();
            this.refreshQuote();

            // Return focus immediately to barcode input in cashier mode
            const cashierInput = document.getElementById('barcode-scanner-input');
            if (cashierInput) {
                cashierInput.value = '';
                cashierInput.focus();
            } else {
                const searchInput = document.getElementById('product-search');
                if (searchInput) {
                    searchInput.value = '';
                    searchInput.focus();
                }
            }
        },

        /* ── NOTIFICATIONS & FEEDBACK ──────────────────────────────────────── */
        showError(msg) {
            const errBox = document.getElementById('global-alert');
            if (errBox) {
                errBox.textContent = msg;
                errBox.style.display = 'block';
            }
            this.showToast(msg, 'error');
        },

        showSuccess(msg) {
            const succBox = document.getElementById('global-success');
            if (succBox) {
                succBox.textContent = msg;
                succBox.style.display = 'block';
            }
        },

        showToast(msg, type = 'success') {
            const t = document.getElementById('toast');
            if (!t) return;
            t.className = `toast show ${type}`;
            t.innerHTML = `<i class="bi bi-${type === 'success' ? 'check-circle-fill' : 'exclamation-triangle-fill'}"></i> <span>${msg}</span>`;
            setTimeout(() => {
                t.className = 'toast';
            }, 3500);
        },

        /* ── GLOBAL KEYBOARD & CUSTOMER LOOKUP EVENTS ──────────────────────── */
        bindCoreEvents() {
            // Customer lookup with debounce
            const cInput = document.getElementById('customer-input');
            const cDropdown = document.getElementById('customer-dropdown');

            if (cInput && cDropdown) {
                cInput.addEventListener('input', () => {
                    clearTimeout(this.customerTimer);
                    const q = cInput.value.trim();
                    if (!q) {
                        cDropdown.style.display = 'none';
                        return;
                    }

                    this.customerTimer = setTimeout(() => {
                        fetch(`${POS_CONFIG.customersUrl}?search=${encodeURIComponent(q)}&limit=8`)
                            .then(r => r.json())
                            .then(data => {
                                const list = data.results || (Array.isArray(data) ? data : []);
                                cDropdown.innerHTML = '';
                                if (list.length === 0) {
                                    cDropdown.innerHTML = '<div style="padding:8px 12px;font-size:11px;color:var(--text-dim);">No customers found</div>';
                                } else {
                                    list.forEach(c => {
                                        const item = document.createElement('div');
                                        item.className = 'customer-item';
                                        item.innerHTML = `
                                            <div class="cname">${c.name}</div>
                                            <div class="cphone">${c.phone} · Credit: ${formatINR(c.credit_balance || 0)}</div>
                                        `;
                                        item.addEventListener('click', () => {
                                            this.selectCustomer(c);
                                        });
                                        cDropdown.appendChild(item);
                                    });
                                }
                                cDropdown.style.display = 'block';
                            })
                            .catch(() => {});
                    }, 200);
                });

                document.addEventListener('click', (e) => {
                    if (!e.target.closest('#customer-search-wrap')) {
                        cDropdown.style.display = 'none';
                    }
                });
            }

            // ── GLOBAL HARDWARE HID BARCODE SCANNER BUFFER ──────────────────────
            let hidBuffer = '';
            let lastKeyTime = 0;
            const HID_BURST_MAX_MS = 65;

            document.addEventListener('keydown', (e) => {
                const now = Date.now();
                const interval = now - lastKeyTime;
                lastKeyTime = now;

                const activeEl = document.activeElement;
                const isBarcodeInput = activeEl && activeEl.id === 'barcode-scanner-input';
                const isTestInput = activeEl && activeEl.id === 'modal-test-scanner-input';
                const isOtherInput = activeEl && (
                    activeEl.tagName === 'INPUT' || 
                    activeEl.tagName === 'TEXTAREA' || 
                    activeEl.isContentEditable
                ) && !isBarcodeInput && !isTestInput;

                // Handle scanner terminator Enter
                if (e.key === 'Enter') {
                    if (hidBuffer.length >= 3) {
                        const code = hidBuffer.trim();
                        hidBuffer = '';
                        e.preventDefault();
                        if (isTestInput) {
                            this.testModalScannerInput(code);
                        } else {
                            this.lookupBarcode(code);
                        }
                        return;
                    }
                    hidBuffer = '';
                    return;
                }

                // If not Enter, accumulate single printable characters
                if (e.key.length === 1 && !e.ctrlKey && !e.altKey && !e.metaKey) {
                    if (isOtherInput) {
                        // High speed HID burst detected while cursor inside another input
                        if (interval > 45) {
                            hidBuffer = '';
                            return;
                        }
                        hidBuffer += e.key;
                        return;
                    }

                    if (interval > HID_BURST_MAX_MS && !isBarcodeInput) {
                        hidBuffer = e.key;
                    } else {
                        hidBuffer += e.key;
                    }
                }
            });

            // Keyboard navigation
            document.addEventListener('keydown', (e) => {
                const activeEl = document.activeElement;
                const overlayInput = document.getElementById('pos-search-overlay-input');
                const isOverlayInput = activeEl && activeEl === overlayInput;
                const isTyping = activeEl && (
                    activeEl.tagName === 'INPUT' || 
                    activeEl.tagName === 'TEXTAREA' || 
                    activeEl.tagName === 'SELECT' || 
                    activeEl.isContentEditable
                );

                // Ctrl + Enter: Checkout
                if (e.ctrlKey && e.key === 'Enter') {
                    e.preventDefault();
                    this.checkout();
                    return;
                }

                // F2: Dedicated POS Product & Barcode Search Overlay (POS ONLY)
                if (e.key === 'F2') {
                    if (!isTyping || isOverlayInput) {
                        e.preventDefault();
                        e.stopPropagation();
                        this.openProductSearchOverlay();
                        return;
                    }
                }

                // Esc: Clear overlay or modals (STRICTLY PRESERVES CART ITEMS)
                if (e.key === 'Escape') {
                    const overlay = document.getElementById('pos-search-overlay');
                    if (overlay && overlay.style.display !== 'none') {
                        e.preventDefault();
                        this.closeProductSearchOverlay();
                        return;
                    }
                    const diagModal = document.getElementById('scanner-status-modal');
                    if (diagModal && diagModal.style.display !== 'none') {
                        e.preventDefault();
                        this.closeScannerStatusModal();
                        return;
                    }
                    const unkModal = document.getElementById('unknown-barcode-modal');
                    if (unkModal && unkModal.style.display !== 'none') {
                        e.preventDefault();
                        this.closeUnknownBarcodeModal();
                        return;
                    }
                    const phoneModal = document.getElementById('phone-scanner-modal');
                    if (phoneModal && phoneModal.style.display !== 'none') {
                        e.preventDefault();
                        this.closePhoneScannerModal();
                        return;
                    }
                    const cDropdown = document.getElementById('customer-dropdown');
                    if (cDropdown) cDropdown.style.display = 'none';
                    const sResults = document.getElementById('search-results');
                    if (sResults) sResults.style.display = 'none';
                    const grid = document.getElementById('catalog-items-grid');
                    if (grid) grid.style.display = 'flex';
                }
            });
        }
    };

    window.Cart = Cart;
    document.addEventListener('DOMContentLoaded', () => Cart.init());
})();
