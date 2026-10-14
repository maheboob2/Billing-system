import asyncio
import json
import urllib.request
import websockets

class CDPSession:
    def __init__(self, ws):
        self.ws = ws
        self.msg_id = 0

    async def send(self, method, params=None):
        self.msg_id += 1
        payload = {"id": self.msg_id, "method": method, "params": params or {}}
        await self.ws.send(json.dumps(payload))
        while True:
            resp = json.loads(await self.ws.recv())
            if resp.get("id") == self.msg_id:
                return resp.get("result", {})

    async def navigate(self, url, wait_sec=2.0):
        await self.send("Page.navigate", {"url": url})
        await asyncio.sleep(wait_sec)

    async def eval_js(self, expression):
        res = await self.send("Runtime.evaluate", {"expression": expression, "returnByValue": True})
        return res.get("result", {}).get("value")

    async def screenshot(self, filename):
        res = await self.send("Page.captureScreenshot", {"format": "png"})
        import base64
        with open(filename, "wb") as f:
            f.write(base64.b64decode(res["data"]))
        print(f"  [OK] Screenshot saved: {filename}")


async def run_verification():
    print("=================================================================")
    print("PHASE 11: HONEST TELEGRAM & DEVICE STATUS VERIFICATION")
    print("=================================================================")

    tabs = json.loads(urllib.request.urlopen("http://127.0.0.1:9222/json").read().decode())
    target_tab = None
    for t in tabs:
        if t.get("type") == "page":
            target_tab = t
            break

    if not target_tab:
        print("ERROR: No Chrome tab found on port 9222!")
        return

    ws = await websockets.connect(target_tab["webSocketDebuggerUrl"], max_size=50_000_000)
    cdp = CDPSession(ws)
    await cdp.send("Page.enable")
    await cdp.send("Runtime.enable")

    results = {}

    # 1. LOGIN IF NEEDED
    await cdp.navigate("http://127.0.0.1:8000/dashboard/")
    is_login = await cdp.eval_js("window.location.pathname.includes('/accounts')")
    if is_login:
        print("Logging in as owner (helloshaikh)...")
        await cdp.eval_js("document.getElementById('id_username').value = 'helloshaikh'")
        await cdp.eval_js("document.getElementById('id_password').value = 'pass1234'")
        await cdp.eval_js("document.getElementById('btn-login-submit').click()")
        await asyncio.sleep(2.5)

    # 2. CHECK TOPBAR STATUS INDICATORS
    print("\n--- TEST 1: TOPBAR HONEST STATUS BADGES ---")
    await cdp.navigate("http://127.0.0.1:8000/dashboard/")
    topbar_scanner_text = await cdp.eval_js("document.getElementById('global-scanner-status-text') ? document.getElementById('global-scanner-status-text').textContent.trim() : ''")
    topbar_sync_text = await cdp.eval_js("document.getElementById('cloud-sync-nav-text') ? document.getElementById('cloud-sync-nav-text').textContent.trim() : ''")
    print(f"  Topbar Scanner text: '{topbar_scanner_text}' (Should NOT be 'HID Ready')")
    print(f"  Topbar Cloud Sync text: '{topbar_sync_text}'")
    results["honest_topbar_scanner"] = (topbar_scanner_text == "HID Supported")
    results["honest_topbar_sync"] = ("Cloud Synced" not in topbar_sync_text or "Local DB" in topbar_sync_text or "Pending" in topbar_sync_text or "Offline" in topbar_sync_text)

    # 3. CHECK TELEGRAM SETTINGS HONEST STATUS
    print("\n--- TEST 2: TELEGRAM SETTINGS HONEST STATUS ---")
    await cdp.navigate("http://127.0.0.1:8000/settings/telegram/")
    tg_text = await cdp.eval_js("document.body.innerText")
    has_not_configured = ("NOT CONFIGURED" in tg_text or "Not configured" in tg_text)
    fake_connected = ("● CONNECTED" in tg_text or "● PAIRED" in tg_text and "Bot unconfigured" in tg_text)
    print(f"  Mentions 'Not configured' when token is missing: {has_not_configured}")
    print(f"  Has Test Telegram Connection button: {'Test Telegram Connection' in tg_text}")
    
    # Test clicking the "Test Telegram Connection" button
    print("  Testing 'Test Telegram Connection' action...")
    await cdp.eval_js("""
        (() => {
            const btn = Array.from(document.querySelectorAll('button')).find(b => b.textContent.includes('Test Telegram Connection'));
            if (btn) btn.click();
        })()
    """)
    await asyncio.sleep(2.0)
    after_test_text = await cdp.eval_js("document.body.innerText")
    test_handled_safely = ("Telegram Bot Token is not configured" in after_test_text or "Set TELEGRAM_BOT_TOKEN" in after_test_text)
    print(f"  Connection test handled safely without crashing: {test_handled_safely}")
    await cdp.screenshot("evidence_phase11_telegram_honest_status.png")
    results["telegram_honest_status"] = has_not_configured and test_handled_safely

    # 4. CHECK SCANNER SETTINGS HONEST STATUS
    print("\n--- TEST 3: SCANNER SETTINGS HONEST STATUS ---")
    await cdp.navigate("http://127.0.0.1:8000/settings/scanner/")
    scanner_text = await cdp.eval_js("document.body.innerText")
    hid_badge_honest = ("HID SCANNER SUPPORTED" in scanner_text and "CONNECTED / READY" not in scanner_text)
    phone_badge_honest = ("Pairing Available" in scanner_text or "Not Paired" in scanner_text or "Paired" in scanner_text)
    has_scan_evidence = ("Scan Activity" in scanner_text or "No physical scans received yet" in scanner_text)
    print(f"  HID badge reflects driverless support (not fake connected): {hid_badge_honest}")
    print(f"  Phone companion shows honest pairing availability: {phone_badge_honest}")
    print(f"  Shows scan activity / evidence line: {has_scan_evidence}")
    await cdp.screenshot("evidence_phase11_scanner_honest_status.png")
    results["scanner_honest_status"] = hid_badge_honest and phone_badge_honest and has_scan_evidence

    # 5. CHECK PAYROLL ACCESS RESTRICTION & HISTORY
    print("\n--- TEST 4: PAYROLL ACCESS & RECONCILIATION ---")
    await cdp.navigate("http://127.0.0.1:8000/payroll/?year=2025&month=10")
    payroll_text = await cdp.eval_js("document.body.innerText")
    payroll_lower = payroll_text.lower()
    has_oct_2025 = ("october 2025" in payroll_lower)
    has_history = ("payroll history & reports" in payroll_lower and "payroll period" in payroll_lower)
    print(f"  October 2025 period accessible: {has_oct_2025}")
    print(f"  Payroll table and summary present: {has_history}")
    results["payroll_history_accessible"] = has_oct_2025 and has_history

    print("\n=================================================================")
    print("PHASE 11 CDP VERIFICATION SUMMARY:")
    for k, v in results.items():
        print(f"  {k}: {'PASS' if v else 'FAIL'}")
    print("=================================================================")

    await ws.close()

asyncio.run(run_verification())
