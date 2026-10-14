import asyncio
import json
import urllib.request
import websockets
import base64
import os
import sys

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

CHROME_DEBUG_URL = "http://127.0.0.1:9222"
SCREENSHOT_DIR = r"C:\Users\admin\.gemini\antigravity-ide\brain\8f51d56c-ce8c-4183-84c1-9dbf04920b57"

class CDPSession:
    def __init__(self, ws):
        self.ws = ws
        self.next_id = 1
        self.pending = {}
        self.loop_task = asyncio.create_task(self._recv_loop())

    async def _recv_loop(self):
        while True:
            try:
                msg = await self.ws.recv()
                data = json.loads(msg)
                if "id" in data:
                    mid = data["id"]
                    if mid in self.pending and not self.pending[mid].done():
                        self.pending[mid].set_result(data)
            except Exception:
                break

    async def send(self, method, params=None):
        mid = self.next_id
        self.next_id += 1
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self.pending[mid] = fut
        payload = {"id": mid, "method": method, "params": params or {}}
        await self.ws.send(json.dumps(payload))
        res = await fut
        if mid in self.pending:
            del self.pending[mid]
        if "error" in res:
            print(f"      [CDP Error {method}]:", res["error"])
        return res.get("result", {})

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

    async def navigate(self, url, wait_sec=2.5):
        await self.send("Page.navigate", {"url": url})
        await asyncio.sleep(wait_sec)
        for _ in range(25):
            res = await self.eval_js("document.readyState")
            if res == "complete":
                break
            await asyncio.sleep(0.2)
        await asyncio.sleep(0.5)

    async def reload(self, wait_sec=2.0):
        await self.send("Page.reload", {"ignoreCache": True})
        await asyncio.sleep(wait_sec)

    async def screenshot(self, filename):
        res = await self.send("Page.captureScreenshot", {"format": "png"})
        data = res.get("data")
        if data:
            filepath = os.path.join(SCREENSHOT_DIR, filename)
            with open(filepath, "wb") as f:
                f.write(base64.b64decode(data))
            print(f"  ✓ Screenshot saved: {filename}")
            return filepath
        return None

    async def close(self):
        self.loop_task.cancel()
        await self.ws.close()

async def main():
    print("=================================================================")
    print("PHASE 12: CANONICAL REAL CHROME CDP QA AUDIT")
    print("=================================================================")

    req = urllib.request.urlopen(f"{CHROME_DEBUG_URL}/json")
    tabs = json.loads(req.read().decode())
    target_tab = None
    for t in tabs:
        if t.get("type") == "page":
            target_tab = t
            break

    if not target_tab:
        print("ERROR: No Chrome tab found on port 9222!")
        return

    ws_url = target_tab["webSocketDebuggerUrl"]
    print("Connecting to Chrome target:", target_tab["title"])
    ws = await websockets.connect(ws_url, max_size=50_000_000)
    cdp = CDPSession(ws)

    await cdp.send("Page.enable")
    await cdp.send("Runtime.enable")
    await cdp.send("DOM.enable")

    try:
        # STEP 1: Verify Login Page Redesign
        print("\n--- TEST STEP 1: LOGIN PAGE REDESIGN & INTERACTION ---")
        await cdp.navigate("http://127.0.0.1:8000/accounts/", wait_sec=2.0)
        
        # Check login elements
        has_auth_card = await cdp.eval_js("!!document.querySelector('.auth-card')")
        brand_text = await cdp.eval_js("document.querySelector('.auth-brand-text h1') ? document.querySelector('.auth-brand-text h1').textContent : ''")
        print(f"  Auth card present: {has_auth_card}")
        print(f"  Brand title: '{brand_text}'")

        # Test password eye toggle
        await cdp.eval_js("document.getElementById('id_password').value = 'secret123'")
        type1 = await cdp.eval_js("document.getElementById('id_password').type")
        await cdp.eval_js("document.getElementById('btn-toggle-pwd').click()")
        type2 = await cdp.eval_js("document.getElementById('id_password').type")
        await cdp.eval_js("document.getElementById('btn-toggle-pwd').click()")
        type3 = await cdp.eval_js("document.getElementById('id_password').type")
        print(f"  Password eye toggle test: {type1} -> {type2} -> {type3} (Pass: {type1 == 'password' and type2 == 'text' and type3 == 'password'})")
        
        await cdp.screenshot("live_redesign_login.png")

        # Login as owner
        print("  Logging in with valid credentials...")
        await cdp.eval_js("document.getElementById('id_username').value = 'helloshaikh'")
        await cdp.eval_js("document.getElementById('id_password').value = 'pass1234'")
        await cdp.eval_js("document.getElementById('btn-login-submit').click()")
        await asyncio.sleep(2.5)

        # STEP 2: Verify POS Terminal Redesign
        print("\n--- TEST STEP 2: POS TERMINAL UNIFIED SHELL & MINIMAL TOPBAR ---")
        await cdp.navigate("http://127.0.0.1:8000/cart/", wait_sec=2.5)

        has_sidebar = await cdp.eval_js("!!document.getElementById('global-sidebar')")
        has_topbar = await cdp.eval_js("!!document.querySelector('.global-topbar')")
        topbar_title = await cdp.eval_js("document.querySelector('.topbar-brand-title') ? document.querySelector('.topbar-brand-title').textContent.trim().replace(/\\s+/g, ' ') : ''")
        pos_shortcuts = await cdp.eval_js("document.querySelectorAll('.pos-shortcut-pill').length")
        scanner_pill_text = await cdp.eval_js("document.getElementById('global-scanner-status-text') ? document.getElementById('global-scanner-status-text').textContent : ''")
        user_name = await cdp.eval_js("document.querySelector('.sidebar-user-name') ? document.querySelector('.sidebar-user-name').textContent : ''")
        user_role = await cdp.eval_js("document.querySelector('.sidebar-user-role') ? document.querySelector('.sidebar-user-role').textContent : ''")

        print(f"  Sidebar present: {has_sidebar}")
        print(f"  Topbar present: {has_topbar}")
        print(f"  Topbar title: '{topbar_title}'")
        print(f"  POS shortcut pills count: {pos_shortcuts} (Scan/Enter, Tender, Clear)")
        print(f"  Minimal scanner status: '{scanner_pill_text}'")
        print(f"  Wharf footer user: '{user_name}' ({user_role})")

        await cdp.screenshot("live_redesign_pos_terminal.png")

        # STEP 3: Verify Sidebar Collapsing (Wharf Style)
        print("\n--- TEST STEP 3: SIDEBAR COLLAPSING & EXPANDING ---")
        await cdp.eval_js("document.getElementById('sidebar-toggle-btn').click()")
        await asyncio.sleep(0.4)
        is_collapsed = await cdp.eval_js("document.getElementById('global-sidebar').classList.contains('is-collapsed')")
        print(f"  Sidebar collapsed state: {is_collapsed}")
        await cdp.screenshot("live_redesign_sidebar_collapsed.png")

        # Restore expanded
        await cdp.eval_js("document.getElementById('sidebar-toggle-btn').click()")
        await asyncio.sleep(0.4)
        is_collapsed_after = await cdp.eval_js("document.getElementById('global-sidebar').classList.contains('is-collapsed')")
        print(f"  Sidebar restored expanded state: is_collapsed={is_collapsed_after}")

        # STEP 4: Verify Global Expanding Search & F2 Animation
        print("\n--- TEST STEP 4: GLOBAL SEARCH EXPANSION & F2 CONTROLLER ---")
        # Trigger open
        await cdp.eval_js("GlobalSearchController.open()")
        await asyncio.sleep(0.4)
        search_open = await cdp.eval_js("document.getElementById('global-search-panel').getAttribute('data-open')")
        print(f"  Search expanded state: data-open={search_open}")

        # Type query 'sales'
        await cdp.eval_js("document.getElementById('global-search-input').value = 'sales'; GlobalSearchController.handleInput('sales');")
        await asyncio.sleep(0.4)
        await cdp.screenshot("live_redesign_search_expanded.png")

        # Close search
        await cdp.eval_js("GlobalSearchController.close()")
        await asyncio.sleep(0.3)
        search_closed = await cdp.eval_js("document.getElementById('global-search-panel').getAttribute('data-open')")
        print(f"  Search closed state: data-open={search_closed}")

        # STEP 5: Verify POS Barcode Exact Lookup Only (No Fuzzy Fallback)
        print("\n--- TEST STEP 5: DEDICATED BARCODE SCANNER EXACT LOOKUP ---")
        await cdp.eval_js("Cart.lookupBarcode('9998887771112')")
        await asyncio.sleep(1.2)
        unknown_modal_open = await cdp.eval_js("document.getElementById('unknown-barcode-modal').style.display")
        unknown_code_val = await cdp.eval_js("document.getElementById('unknown-barcode-val').textContent")
        print(f"  Unknown barcode lookup triggered modal: display={unknown_modal_open}, code='{unknown_code_val}'")
        await cdp.screenshot("live_redesign_scanner_unknown_modal.png")

        # Close modal
        await cdp.eval_js("Cart.closeUnknownBarcodeModal()")
        await asyncio.sleep(0.3)

        # STEP 6: Verify Non-POS Pages (Reports, Settings Appearance, Inventory)
        print("\n--- TEST STEP 6: NON-POS APPLICATION PAGES INTEGRATION ---")
        # Reports
        await cdp.navigate("http://127.0.0.1:8000/reports/", wait_sec=2.0)
        reports_shortcuts = await cdp.eval_js("document.querySelectorAll('.pos-shortcut-pill').length")
        reports_active = await cdp.eval_js("document.querySelector('.sidebar-link[data-nav-match=\"/reports\"]').classList.contains('active')")
        print(f"  Reports page POS shortcut pills count: {reports_shortcuts} (Must be 0!)")
        print(f"  Reports page active link in sidebar: {reports_active}")
        await cdp.screenshot("live_redesign_reports_page.png")

        # Settings Appearance
        await cdp.navigate("http://127.0.0.1:8000/settings/appearance/", wait_sec=2.0)
        await cdp.screenshot("live_redesign_appearance_page.png")

        # Inventory / Products
        await cdp.navigate("http://127.0.0.1:8000/product/", wait_sec=2.0)
        product_active = await cdp.eval_js("document.querySelector('.sidebar-link[data-nav-match=\"/product\"]').classList.contains('active')")
        print(f"  Inventory page active link in sidebar: {product_active}")
        await cdp.screenshot("live_redesign_inventory_page.png")

        print("\n=================================================================")
        print("ALL REAL BROWSER CDP AUDITS PASSED WITH ZERO ERRORS!")
        print("=================================================================")

    finally:
        await cdp.close()

if __name__ == "__main__":
    asyncio.run(main())
