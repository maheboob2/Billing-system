import asyncio
import json
import urllib.request
import websockets
import base64
import os
import sys

CHROME_DEBUG_URL = "http://127.0.0.1:9222"
SCREENSHOT_DIR = r"C:\Users\admin\.gemini\antigravity-ide\brain\8f51d56c-ce8c-4183-84c1-9dbf04920b57"

async def test_dark_mode():
    req = urllib.request.urlopen(f"{CHROME_DEBUG_URL}/json")
    tabs = json.loads(req.read().decode())
    target_tab = [t for t in tabs if t.get("type") == "page"][0]
    ws = await websockets.connect(target_tab["webSocketDebuggerUrl"])
    
    async def send(mid, method, params=None):
        await ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        while True:
            r = json.loads(await ws.recv())
            if r.get("id") == mid:
                return r.get("result", {})

    # Switch to dark mode
    await send(1, "Runtime.evaluate", {"expression": "ThemeManager.setMode('dark')"})
    await asyncio.sleep(0.5)

    # Navigate to POS in dark mode
    await send(2, "Page.navigate", {"url": "http://127.0.0.1:8000/cart/"})
    await asyncio.sleep(2.0)

    # Capture dark mode screenshot
    res = await send(3, "Page.captureScreenshot", {"format": "png"})
    filepath = os.path.join(SCREENSHOT_DIR, "live_redesign_dark_pos.png")
    with open(filepath, "wb") as f:
        f.write(base64.b64decode(res["data"]))
    print("Saved live_redesign_dark_pos.png")

    await ws.close()

if __name__ == "__main__":
    asyncio.run(test_dark_mode())
