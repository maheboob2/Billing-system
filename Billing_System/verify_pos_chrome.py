import asyncio
import json
import urllib.request
import websockets
import time
import base64
import os

CHROME_DEBUG_URL = "http://127.0.0.1:9222"
SCREENSHOT_DIR = r"C:\Users\admin\.gemini\antigravity-ide\brain\8f51d56c-ce8c-4183-84c1-9dbf04920b57"

class ChromeRunner:
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

    async def screenshot(self, filename):
        res = await self.send("Page.captureScreenshot", {"format": "png"})
        data = res.get("data")
        if data:
            filepath = os.path.join(SCREENSHOT_DIR, filename)
            with open(filepath, "wb") as f:
                f.write(base64.b64decode(data))
            print(f"  [Screenshot saved]: {filename}")
            return filepath
        return None

async def run_verification():
    print("=== STARTING POS REAL CHROME VERIFICATION ===")
    req = urllib.request.urlopen(f"{CHROME_DEBUG_URL}/json")
    tabs = json.loads(req.read().decode())
    target_tab = None
    for t in tabs:
        if t.get("type") == "page":
            target_tab = t
            break

    if not target_tab:
        print("ERROR: No suitable Chrome tab found.")
        return

    runner = ChromeRunner(target_tab["webSocketDebuggerUrl"])
    await runner.connect()

    try:
        # Step 1: Login
        print("\nStep 1: Navigating to login page...")
        await runner.navigate("http://127.0.0.1:8000/login/", wait_sec=2.0)
        
        is_login_page = await runner.eval_js("(() => document.querySelector('input[name=\"username\"]') !== null)()")
        if is_login_page:
            print("  Logging in as helloshaikh...")
            await runner.eval_js("""(() => {
                const u = document.querySelector('input[name="username"]');
                const p = document.querySelector('input[name="password"]');
                if (u && p) {
                    u.value = 'helloshaikh';
                    p.value = 'pass1234';
                    document.querySelector('form').submit();
                }
            })()""")
            await asyncio.sleep(2.5)

        # Step 2: Navigate to POS Cart (/cart/)
        print("\nStep 2: Navigating to POS (/cart/)...")
        await runner.navigate("http://127.0.0.1:8000/cart/", wait_sec=2.5)

        title = await runner.eval_js("document.title")
        print(f"  Page title: {title}")

        barcode_input_exists = await runner.eval_js("(() => document.getElementById('barcode-scanner-input') !== null)()")
        search_input_exists = await runner.eval_js("(() => document.getElementById('product-search') !== null)()")
        print(f"  Dedicated Barcode Scanner input exists: {barcode_input_exists}")
        print(f"  Dedicated Product Search input exists: {search_input_exists}")

        # Step 3: Test Bug #2 (Barcode Input typing 'A' and 'AM')
        print("\nStep 3: Testing Barcode Input with partial characters 'A' and 'AM'...")
        await runner.eval_js("""(() => {
            Cart.clear();
            const bi = document.getElementById('barcode-scanner-input');
            bi.focus();
            bi.value = 'A';
            bi.dispatchEvent(new Event('input', { bubbles: true }));
        })()""")
        await asyncio.sleep(0.5)

        cart_count = await runner.eval_js("Cart.items.length")
        search_results_visible = await runner.eval_js("""(() => {
            const sr = document.getElementById('search-results');
            return sr ? (sr.style.display !== 'none' && sr.children.length > 0) : false;
        })()""")
        print(f"  Typing 'A' into barcode input -> Suggestions visible: {search_results_visible}, Cart items: {cart_count}")
        await runner.screenshot("pos_barcode_input_A.png")

        # Type 'AM'
        await runner.eval_js("""(() => {
            const bi = document.getElementById('barcode-scanner-input');
            bi.value = 'AM';
            bi.dispatchEvent(new Event('input', { bubbles: true }));
        })()""")
        await asyncio.sleep(0.5)

        cart_count = await runner.eval_js("Cart.items.length")
        search_results_visible = await runner.eval_js("""(() => {
            const sr = document.getElementById('search-results');
            return sr ? (sr.style.display !== 'none' && sr.children.length > 0) : false;
        })()""")
        print(f"  Typing 'AM' into barcode input -> Suggestions visible: {search_results_visible}, Cart items: {cart_count}")
        await runner.screenshot("pos_barcode_input_AM.png")

        # Step 4: Test Exact Barcode Scan (890126201001)
        print("\nStep 4: Testing Exact Barcode Scan for Amul Taaza Milk 1L (890126201001)...")
        await runner.eval_js("""(() => {
            Cart.clear();
            const bi = document.getElementById('barcode-scanner-input');
            bi.value = '890126201001';
            bi.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
        })()""")
        await asyncio.sleep(1.5)

        item_info = await runner.eval_js("""(() => {
            if (Cart.items.length === 0) return null;
            return {
                name: Cart.items[0].product_name,
                qty: Cart.items[0].quantity,
                avail: Cart.items[0].available_stock,
                unit_price: Cart.items[0].unit_price
            };
        })()""")
        print(f"  After scanning exact barcode 890126201001: {item_info}")
        await runner.screenshot("pos_amul_added.png")

        # Step 5: Test Bug #1 (Stock Bounds - Available 2307, Cart 2307, Try 2308)
        print("\nStep 5: Testing Stock Bounds for Amul Taaza Milk 1L (Stock: 2307)...")
        # Increase quantity directly to 2307
        await runner.eval_js("Cart.updateQty(20, 2307);")
        await asyncio.sleep(1.0)

        cart_state_2307 = await runner.eval_js("""(() => {
            const item = Cart.items[0];
            const incBtn = document.querySelector('.btn-qty-inc');
            const previewAddBtn = document.getElementById('btn-add-preview');
            const previewRem = document.getElementById('preview-cart-remaining');
            return {
                cartQty: item ? item.quantity : null,
                availableStock: item ? item.available_stock : null,
                incBtnDisabled: incBtn ? incBtn.disabled : null,
                previewAddBtnDisabled: previewAddBtn ? previewAddBtn.disabled : null,
                previewRemText: previewRem ? previewRem.innerText : null
            };
        })()""")
        print(f"  At Quantity 2307: {cart_state_2307}")
        await runner.screenshot("pos_amul_max_stock_2307.png")

        # Try to scan barcode 890126201001 again while already at 2307
        print("  Attempting to scan barcode 890126201001 again when cart is at 2307...")
        await runner.eval_js("""(() => {
            const bi = document.getElementById('barcode-scanner-input');
            bi.value = '890126201001';
            bi.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
        })()""")
        await asyncio.sleep(1.0)

        post_scan_state = await runner.eval_js("""(() => {
            const item = Cart.items[0];
            const feedback = document.getElementById('scan-feedback');
            return {
                cartQty: item ? item.quantity : null,
                feedbackText: feedback ? feedback.innerText : null,
                feedbackVisible: feedback ? feedback.style.display !== 'none' : false
            };
        })()""")
        print(f"  After repeated scan attempt at max stock: {post_scan_state}")
        await runner.screenshot("pos_scan_reject_stock.png")

        # Try to click the disabled '+' button or call updateQty(20, 2308)
        print("  Attempting updateQty(20, 2308)...")
        await runner.eval_js("Cart.updateQty(20, 2308);")
        await asyncio.sleep(0.5)

        post_inc_state = await runner.eval_js("""(() => {
            const item = Cart.items[0];
            const feedback = document.getElementById('scan-feedback');
            return {
                cartQty: item ? item.quantity : null,
                feedbackText: feedback ? feedback.innerText : null
            };
        })()""")
        print(f"  After attempt to exceed stock to 2308: {post_inc_state}")

        # Step 6: Clear Cart
        print("\nStep 6: Clearing cart...")
        await runner.eval_js("Cart.clear();")
        await asyncio.sleep(0.5)
        cleared_count = await runner.eval_js("Cart.items.length")
        print(f"  Cart items count after clear: {cleared_count}")

        # Step 7: Test Separate Product Search Field (#product-search)
        print("\nStep 7: Testing Separate Product Search Field with 'Amul'...")
        await runner.eval_js("""(() => {
            const ps = document.getElementById('product-search');
            ps.value = 'Amul';
            ps.dispatchEvent(new Event('input', { bubbles: true }));
        })()""")
        await asyncio.sleep(1.0)

        search_state = await runner.eval_js("""(() => {
            const sr = document.getElementById('search-results');
            const cards = sr ? Array.from(sr.querySelectorAll('.catalog-card .name')).map(el => el.innerText) : [];
            return {
                visible: sr ? sr.style.display !== 'none' : false,
                foundProducts: cards
            };
        })()""")
        print(f"  Product Search results for 'Amul': {search_state}")
        await runner.screenshot("pos_product_search_amul.png")

        # Step 8: Test Unknown Barcode Modal (999999999999)
        print("\nStep 8: Testing Unknown Barcode (999999999999)...")
        await runner.eval_js("""(() => {
            const bi = document.getElementById('barcode-scanner-input');
            bi.value = '999999999999';
            bi.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
        })()""")
        await asyncio.sleep(1.0)

        modal_state = await runner.eval_js("""(() => {
            const m = document.getElementById('unknown-barcode-modal');
            const codeEl = document.getElementById('unknown-barcode-val');
            return {
                visible: m ? m.style.display !== 'none' : false,
                scannedCode: codeEl ? codeEl.innerText : null
            };
        })()""")
        print(f"  Unknown Barcode modal state: {modal_state}")
        await runner.screenshot("pos_unknown_barcode_modal.png")

        print("\n=== VERIFICATION COMPLETED SUCCESSFULLY ===")

    finally:
        await runner.close()

if __name__ == '__main__':
    asyncio.run(run_verification())
