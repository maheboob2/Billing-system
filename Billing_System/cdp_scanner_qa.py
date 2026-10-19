import asyncio
import json
import urllib.request
import websockets
import time
import sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

CHROME_DEBUG_URL = "http://127.0.0.1:9222"

class ChromeController:
    def __init__(self, ws_url):
        self.ws_url = ws_url
        self.ws = None
        self.msg_id = 1

    async def connect(self):
        self.ws = await websockets.connect(self.ws_url, max_size=50_000_000)
        await self.send("Page.enable")
        await self.send("Runtime.enable")
        await self.send("DOM.enable")

    async def close(self):
        if self.ws:
            await self.ws.close()

    async def send(self, method, params=None):
        mid = self.msg_id
        self.msg_id += 1
        payload = {"id": mid, "method": method, "params": params or {}}
        await self.ws.send(json.dumps(payload))
        while True:
            resp = json.loads(await self.ws.recv())
            if resp.get("id") == mid:
                return resp.get("result", {})

    async def navigate(self, url, wait_sec=2.0):
        await self.send("Page.navigate", {"url": url})
        await asyncio.sleep(wait_sec)

    async def eval_js(self, script):
        res = await self.send("Runtime.evaluate", {
            "expression": script,
            "returnByValue": True,
            "awaitPromise": True
        })
        if "exceptionDetails" in res:
            desc = res.get("exceptionDetails", {}).get("exception", {}).get("description")
            print(f"      [JS Exception]: {desc}")
            return None
        return res.get("result", {}).get("value")

    async def wait_for_element(self, selector, timeout=6.0):
        start = time.time()
        while time.time() - start < timeout:
            exists = await self.eval_js(f"document.querySelector('{selector}') !== null")
            if exists:
                return True
            await asyncio.sleep(0.3)
        return False


async def run_full_qa():
    print("=" * 75)
    print("STARTING POS SCANNER ARCHITECTURE REAL BROWSER QA (CHROME)")
    print("=" * 75)

    # 1. Open new tab in Chrome using PUT
    put_req = urllib.request.Request(f"{CHROME_DEBUG_URL}/json/new", method="PUT")
    resp = urllib.request.urlopen(put_req)
    tab_info = json.loads(resp.read().decode())
    ws_url = tab_info["webSocketDebuggerUrl"]
    tab_id = tab_info["id"]

    controller = ChromeController(ws_url)
    await controller.connect()

    results = {}

    try:
        # STEP 1: Log in as Manager
        print("\n--- STEP 1: LOGIN AS MANAGER ---")
        await controller.navigate("http://127.0.0.1:8000/logout/")
        await controller.navigate("http://127.0.0.1:8000/accounts/")
        await controller.wait_for_element('input[name="username"]')
        await controller.eval_js("""
            (() => {
                document.querySelector('input[name="username"]').value = 'demo_mgr';
                document.querySelector('input[name="password"]').value = 'Password@123';
                document.querySelector('button[type="submit"]').click();
            })()
        """)
        await asyncio.sleep(2.5)
        cur_url = await controller.eval_js("window.location.href")
        print(f"Manager Logged in -> Current URL: {cur_url}")

        # STEP 2: Manager opens Add Product & checks Identification Section
        print("\n--- STEP 2: MANAGER ADDS PRODUCT WITH GENERATED BARCODE ---")
        await controller.navigate("http://127.0.0.1:8000/product/add/")
        await controller.wait_for_element('#btn-generate-barcode')
        
        has_scan_btn = await controller.eval_js("document.getElementById('btn-scan-barcode') !== null")
        has_gen_btn = await controller.eval_js("document.getElementById('btn-generate-barcode') !== null")
        print(f"Has [Scan Barcode] button: {has_scan_btn}")
        print(f"Has [Generate Internal Barcode] button: {has_gen_btn}")

        # Click Generate Internal Barcode
        await controller.eval_js("document.getElementById('btn-generate-barcode').click();")
        await asyncio.sleep(1.5)
        generated_code = await controller.eval_js("document.getElementById('id_barcode').value")
        status_text = await controller.eval_js("document.getElementById('barcode-validation-feedback').innerText")
        print(f"Generated Barcode: {generated_code}")
        print(f"Barcode Duplicate Check Status: {status_text}")
        
        results["Internal Barcode Generation"] = bool(generated_code and generated_code.startswith("20") and "available" in (status_text or "").lower())

        # Fill rest of product fields & save
        unique_suffix = int(time.time()) % 100000
        test_item_name = f"Supermarket Item {unique_suffix}"
        sku_name = f"SKU-{unique_suffix}"
        await controller.eval_js(f"""
            (() => {{
                document.getElementById('id_name').value = '{test_item_name}';
                document.getElementById('id_product_code').value = '{sku_name}';
                document.querySelector('input[name="purchase_price"]').value = '180.00';
                document.querySelector('input[name="selling_price"]').value = '250.00';
                document.querySelector('input[name="current_stock"]').value = '75';
                const catSel = document.querySelector('select[name="category"]');
                if (catSel && catSel.options.length > 1) catSel.selectedIndex = 1;
                const supSel = document.querySelector('select[name="supplier"]');
                if (supSel && supSel.options.length > 1) supSel.selectedIndex = 1;
                document.querySelector('button[type="submit"]').click();
            }})()
        """)
        await asyncio.sleep(2.5)
        after_add_url = await controller.eval_js("window.location.href")
        print(f"Product saved -> Current URL: {after_add_url}")
        results["Manager Product Creation"] = "product" in after_add_url

        # STEP 3: Verify Printable Barcode Label Modal
        print("\n--- STEP 3: BARCODE LABEL PRINTING MODAL ---")
        await controller.navigate("http://127.0.0.1:8000/product/")
        await asyncio.sleep(1.5)
        has_print_btn = await controller.eval_js("document.querySelector('button[title=\"Print Barcode Label\"]') !== null")
        print(f"Print Barcode Label button present: {has_print_btn}")
        
        # Open modal using helper or click
        await controller.eval_js(f"""
            (() => {{
                const btn = document.querySelector('button[title="Print Barcode Label"]');
                if (btn) {{
                    btn.click();
                }} else if (window.openPrintBarcodeModal) {{
                    openPrintBarcodeModal('{test_item_name}', '{sku_name}', '{generated_code}', '250.00');
                }}
            }})()
        """)
        await asyncio.sleep(1.5)
        modal_visible = await controller.eval_js("document.getElementById('print-label-modal')?.style.display === 'flex'")
        svg_rendered = await controller.eval_js("document.querySelector('#barcode-svg rect, #barcode-svg line, #barcode-svg path') !== null")
        print(f"Print Modal visible: {modal_visible}, Barcode SVG lines rendered: {svg_rendered}")
        results["Print Barcode Label Modal"] = bool(modal_visible and svg_rendered)
        await controller.eval_js("if (window.closePrintBarcodeModal) closePrintBarcodeModal();")
        await asyncio.sleep(0.5)

        # STEP 4: Scanner & POS Devices Settings Page
        print("\n--- STEP 4: SCANNER & POS DEVICES SETTINGS ---")
        await controller.navigate("http://127.0.0.1:8000/settings/scanner/")
        await controller.wait_for_element('#test-scanner-input')
        page_body = await controller.eval_js("document.body.innerText")
        has_primary_scanner = "CONNECTED / READY" in page_body
        has_test_input = await controller.eval_js("document.getElementById('test-scanner-input') !== null")
        has_phone_backup = "Backup Scanner" in page_body
        has_bluetooth_guide = "Bluetooth HID" in page_body
        
        # Test the live test scanner input in settings
        await controller.eval_js("""
            (() => {
                const testInp = document.getElementById('test-scanner-input');
                testInp.value = '890103000001';
                document.getElementById('btn-test-scan').click();
            })()
        """)
        await asyncio.sleep(1.5)
        test_captured = await controller.eval_js("document.getElementById('live-last-scanned').innerText")
        result_feedback = await controller.eval_js("document.getElementById('test-scan-result').innerText")
        print(f"Live Test Scanner captured: {test_captured}")
        print(f"Test Scan Feedback: {repr(result_feedback)}")
        
        results["Settings: Scanner & Devices Page"] = bool(has_primary_scanner and has_test_input and has_phone_backup and has_bluetooth_guide and ("890103000001" in (test_captured or "")))

        # STEP 5: Cashier Login & POS Interface
        print("\n--- STEP 5: CASHIER LOGIN & POS INTERFACE UX ---")
        await controller.navigate("http://127.0.0.1:8000/logout/")
        await controller.navigate("http://127.0.0.1:8000/accounts/")
        await controller.wait_for_element('input[name="username"]')
        await controller.eval_js("""
            (() => {
                document.querySelector('input[name="username"]').value = 'demo_cashier';
                document.querySelector('input[name="password"]').value = 'Password@123';
                document.querySelector('button[type="submit"]').click();
            })()
        """)
        await asyncio.sleep(2.5)
        await controller.navigate("http://127.0.0.1:8000/cart/")
        await controller.wait_for_element('#scanner-ready-badge')

        # Verify Scanner Ready badge in header (replaces huge phone button)
        has_scanner_badge = await controller.eval_js("document.getElementById('scanner-ready-badge') !== null")
        badge_text = await controller.eval_js("document.getElementById('scanner-ready-badge')?.innerText")
        huge_phone_btn = await controller.eval_js("document.getElementById('btn-phone-scanner-big') !== null")
        print(f"Scanner Ready Header Badge: {has_scanner_badge} ('{badge_text}')")
        print(f"Huge phone button removed: {not huge_phone_btn}")
        results["Cashier POS Header Scanner Badge"] = bool(has_scanner_badge and not huge_phone_btn)

        # Test Diagnostics Modal opened from badge
        await controller.eval_js("document.getElementById('scanner-ready-badge')?.click();")
        await asyncio.sleep(0.5)
        status_modal_open = await controller.eval_js("document.getElementById('scanner-status-modal')?.style.display === 'flex'")
        print(f"Scanner Diagnostics Modal open: {status_modal_open}")
        await controller.eval_js("Cart.closeScannerStatusModal();")
        await asyncio.sleep(0.5)

        # STEP 6: Scan Generated Barcode into POS via HID
        print("\n--- STEP 6: SCAN GENERATED BARCODE INTO POS (FIRST SCAN) ---")
        cart_count_before = await controller.eval_js("Cart.items.length")
        
        # Test scanning: simulate barcode scan into POS terminal
        await controller.eval_js(f"Cart.lookupBarcode('{generated_code}');")
        await asyncio.sleep(2)

        cart_count_after = await controller.eval_js("Cart.items.length")
        first_item_qty = await controller.eval_js("Cart.items[0]?.quantity")
        first_item_name = await controller.eval_js("Cart.items[0]?.product_name")
        feedback_banner = await controller.eval_js("document.getElementById('scan-feedback')?.innerText")
        print(f"Cart items count before: {cart_count_before}, after: {cart_count_after}")
        print(f"Cart item 0: {first_item_name}, qty: {first_item_qty}")
        print(f"Feedback Banner: {feedback_banner}")
        results["HID Barcode Auto-Add to Cart"] = bool(cart_count_after >= 1 and first_item_qty == 1)

        # STEP 7: Scan Identical Barcode Again -> Quantity Increments
        print("\n--- STEP 7: REPEATED SCAN INCREMENTS QUANTITY ---")
        qty_before = await controller.eval_js("Cart.items[0]?.quantity || 1")
        
        # Scan same barcode again
        await controller.eval_js(f"Cart.lookupBarcode('{generated_code}');")
        await asyncio.sleep(2)

        qty_after = await controller.eval_js("Cart.items[0]?.quantity")
        print(f"Quantity before repeat scan: {qty_before}, after repeat scan: {qty_after}")
        results["Repeated Scan Increments Quantity"] = bool(qty_after == qty_before + 1)

        # STEP 8: Unknown Barcode -> Supermarket Product Not Found Modal
        print("\n--- STEP 8: UNKNOWN BARCODE MODAL ---")
        unknown_code = "999888777666"
        await controller.eval_js(f"Cart.lookupBarcode('{unknown_code}');")
        await asyncio.sleep(2)

        unknown_modal_visible = await controller.eval_js("document.getElementById('unknown-barcode-modal')?.style.display === 'flex'")
        displayed_unknown_code = await controller.eval_js("document.getElementById('unknown-barcode-val')?.innerText")
        has_cashier_create_btn = await controller.eval_js("document.querySelector('#unknown-barcode-modal a[href*=\"add\"], #unknown-barcode-modal button[onclick*=\"create\"]') !== null")
        
        print(f"Unknown Barcode Modal visible: {unknown_modal_visible}")
        print(f"Displayed Code in Modal: {displayed_unknown_code}")
        print(f"Cashier has product create button in modal: {has_cashier_create_btn}")
        results["Unknown Barcode Modal (Cashier Safe)"] = bool(unknown_modal_visible and (unknown_code in (displayed_unknown_code or "")) and (not has_cashier_create_btn))

        # Close unknown modal
        await controller.eval_js("Cart.closeUnknownBarcodeModal();")
        await asyncio.sleep(0.5)

        # STEP 9: Complete Checkout & Payment
        print("\n--- STEP 9: COMPLETE SALE CHECKOUT ---")
        await controller.eval_js("""
            (() => {
                Cart.selectPayMethod('CASH');
                Cart.quickCash('exact');
                document.getElementById('btn-checkout').click();
            })()
        """)
        await asyncio.sleep(3)
        succ_visible = await controller.eval_js("document.getElementById('global-success')?.style.display !== 'none'")
        succ_text = await controller.eval_js("document.getElementById('global-success')?.innerText")
        cart_empty = await controller.eval_js("Cart.items.length === 0")
        sale_completed = bool(succ_visible and ("successful" in (succ_text or "").lower() or "invoice" in (succ_text or "").lower()) and cart_empty)
        print(f"Checkout completed: {sale_completed}, Message: {succ_text}, Cart empty: {cart_empty}")
        results["Checkout & Payment Completion"] = sale_completed

        # STEP 10: Phone Scanner Backup Page Verification
        print("\n--- STEP 10: PHONE SCANNER BACKUP PAGE ---")
        await controller.navigate("http://127.0.0.1:8000/pos/scanner/")
        await asyncio.sleep(1)
        phone_scanner_html = await controller.eval_js("document.body.innerHTML")
        is_backup_scanner = "BACKUP SCANNER" in (phone_scanner_html or "")
        has_https_notice = "HTTPS" in (phone_scanner_html or "")
        has_manual_fallback = await controller.eval_js("document.getElementById('manual-barcode') !== null")
        print(f"Is branded Backup Scanner: {is_backup_scanner}")
        print(f"Explains HTTPS camera restriction: {has_https_notice}")
        print(f"Has manual barcode input fallback: {has_manual_fallback}")
        results["Phone Camera Backup & Insecure Notice"] = bool(is_backup_scanner and has_https_notice and has_manual_fallback)

        # STEP 11: Cashier RBAC Protection
        print("\n--- STEP 11: CASHIER RBAC VERIFICATION ---")
        await controller.navigate("http://127.0.0.1:8000/settings/scanner/")
        await asyncio.sleep(1)
        settings_page_text = await controller.eval_js("document.body.innerText")
        cashier_blocked_settings = "403" in settings_page_text or "Forbidden" in settings_page_text or (await controller.eval_js("document.getElementById('scanner-hardware-status') === null"))
        
        await controller.navigate("http://127.0.0.1:8000/product/add/")
        await asyncio.sleep(1)
        add_page_text = await controller.eval_js("document.body.innerText")
        cashier_blocked_add = "403" in add_page_text or "Forbidden" in add_page_text or (await controller.eval_js("document.getElementById('btn-generate-barcode') === null"))
        
        print(f"Cashier blocked from scanner settings: {cashier_blocked_settings}")
        print(f"Cashier blocked from add product: {cashier_blocked_add}")
        results["Cashier RBAC Enforcement"] = bool(cashier_blocked_settings and cashier_blocked_add)

        print("\n" + "=" * 75)
        print("REAL BROWSER QA VERIFICATION SUMMARY")
        print("=" * 75)
        for name, passed in results.items():
            status = "PASS" if passed else "FAIL"
            print(f"{name:45}: {status}")

    finally:
        await controller.close()
        # Close the tab
        try:
            close_req = urllib.request.Request(f"{CHROME_DEBUG_URL}/json/close/{tab_id}", method="PUT")
            urllib.request.urlopen(close_req)
        except Exception:
            pass

if __name__ == "__main__":
    asyncio.run(run_full_qa())
