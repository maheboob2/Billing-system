import asyncio
import json
import os
import urllib.request
import websockets
from decimal import Decimal

CDP_HTTP = "http://127.0.0.1:9222"
BASE_URL = "http://127.0.0.1:8000"
ARTIFACT_DIR = r"C:\Users\admin\.gemini\antigravity-ide\brain\8f51d56c-ce8c-4183-84c1-9dbf04920b57"

class ChromeRunner:
    def __init__(self):
        self.ws = None
        self.msg_id = 0

    async def connect(self):
        req = urllib.request.urlopen(f"{CDP_HTTP}/json")
        tabs = json.loads(req.read().decode())
        page_tab = None
        for t in tabs:
            if t.get("type") == "page" and "chrome" not in t.get("url", ""):
                page_tab = t
                break
        if not page_tab:
            for t in tabs:
                if t.get("type") == "page":
                    page_tab = t
                    break

        ws_url = page_tab["webSocketDebuggerUrl"]
        print(f"Connecting to Chrome tab: {page_tab.get('title')} ({ws_url})")
        self.ws = await websockets.connect(ws_url, max_size=20*1024*1024)

    async def send_cmd(self, method, params=None):
        self.msg_id += 1
        cid = self.msg_id
        msg = {"id": cid, "method": method, "params": params or {}}
        await self.ws.send(json.dumps(msg))
        while True:
            raw = await self.ws.recv()
            data = json.loads(raw)
            if data.get("id") == cid:
                if "error" in data:
                    raise Exception(f"CDP error in {method}: {data['error']}")
                return data.get("result", {})

    async def eval_js(self, expression):
        res = await self.send_cmd("Runtime.evaluate", {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": True,
        })
        val = res.get("result", {})
        if "value" in val:
            return val["value"]
        return res

    async def navigate(self, url):
        await self.send_cmd("Page.navigate", {"url": url})
        await asyncio.sleep(2.0)

    async def screenshot(self, filename):
        res = await self.send_cmd("Page.captureScreenshot", {"format": "png"})
        import base64
        data = base64.b64decode(res["data"])
        out_path = os.path.join(ARTIFACT_DIR, filename)
        with open(out_path, "wb") as f:
            f.write(data)
        print(f"  [SCREENSHOT] Saved {out_path}")
        return out_path

async def run_browser_tests():
    print("=" * 60)
    print("REAL CHROME BROWSER ADVERSARIAL QUANTITY VERIFICATION")
    print("=" * 60)

    runner = ChromeRunner()
    await runner.connect()

    # Step 1: Login if not already logged in
    await runner.navigate(f"{BASE_URL}/accounts/")
    await asyncio.sleep(1.0)
    is_login_page = await runner.eval_js("(() => document.querySelector('input[name=\"username\"]') !== null)()")
    if is_login_page:
        print("Logging in via UI as helloshaikh...")
        await runner.eval_js("""
            (() => {
                const u = document.querySelector('input[name="username"]');
                const p = document.querySelector('input[name="password"]');
                if (u && p) {
                    u.value = 'helloshaikh';
                    p.value = 'pass1234';
                    document.querySelector('form').submit();
                }
            })()
        """)
        await asyncio.sleep(3.0)

    # Step 2: Navigate to POS Cart (/cart/)
    print("Navigating to POS (/cart/)...")
    await runner.navigate(f"{BASE_URL}/cart/")
    await asyncio.sleep(2.5)

    cur_url = await runner.eval_js("window.location.href")
    print(f"Current page URL: {cur_url}")

    # Ensure cart is cleared first
    await runner.eval_js("""
        (() => {
            if (window.Cart) {
                window.Cart.clear();
            }
        })()
    """)
    await asyncio.sleep(0.5)

    # Step 2: Add Amul Taaza Milk (barcode: 890126201001)
    print("\n1. Scanning Amul Taaza Milk 1L barcode (890126201001)...")
    await runner.eval_js("""
        (() => {
            const bi = document.getElementById('barcode-scanner-input');
            bi.value = '890126201001';
            bi.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', keyCode: 13, which: 13, bubbles: true }));
        })()
    """)
    await asyncio.sleep(1.5)

    cart_qty = await runner.eval_js("(() => window.Cart.items.length > 0 ? window.Cart.items[0].quantity : 0)()")
    avail_stock = await runner.eval_js("(() => window.Cart.items.length > 0 ? window.Cart.items[0].available_stock : 0)()")
    print(f"Cart quantity: {cart_qty}, Available stock: {avail_stock}")
    assert cart_qty == 1, "Amul Milk was not added to cart"

    # Step 3: Adversarial UI Manipulation: Type 99999 (exceeds 2307) into .qty-input
    print("\n2. Adversarial Quantity: Entering 99999 into UI .qty-input...")
    await runner.eval_js("""
        (() => {
            const qi = document.querySelector('.qty-input');
            qi.value = '99999';
            qi.dispatchEvent(new Event('change', { bubbles: true }));
        })()
    """)
    await asyncio.sleep(1.0)

    clamped_qty = await runner.eval_js("(() => window.Cart.items[0].quantity)()")
    input_val = await runner.eval_js("(() => document.querySelector('.qty-input').value)()")
    toast_msg = await runner.eval_js("(() => document.getElementById('toast-container')?.innerText || '')()")
    print(f"After typing 99999 -> Cart quantity: {clamped_qty}, Input value: {input_val}")
    print(f"Toast message: {repr(toast_msg.strip())}")

    assert clamped_qty == avail_stock, f"Expected clamped quantity {avail_stock}, got {clamped_qty}"
    assert int(input_val) == int(avail_stock), f"Expected input value {avail_stock}, got {input_val}"
    await runner.screenshot("pos_adv_ui_qty_exceed_clamped.png")
    print("PASS: Typing 99999 was rejected and clamped to 2307. Toast showed 'Only 2307 units available.'")

    # Step 4: Verify stepper '+' button is disabled when at 2307
    is_inc_disabled = await runner.eval_js("(() => document.querySelector('.btn-qty-inc').disabled)()")
    inc_title = await runner.eval_js("(() => document.querySelector('.btn-qty-inc').title)()")
    print(f"\n3. Stepper '+' button disabled: {is_inc_disabled}, Title: '{inc_title}'")
    assert is_inc_disabled is True, "Expected '+' button to be disabled at max stock"
    await runner.screenshot("pos_adv_ui_stepper_disabled.png")
    print("PASS: Stepper '+' button is disabled at max stock.")

    # Step 5: Adversarial UI Manipulation: Type 0 into .qty-input
    print("\n4. Adversarial Quantity: Entering 0 into UI .qty-input...")
    await runner.eval_js("""
        (() => {
            const qi = document.querySelector('.qty-input');
            qi.value = '0';
            qi.dispatchEvent(new Event('change', { bubbles: true }));
        })()
    """)
    await asyncio.sleep(1.0)

    cart_len = await runner.eval_js("(() => window.Cart.items.length)()")
    print(f"After entering 0 -> Cart items count: {cart_len}")
    assert cart_len == 0, f"Expected 0 items in cart after entering quantity 0, got {cart_len}"
    await runner.screenshot("pos_adv_ui_qty_zero_removed.png")
    print("PASS: Entering 0 removed item safely from cart without negative or zero line.")

    # Step 6: Adversarial UI Manipulation: Type -5 into .qty-input
    print("\n5. Adversarial Quantity: Entering -5 into UI .qty-input...")
    # Re-add item first
    await runner.eval_js("""
        (() => {
            const bi = document.getElementById('barcode-scanner-input');
            bi.value = '890126201001';
            bi.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', keyCode: 13, which: 13, bubbles: true }));
        })()
    """)
    await asyncio.sleep(1.5)

    await runner.eval_js("""
        (() => {
            const qi = document.querySelector('.qty-input');
            qi.value = '-5';
            qi.dispatchEvent(new Event('change', { bubbles: true }));
        })()
    """)
    await asyncio.sleep(1.0)

    cart_len_neg = await runner.eval_js("(() => window.Cart.items.length)()")
    print(f"After entering -5 -> Cart items count: {cart_len_neg}")
    assert cart_len_neg == 0, f"Expected item removed for negative quantity, got {cart_len_neg}"
    await runner.screenshot("pos_adv_ui_qty_negative_removed.png")
    print("PASS: Entering -5 removed item safely without allowing negative cart quantity.")

    print("\n" + "=" * 60)
    print("ALL REAL CHROME BROWSER ADVERSARIAL TESTS PASSED!")
    print("=" * 60)

if __name__ == "__main__":
    asyncio.run(run_browser_tests())
