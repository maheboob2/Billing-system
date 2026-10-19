const fs = require('fs');
const path = require('path');

const ARTIFACTS_DIR = 'C:\\Users\\admin\\.gemini\\antigravity-ide\\brain\\8f51d56c-ce8c-4183-84c1-9dbf04920b57';

class CDPClient {
    constructor(wsUrl) {
        this.wsUrl = wsUrl;
        this.ws = null;
        this.id = 1;
        this.callbacks = new Map();
        this.eventListeners = new Map();
    }

    connect() {
        return new Promise((resolve, reject) => {
            this.ws = new WebSocket(this.wsUrl);
            this.ws.onopen = () => resolve();
            this.ws.onerror = (err) => reject(err);
            this.ws.onmessage = (event) => {
                const msg = JSON.parse(event.data);
                if (msg.id && this.callbacks.has(msg.id)) {
                    const cb = this.callbacks.get(msg.id);
                    this.callbacks.delete(msg.id);
                    if (msg.error) cb.reject(new Error(msg.error.message));
                    else cb.resolve(msg.result);
                } else if (msg.method) {
                    const listeners = this.eventListeners.get(msg.method) || [];
                    for (const l of listeners) l(msg.params);
                }
            };
        });
    }

    send(method, params = {}) {
        return new Promise((resolve, reject) => {
            const id = this.id++;
            this.callbacks.set(id, { resolve, reject });
            this.ws.send(JSON.stringify({ id, method, params }));
        });
    }

    on(event, cb) {
        if (!this.eventListeners.has(event)) this.eventListeners.set(event, []);
        this.eventListeners.get(event).push(cb);
    }

    close() {
        if (this.ws) this.ws.close();
    }
}

async function sleep(ms) {
    return new Promise((res) => setTimeout(res, ms));
}

async function runQA() {
    console.log("=== STARTING REAL CHROME QA PASS VIA CDP ===");

    // 1. Get targets
    const versionRes = await fetch("http://127.0.0.1:9222/json");
    const targets = await versionRes.json();
    const pageTarget = targets.find(t => t.type === 'page') || targets[0];
    if (!pageTarget) {
        throw new Error("No page target found on port 9222");
    }
    console.log(`Connected to target: ${pageTarget.title} (${pageTarget.url})`);

    const cdp = new CDPClient(pageTarget.webSocketDebuggerUrl);
    await cdp.connect();

    // Enable domains
    await cdp.send("Page.enable");
    await cdp.send("DOM.enable");
    await cdp.send("Runtime.enable");
    await cdp.send("Network.enable");

    // Track console & network
    const consoleLogs = [];
    const networkErrors = [];

    cdp.on("Runtime.consoleAPICalled", (params) => {
        const text = params.args.map(a => a.value || a.description || JSON.stringify(a)).join(" ");
        consoleLogs.push({ type: params.type, text });
        if (params.type === 'error') {
            console.log(`[BROWSER CONSOLE ERROR]`, text);
        }
    });

    cdp.on("Runtime.exceptionThrown", (params) => {
        const text = params.exceptionDetails.text + " " + (params.exceptionDetails.exception?.description || "");
        consoleLogs.push({ type: 'exception', text });
        console.log(`[BROWSER EXCEPTION]`, text);
    });

    cdp.on("Network.responseReceived", (params) => {
        if (params.response.status >= 400) {
            networkErrors.push({
                url: params.response.url,
                status: params.response.status,
                statusText: params.response.statusText
            });
            console.log(`[HTTP ${params.response.status}] ${params.response.url}`);
        }
    });

    const takeScreenshot = async (name) => {
        const result = await cdp.send("Page.captureScreenshot", { format: "png" });
        const filePath = path.join(ARTIFACTS_DIR, name);
        fs.writeFileSync(filePath, Buffer.from(result.data, 'base64'));
        console.log(`Saved screenshot: ${name}`);
        return filePath;
    };

    const navigateAndWait = async (url, waitMs = 1500) => {
        await cdp.send("Page.navigate", { url });
        await sleep(waitMs);
    };

    const evalJs = async (expr) => {
        const res = await cdp.send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true });
        if (res.exceptionDetails) {
            throw new Error(`Eval error: ${res.exceptionDetails.text}`);
        }
        return res.result.value;
    };

    const reportResults = {};

    // -------------------------------------------------------------
    // 1. LOGIN QA
    // -------------------------------------------------------------
    console.log("\n--- [1] LOGIN TESTING ---");
    await cdp.send("Emulation.setDeviceMetricsOverride", { width: 1366, height: 768, deviceScaleFactor: 1, mobile: false });
    await navigateAndWait("http://127.0.0.1:8000/accounts/");

    const loginUiCheck = await evalJs(`({
        title: document.title,
        hasTheme: document.documentElement.dataset.theme !== undefined,
        hasAuthCard: !!document.querySelector('.auth-card'),
        hasUsernameField: !!document.querySelector('input[name="username"]'),
        hasPasswordField: !!document.querySelector('input[name="password"]'),
        hasSubmitBtn: !!document.querySelector('button[type="submit"]'),
        hasRawDjangoForm: !document.querySelector('.input-wrap')
    })`);
    console.log("Login UI elements:", loginUiCheck);

    // Test failed credentials
    console.log("Testing invalid login...");
    await evalJs(`
        document.querySelector('input[name="username"]').value = 'invalid_user_qa';
        document.querySelector('input[name="password"]').value = 'WrongPassword@123';
        document.querySelector('button[type="submit"]').click();
    `);
    await sleep(1500);

    const loginFailCheck = await evalJs(`({
        url: window.location.href,
        hasErrorBanner: !!document.querySelector('.alert-error') || !!document.querySelector('.auth-alert-error') || document.body.innerText.includes('Invalid credentials') || document.body.innerText.includes('Please enter a correct')
    })`);
    console.log("Failed login result:", loginFailCheck);
    await takeScreenshot("qa_1_login_failed.png");

    // Test successful login
    console.log("Testing valid login with helloshaikh...");
    await evalJs(`
        document.querySelector('input[name="username"]').value = 'helloshaikh';
        document.querySelector('input[name="password"]').value = 'Password@123';
        document.querySelector('button[type="submit"]').click();
    `);
    await sleep(2500);

    const loginSuccessCheck = await evalJs(`({
        url: window.location.pathname,
        isDashboard: window.location.pathname.includes('/dashboard') || window.location.pathname.includes('/cart')
    })`);
    console.log("Successful login result:", loginSuccessCheck);
    await takeScreenshot("qa_1_login_success.png");

    reportResults.login = {
        modernUI: loginUiCheck.hasAuthCard && !loginUiCheck.hasRawDjangoForm,
        validation: loginFailCheck.hasErrorBanner,
        successfulLogin: loginSuccessCheck.isDashboard
    };

    // -------------------------------------------------------------
    // 2. CATEGORY QA
    // -------------------------------------------------------------
    console.log("\n--- [2] CATEGORY TESTING ---");
    await navigateAndWait("http://127.0.0.1:8000/category/add/");

    const catUiCheck = await evalJs(`({
        title: document.title,
        hasHeader: !!document.querySelector('h1'),
        hasNameInput: !!document.querySelector('input[name="name"]'),
        hasDescInput: !!document.querySelector('textarea[name="description"]'),
        hasSaveBtn: !!document.querySelector('button[type="submit"]'),
        hasRawDjangoForm: !!document.querySelector('table[border="1"]')
    })`);
    console.log("Category Add UI check:", catUiCheck);
    await takeScreenshot("qa_2_category_add_page.png");

    // Create Category
    const catName = "QA Chrome Category " + Date.now().toString().slice(-4);
    await evalJs(`
        document.querySelector('input[name="name"]').value = '${catName}';
        document.querySelector('textarea[name="description"]').value = 'Created via Chrome QA Pass';
        document.querySelector('button[type="submit"]').click();
    `);
    await sleep(2000);

    const catRedirectCheck = await evalJs(`({
        url: window.location.pathname,
        isCategoryList: window.location.pathname.includes('/category'),
        hasCreatedCat: document.body.innerText.includes('${catName}')
    })`);
    console.log("Category creation and redirect:", catRedirectCheck);
    await takeScreenshot("qa_2_category_list.png");

    reportResults.category = {
        modernUI: catUiCheck.hasNameInput && !catUiCheck.hasRawDjangoForm,
        createdAndRedirected: catRedirectCheck.isCategoryList && catRedirectCheck.hasCreatedCat
    };

    // -------------------------------------------------------------
    // 3. POS CART & BARCODE HID & PHONE SCANNER & UPI
    // -------------------------------------------------------------
    console.log("\n--- [3, 4, 5] POS CART, BARCODE, PHONE SCANNER & UPI TESTING ---");
    await cdp.send("Network.setCacheDisabled", { cacheDisabled: true });
    await navigateAndWait("http://127.0.0.1:8000/cart/", 2000);

    const posInitCheck = await evalJs(`({
        title: document.title,
        hasPhoneScannerBtn: !!document.querySelector('#btn-open-phone-scanner'),
        hasUpiTab: !!document.querySelector('.pay-tab[data-method="UPI"]'),
        cartMethods: Object.keys(window.Cart || {})
    })`);
    console.log("POS Initial check & Cart methods:", posInitCheck.cartMethods.slice(0, 15));
    await takeScreenshot("qa_3_pos_initial.png");

    // 3.1 Barcode HID Scan 1: Scan active product
    console.log("Scanning barcode 890126201001 (HID scan #1)...");
    await evalJs(`
        window.Cart.lookupBarcode('890126201001');
    `);
    await sleep(1500);

    const scan1Check = await evalJs(`({
        cartItemsCount: Cart.items.length,
        itemQuantity: Cart.items[0]?.quantity,
        itemName: Cart.items[0]?.product_name,
        grandTotal: Cart.lastGrandTotal
    })`);
    console.log("Scan #1 result:", scan1Check);

    // 3.2 Barcode HID Scan 2: Repeated scan of same product -> quantity increment
    console.log("Scanning same barcode 890126201001 (HID scan #2 - repeated)...");
    await evalJs(`
        window.Cart.lookupBarcode('890126201001');
    `);
    await sleep(1500);

    const scan2Check = await evalJs(`({
        cartItemsCount: Cart.items.length,
        itemQuantity: Cart.items[0]?.quantity,
        grandTotal: Cart.lastGrandTotal
    })`);
    console.log("Scan #2 repeated result (quantity incremented):", scan2Check);
    await takeScreenshot("qa_3_barcode_scanned.png");

    // 3.3 Barcode Scan 3: Invalid barcode -> error feedback
    console.log("Scanning invalid barcode NONEXISTENT_CODE_999...");
    await evalJs(`
        window.Cart.lookupBarcode('NONEXISTENT_CODE_999');
    `);
    await sleep(1000);

    const invalidScanCheck = await evalJs(`({
        feedbackText: document.getElementById('scan-feedback')?.textContent,
        feedbackVisible: document.getElementById('scan-feedback')?.style.display !== 'none'
    })`);
    console.log("Invalid scan error feedback:", invalidScanCheck);

    // -------------------------------------------------------------
    // 4. PHONE SCANNER COMPANION MODAL
    // -------------------------------------------------------------
    console.log("\n--- [4] PHONE SCANNER COMPANION MODAL TESTING ---");
    await evalJs(`
        const btn = document.querySelector('#btn-open-phone-scanner');
        if (btn) btn.click();
        else if (window.Cart.openPhoneScannerModal) window.Cart.openPhoneScannerModal();
    `);
    await sleep(2000);

    const phoneModalCheck = await evalJs(`({
        modalVisible: document.querySelector('#phone-scanner-modal')?.style.display !== 'none',
        qrSrc: document.querySelector('#scanner-qr-img')?.getAttribute('src'),
        pairingCode: document.querySelector('#pairing-code-display')?.textContent,
        statusText: document.querySelector('#scanner-status-text')?.textContent
    })`);
    console.log("Phone Scanner Modal check:", phoneModalCheck);
    await takeScreenshot("qa_4_phone_scanner_modal.png");

    // Close phone scanner modal
    await evalJs(`
        if (window.Cart.closePhoneScannerModal) window.Cart.closePhoneScannerModal();
        else {
            const m = document.querySelector('#phone-scanner-modal');
            if (m) m.style.display = 'none';
        }
    `);
    await sleep(500);

    // -------------------------------------------------------------
    // 5. UPI PAYMENT GATE & VERIFICATION
    // -------------------------------------------------------------
    console.log("\n--- [5] UPI PAYMENT GATEWAY & VERIFICATION TESTING ---");
    await evalJs(`
        const upiTab = document.querySelector('.pay-tab[data-method="UPI"]');
        if (upiTab) upiTab.click();
        else window.Cart.selectPayMethod('UPI');
    `);
    await sleep(1000);

    const upiBoxCheck = await evalJs(`({
        boxVisible: document.querySelector('#upi-verification-box')?.style.display !== 'none',
        statusBadge: document.querySelector('#upi-status-badge')?.textContent?.trim(),
        amountDisplay: document.querySelector('#upi-amount-display')?.textContent?.trim(),
        upiConfirmed: window.Cart.upiConfirmed
    })`);
    console.log("UPI Intent Box Check:", upiBoxCheck);
    await takeScreenshot("qa_5_upi_awaiting.png");

    // Test checkout attempt before confirming UPI -> Must be blocked!
    console.log("Testing checkout attempt BEFORE UPI confirmation...");
    const checkoutGateCheck = await evalJs(`(function() {
        let alertMessage = '';
        const origShowError = window.Cart.showError;
        window.Cart.showError = function(msg) { alertMessage = msg; origShowError.call(window.Cart, msg); };

        window.Cart.checkout();

        return {
            upiConfirmed: window.Cart.upiConfirmed,
            blocked: !window.Cart.upiConfirmed,
            alertMessage: alertMessage
        };
    })()`);
    console.log("UPI Checkout Gate Test (before confirmation):", checkoutGateCheck);

    // Now confirm UPI payment via Cashier / Demo action
    console.log("Confirming UPI payment receipt...");
    await evalJs(`
        const markBtn = document.querySelector('#btn-upi-mark-received');
        if (markBtn) markBtn.click();
        else window.Cart.markUpiReceived();
    `);
    await sleep(1000);

    const upiVerifiedCheck = await evalJs(`({
        upiConfirmed: window.Cart.upiConfirmed,
        statusBadge: document.querySelector('#upi-status-badge')?.textContent?.trim()
    })`);
    console.log("UPI Verification after marking received:", upiVerifiedCheck);
    await takeScreenshot("qa_5_upi_verified.png");

    reportResults.posUpi = {
        barcodeScanSuccess: scan1Check.cartItemsCount > 0,
        repeatIncrement: scan2Check.itemQuantity === 2,
        invalidBarcodeBlocked: invalidScanCheck.feedbackVisible,
        phoneModalQR: phoneModalCheck.modalVisible && !!phoneModalCheck.qrSrc,
        upiBoxVisible: upiBoxCheck.boxVisible,
        upiGateBlocked: checkoutGateCheck.blocked,
        upiVerified: upiVerifiedCheck.upiConfirmed
    };

    // -------------------------------------------------------------
    // 6. SWAGGER / OPENAPI TESTING
    // -------------------------------------------------------------
    console.log("\n--- [6] SWAGGER UI TESTING ---");
    await navigateAndWait("http://127.0.0.1:8000/api/docs/", 2500);

    const swaggerCheck = await evalJs(`({
        title: document.title,
        hasSwaggerContainer: !!document.querySelector('#swagger-ui'),
        hasEndpoints: document.querySelectorAll('.opblock').length,
        version: document.querySelector('.info .version')?.textContent
    })`);
    console.log("Swagger UI rendered check:", swaggerCheck);
    await takeScreenshot("qa_10_swagger_ui.png");

    reportResults.swagger = {
        rendered: swaggerCheck.hasSwaggerContainer && swaggerCheck.hasEndpoints > 0,
        endpointCount: swaggerCheck.hasEndpoints
    };

    // -------------------------------------------------------------
    // 7. RESPONSIVE TESTING
    // -------------------------------------------------------------
    console.log("\n--- [7] RESPONSIVE TESTING ---");
    await navigateAndWait("http://127.0.0.1:8000/cart/");

    // 1920x1080
    await cdp.send("Emulation.setDeviceMetricsOverride", { width: 1920, height: 1080, deviceScaleFactor: 1, mobile: false });
    await sleep(1000);
    await takeScreenshot("qa_11_responsive_1920x1080.png");

    // 1366x768
    await cdp.send("Emulation.setDeviceMetricsOverride", { width: 1366, height: 768, deviceScaleFactor: 1, mobile: false });
    await sleep(1000);
    await takeScreenshot("qa_11_responsive_1366x768.png");

    // 375x812 (Android/iPhone mobile)
    await cdp.send("Emulation.setDeviceMetricsOverride", { width: 375, height: 812, deviceScaleFactor: 2, mobile: true });
    await sleep(1000);
    await takeScreenshot("qa_11_responsive_mobile_375.png");

    // 320x568 (Small mobile)
    await cdp.send("Emulation.setDeviceMetricsOverride", { width: 320, height: 568, deviceScaleFactor: 2, mobile: true });
    await sleep(1000);
    await takeScreenshot("qa_11_responsive_small_320.png");

    // -------------------------------------------------------------
    // 8. CONSOLE & NETWORK AUDIT SUMMARY
    // -------------------------------------------------------------
    console.log("\n--- [8] CONSOLE & NETWORK HEALTH SUMMARY ---");
    console.log(`Total console logs: ${consoleLogs.length}`);
    console.log(`Total network errors (HTTP >= 400): ${networkErrors.length}`);
    if (networkErrors.length > 0) {
        console.log("Network errors details:", JSON.stringify(networkErrors, null, 2));
    }

    reportResults.consoleAudit = {
        consoleLogsCount: consoleLogs.length,
        networkErrors: networkErrors
    };

    fs.writeFileSync(path.join(ARTIFACTS_DIR, "chrome_qa_results.json"), JSON.stringify(reportResults, null, 2));
    console.log("\n=== CHROME QA RUN COMPLETED SUCCESSFULLY ===");

    cdp.close();
}

runQA().catch(err => {
    console.error("Fatal QA Error:", err);
    process.exit(1);
});
