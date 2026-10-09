# Local HTTPS & LAN Configuration Guide for POS Phone Camera Scanner

This guide explains how to enable hardware camera streaming on physical mobile devices (Android / iOS) over your local Wi-Fi network without third-party cloud tunneling services (e.g. ngrok).

---

## 1. Why HTTPS is Required for Phone Cameras

Under the **W3C Media Capture and Streams / Secure Contexts specification**, modern mobile browsers (Chrome on Android, Safari on iOS):
- **Permit** `getUserMedia` only on `localhost`, `127.0.0.1`, or an **HTTPS** connection (`window.isSecureContext === true`).
- **Block** camera hardware access when opening an insecure `http://192.168.x.x:8000` URL from a remote device.

> **Note on POS Companion Architecture:**
> 1. Over **HTTP** (`http://192.168.x.x:8000`), the phone companion connects, pairs, tracks presence/heartbeat, and supports **manual barcode input** and hardware HID inputs.
> 2. To activate the **camera live viewfinder** on a physical phone, you need a local HTTPS development certificate.

---

## 2. Setting Up Local HTTPS with `mkcert`

`mkcert` is a zero-config tool that creates locally-trusted certificates using your own local Certificate Authority (CA).

### Step A: Install `mkcert` on Windows

Using **Chocolatey**:
```powershell
choco install mkcert
```
Or using **Scoop**:
```powershell
scoop bucket add extras
scoop install mkcert
```

### Step B: Create Local Certificate Authority (CA)

In PowerShell (Run as Administrator):
```powershell
mkcert -install
```
This registers the root CA in the Windows certificate store.

### Step C: Generate Certificate for your PC's LAN IP

Find your PC's Wi-Fi IP (e.g. `192.168.31.128`):
```powershell
ipconfig
```

Generate the certificate in your project directory:
```powershell
cd "h:\Git hub\Billing-system\Billing_System"
mkcert -cert-file cert.pem -key-file key.pem 192.168.31.128 localhost 127.0.0.1
```
*(Replace `192.168.31.128` with your actual LAN IPv4 address)*

### Step D: Install Root CA on your Physical Phone

For your phone's browser to trust the HTTPS certificate:
1. Find where the root CA is located:
   ```powershell
   mkcert -CAROOT
   ```
2. Copy `rootCA.pem` to your phone (via email, USB, or local file share).
3. On **Android**:
   - Go to **Settings** → **Security** → **Encryption & credentials** → **Install a certificate** → **CA certificate**.
   - Select `rootCA.pem`.
4. On **iOS**:
   - AirDrop or open `rootCA.pem` in Safari.
   - Go to **Settings** → **Profile Downloaded** → **Install**.
   - Go to **Settings** → **General** → **About** → **Certificate Trust Settings** → Enable Full Trust.

---

## 3. Running Django Development Server with HTTPS

### Option 1: Using `django-extensions` / `Werkzeug` or `runsslserver`

Install `django-sslserver`:
```powershell
pip install django-sslserver
```

Run server bound to `0.0.0.0:8000`:
```powershell
python manage.py runsslserver 0.0.0.0:8000 --certificate cert.pem --key key.pem
```

### Option 2: Using a Lightweight Local Reverse Proxy (e.g. Caddy)

`Caddy` can terminate HTTPS locally and proxy to Django:
```caddyfile
# Caddyfile
https://192.168.31.128:8443 {
    tls cert.pem key.pem
    reverse_proxy 127.0.0.1:8000
}
```

Run Caddy:
```powershell
caddy run
```

Then in `.env`:
```env
POS_SCANNER_BASE_URL=https://192.168.31.128:8443
```

---

## 4. Configuring `.env` for Scanner Base URL

To ensure the QR code on the PC POS terminal encodes the HTTPS URL, set in your `.env`:

```env
POS_SCANNER_BASE_URL=https://192.168.31.128:8000
```

When configured, all generated QR codes and connection links will automatically use this URL.

---

## 5. Windows Firewall & Wi-Fi Troubleshooting

If your physical phone shows **"This site can't be reached"**:

### Check 1: Server Bound to `0.0.0.0`
Verify Django is running with:
```powershell
python manage.py runserver 0.0.0.0:8000
```
*(If run as `127.0.0.1:8000`, the PC drops all external network packets)*

### Check 2: Wi-Fi Network Profile
1. On Windows, open **Settings** → **Network & Internet** → **Wi-Fi** → your network.
2. Ensure **Network profile type** is set to **Private** (not Public).
   - "Public" networks block incoming connections by default.

### Check 3: Windows Firewall Inbound Rule
Verify port 8000 is open in PowerShell (Administrator):
```powershell
New-NetFirewallRule -DisplayName "Django POS LAN Port 8000" -Direction Inbound -LocalPort 8000 -Protocol TCP -Action Allow
```

### Check 4: Wi-Fi Client Isolation (AP Isolation)
Some Wi-Fi routers (guest networks or office networks) enable "AP Isolation", preventing connected devices from talking to one another. Ensure your PC and mobile phone are on the main 2.4GHz/5GHz home/office network.
