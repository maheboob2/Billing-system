"""
POS Scanner Network Utilities
Resolves LAN-accessible IP addresses, base URLs, and connection diagnostics
for phone scanner companion devices.
"""

import os
import socket
from urllib.parse import urlparse
from django.conf import settings


def get_lan_ip():
    """
    Detect the primary LAN-reachable IPv4 address of this machine.
    Never returns loopback (127.x.x.x, localhost) or 0.0.0.0 or link-local (169.254.x.x).
    """
    # 1. Probe active outbound network route via UDP socket (does not transmit packets)
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        if ip and not ip.startswith("127.") and not ip.startswith("0.") and not ip.startswith("169.254."):
            return ip
    except Exception:
        pass

    # 2. Enumerate host addresses via getaddrinfo
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None):
            ip = info[4][0]
            if ":" not in ip and not ip.startswith("127.") and not ip.startswith("0.") and not ip.startswith("169.254."):
                return ip
    except Exception:
        pass

    # 3. Fallback to gethostbyname
    try:
        ip = socket.gethostbyname(socket.gethostname())
        if ip and not ip.startswith("127.") and not ip.startswith("0.") and not ip.startswith("169.254."):
            return ip
    except Exception:
        pass

    return None


def get_all_lan_ips():
    """
    Returns a sorted list of all non-loopback, non-link-local IPv4 addresses found on this host.
    """
    ips = set()
    primary = get_lan_ip()
    if primary:
        ips.add(primary)

    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None):
            ip = info[4][0]
            if ":" not in ip and not ip.startswith("127.") and not ip.startswith("0.") and not ip.startswith("169.254."):
                ips.add(ip)
    except Exception:
        pass

    return sorted(list(ips))


def is_lan_host(host_str):
    """
    Checks if a hostname / IP string is reachable over LAN (not loopback, not 0.0.0.0).
    """
    if not host_str:
        return False
    h = host_str.split(":")[0].strip().lower()
    if h in ("127.0.0.1", "localhost", "0.0.0.0", "::1", "testserver"):
        return False
    if h.startswith("127."):
        return False
    return True


def get_pos_scanner_base_url(request=None):
    """
    Resolves the base URL for the phone scanner according to strict priority:
    1. Explicit configured POS_SCANNER_BASE_URL (from settings or env).
    2. Current request host if it is a LAN-reachable address (not loopback/0.0.0.0).
    3. Detected server LAN IPv4 address with port from request or 8000.
    4. Never returns 127.0.0.1, localhost, or 0.0.0.0 for phone QR.

    Returns a dict with:
    {
        "base_url": str,
        "lan_ip": str,
        "port": str,
        "source": "CONFIGURED" | "REQUEST_HOST" | "DETECTED_LAN_IP" | "FALLBACK",
        "is_configured": bool,
        "is_https": bool,
        "all_lan_ips": list
    }
    """
    # 1. Explicit configured POS_SCANNER_BASE_URL
    configured_base = getattr(settings, "POS_SCANNER_BASE_URL", "") or os.getenv("POS_SCANNER_BASE_URL", "").strip()
    if configured_base:
        clean_url = configured_base.rstrip("/")
        is_https = clean_url.startswith("https://")
        parsed = urlparse(clean_url)
        return {
            "base_url": clean_url,
            "lan_ip": parsed.hostname or "",
            "port": str(parsed.port) if parsed.port else ("443" if is_https else "80"),
            "source": "CONFIGURED",
            "is_configured": True,
            "is_https": is_https,
            "all_lan_ips": get_all_lan_ips(),
        }

    # Extract request port and scheme if request available
    port = "8000"
    scheme = "http"
    if request:
        scheme = request.scheme
        req_host = request.get_host()
        if ":" in req_host:
            port = req_host.split(":")[-1]

    # 2. Current request host if LAN-reachable
    if request:
        req_host = request.get_host()
        if is_lan_host(req_host):
            base_url = f"{scheme}://{req_host}".rstrip("/")
            return {
                "base_url": base_url,
                "lan_ip": req_host.split(":")[0],
                "port": port,
                "source": "REQUEST_HOST",
                "is_configured": False,
                "is_https": scheme == "https",
                "all_lan_ips": get_all_lan_ips(),
            }

    # 3. Detect server LAN IPv4 address
    lan_ip = get_lan_ip()
    if lan_ip:
        base_url = f"{scheme}://{lan_ip}:{port}"
        return {
            "base_url": base_url,
            "lan_ip": lan_ip,
            "port": port,
            "source": "DETECTED_LAN_IP",
            "is_configured": False,
            "is_https": scheme == "https",
            "all_lan_ips": get_all_lan_ips(),
        }

    # 4. Fallback if no network interface is active
    fallback_ip = "192.168.1.100"
    return {
        "base_url": f"{scheme}://{fallback_ip}:{port}",
        "lan_ip": fallback_ip,
        "port": port,
        "source": "FALLBACK",
        "is_configured": False,
        "is_https": scheme == "https",
        "all_lan_ips": [],
    }


def get_phone_scanner_full_url(session_token, request=None):
    """
    Builds the full URL for pairing a phone scanner session.
    Guaranteed to never contain 127.0.0.1, localhost, or 0.0.0.0.
    """
    info = get_pos_scanner_base_url(request=request)
    info["phone_url"] = f"{info['base_url']}/pos/scanner/?session={session_token}"
    return info
