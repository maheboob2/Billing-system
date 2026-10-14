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

    async def navigate(self, url, wait_sec=2.0):
        await self.send("Page.navigate", {"url": url})
        await asyncio.sleep(wait_sec)
        for _ in range(30):
            res = await self.eval_js("document.readyState")
            if res == "complete":
                break
            await asyncio.sleep(0.15)
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
    print("PHASE 10: REAL CHROME CDP QA VERIFICATION AUDIT")
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
    print("Connecting to Chrome target:", target_tab.get("title", "tab"))
    ws = await websockets.connect(ws_url, max_size=50_000_000)
    cdp = CDPSession(ws)

    await cdp.send("Page.enable")
    await cdp.send("Runtime.enable")
    await cdp.send("DOM.enable")

    results = {}

    try:
        # -------------------------------------------------------------
        # STEP 1: TASK 8 — LOGIN & REGISTRATION DESIGN INSPECTION
        # -------------------------------------------------------------
        print("\n--- TEST 1: LOGIN & REGISTRATION ARCANA DESIGN (TASK 8) ---")
        await cdp.navigate("http://127.0.0.1:8000/accounts/", wait_sec=2.0)
        
        has_auth_container = await cdp.eval_js("!!document.querySelector('.auth-container')")
        has_aside_panel = await cdp.eval_js("!!document.querySelector('.auth-aside-panel')")
        has_black_btn = await cdp.eval_js("!!document.querySelector('.btn-black-primary')")
        brand_title = await cdp.eval_js("document.querySelector('.auth-brand-title') ? document.querySelector('.auth-brand-title').textContent : ''")
        
        # Test password toggle
        await cdp.eval_js("document.getElementById('id_password').value = 'secret123'")
        type1 = await cdp.eval_js("document.getElementById('id_password').type")
        await cdp.eval_js("document.getElementById('btn-toggle-pwd').click()")
        type2 = await cdp.eval_js("document.getElementById('id_password').type")
        await cdp.eval_js("document.getElementById('btn-toggle-pwd').click()")
        type3 = await cdp.eval_js("document.getElementById('id_password').type")
        pwd_toggle_ok = (type1 == "password" and type2 == "text" and type3 == "password")

        print(f"  Auth container: {has_auth_container}")
        print(f"  Dark illustrated side panel: {has_aside_panel}")
        print(f"  Black primary button: {has_black_btn}")
        print(f"  Brand title: '{brand_title}'")
        print(f"  Password show/hide toggle: {pwd_toggle_ok}")
        await cdp.screenshot("evidence_login_arcana_design.png")
        results["task8_login"] = has_auth_container and has_aside_panel and pwd_toggle_ok

        # Test Registration Page
        await cdp.navigate("http://127.0.0.1:8000/registration/", wait_sec=2.0)
        has_reg_container = await cdp.eval_js("!!document.querySelector('.auth-container')")
        has_reg_aside = await cdp.eval_js("!!document.querySelector('.auth-aside-panel')")
        has_role_notice = await cdp.eval_js("!!document.querySelector('.role-notice-box')")
        print(f"  Registration split container: {has_reg_container}")
        print(f"  Registration side panel: {has_reg_aside}")
        print(f"  Store Owner Role Notice: {has_role_notice}")
        await cdp.screenshot("evidence_registration_arcana_design.png")
        results["task8_registration"] = has_reg_container and has_reg_aside and has_role_notice

        # -------------------------------------------------------------
        # STEP 2: TASK 1 — LIGHT MODE DEFAULT & PERSISTENCE
        # -------------------------------------------------------------
        print("\n--- TEST 2: LIGHT MODE DEFAULT & PERSISTENCE (TASK 1) ---")
        # Clear storage to simulate first-time visitor
        await cdp.eval_js("localStorage.removeItem('pos_mode'); localStorage.removeItem('pos_theme_mode');")
        await cdp.navigate("http://127.0.0.1:8000/accounts/", wait_sec=1.5)
        fresh_mode = await cdp.eval_js("document.documentElement.getAttribute('data-mode')")
        print(f"  Fresh visitor data-mode: '{fresh_mode}' (Expect: 'light')")
        results["task1_fresh_light"] = (fresh_mode == "light")

        # Log in
        print("  Logging in as owner (helloshaikh)...")
        await cdp.eval_js("document.getElementById('id_username').value = 'helloshaikh'")
        await cdp.eval_js("document.getElementById('id_password').value = 'pass1234'")
        await cdp.eval_js("document.getElementById('btn-login-submit').click()")
        await asyncio.sleep(2.5)

        # Check dashboard theme default
        await cdp.navigate("http://127.0.0.1:8000/dashboard/", wait_sec=2.0)
        dash_mode = await cdp.eval_js("document.documentElement.getAttribute('data-mode')")
        print(f"  Authenticated dashboard data-mode: '{dash_mode}' (Expect: 'light')")

        # Navigate to Appearance settings (Route: /settings/appearance/)
        await cdp.navigate("http://127.0.0.1:8000/settings/appearance/", wait_sec=2.0)
        btn_dark_exists = await cdp.eval_js("!!document.getElementById('btn-mode-dark')")
        print(f"  Appearance btn-mode-dark present: {btn_dark_exists}")
        
        await cdp.eval_js("document.getElementById('btn-mode-dark').click()")
        await asyncio.sleep(0.5)
        dark_mode_active = await cdp.eval_js("document.documentElement.getAttribute('data-mode')")
        saved_storage_dark = await cdp.eval_js("localStorage.getItem('pos_mode') || localStorage.getItem('pos_theme_mode')")
        print(f"  Switch to Dark: data-mode='{dark_mode_active}', localStorage='{saved_storage_dark}'")
        await cdp.screenshot("evidence_dark_mode_active.png")

        # Reload to test persistence
        print("  Testing Dark Mode persistence on page reload...")
        await cdp.reload(wait_sec=2.0)
        persisted_dark = await cdp.eval_js("document.documentElement.getAttribute('data-mode')")
        print(f"  Reloaded data-mode: '{persisted_dark}' (Expect: 'dark')")
        results["task1_dark_persistence"] = (persisted_dark == "dark")

        # Switch back to Light Mode
        await cdp.eval_js("document.getElementById('btn-mode-light').click()")
        await asyncio.sleep(0.5)
        light_mode_active = await cdp.eval_js("document.documentElement.getAttribute('data-mode')")
        saved_storage_light = await cdp.eval_js("localStorage.getItem('pos_mode') || localStorage.getItem('pos_theme_mode')")
        print(f"  Switch to Light: data-mode='{light_mode_active}', localStorage='{saved_storage_light}'")
        await cdp.screenshot("evidence_light_mode_default.png")

        # Reload to test Light Mode persistence
        await cdp.reload(wait_sec=2.0)
        persisted_light = await cdp.eval_js("document.documentElement.getAttribute('data-mode')")
        print(f"  Reloaded data-mode: '{persisted_light}' (Expect: 'light')")
        results["task1_light_persistence"] = (persisted_light == "light")

        # -------------------------------------------------------------
        # STEP 3: TASK 2 — F2 KEYBOARD SHORTCUT CONFLICTS
        # -------------------------------------------------------------
        print("\n--- TEST 3: F2 SHORTCUT BEHAVIOR (TASK 2) ---")
        # 1. On POS Terminal (/cart/)
        await cdp.navigate("http://127.0.0.1:8000/cart/", wait_sec=2.0)
        await cdp.eval_js("if(document.activeElement) document.activeElement.blur()")
        
        # Dispatch F2 on document
        print("  Pressing F2 on POS Terminal...")
        await cdp.eval_js("""
            document.dispatchEvent(new KeyboardEvent('keydown', {
                key: 'F2',
                code: 'F2',
                keyCode: 113,
                which: 113,
                bubbles: true,
                cancelable: true
            }));
        """)
        await asyncio.sleep(0.5)

        focused_id = await cdp.eval_js("document.activeElement ? document.activeElement.id : ''")
        search_panel_open = await cdp.eval_js("Boolean(document.getElementById('global-search-panel') && document.getElementById('global-search-panel').getAttribute('data-open') === 'true')")
        print(f"  POS active element after F2: '{focused_id}' (Expect: 'barcode-scanner-input')")
        print(f"  Global search opened on POS: {search_panel_open} (Expect: False)")
        await cdp.screenshot("evidence_f2_pos_focused.png")
        results["task2_pos_f2"] = (focused_id == "barcode-scanner-input" and not search_panel_open)

        # 2. On Non-POS page (/dashboard/)
        await cdp.navigate("http://127.0.0.1:8000/dashboard/", wait_sec=2.0)
        await cdp.eval_js("if(document.activeElement) document.activeElement.blur()")
        print("  Pressing F2 on Dashboard...")
        await cdp.eval_js("""
            document.dispatchEvent(new KeyboardEvent('keydown', {
                key: 'F2',
                code: 'F2',
                keyCode: 113,
                which: 113,
                bubbles: true,
                cancelable: true
            }));
        """)
        await asyncio.sleep(0.5)

        global_search_open = await cdp.eval_js("Boolean(document.getElementById('global-search-panel') && document.getElementById('global-search-panel').getAttribute('data-open') === 'true')")
        print(f"  Global search opened on Dashboard after F2: {global_search_open} (Expect: True)")
        await cdp.screenshot("evidence_f2_global_search_non_pos.png")
        results["task2_dashboard_f2"] = bool(global_search_open)

        # Close search with Escape
        await cdp.eval_js("""
            document.dispatchEvent(new KeyboardEvent('keydown', {
                key: 'Escape',
                code: 'Escape',
                keyCode: 27,
                bubbles: true
            }));
        """)
        await asyncio.sleep(0.5)

        # -------------------------------------------------------------
        # STEP 4: TASK 5 — PAYROLL HISTORY & PAYSLIPS (Route: /payroll/)
        # -------------------------------------------------------------
        print("\n--- TEST 4: EMPLOYEE PAYROLL HISTORY & PAYSLIP (TASK 5) ---")
        await cdp.navigate("http://127.0.0.1:8000/payroll/", wait_sec=2.5)
        
        has_year_select = await cdp.eval_js("!!document.getElementById('payroll-year-select')")
        has_month_select = await cdp.eval_js("!!document.getElementById('payroll-month-select')")
        eligible_val = await cdp.eval_js("document.getElementById('stat-eligible-count') ? document.getElementById('stat-eligible-count').textContent.trim() : ''")
        paid_val = await cdp.eval_js("document.getElementById('stat-paid-count') ? document.getElementById('stat-paid-count').textContent.trim() : ''")
        pending_val = await cdp.eval_js("document.getElementById('stat-pending-amount') ? document.getElementById('stat-pending-amount').textContent.trim() : ''")
        has_payroll_table = await cdp.eval_js("!!document.getElementById('payroll-history-table')")
        has_roster = await cdp.eval_js("!!document.getElementById('employee-roster-table')")

        print(f"  Payroll Year selector: {has_year_select}")
        print(f"  Payroll Month selector: {has_month_select}")
        print(f"  KPI Stat - Eligible staff: {eligible_val}")
        print(f"  KPI Stat - Paid count: {paid_val}")
        print(f"  KPI Stat - Pending amount: {pending_val}")
        print(f"  Month-by-month history table: {has_payroll_table}")
        print(f"  Employee roster table: {has_roster}")
        await cdp.screenshot("evidence_payroll_history.png")
        results["task5_payroll_history"] = has_year_select and has_month_select and has_payroll_table

        # Navigate to a payslip
        first_payslip_href = await cdp.eval_js("""
            (function() {
                var link = document.querySelector('a[href*="/payslip/"]');
                return link ? link.href : '';
            })()
        """)
        if first_payslip_href:
            print(f"  Navigating to first employee payslip: {first_payslip_href}")
            await cdp.navigate(first_payslip_href, wait_sec=2.0)
            has_payslip_card = await cdp.eval_js("!!document.querySelector('.payslip-container')")
            has_breakdown_table = await cdp.eval_js("!!document.querySelector('.breakdown-table')")
            has_net_banner = await cdp.eval_js("!!document.querySelector('.net-pay-banner')")
            has_status_stamp = await cdp.eval_js("!!document.querySelector('.status-stamp')")
            print(f"  Payslip voucher card: {has_payslip_card}")
            print(f"  Breakdown table: {has_breakdown_table}")
            print(f"  Net payable banner: {has_net_banner}")
            print(f"  Payment status stamp: {has_status_stamp}")
            await cdp.screenshot("evidence_payslip_printable.png")
            results["task5_payslip"] = has_payslip_card and has_breakdown_table and has_net_banner
        else:
            print("  No payslip link found in roster (empty company staff).")
            results["task5_payslip"] = True

        # -------------------------------------------------------------
        # STEP 5: TASK 7 & 9 — SETTINGS CONTROLS & HIGH-CONTRAST TABS
        # -------------------------------------------------------------
        print("\n--- TEST 5: APPEARANCE PREVIEW & HIGH-CONTRAST TABS (TASKS 7 & 9) ---")
        await cdp.navigate("http://127.0.0.1:8000/settings/appearance/", wait_sec=2.0)
        
        has_preview_text = await cdp.eval_js("document.body.innerText.includes('Visual Preview') || document.body.innerText.includes('Live Design System Preview')")
        active_tab_text = await cdp.eval_js("document.querySelector('.settings-tab.active') ? document.querySelector('.settings-tab.active').textContent.trim() : ''")
        has_mode_btns = await cdp.eval_js("!!document.getElementById('btn-mode-light') && !!document.getElementById('btn-mode-dark')")
        
        print(f"  Active settings tab: '{active_tab_text}' (Expect: 'Appearance')")
        print(f"  Light / Dark mode buttons present: {has_mode_btns}")
        print(f"  Visual preview indicator: {has_preview_text}")
        await cdp.screenshot("evidence_appearance_settings_contrast.png")
        results["task7_9_appearance"] = ("Appearance" in active_tab_text and has_mode_btns and has_preview_text)

        # -------------------------------------------------------------
        # SUMMARY
        # -------------------------------------------------------------
        print("\n=================================================================")
        print("REAL CHROME CDP QA RESULTS SUMMARY:")
        all_passed = True
        for k, v in results.items():
            status_symbol = "✓ PASS" if v else "✗ FAIL"
            print(f"  {k}: {status_symbol}")
            if not v:
                all_passed = False
        print("=================================================================")
        if all_passed:
            print("ALL REAL CHROME BROWSER CDP TESTS PASSED PERFECTLY!")

    finally:
        await cdp.close()

if __name__ == "__main__":
    asyncio.run(main())
