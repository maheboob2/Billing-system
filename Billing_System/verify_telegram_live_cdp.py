import asyncio
import json
import urllib.request
import websockets
import sys
import base64

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

async def snap():
    req = urllib.request.urlopen('http://127.0.0.1:9222/json')
    tabs = json.loads(req.read().decode())
    target = next(t for t in tabs if t.get('type') == 'page')
    ws = await websockets.connect(target['webSocketDebuggerUrl'])
    await ws.send(json.dumps({'id': 1, 'method': 'Page.navigate', 'params': {'url': 'http://127.0.0.1:8000/settings/telegram/'}}))
    await asyncio.sleep(2)

    click_js = """
    (() => {
        const btn = document.querySelector('button[title*=\"getMe\"], form button.btn-secondary');
        const forms = Array.from(document.querySelectorAll('form'));
        for (const f of forms) {
            const act = f.querySelector('input[name="action"][value="test_connection"]');
            if (act) {
                f.submit();
                return 'SUBMITTED';
            }
        }
        return 'FORM_NOT_FOUND';
    })()
    """
    await ws.send(json.dumps({'id': 2, 'method': 'Runtime.evaluate', 'params': {'expression': click_js, 'returnByValue': True}}))
    await asyncio.sleep(3)

    await ws.send(json.dumps({'id': 3, 'method': 'Page.captureScreenshot', 'params': {'format': 'png'}}))
    while True:
        res = await ws.recv()
        data = json.loads(res)
        if data.get('id') == 3:
            img = data['result']['data']
            with open('evidence_telegram_connected_brisksmith.png', 'wb') as f:
                f.write(base64.b64decode(img))
            print('[OK] Screenshot saved: evidence_telegram_connected_brisksmith.png')
            break

    await ws.send(json.dumps({'id': 4, 'method': 'Runtime.evaluate', 'params': {'expression': 'document.body.innerText', 'returnByValue': True}}))
    while True:
        res = await ws.recv()
        data = json.loads(res)
        if data.get('id') == 4:
            text = data['result']['result']['value']
            print("\n--- RELEVANT PAGE TEXT ---")
            for line in text.splitlines():
                if any(w in line.lower() for w in ['verified', 'brisksmith', 'gateway', 'connection', 'success', 'bot']):
                    print('->', line)
            break

    await ws.close()

if __name__ == '__main__':
    asyncio.run(snap())
