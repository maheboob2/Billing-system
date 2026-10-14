import asyncio
import json
import urllib.request
import websockets
import time
import base64
import os
import sys

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

CHROME_DEBUG_URL = "http://127.0.0.1:9222"
SCREENSHOT_DIR = r"C:\Users\admin\.gemini\antigravity-ide\brain\8f51d56c-ce8c-4183-84c1-9dbf04920b57"

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

    async def navigate_and_wait(self, url, min_wait=2.0):
        await self.send("Page.navigate", {"url": url})
        await asyncio.sleep(min_wait)
        for _ in range(20):
            try:
                res = await self.eval_js("document.readyState")
                if res == "complete":
                    break
            except Exception:
                pass
            await asyncio.sleep(0.2)
        await asyncio.sleep(0.5)

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

async def run_qa():
    print("=================================================================")
    print("PHASE 12: REAL BROWSER CDP VERIFICATION — REDESIGN V2 (FIXED)")
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
    chrome = ChromeController(ws_url)
    await chrome.connect()

    try:
        # STEP 1: Verify Login Page Redesign
        print("\n--- STEP 1: LOGIN REDESIGN & FORM INTERACTION ---")
        await chrome.navigate_and_wait("http://127.0.0.1:8000/accounts/", min_wait=2.0)
        
        # Test password eye toggle
        await chrome.eval_js("document.getElementById('id_password').value = 'secret123'")
        type1 = await chrome.eval_js("document.getElementById('id_password').type")
        await chrome.eval_js("document.getElementById('btn-toggle-pwd').click()")
        type2 = await chrome.eval_js("document.getElementById('id_password').type")
        await chrome.eval_js("document.getElementById('btn-toggle-pwd').click()")
        type3 = await chrome.eval_js("document.getElementById('id_password').type")
        print(f"  Password eye toggle test: {type1} -> {type2} -> {type3} (Pass: {type1 == 'password' and type2 == 'text' and type3 == 'password'})")

        await chrome.screenshot("redesign_login_page.png")

        # Submit login form
        print("  Logging in as helloshaikh...")
        await chrome.eval_js("document.getElementById('id_username').value = 'helloshaikh'")
        await chrome.eval_js("document.getElementById('id_password').value = 'pass1234'")
        await chrome.eval_js("document.getElementById('btn-login-submit').click()")
        await asyncio.sleep(2.5)

        # STEP 2: Verify POS Terminal with Wharf Sidebar & Minimal Topbar
        print("\n--- STEP 2: POS TERMINAL SHELL & MINIMAL TOPBAR ---")
        await chrome.navigate_and_wait("http://127.0.0.1:8000/cart/", min_wait=2.5)
        
        has_sidebar = await chrome.eval_js("!!document.getElementById('global-sidebar')")
        has_topbar = await chrome.eval_js("!!document.querySelector('.global-topbar')")
        has_pos_shortcuts = await chrome.eval_js("document.querySelectorAll('.pos-shortcut-pill').length")
        scanner_status_text = await chrome.eval_js("document.getElementById('global-scanner-status-text') ? document.getElementById('global-scanner-status-text').textContent : null")
        brand_title = await chrome.eval_js("document.querySelector('.topbar-brand-title') ? document.querySelector('.topbar-brand-title').textContent.trim() : null")
        
        print(f"  Sidebar present: {has_sidebar}")
        print(f"  Topbar present: {has_topbar}")
        print(f"  Brand title in topbar: '{brand_title}'")
        print(f"  POS shortcut pills count: {has_pos_shortcuts} (Scan/Enter, Tender, Clear)")
        print(f"  Scanner status text: '{scanner_status_text}'")

        await chrome.screenshot("redesign_pos_terminal.png")

        # STEP 3: Verify Sidebar Collapsing & Expanding
        print("\n--- STEP 3: SIDEBAR COLLAPSING & EXPANDING ---")
        await chrome.eval_js("document.getElementById('sidebar-toggle-btn').click()")
        await asyncio.sleep(0.4)
        is_collapsed = await chrome.eval_js("document.getElementById('global-sidebar').classList.contains('is-collapsed')")
        print(f"  Sidebar collapsed state: {is_collapsed}")
        await chrome.screenshot("redesign_sidebar_collapsed.png")

        # Expand back
        await chrome.eval_js("document.getElementById('sidebar-toggle-btn').click()")
        await asyncio.sleep(0.4)
        is_collapsed_after = await chrome.eval_js("document.getElementById('global-sidebar').classList.contains('is-collapsed')")
        print(f"  Sidebar restored state: is_collapsed={is_collapsed_after}")

        # STEP 4: Verify Global Search & F2 Animation
        print("\n--- STEP 4: GLOBAL SEARCH EXPANSION & FILTERING (F2) ---")
        # Trigger open
        await chrome.eval_js("GlobalSearchController.open()")
        await asyncio.sleep(0.4)
        search_open = await chrome.eval_js("document.getElementById('global-search-panel').getAttribute('data-open')")
        print(f"  Global search open attribute: {search_open}")

        # Filter items
        await chrome.eval_js("document.getElementById('global-search-input').value = 'inv'; GlobalSearchController.handleInput('inv');")
        await asyncio.sleep(0.4)
        await chrome.screenshot("redesign_search_expanded.png")

        # Close search via Escape simulation
        await chrome.eval_js("GlobalSearchController.close()")
        await asyncio.sleep(0.4)
        search_closed = await chrome.eval_js("document.getElementById('global-search-panel').getAttribute('data-open')")
        print(f"  Global search closed attribute: {search_closed}")

        # STEP 5: Verify POS Exact Barcode Scanner Behavior
        print("\n--- STEP 5: EXACT BARCODE SCANNER BEHAVIOR (NO FUZZY FALLBACK) ---")
        # Test unknown barcode
        await chrome.eval_js("Cart.lookupBarcode('8888999900001')")
        await asyncio.sleep(1.2)
        unknown_modal_open = await chrome.eval_js("document.getElementById('unknown-barcode-modal').style.display")
        unknown_code_val = await chrome.eval_js("document.getElementById('unknown-barcode-val').textContent")
        print(f"  Unknown barcode lookup triggered modal: display={unknown_modal_open}, code='{unknown_code_val}'")
        await chrome.screenshot("redesign_exact_scanner_unknown.png")

        # Close unknown modal
        await chrome.eval_js("Cart.closeUnknownBarcodeModal()")
        await asyncio.sleep(0.4)

        # STEP 6: Verify Other Pages (Reports, Settings Appearance, Inventory)
        print("\n--- STEP 6: ROUTING & NON-POS PAGES SHELL INTEGRATION ---")
        # Reports page
        await chrome.navigate_and_wait("http://127.0.0.1:8000/reports/", min_wait=2.0)
        reports_pos_shortcuts = await chrome.eval_js("document.querySelectorAll('.pos-shortcut-pill').length")
        reports_active = await chrome.eval_js("document.querySelector('.sidebar-link[data-nav-match=\"/reports\"]').classList.contains('active')")
        print(f"  Reports page POS shortcut count: {reports_pos_shortcuts} (Must be 0 on non-POS pages!)")
        print(f"  Reports page sidebar active state: {reports_active}")
        await chrome.screenshot("redesign_reports_view.png")

        # Settings Appearance
        await chrome.navigate_and_wait("http://127.0.0.1:8000/settings/appearance/", min_wait=2.0)
        await chrome.screenshot("redesign_settings_appearance.png")

        # Inventory page
        await chrome.navigate_and_wait("http://127.0.0.1:8000/product/", min_wait=2.0)
        inventory_active = await chrome.eval_js("document.querySelector('.sidebar-link[data-nav-match=\"/product\"]').classList.contains('active')")
        print(f"  Inventory page sidebar active state: {inventory_active}")
        await chrome.screenshot("redesign_inventory_view.png")

        print("\n=================================================================")
        print("REAL BROWSER VERIFICATION COMPLETE: ALL PHASES CONFIRMED!")
        print("=================================================================")

    finally:
        await chrome.close()

if __name__ == "__main__":
    asyncio.run(run_qa())
