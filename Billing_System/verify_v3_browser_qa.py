import asyncio
import json
import urllib.request
import os
import base64
import websockets

SCREENSHOT_DIR = os.path.abspath("qa_evidence_v3")
os.makedirs(SCREENSHOT_DIR, exist_ok=True)

async def send_cmd(ws, msg_id, method, params=None):
    payload = {"id": msg_id, "method": method}
    if params:
        payload["params"] = params
    await ws.send(json.dumps(payload))
    while True:
        resp = await ws.recv()
        data = json.loads(resp)
        if data.get("id") == msg_id:
            return data

async def capture_screenshot(ws, msg_id, filename):
    res = await send_cmd(ws, msg_id, "Page.captureScreenshot", {"format": "png"})
    img_data = base64.b64decode(res["result"]["data"])
    filepath = os.path.join(SCREENSHOT_DIR, filename)
    with open(filepath, "wb") as f:
        f.write(img_data)
    print(f"Captured: {filepath}")
    return filepath

async def run_qa():
    # 1. Connect to CDP
    req = urllib.request.urlopen("http://127.0.0.1:9222/json")
    tabs = json.loads(req.read().decode())
    page_tabs = [x for x in tabs if x.get("type") == "page"]
    ws_url = page_tabs[0]["webSocketDebuggerUrl"]
    print("Connecting to CDP tab:", ws_url)
    ws = await websockets.connect(ws_url, max_size=50*1024*1024)

    msg_id = 1
    await send_cmd(ws, msg_id, "Page.enable"); msg_id += 1
    await send_cmd(ws, msg_id, "Runtime.enable"); msg_id += 1
    await send_cmd(ws, msg_id, "DOM.enable"); msg_id += 1

    # ─────────────────────────────────────────────────────────────
    # STEP 1: POS Screen - Default Collapsed Scanner State
    # ─────────────────────────────────────────────────────────────
    print("Navigating to POS /cart/...")
    await send_cmd(ws, msg_id, "Page.navigate", {"url": "http://127.0.0.1:8000/cart/"}); msg_id += 1
    await asyncio.sleep(2.5)

    res = await send_cmd(ws, msg_id, "Runtime.evaluate", {
        "expression": """(() => {
            const toggle = document.getElementById('toggle-barcode-input');
            const panel = document.getElementById('barcode-input-panel');
            const searchCard = document.getElementById('pos-search-card');
            return {
                togglePresent: !!toggle,
                toggleAriaExpanded: toggle ? toggle.getAttribute('aria-expanded') : null,
                panelHidden: panel ? (panel.hidden || panel.hasAttribute('hidden')) : null,
                searchCardAbsent: !searchCard
            };
        })()""",
        "returnByValue": True
    }); msg_id += 1
    print("POS Default State Check:", res.get("result", {}).get("value"))
    await capture_screenshot(ws, msg_id, "01_pos_default_collapsed.png"); msg_id += 1

    # ─────────────────────────────────────────────────────────────
    # STEP 2: POS Scanner Expanded & Focused
    # ─────────────────────────────────────────────────────────────
    print("Expanding barcode scanner input...")
    await send_cmd(ws, msg_id, "Runtime.evaluate", {
        "expression": """(() => {
            const toggle = document.getElementById('toggle-barcode-input');
            if (toggle) toggle.click();
        })()"""
    }); msg_id += 1
    await asyncio.sleep(0.8)

    res = await send_cmd(ws, msg_id, "Runtime.evaluate", {
        "expression": """(() => {
            const toggle = document.getElementById('toggle-barcode-input');
            const panel = document.getElementById('barcode-input-panel');
            const input = document.getElementById('barcode-scanner-input');
            return {
                toggleAriaExpanded: toggle.getAttribute('aria-expanded'),
                panelHidden: panel.hidden || panel.hasAttribute('hidden'),
                isFocused: document.activeElement === input
            };
        })()""",
        "returnByValue": True
    }); msg_id += 1
    print("POS Scanner Expanded Check:", res.get("result", {}).get("value"))
    await capture_screenshot(ws, msg_id, "02_pos_scanner_expanded.png"); msg_id += 1

    # ─────────────────────────────────────────────────────────────
    # STEP 3: Exact Barcode Scan (Enter triggers add to cart)
    # ─────────────────────────────────────────────────────────────
    print("Triggering exact scan: 890000000001...")
    await send_cmd(ws, msg_id, "Runtime.evaluate", {
        "expression": """(() => {
            const input = document.getElementById('barcode-scanner-input');
            if (input) {
                input.value = '890000000001';
                const event = new KeyboardEvent('keydown', { key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true });
                input.dispatchEvent(event);
            }
        })()"""
    }); msg_id += 1
    await asyncio.sleep(2.0)

    res = await send_cmd(ws, msg_id, "Runtime.evaluate", {
        "expression": """(() => {
            const items = window.Cart ? window.Cart.items : [];
            const countBadge = document.getElementById('cart-item-count');
            return {
                cartCount: items.length,
                firstItemName: items.length > 0 ? items[0].name : null,
                firstItemQty: items.length > 0 ? items[0].quantity : null,
                badgeText: countBadge ? countBadge.textContent : null
            };
        })()""",
        "returnByValue": True
    }); msg_id += 1
    print("Scan Success Result:", res.get("result", {}).get("value"))
    await capture_screenshot(ws, msg_id, "03_pos_scan_success.png"); msg_id += 1

    # ─────────────────────────────────────────────────────────────
    # STEP 4: Unknown Barcode Scan (No cart mutation, clear error)
    # ─────────────────────────────────────────────────────────────
    print("Triggering unknown barcode: 999999999999...")
    await send_cmd(ws, msg_id, "Runtime.evaluate", {
        "expression": """(() => {
            const input = document.getElementById('barcode-scanner-input');
            if (input) {
                input.value = '999999999999';
                const event = new KeyboardEvent('keydown', { key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true });
                input.dispatchEvent(event);
            }
        })()"""
    }); msg_id += 1
    await asyncio.sleep(1.5)

    res = await send_cmd(ws, msg_id, "Runtime.evaluate", {
        "expression": """(() => {
            const modal = document.getElementById('unknown-barcode-modal');
            const items = window.Cart ? window.Cart.items : [];
            return {
                unknownModalVisible: modal ? modal.style.display !== 'none' : false,
                cartCountAfterUnknown: items.length
            };
        })()""",
        "returnByValue": True
    }); msg_id += 1
    print("Unknown Scan Check:", res.get("result", {}).get("value"))
    await capture_screenshot(ws, msg_id, "04_pos_scan_unknown_error.png"); msg_id += 1

    # Close modal if open
    await send_cmd(ws, msg_id, "Runtime.evaluate", {
        "expression": "if (window.Cart && Cart.closeUnknownBarcodeModal) Cart.closeUnknownBarcodeModal();"
    }); msg_id += 1
    await asyncio.sleep(0.5)

    # ─────────────────────────────────────────────────────────────
    # STEP 5: Overview Dashboard & Icon-Only Global Search
    # ─────────────────────────────────────────────────────────────
    print("Navigating to Dashboard Overview...")
    await send_cmd(ws, msg_id, "Page.navigate", {"url": "http://127.0.0.1:8000/dashboard/"}); msg_id += 1
    await asyncio.sleep(2.0)

    res = await send_cmd(ws, msg_id, "Runtime.evaluate", {
        "expression": """(() => {
            const searchPanel = document.getElementById('global-search-panel');
            const rect = searchPanel ? searchPanel.getBoundingClientRect() : null;
            return {
                searchOpen: searchPanel ? searchPanel.getAttribute('data-open') : null,
                width: rect ? rect.width : null,
                height: rect ? rect.height : null
            };
        })()""",
        "returnByValue": True
    }); msg_id += 1
    print("Global Search Collapsed Check:", res.get("result", {}).get("value"))
    await capture_screenshot(ws, msg_id, "05_global_search_collapsed.png"); msg_id += 1

    # Trigger F2 to expand Global Search
    print("Triggering F2 key to expand search...")
    await send_cmd(ws, msg_id, "Runtime.evaluate", {
        "expression": """(() => {
            const event = new KeyboardEvent('keydown', { key: 'F2', code: 'F2', keyCode: 113, which: 113, bubbles: true });
            document.dispatchEvent(event);
        })()"""
    }); msg_id += 1
    await asyncio.sleep(0.8)

    res = await send_cmd(ws, msg_id, "Runtime.evaluate", {
        "expression": """(() => {
            const searchPanel = document.getElementById('global-search-panel');
            const input = document.getElementById('global-search-input');
            const rect = searchPanel ? searchPanel.getBoundingClientRect() : null;
            return {
                searchOpen: searchPanel ? searchPanel.getAttribute('data-open') : null,
                width: rect ? rect.width : null,
                isInputFocused: document.activeElement === input
            };
        })()""",
        "returnByValue": True
    }); msg_id += 1
    print("Global Search Expanded Check:", res.get("result", {}).get("value"))
    await capture_screenshot(ws, msg_id, "06_global_search_expanded.png"); msg_id += 1

    # Close search with Escape
    await send_cmd(ws, msg_id, "Runtime.evaluate", {
        "expression": """(() => {
            const event = new KeyboardEvent('keydown', { key: 'Escape', code: 'Escape', keyCode: 27, which: 27, bubbles: true });
            document.dispatchEvent(event);
        })()"""
    }); msg_id += 1
    await asyncio.sleep(0.5)

    # ─────────────────────────────────────────────────────────────
    # STEP 6: Sidebar Single Account Area & Role Label
    # ─────────────────────────────────────────────────────────────
    res = await send_cmd(ws, msg_id, "Runtime.evaluate", {
        "expression": """(() => {
            const userCard = document.querySelector('.sidebar-user-card');
            const roleEl = document.querySelector('.sidebar-user-role');
            const brandEl = document.querySelector('.sidebar-brand-text');
            const topbarUser = document.querySelector('.topbar-user-badge');
            return {
                brandName: brandEl ? brandEl.textContent.trim() : null,
                roleLabel: roleEl ? roleEl.textContent.trim() : null,
                hasTopRightDuplicate: !!topbarUser
            };
        })()""",
        "returnByValue": True
    }); msg_id += 1
    print("Sidebar Account & Role Check:", res.get("result", {}).get("value"))
    await capture_screenshot(ws, msg_id, "07_sidebar_account_role.png"); msg_id += 1

    # ─────────────────────────────────────────────────────────────
    # STEP 7: Settings → Telegram Owner Reporting
    # ─────────────────────────────────────────────────────────────
    print("Navigating to Settings -> Telegram...")
    await send_cmd(ws, msg_id, "Page.navigate", {"url": "http://127.0.0.1:8000/settings/telegram/"}); msg_id += 1
    await asyncio.sleep(2.0)
    await capture_screenshot(ws, msg_id, "08_settings_telegram.png"); msg_id += 1

    # ─────────────────────────────────────────────────────────────
    # STEP 8: Settings → Scanner & POS Devices (QR & POS-001)
    # ─────────────────────────────────────────────────────────────
    print("Navigating to Settings -> Scanner & POS Devices...")
    await send_cmd(ws, msg_id, "Page.navigate", {"url": "http://127.0.0.1:8000/settings/scanner/"}); msg_id += 1
    await asyncio.sleep(2.0)

    res = await send_cmd(ws, msg_id, "Runtime.evaluate", {
        "expression": """(() => {
            const qrImg = document.querySelector('img[alt*="Pairing QR"]');
            const termBadge = document.querySelector('span[style*="38bdf8"]');
            return {
                hasQrImage: !!qrImg,
                qrSrc: qrImg ? qrImg.src.slice(0, 80) : null,
                terminalText: termBadge ? termBadge.textContent.trim() : null
            };
        })()""",
        "returnByValue": True
    }); msg_id += 1
    print("Scanner QR & Terminal Check:", res.get("result", {}).get("value"))
    await capture_screenshot(ws, msg_id, "09_settings_scanner_qr.png"); msg_id += 1

    # ─────────────────────────────────────────────────────────────
    # STEP 9: Reports & Analytics Page (Date Filter & Charts)
    # ─────────────────────────────────────────────────────────────
    print("Navigating to Reports & Analytics...")
    await send_cmd(ws, msg_id, "Page.navigate", {"url": "http://127.0.0.1:8000/reports/"}); msg_id += 1
    await asyncio.sleep(2.0)
    await capture_screenshot(ws, msg_id, "10_reports_analytics.png"); msg_id += 1

    await ws.close()
    print("Browser QA Verification Completed Successfully!")

if __name__ == "__main__":
    asyncio.run(run_qa())
