import asyncio
import json
import urllib.request
import websockets

async def test():
    req = urllib.request.urlopen("http://127.0.0.1:9222/json")
    tabs = json.loads(req.read().decode())
    ws_url = [x for x in tabs if x.get('type') == 'page'][0]['webSocketDebuggerUrl']
    print("Connecting to:", ws_url)
    ws = await websockets.connect(ws_url)
    
    # Enable Page domain
    await ws.send(json.dumps({'id': 1, 'method': 'Page.enable'}))
    print("Page.enable:", await ws.recv())

    # Navigate
    await ws.send(json.dumps({'id': 2, 'method': 'Page.navigate', 'params': {'url': 'http://127.0.0.1:8000/cart/'}}))
    print("Page.navigate response:", await ws.recv())
    
    await asyncio.sleep(2)
    await ws.send(json.dumps({'id': 3, 'method': 'Runtime.evaluate', 'params': {'expression': 'window.location.href', 'returnByValue': True}}))
    print("Current href:", await ws.recv())

    await ws.send(json.dumps({'id': 4, 'method': 'Runtime.evaluate', 'params': {'expression': '!!document.getElementById("global-sidebar")', 'returnByValue': True}}))
    print("Has global-sidebar:", await ws.recv())

    await ws.send(json.dumps({'id': 5, 'method': 'Page.reload', 'params': {'ignoreCache': True}}))
    print("Page.reload:", await ws.recv())

    await asyncio.sleep(2)
    await ws.send(json.dumps({'id': 6, 'method': 'Runtime.evaluate', 'params': {'expression': '!!document.getElementById("global-sidebar")', 'returnByValue': True}}))
    print("Has global-sidebar after reload:", await ws.recv())

    await ws.close()

if __name__ == "__main__":
    asyncio.run(test())
