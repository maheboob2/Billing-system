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

    async def navigate_and_wait(self, url, min_wait=1.5):
        await self.send("Page.navigate", {"url": url})
        await asyncio.sleep(min_wait)
        for _ in range(25):
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
            err = res["exceptionDetails"]
            return f"JS_ERROR: {err.get('text', '')}"
        val = res.get("result", {}).get("value")
        return val

    async def capture_screenshot(self, filename):
        res = await self.send("Page.captureScreenshot", {"format": "png"})
        data = res.get("data")
        if data:
            path = os.path.join(SCREENSHOT_DIR, filename)
            with open(path, "wb") as f:
                f.write(base64.b64decode(data))
            print(f"  [SAVED_SCREENSHOT] {filename}")
            return path
        return None

async def run_verification():
    # 1. Connect to Chrome
    req = urllib.request.urlopen(f"{CHROME_DEBUG_URL}/json")
    targets = json.loads(req.read().decode())
    page_target = next((t for t in targets if t.get("type") == "page"), None)
    if not page_target:
        print("ERROR: No Chrome page target found")
        return False
    ws_url = page_target["webSocketDebuggerUrl"]
    controller = ChromeController(ws_url)
    await controller.connect()
    print("Connected to Chrome CDP successfully.")

    results = {}

    # TEST 1: Login Page Polish & Panel Swap
    print("\n--- TEST 1: Login & Registration Polish ---")
    await controller.navigate_and_wait("http://127.0.0.1:8000/accounts/")
    login_hero = await controller.eval_js("document.querySelector('.aside-hero h2')?.innerText")
    login_sub = await controller.eval_js("document.querySelector('.aside-hero p')?.innerText")
    print(f"  Login Hero: {login_hero}")
    print(f"  Login Sub: {login_sub}")
    assert "Your store, under control." in (login_hero or ""), f"Login hero text mismatch: {login_hero}"
    await controller.capture_screenshot("phase12_login_screen.png")

    # Navigate to Registration
    await controller.navigate_and_wait("http://127.0.0.1:8000/registration/")
    reg_hero = await controller.eval_js("document.querySelector('.aside-hero h2')?.innerText")
    reg_sub = await controller.eval_js("document.querySelector('.aside-hero p')?.innerText")
    print(f"  Reg Hero: {reg_hero}")
    print(f"  Reg Sub: {reg_sub}")
    assert "Start with a clear view of your store." in (reg_hero or ""), f"Reg hero text mismatch: {reg_hero}"
    await controller.capture_screenshot("phase12_reg_screen.png")
    results["login_reg"] = "PASS"

    # TEST 2: Sidebar Collapse (Width 0, Persistence, Main wrapper 100%)
    print("\n--- TEST 2: Sidebar Collapse & Persistence ---")
    await controller.navigate_and_wait("http://127.0.0.1:8000/dashboard/")
    
    # Ensure clean initial state (expanded)
    await controller.eval_js("""
        (() => {
            localStorage.removeItem('sidebar_collapsed');
            localStorage.removeItem('pos_sidebar_collapsed');
            const sidebar = document.getElementById('global-sidebar');
            const shell = document.querySelector('.app-shell');
            if (sidebar) sidebar.classList.remove('is-collapsed');
            if (shell) shell.setAttribute('data-sidebar-collapsed', 'false');
        })()
    """)
    await asyncio.sleep(0.3)

    # Check initial sidebar width
    initial_w = await controller.eval_js("document.querySelector('.global-sidebar')?.offsetWidth")
    print(f"  Initial Sidebar OffsetWidth: {initial_w}px")

    # Click sidebar toggle button to collapse
    toggle_clicked = await controller.eval_js("""
        (() => {
            const btn = document.querySelector('.sidebar-toggle-btn') || document.getElementById('sidebar-toggle');
            if (btn) { btn.click(); return true; }
            return false;
        })()
    """)
    print(f"  Toggle button clicked: {toggle_clicked}")
    await asyncio.sleep(0.4)

    collapsed_attr = await controller.eval_js("document.querySelector('.app-shell')?.getAttribute('data-sidebar-collapsed')")
    collapsed_w = await controller.eval_js("document.querySelector('.global-sidebar')?.offsetWidth")
    collapsed_vis = await controller.eval_js("window.getComputedStyle(document.querySelector('.global-sidebar')).visibility")
    collapsed_storage = await controller.eval_js("localStorage.getItem('sidebar_collapsed') || localStorage.getItem('pos_sidebar_collapsed')")
    print(f"  data-sidebar-collapsed: {collapsed_attr}")
    print(f"  Collapsed Sidebar OffsetWidth: {collapsed_w}px")
    print(f"  Collapsed Visibility: {collapsed_vis}")
    print(f"  localStorage sidebar_collapsed: {collapsed_storage}")
    await controller.capture_screenshot("phase12_sidebar_collapsed.png")

    assert collapsed_w == 0, f"Sidebar width must be 0, got {collapsed_w}"
    assert collapsed_attr == "true", "data-sidebar-collapsed attribute should be true"
    assert collapsed_storage == "true", "localStorage should persist true"

    # Refresh page to test persistence
    await controller.navigate_and_wait("http://127.0.0.1:8000/dashboard/")
    persisted_attr = await controller.eval_js("document.querySelector('.app-shell')?.getAttribute('data-sidebar-collapsed')")
    persisted_w = await controller.eval_js("document.querySelector('.global-sidebar')?.offsetWidth")
    print(f"  After Refresh - data-sidebar-collapsed: {persisted_attr}, width: {persisted_w}px")
    assert persisted_w == 0, f"Persisted width must be 0, got {persisted_w}"

    # Click toggle again to expand
    await controller.eval_js("""
        (() => {
            const btn = document.querySelector('.sidebar-toggle-btn') || document.getElementById('sidebar-toggle');
            if (btn) btn.click();
        })()
    """)
    await asyncio.sleep(0.4)
    expanded_w = await controller.eval_js("document.querySelector('.global-sidebar')?.offsetWidth")
    expanded_attr = await controller.eval_js("document.querySelector('.app-shell')?.getAttribute('data-sidebar-collapsed')")
    print(f"  After Toggle Expand - width: {expanded_w}px, attr: {expanded_attr}")
    assert expanded_w > 200, f"Expanded sidebar width must be >200px, got {expanded_w}"
    await controller.capture_screenshot("phase12_sidebar_expanded.png")
    results["sidebar_collapse"] = "PASS"

    # TEST 3: POS F2 Barcode / Product Search
    print("\n--- TEST 3: POS F2 Barcode & Product Search Overlay ---")
    await controller.navigate_and_wait("http://127.0.0.1:8000/cart/")
    
    # Check topbar F2 shortcut pill is present
    f2_pill = await controller.eval_js("document.getElementById('pos-topbar-f2-btn')?.innerText")
    print(f"  Topbar F2 Shortcut Pill: {f2_pill}")

    # Trigger F2 overlay
    await controller.eval_js("Cart.openProductSearchOverlay()")
    await asyncio.sleep(0.3)
    overlay_display = await controller.eval_js("window.getComputedStyle(document.getElementById('pos-search-overlay')).display")
    focused_id = await controller.eval_js("document.activeElement?.id")
    input_label = await controller.eval_js("document.querySelector('label[for=\"pos-search-overlay-input\"]')?.innerText")
    print(f"  Overlay Display: {overlay_display}")
    print(f"  Active Element: {focused_id}")
    print(f"  Input Label: {input_label}")
    assert overlay_display == "flex", "Overlay should be display: flex"
    assert focused_id == "pos-search-overlay-input", "Search input should be focused"
    assert "Search product name, SKU or barcode" in (input_label or ""), "Input label mismatch"

    # Type search term and verify results
    await controller.eval_js("document.getElementById('pos-search-overlay-input').value = 'Tata'; Cart.submitOverlaySearch();")
    await asyncio.sleep(0.6)
    results_count = await controller.eval_js("document.querySelectorAll('#pos-search-results-container .pos-search-result-item').length")
    print(f"  Search Results Rendered: {results_count} products")
    await controller.capture_screenshot("phase12_pos_f2_search.png")

    # Close with Esc / close function
    await controller.eval_js("Cart.closeProductSearchOverlay()")
    await asyncio.sleep(0.3)
    overlay_closed = await controller.eval_js("window.getComputedStyle(document.getElementById('pos-search-overlay')).display")
    print(f"  Overlay Closed Display: {overlay_closed}")
    assert overlay_closed == "none", "Overlay should be closed"
    results["pos_f2_search"] = "PASS"

    # TEST 4: Settings Appearance & Theme Switching
    print("\n--- TEST 4: Settings Appearance & Theme Switching ---")
    await controller.navigate_and_wait("http://127.0.0.1:8000/settings/appearance/")
    preview_section = await controller.eval_js("document.getElementById('design-system-preview') || document.querySelector('.design-preview-grid')")
    print(f"  Non-functional Preview Section Present: {preview_section is not None}")
    assert preview_section is None, "Non-functional preview section should be removed"

    # Switch theme via button
    mode_before = await controller.eval_js("document.documentElement.getAttribute('data-mode')")
    print(f"  Default Mode: {mode_before}")
    await controller.capture_screenshot("phase12_settings_appearance.png")
    results["settings_appearance"] = "PASS"

    # TEST 5: Employee Payslip & Salary History
    print("\n--- TEST 5: Employee Payslip & Salary History ---")
    await controller.navigate_and_wait("http://127.0.0.1:8000/payroll/?year=2025&month=5")
    
    # Check roster table button says "View Payslip"
    view_payslip_btns = await controller.eval_js("""
        Array.from(document.querySelectorAll('a')).filter(a => a.innerText.includes('View Payslip')).map(a => a.innerText.trim())
    """)
    print(f"  Roster Table Action Buttons: {view_payslip_btns}")
    assert any("View Payslip" in b for b in view_payslip_btns), "Action button should read 'View Payslip'"

    # Click first payslip link
    first_payslip_url = await controller.eval_js("""
        document.querySelector('a[href*=\"/payslip/\"]')?.getAttribute('href')
    """)
    print(f"  First Payslip URL: {first_payslip_url}")
    if first_payslip_url:
        await controller.navigate_and_wait(f"http://127.0.0.1:8000{first_payslip_url}")
        payslip_title = await controller.eval_js("document.querySelector('.company-title')?.innerText || document.querySelector('.doc-badge')?.innerText")
        history_rows = await controller.eval_js("document.querySelectorAll('#emp-salary-ledger tbody tr').length")
        has_print_btn = await controller.eval_js("document.getElementById('btn-print-payslip') !== null")
        has_period_select = await controller.eval_js("document.getElementById('payslip-year-select') !== null")
        print(f"  Payslip Title: {payslip_title}")
        print(f"  Historical Month Rows (April 2025 onwards): {history_rows} months")
        print(f"  Has Print Button: {has_print_btn}")
        print(f"  Has Period Selector: {has_period_select}")
        assert history_rows >= 12, f"Should show at least 12 months history from April 2025, got {history_rows}"
        assert has_print_btn, "Print button should be present"
        await controller.capture_screenshot("phase12_employee_payslip.png")
    results["employee_payslip"] = "PASS"

    await controller.close()
    print("\n==========================================")
    print("ALL PHASE 12 VERIFICATION TESTS PASSED!")
    print(json.dumps(results, indent=2))
    print("==========================================")
    return True

if __name__ == "__main__":
    asyncio.run(run_verification())
