import os
import json
import re
import secrets
import calendar
from datetime import timedelta, datetime, date
from decimal import Decimal
from django.utils import timezone
from django.db.models import Sum, Count, Q
from django.conf import settings

from .models import TelegramOwnerLink, TelegramOutboundMessage, CompanyCloudSnapshot, RemoteSyncStatus
from .sync_service import SyncService
from .media_reports import (
    generate_executive_pdf,
    generate_chart_image,
    generate_sales_chart,
    generate_returns_chart,
    generate_payments_chart,
    generate_inventory_chart,
    generate_all_chart,
)

from sales.models import Sale, SaleItem, ReturnRequest, Payment, Customer
from inventory.models import Product
from purchases.models import Purchase


def generate_pairing_code(company, owner):
    """
    Generate a secure 6-character one-time pairing code valid for 15 minutes.
    Telegram MUST NEVER require or accept the owner's application password.
    Invalidates any pending unverified links for this company/owner.
    Also syncs verification_challenge with MerchantTelegramBot if one exists for the company.
    """
    TelegramOwnerLink.objects.filter(company=company, owner=owner, is_verified=False).delete()

    code = secrets.token_hex(3).upper()
    expires_at = timezone.now() + timedelta(minutes=15)

    link = TelegramOwnerLink.objects.create(
        company=company,
        owner=owner,
        pairing_code=code,
        pairing_code_expires_at=expires_at,
        is_verified=False
    )

    from dashboard.models import MerchantTelegramBot
    m_bot = MerchantTelegramBot.objects.filter(company=company).first()
    if m_bot and m_bot.status != "CONNECTED":
        m_bot.verification_challenge = code
        m_bot.verification_expires_at = expires_at
        if m_bot.status != "INVALID_TOKEN":
            m_bot.status = "PENDING_VERIFICATION"
        m_bot.save()

    return link



def verify_pairing_code(pairing_code, chat_id, telegram_username=""):
    """
    Verify pairing code from Telegram chat and bind chat_id to the owner/company.
    Guarantees no password exposure: pairing is purely token-bound.
    Supports both default TelegramOwnerLink pairing codes and MerchantTelegramBot verification challenges.
    """
    clean_code = pairing_code.strip().upper()
    now = timezone.now()

    link = TelegramOwnerLink.objects.filter(
        pairing_code=clean_code,
        pairing_code_expires_at__gte=now,
        is_verified=False
    ).first()

    if not link:
        from dashboard.models import MerchantTelegramBot
        m_bot = MerchantTelegramBot.objects.filter(
            verification_challenge__iexact=clean_code,
            status="PENDING_VERIFICATION"
        ).first()
        if m_bot:
            if m_bot.verification_expires_at and m_bot.verification_expires_at < now:
                return False, None, "Verification challenge expired. Please generate a new code in POS Settings > Telegram."
            m_bot.status = "CONNECTED"
            m_bot.is_active = True
            m_bot.verified_chat_id = str(chat_id).strip()
            m_bot.verified_username = telegram_username.strip()
            m_bot.verified_at = now
            m_bot.verification_challenge = ""
            m_bot.save()

            link, _ = TelegramOwnerLink.objects.get_or_create(
                company=m_bot.company,
                owner=m_bot.owner,
                defaults={"is_verified": True, "telegram_chat_id": str(chat_id).strip()}
            )
            link.is_verified = True
            link.telegram_chat_id = str(chat_id).strip()
            link.telegram_username = telegram_username.strip()
            link.pairing_code = ""
            link.save()
            return True, link, f"✅ Pairing successful! Linked to '{m_bot.company.company_name}' as {m_bot.owner.username}."

        return False, None, "Invalid or expired pairing code. Please generate a new code in POS Settings > Telegram."

    link.telegram_chat_id = str(chat_id).strip()
    link.telegram_username = telegram_username.strip()
    link.is_verified = True
    link.pairing_code = ""
    link.save()

    # Also synchronize MerchantTelegramBot if exists for this company
    from dashboard.models import MerchantTelegramBot
    m_bot = MerchantTelegramBot.objects.filter(company=link.company).first()
    if m_bot and m_bot.status == "PENDING_VERIFICATION":
        m_bot.status = "CONNECTED"
        m_bot.is_active = True
        m_bot.verified_chat_id = str(chat_id).strip()
        m_bot.verified_username = telegram_username.strip()
        m_bot.verified_at = now
        m_bot.verification_challenge = ""
        m_bot.save()

    return True, link, f"✅ Pairing successful! Linked to '{link.company.company_name}' as {link.owner.username}."



def queue_telegram_message(company, chat_id, message_text, media_type="TEXT", media_path="", media_filename=""):
    """
    Queue an outbound message (text, PDF document, or chart photo) to survive offline or intermittent connectivity.
    """
    return TelegramOutboundMessage.objects.create(
        company=company,
        chat_id=str(chat_id),
        message_text=message_text,
        media_type=media_type,
        media_path=media_path,
        media_filename=media_filename,
        status="QUEUED"
    )


_telegram_verification_state = {
    "status": None,
    "bot_username": None,
    "last_checked": None,
    "last_error": None,
}

_bot_worker_state = {
    "last_poll_timestamp": None,
    "last_update_received": None,
    "last_update_text": None,
    "active": False,
}


def _get_heartbeat_filepath():
    return os.path.join(settings.BASE_DIR, ".bot_heartbeat.json")


def _read_heartbeat_file():
    path = _get_heartbeat_filepath()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _write_heartbeat_file(data):
    path = _get_heartbeat_filepath()
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception:
        pass


def record_worker_heartbeat():
    """Records that the background bot polling worker is active and listening."""
    now_dt = timezone.now()
    _bot_worker_state["last_poll_timestamp"] = now_dt
    _bot_worker_state["active"] = True

    try:
        data = _read_heartbeat_file()
        data["last_poll_timestamp"] = now_dt.isoformat()
        data["pid"] = os.getpid()
        _write_heartbeat_file(data)
    except Exception:
        pass


def record_update_received(text=""):
    """Records timestamp of the last message update received from Telegram."""
    now_dt = timezone.now()
    _bot_worker_state["last_update_received"] = now_dt
    _bot_worker_state["last_update_text"] = (text[:30] if text else "")

    try:
        data = _read_heartbeat_file()
        data["last_update_received"] = now_dt.isoformat()
        data["last_update_text"] = (text[:30] if text else "")
        _write_heartbeat_file(data)
    except Exception:
        pass


def is_bot_worker_active():
    """Returns True if the background polling loop has reported a heartbeat in the last 45 seconds."""
    try:
        data = _read_heartbeat_file()
        ts_str = data.get("last_poll_timestamp")
        if ts_str:
            ts = datetime.fromisoformat(ts_str)
            if timezone.is_naive(ts):
                ts = timezone.make_aware(ts)
            if (timezone.now() - ts).total_seconds() < 45:
                return True
    except Exception:
        pass

    last_poll = _bot_worker_state.get("last_poll_timestamp")
    if not last_poll:
        return False
    return (timezone.now() - last_poll).total_seconds() < 45


def is_bot_token_configured():
    token = getattr(settings, "TELEGRAM_BOT_TOKEN", "") or ""
    token = token.strip()
    if not token or len(token) < 10:
        return False
    lower = token.lower()
    if any(placeholder in lower for placeholder in ["your_bot_token", "demo_token", "placeholder", "xxx"]):
        return False
    return True


def get_telegram_gateway_status(company=None):
    """
    Evaluates accurate Telegram integration state without exposing the token.
    Truthfully distinguishes:
      - Token not configured
      - Token configured but invalid
      - Telegram API reachable
      - Bot handler / worker running vs stopped
      - Delivery mechanism (Long-polling)
      - Last successful update received
      - Django database connected
      - Outbound buffer / sync status
    """
    db_connected = True
    try:
        from accounts.models import companyRegistration
        companyRegistration.objects.exists()
    except Exception:
        db_connected = False

    if not is_bot_token_configured():
        return {
            "status": "Not configured",
            "badge_label": "Not configured",
            "is_configured": False,
            "is_verified": False,
            "is_api_reachable": False,
            "worker_active": False,
            "mode": "Long-polling (Standby)",
            "badge_class": "neutral",
            "bot_username": None,
            "db_connected": db_connected,
            "last_update_received": _bot_worker_state.get("last_update_received"),
            "description": "Telegram bot token is not configured in .env. Bot is offline.",
        }

    v_status = _telegram_verification_state.get("status")
    bot_user = _telegram_verification_state.get("bot_username")
    worker_running = is_bot_worker_active()

    if v_status is None:
        return {
            "status": "Configured but not verified",
            "badge_label": "Unverified",
            "is_configured": True,
            "is_verified": False,
            "is_api_reachable": False,
            "worker_active": worker_running,
            "mode": "Long-polling (Standby)",
            "badge_class": "warning",
            "bot_username": None,
            "db_connected": db_connected,
            "last_update_received": _bot_worker_state.get("last_update_received"),
            "description": "Telegram bot token is configured but connection has not been verified yet.",
        }

    last_upd = _bot_worker_state.get("last_update_received")
    if not last_upd:
        try:
            hb_data = _read_heartbeat_file()
            upd_str = hb_data.get("last_update_received")
            if upd_str:
                dt = datetime.fromisoformat(upd_str)
                if timezone.is_naive(dt):
                    dt = timezone.make_aware(dt)
                last_upd = dt
        except Exception:
            pass

    pending_count = 0
    failed_count = 0
    if company:
        failed_count = TelegramOutboundMessage.objects.filter(company=company, status="FAILED").count()
        pending_count = TelegramOutboundMessage.objects.filter(company=company, status="QUEUED").count()

    if failed_count > 0:
        return {
            "status": "Sync failed",
            "badge_label": "Sync failed",
            "is_configured": True,
            "is_verified": True,
            "is_api_reachable": True,
            "worker_active": worker_running,
            "mode": "Long-polling (Standalone Daemon)",
            "badge_class": "danger",
            "bot_username": bot_user,
            "db_connected": db_connected,
            "pending_count": pending_count,
            "failed_count": failed_count,
            "last_update_received": last_upd,
            "last_checked": _telegram_verification_state.get("last_checked"),
            "description": f"Sync failed: {failed_count} message(s) failed delivery.",
        }

    if pending_count > 0:
        return {
            "status": "Sync pending",
            "badge_label": "Sync pending",
            "is_configured": True,
            "is_verified": True,
            "is_api_reachable": True,
            "worker_active": worker_running,
            "mode": "Long-polling (Standalone Daemon)",
            "badge_class": "warning",
            "bot_username": bot_user,
            "db_connected": db_connected,
            "pending_count": pending_count,
            "failed_count": failed_count,
            "last_update_received": last_upd,
            "last_checked": _telegram_verification_state.get("last_checked"),
            "description": f"Sync pending: {pending_count} outbound messages queued in buffer.",
        }

    if v_status in ["Token configured but invalid", "Connection failed"]:
        return {
            "status": "Connection failed",
            "badge_label": "Connection failed",
            "is_configured": True,
            "is_verified": False,
            "is_api_reachable": False,
            "worker_active": worker_running,
            "mode": "Long-polling",
            "badge_class": "danger",
            "bot_username": None,
            "db_connected": db_connected,
            "last_update_received": _bot_worker_state.get("last_update_received"),
            "description": _telegram_verification_state.get("last_error") or "Telegram Bot API verification failed. Check token in .env.",
        }

    is_api_reachable = (v_status == "Connection verified")
    badge_label = f"Verified ({bot_user or 'Bot'})"
    badge_class = "success"
    if not worker_running:
        badge_label = "Verified · Worker Standby"

    desc = f"Telegram Bot API reachable ({bot_user or 'Bot'}). Polling worker: {'Active (Listening)' if worker_running else 'Standby'}."

    return {
        "status": "Connection verified" if is_api_reachable else "Telegram API reachable",
        "badge_label": badge_label,
        "is_configured": True,
        "is_verified": is_api_reachable,
        "is_api_reachable": is_api_reachable,
        "worker_active": worker_running,
        "mode": "Long-polling (Standalone Daemon)",
        "badge_class": badge_class,
        "bot_username": bot_user,
        "db_connected": db_connected,
        "pending_count": pending_count,
        "failed_count": failed_count,
        "last_update_received": last_upd,
        "last_checked": _telegram_verification_state.get("last_checked"),
        "description": desc,
    }


def test_telegram_connection():
    """
    Performs real authenticated Telegram Bot API verification (getMe) only when configured.
    Never exposes token in responses, error messages, logs, or UI.
    Does not send messages or reports. Handles timeouts gracefully without crashing.
    """
    token = getattr(settings, "TELEGRAM_BOT_TOKEN", "") or ""
    token = token.strip()
    if not is_bot_token_configured():
        _telegram_verification_state["status"] = "Not configured"
        _telegram_verification_state["last_error"] = "Token not configured in .env"
        return {
            "success": False,
            "status": "Not configured",
            "message": "Telegram Bot Token is not configured. Set TELEGRAM_BOT_TOKEN in your environment or .env file before testing.",
            "bot_username": None,
        }

    import urllib.request
    import json
    import ssl

    url = f"https://api.telegram.org/bot{token}/getMe"
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "BillingSystem-POS/1.0 (Python)"}
        )
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=6, context=ctx) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("ok"):
                result = data.get("result", {})
                bot_user = result.get("username", "UnknownBot")
                bot_name = result.get("first_name", "")
                _telegram_verification_state["status"] = "Connection verified"
                _telegram_verification_state["bot_username"] = f"@{bot_user}"
                _telegram_verification_state["last_checked"] = timezone.now()
                _telegram_verification_state["last_error"] = None
                return {
                    "success": True,
                    "status": "Connection verified",
                    "message": f"Connection verified successfully with Telegram Bot API: @{bot_user} ({bot_name})",
                    "bot_username": f"@{bot_user}",
                }
            else:
                desc = data.get("description", "Unknown error")
                _telegram_verification_state["status"] = "Connection failed"
                _telegram_verification_state["last_error"] = desc
                return {
                    "success": False,
                    "status": "Connection failed",
                    "message": f"Telegram API returned error: {desc}",
                    "bot_username": None,
                }
    except urllib.error.HTTPError as e:
        err = f"HTTP {e.code}: Invalid bot token or unauthorized"
        _telegram_verification_state["status"] = "Token configured but invalid" if e.code in [401, 404] else "Connection failed"
        _telegram_verification_state["last_error"] = err
        return {
            "success": False,
            "status": _telegram_verification_state["status"],
            "message": f"Telegram API authentication failed ({err}). Please verify your TELEGRAM_BOT_TOKEN.",
            "bot_username": None,
        }
    except Exception as e:
        err = "Network error: unable to reach api.telegram.org (timeout or connection refused)"
        _telegram_verification_state["status"] = "Connection failed"
        _telegram_verification_state["last_error"] = err
        return {
            "success": False,
            "status": "Connection failed",
            "message": f"Connection test failed: {err}",
            "bot_username": None,
        }


def send_telegram_direct_message(chat_id, text, parse_mode="Markdown"):
    """
    Directly sends an immediate message to a Telegram chat via HTTP Bot API sendMessage.
    Useful for immediate responses to unlinked users (/start, /help, /pair errors)
    who do not have a company associated in TelegramOutboundMessage, as well as
    real-time interactive replies in the polling daemon.
    Never exposes or logs the token.
    """
    token = getattr(settings, "TELEGRAM_BOT_TOKEN", "") or ""
    token = token.strip()
    if not is_bot_token_configured():
        return False, "Bot token not configured."

    import urllib.request
    import json
    import ssl

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps({
        "chat_id": str(chat_id),
        "text": text,
        "parse_mode": parse_mode,
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "BillingSystem-POS/1.0 (Python)"
        }
    )
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=8, context=ctx) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("ok"):
                return True, "Message delivered"
            # Fallback if markdown parsing fails
            if "can't parse entities" in (data.get("description") or "").lower():
                payload_plain = json.dumps({
                    "chat_id": str(chat_id),
                    "text": text,
                }).encode("utf-8")
                req_plain = urllib.request.Request(url, data=payload_plain, headers={"Content-Type": "application/json", "User-Agent": "BillingSystem-POS/1.0 (Python)"})
                with urllib.request.urlopen(req_plain, timeout=8, context=ctx) as resp2:
                    data2 = json.loads(resp2.read().decode("utf-8"))
                    if data2.get("ok"):
                        return True, "Message delivered (plain)"
            return False, data.get("description", "Failed to deliver message")
    except Exception as e:
        return False, str(e)



def _build_multipart_payload(fields, files, boundary=None):
    if not boundary:
        boundary = "----WebKitFormBoundary" + secrets.token_hex(16)

    body = bytearray()
    for name, value in fields.items():
        if value is None:
            continue
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"))
        body.extend(str(value).encode("utf-8"))
        body.extend(b"\r\n")

    for name, (filename, content, content_type) in files.items():
        if content is None:
            continue
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'.encode("utf-8"))
        body.extend(f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"))
        body.extend(content)
        body.extend(b"\r\n")

    body.extend(f"--{boundary}--\r\n".encode("utf-8"))
    return bytes(body), f"multipart/form-data; boundary={boundary}"


def send_telegram_photo(chat_id, photo_source, caption="", filename="chart.png"):
    """
    Directly sends a photo (e.g. chart image) to Telegram chat via multipart/form-data sendPhoto.
    `photo_source` can be a file path, BytesIO, or raw bytes.
    Never exposes or logs the token.
    """
    token = getattr(settings, "TELEGRAM_BOT_TOKEN", "") or ""
    token = token.strip()
    if not is_bot_token_configured():
        return False, "Bot token not configured."

    photo_bytes = None
    if isinstance(photo_source, (bytes, bytearray)):
        photo_bytes = bytes(photo_source)
    elif hasattr(photo_source, "read"):
        photo_bytes = photo_source.read()
    elif isinstance(photo_source, str) and os.path.exists(photo_source):
        try:
            with open(photo_source, "rb") as f:
                photo_bytes = f.read()
        except Exception as e:
            return False, f"Could not read photo file: {e}"

    if not photo_bytes:
        return False, "Photo content is empty or file not found."

    import urllib.request
    import json
    import ssl

    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    fields = {
        "chat_id": str(chat_id),
        "caption": caption[:1024] if caption else "",
        "parse_mode": "Markdown",
    }
    files = {
        "photo": (filename or "chart.png", photo_bytes, "image/png")
    }

    body, content_type = _build_multipart_payload(fields, files)
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": content_type,
            "User-Agent": "BillingSystem-POS/1.0 (Python)"
        }
    )
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=12, context=ctx) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("ok"):
                return True, "Photo delivered"
            # Fallback if markdown parsing fails
            if "can't parse entities" in (data.get("description") or "").lower():
                fields_plain = {
                    "chat_id": str(chat_id),
                    "caption": caption[:1024] if caption else "",
                }
                body_plain, ctype_plain = _build_multipart_payload(fields_plain, files)
                req_plain = urllib.request.Request(url, data=body_plain, headers={"Content-Type": ctype_plain, "User-Agent": "BillingSystem-POS/1.0 (Python)"})
                with urllib.request.urlopen(req_plain, timeout=12, context=ctx) as resp2:
                    data2 = json.loads(resp2.read().decode("utf-8"))
                    if data2.get("ok"):
                        return True, "Photo delivered (plain)"
            return False, data.get("description", "Failed to deliver photo")
    except Exception as e:
        return False, str(e)


def send_telegram_document(chat_id, doc_source, caption="", filename="document.pdf"):
    """
    Directly sends a document (e.g. PDF report) to Telegram chat via multipart/form-data sendDocument.
    `doc_source` can be a file path, BytesIO, or raw bytes.
    Never exposes or logs the token.
    """
    token = getattr(settings, "TELEGRAM_BOT_TOKEN", "") or ""
    token = token.strip()
    if not is_bot_token_configured():
        return False, "Bot token not configured."

    doc_bytes = None
    if isinstance(doc_source, (bytes, bytearray)):
        doc_bytes = bytes(doc_source)
    elif hasattr(doc_source, "read"):
        doc_bytes = doc_source.read()
    elif isinstance(doc_source, str) and os.path.exists(doc_source):
        try:
            with open(doc_source, "rb") as f:
                doc_bytes = f.read()
        except Exception as e:
            return False, f"Could not read document file: {e}"

    if not doc_bytes:
        return False, "Document content is empty or file not found."

    import urllib.request
    import json
    import ssl

    url = f"https://api.telegram.org/bot{token}/sendDocument"
    fields = {
        "chat_id": str(chat_id),
        "caption": caption[:1024] if caption else "",
        "parse_mode": "Markdown",
    }
    content_type_file = "application/pdf" if (filename or "").endswith(".pdf") else "application/octet-stream"
    files = {
        "document": (filename or "document.pdf", doc_bytes, content_type_file)
    }

    body, content_type = _build_multipart_payload(fields, files)
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": content_type,
            "User-Agent": "BillingSystem-POS/1.0 (Python)"
        }
    )
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("ok"):
                return True, "Document delivered"
            if "can't parse entities" in (data.get("description") or "").lower():
                fields_plain = {
                    "chat_id": str(chat_id),
                    "caption": caption[:1024] if caption else "",
                }
                body_plain, ctype_plain = _build_multipart_payload(fields_plain, files)
                req_plain = urllib.request.Request(url, data=body_plain, headers={"Content-Type": ctype_plain, "User-Agent": "BillingSystem-POS/1.0 (Python)"})
                with urllib.request.urlopen(req_plain, timeout=15, context=ctx) as resp2:
                    data2 = json.loads(resp2.read().decode("utf-8"))
                    if data2.get("ok"):
                        return True, "Document delivered (plain)"
            return False, data.get("description", "Failed to deliver document")
    except Exception as e:
        return False, str(e)


def dispatch_outbound_messages():
    """
    Process queued outbound messages. If internet and valid TELEGRAM_BOT_TOKEN is present,
    attempts HTTP dispatch. Otherwise retains status for subsequent retries.
    Supports text, document (PDF), and photo (chart image).
    """
    queued = TelegramOutboundMessage.objects.filter(status="QUEUED").order_by("created_at")
    sent_count = 0

    if not is_bot_token_configured():
        # Do not invent delivery when token is missing. Leave queued for retry.
        return 0

    for msg in queued:
        try:
            if msg.media_type == "TEXT":
                ok, err = send_telegram_direct_message(msg.chat_id, msg.message_text)
                if ok:
                    msg.status = "SENT"
                    msg.sent_at = timezone.now()
                    msg.save()
                    sent_count += 1
                else:
                    msg.status = "FAILED"
                    msg.error_message = (err or "Text delivery failed")[:250]
                    msg.save()
            elif msg.media_type == "PHOTO":
                ok, err = send_telegram_photo(msg.chat_id, msg.media_path, caption=msg.message_text, filename=msg.media_filename)
                if ok:
                    msg.status = "SENT"
                    msg.sent_at = timezone.now()
                    msg.save()
                    sent_count += 1
                else:
                    msg.status = "FAILED"
                    msg.error_message = (err or "Photo delivery failed")[:250]
                    msg.save()
            elif msg.media_type == "DOCUMENT":
                ok, err = send_telegram_document(msg.chat_id, msg.media_path, caption=msg.message_text, filename=msg.media_filename)
                if ok:
                    msg.status = "SENT"
                    msg.sent_at = timezone.now()
                    msg.save()
                    sent_count += 1
                else:
                    msg.status = "FAILED"
                    msg.error_message = (err or "Document delivery failed")[:250]
                    msg.save()
        except Exception as e:
            msg.status = "FAILED"
            msg.error_message = str(e)[:250]
            msg.save()

    return sent_count


MONTH_MAP = {
    "january": 1, "jan": 1,
    "february": 2, "feb": 2,
    "march": 3, "mar": 3,
    "april": 4, "apr": 4,
    "may": 5,
    "june": 6, "jun": 6,
    "july": 7, "jul": 7,
    "august": 8, "aug": 8,
    "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10,
    "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}


def _try_parse_date_str(d_str, current_year=None, current_month=None):
    if not d_str:
        return None
    d_clean = str(d_str).strip()
    if current_year is None:
        current_year = timezone.localdate().year
    if current_month is None:
        current_month = timezone.localdate().month

    # 1. Standard full date formats:
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d", "%d-%m-%y", "%d/%m/%y"):
        try:
            return datetime.strptime(d_clean, fmt).date()
        except ValueError:
            continue

    # 2. Check for Day-Month without year (e.g. "02-10", "2-10", "02/10", "2/10"):
    parts = re.split(r"[-/]", d_clean)
    if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
        d_val = int(parts[0])
        m_val = int(parts[1])
        if 1 <= m_val <= 12:
            try:
                return date(current_year, m_val, d_val)
            except ValueError:
                return None

    # 3. Check for bare day number in current month (e.g. "2"):
    if d_clean.isdigit():
        d_val = int(d_clean)
        try:
            return date(current_year, current_month, d_val)
        except ValueError:
            return None

    return None


class NLPReportIntentParser:
    """
    Interprets natural-language reporting queries safely and accurately.
    Strictly parses intents, dates, and parameters:
    - AI interprets the question, but the backend/database remains the source of truth.
    - AI must never invent financial numbers.
    - AI must never directly execute arbitrary SQL.
    - AI must never modify business records through Telegram.
    - Supports DD-MM-YYYY, DD/MM/YYYY, YYYY-MM-DD, DD-MM, Month YYYY, and natural date ranges.
    - Explicitly detects malformed, ambiguous, or out-of-range dates and returns INVALID_DATE.
    - Never silently replaces an unrecognized date argument with today's report.
    """

    @classmethod
    def parse(cls, raw_text):
        text = (raw_text or "").strip()
        lower_text = text.lower()
        today = timezone.localdate()
        yesterday = today - timedelta(days=1)

        clean = re.sub(r"[^\w\s]", " ", lower_text)
        words = set(clean.split())
        parts = text.split()
        first_word = parts[0].lower() if parts else ""

        start_date = today
        end_date = today
        is_range = False
        date_label = f"Today ({today.strftime('%d-%m-%Y')})"
        intent = "UNKNOWN"

        def matches_any(keywords):
            for k in keywords:
                if " " in k:
                    if k in lower_text:
                        return True
                else:
                    if k in words:
                        return True
            return False

        # ── 1. INTENT CLASSIFICATION ──
        chart_direct_commands = [
            "/chart", "chart", "/chart_sales", "/chart_returns",
            "/chart_payments", "/chart_inventory", "/chart_all", "/chart_stock"
        ]
        is_chart_query = (
            first_word in chart_direct_commands
            or matches_any([
                "chart", "charts", "graph", "graphs", "visualize", "visualization",
                "plot", "trend", "show chart", "chart image"
            ])
        )
        # Disambiguate multi-tender queries from single-tender queries
        tender_kw_count = sum(1 for kw in ["cash", "upi", "card", "credit"] if kw in words)

        if is_chart_query:
            if first_word == "/chart_returns" or matches_any(["return", "returns", "refund", "refunds"]):
                intent = "CHART_RETURNS"
            elif first_word == "/chart_payments" or matches_any(["payment", "payments", "tender", "tenders", "cash", "upi", "card"]):
                intent = "CHART_PAYMENTS"
            elif first_word in ["/chart_inventory", "/chart_stock"] or matches_any(["inventory", "stock", "warehouse", "items"]):
                intent = "CHART_INVENTORY"
            elif first_word == "/chart_all" or matches_any(["all", "all charts", "everything", "complete", "combined", "overview", "executive"]):
                intent = "CHART_ALL"
            else:
                intent = "CHART_SALES"

        elif first_word in ["/pdf", "pdf"] or matches_any(["pdf report", "export pdf", "download pdf", "executive pdf"]):
            intent = "PDF_REPORT"
        elif tender_kw_count >= 2 or matches_any(["payment breakdown", "tender breakdown", "payments", "tender totals", "payment totals", "tenders", "cash upi", "cash and upi", "cash upi and credit"]):
            intent = "PAYMENTS"
        elif first_word in ["/stock", "/inventory", "stock", "inventory"] or matches_any(["low stock", "low_stock", "out of stock", "warehouse", "stock overview", "inventory stock", "current stock", "stock in the last", "products have low stock", "stock"]):
            intent = "INVENTORY"
        elif matches_any(["return", "returns", "refund", "refunds", "pending returns", "returns pending"]):
            intent = "RETURNS"
        elif matches_any(["customer credit", "udhari", "due", "khata", "receivable", "credit totals", "credit balances"]):
            intent = "CREDIT"
        elif matches_any(["cash", "cash collections", "cash sale", "cash payments", "cash collected"]):
            intent = "CASH"
        elif matches_any(["upi", "gpay", "phonepe", "qr code", "qr payment", "upi collections"]):
            intent = "UPI"
        elif matches_any(["credit card", "debit card", "pos card", "card payment", "card payments"]):
            intent = "CARD"
        elif matches_any(["purchase", "purchases", "supplier", "suppliers", "vendor", "po"]):
            intent = "PURCHASES"
        elif matches_any(["customers", "clients"]):
            intent = "CUSTOMERS"
        elif matches_any(["top product", "top products", "top selling", "top seller", "best seller", "best sellers", "best selling", "popular", "top items"]):
            intent = "TOP_PRODUCTS"
        elif matches_any(["compare", "comparison", "versus", " vs ", "wow", "mom", "trend"]):
            intent = "COMPARISON"
        elif first_word in ["/sales", "sales"] or matches_any(["sales report", "daily sales", "total sales", "revenue", "income", "turnover", "invoices", "sell", "sold", "sales", "how much did we sell", "how much did my shop sell", "today sales", "today's sales", "todays sales", "how much did my store sell"]):
            intent = "SALES"
        elif first_word in ["/report", "report", "/today", "today", "/daily", "daily", "/weekly", "weekly", "/monthly", "monthly", "/thisweek", "/thismonth"] or matches_any(["today report", "todays report", "today s report", "daily report", "weekly report", "monthly report", "executive report", "business summary", "report"]):
            intent = "REPORT"
        else:
            intent = "UNKNOWN"

        # ── 2. DATE EXTRACTION ──
        is_explicit_cmd = first_word in [
            "/report", "report", "/sales", "sales", "/pdf", "pdf",
            "/chart", "chart", "/chart_sales", "/chart_returns",
            "/chart_payments", "/chart_inventory", "/chart_all", "/chart_stock",
            "/today", "today", "/daily", "daily", "/weekly", "weekly", "/monthly", "monthly",
            "/thisweek", "/thismonth"
        ]

        if first_word in ["/today", "today", "/daily", "daily"] and (len(parts) == 1 or (len(parts) == 2 and parts[1].lower() in ["report", "sales", "summary", "stats"])):
            start_date = end_date = today
            is_range = False
            date_label = f"Today ({today.strftime('%d-%m-%Y')})"
        elif first_word in ["/weekly", "weekly", "/thisweek"] and (len(parts) == 1 or (len(parts) == 2 and parts[1].lower() in ["report", "sales", "summary", "stats"])):
            start_date = today - timedelta(days=today.weekday())
            end_date = today
            is_range = True
            date_label = f"This Week ({start_date.strftime('%d-%m-%Y')} to {end_date.strftime('%d-%m-%Y')})"
        elif first_word in ["/monthly", "monthly", "/thismonth"] and (len(parts) == 1 or (len(parts) == 2 and parts[1].lower() in ["report", "sales", "summary", "stats"])):
            start_date = today.replace(day=1)
            end_date = today
            is_range = True
            date_label = f"This Month ({start_date.strftime('%d-%m-%Y')} to {end_date.strftime('%d-%m-%Y')})"
        elif is_explicit_cmd and len(parts) > 1:
            cmd_args = parts[1:]
            sub_commands = ["sales", "returns", "payments", "inventory", "all", "stock"]
            if first_word.startswith("/chart") and cmd_args and cmd_args[0].lower() in sub_commands:
                cmd_args = cmd_args[1:]

            # Strip natural language prepositions like 'on', 'for', 'from', 'of', 'date', 'tarikh', 'ko'
            cmd_args = [t for t in cmd_args if t.lower() not in ["on", "for", "from", "of", "date", "tarikh", "ko", "par"]]

            if len(cmd_args) == 0 or (len(cmd_args) == 1 and cmd_args[0].lower() in ["report", "sales", "summary"]):
                start_date = end_date = today
                is_range = False
                date_label = f"Today ({today.strftime('%d-%m-%Y')})"

            elif len(cmd_args) == 1:
                arg0 = cmd_args[0].lower()
                if arg0 in ["today", "aaj", "daily"]:
                    start_date = end_date = today
                    is_range = False
                    date_label = f"Today ({today.strftime('%d-%m-%Y')})"
                elif arg0 in ["yesterday", "kal"]:
                    start_date = end_date = yesterday
                    is_range = False
                    date_label = f"Yesterday ({yesterday.strftime('%d-%m-%Y')})"
                elif arg0 in ["this_month", "this-month", "month", "monthly"]:
                    start_date = today.replace(day=1)
                    end_date = today
                    is_range = True
                    date_label = f"This Month ({start_date.strftime('%d-%m-%Y')} to {end_date.strftime('%d-%m-%Y')})"
                elif arg0 in ["last_month", "last-month"]:
                    first_this_month = today.replace(day=1)
                    last_month_end = first_this_month - timedelta(days=1)
                    start_date = last_month_end.replace(day=1)
                    end_date = last_month_end
                    is_range = True
                    date_label = f"Last Month ({start_date.strftime('%d-%m-%Y')} to {end_date.strftime('%d-%m-%Y')})"
                elif arg0 in ["week", "this_week", "this-week", "weekly"]:
                    start_date = today - timedelta(days=today.weekday())
                    end_date = today
                    is_range = True
                    date_label = f"This Week ({start_date.strftime('%d-%m-%Y')} to {end_date.strftime('%d-%m-%Y')})"
                elif arg0 in MONTH_MAP:
                    m_val = MONTH_MAP[arg0]
                    last_day = calendar.monthrange(today.year, m_val)[1]
                    start_date = date(today.year, m_val, 1)
                    end_date = date(today.year, m_val, last_day)
                    is_range = True
                    date_label = f"{arg0.capitalize()} {today.year}"
                else:
                    pd = _try_parse_date_str(arg0, current_year=today.year, current_month=today.month)
                    if pd:
                        start_date = end_date = pd
                        is_range = False
                        date_label = start_date.strftime("%d-%m-%Y")
                    else:
                        return {
                            "intent": "INVALID_DATE",
                            "error": f"Invalid date: {cmd_args[0]}",
                            "start_date": today,
                            "end_date": today,
                            "is_range": False,
                            "date_label": "invalid",
                            "raw_text": text,
                        }

            elif len(cmd_args) == 2:
                arg0 = cmd_args[0].lower()
                arg1 = cmd_args[1].lower()
                if arg0 in MONTH_MAP and arg1.isdigit():
                    m_val = MONTH_MAP[arg0]
                    y_val = int(arg1)
                    if 1900 <= y_val <= 2100:
                        last_day = calendar.monthrange(y_val, m_val)[1]
                        start_date = date(y_val, m_val, 1)
                        end_date = date(y_val, m_val, last_day)
                        is_range = True
                        date_label = f"{arg0.capitalize()} {y_val}"
                    else:
                        return {
                            "intent": "INVALID_DATE",
                            "error": f"Invalid year: {arg1}",
                            "start_date": today,
                            "end_date": today,
                            "is_range": False,
                            "date_label": "invalid",
                            "raw_text": text,
                        }
                else:
                    d1 = _try_parse_date_str(arg0, current_year=today.year, current_month=today.month)
                    d2 = _try_parse_date_str(arg1, current_year=today.year, current_month=today.month)
                    if d1 and d2:
                        start_date = min(d1, d2)
                        end_date = max(d1, d2)
                        is_range = True
                        date_label = f"{start_date.strftime('%d-%m-%Y')} to {end_date.strftime('%d-%m-%Y')}"
                    else:
                        return {
                            "intent": "INVALID_DATE",
                            "error": f"Invalid date range: {cmd_args[0]} {cmd_args[1]}",
                            "start_date": today,
                            "end_date": today,
                            "is_range": False,
                            "date_label": "invalid",
                            "raw_text": text,
                        }

            else:
                filtered = [t for t in cmd_args if t.lower() not in ["to", "se", "until", "-", "and"]]
                if len(filtered) == 2:
                    d1 = _try_parse_date_str(filtered[0], current_year=today.year, current_month=today.month)
                    d2 = _try_parse_date_str(filtered[1], current_year=today.year, current_month=today.month)
                    if d1 and d2:
                        start_date = min(d1, d2)
                        end_date = max(d1, d2)
                        is_range = True
                        date_label = f"{start_date.strftime('%d-%m-%Y')} to {end_date.strftime('%d-%m-%Y')}"
                    else:
                        return {
                            "intent": "INVALID_DATE",
                            "error": f"Invalid date range: {' '.join(cmd_args)}",
                            "start_date": today,
                            "end_date": today,
                            "is_range": False,
                            "date_label": "invalid",
                            "raw_text": text,
                        }
                else:
                    dmy = re.findall(r"\b(\d{1,2})(?:st|nd|rd|th)?[\s\-_]+([a-zA-Z]+)[\s\-_]+(\d{4})\b", " ".join(cmd_args))
                    if dmy:
                        d_num, m_name, y_num = dmy[0]
                        m_val = MONTH_MAP.get(m_name.lower())
                        if m_val:
                            try:
                                parsed_d = date(int(y_num), m_val, int(d_num))
                                start_date = end_date = parsed_d
                                is_range = False
                                date_label = parsed_d.strftime("%d-%m-%Y")
                            except ValueError:
                                return {
                                    "intent": "INVALID_DATE",
                                    "error": f"Invalid date: {d_num} {m_name} {y_num}",
                                    "start_date": today,
                                    "end_date": today,
                                    "is_range": False,
                                    "date_label": "invalid",
                                    "raw_text": text,
                                }
                        else:
                            return {
                                "intent": "INVALID_DATE",
                                "error": f"Invalid month name: {m_name}",
                                "start_date": today,
                                "end_date": today,
                                "is_range": False,
                                "date_label": "invalid",
                                "raw_text": text,
                            }
                    else:
                        return {
                            "intent": "INVALID_DATE",
                            "error": f"Invalid parameters: {' '.join(cmd_args)}",
                            "start_date": today,
                            "end_date": today,
                            "is_range": False,
                            "date_label": "invalid",
                            "raw_text": text,
                        }

        elif not is_explicit_cmd:
            date_tokens = re.findall(r"\b(\d{1,4}[-/]\d{1,2}(?:[-/]\d{1,4})?)\b", lower_text)
            if date_tokens:
                parsed_dates = []
                for tok in date_tokens:
                    pd = _try_parse_date_str(tok, current_year=today.year, current_month=today.month)
                    if not pd:
                        return {
                            "intent": "INVALID_DATE",
                            "error": f"Invalid date: {tok}",
                            "start_date": today,
                            "end_date": today,
                            "is_range": False,
                            "date_label": "invalid",
                            "raw_text": text,
                        }
                    parsed_dates.append(pd)

                if len(parsed_dates) >= 2:
                    start_date = min(parsed_dates[0], parsed_dates[1])
                    end_date = max(parsed_dates[0], parsed_dates[1])
                    is_range = True
                    date_label = f"{start_date.strftime('%d-%m-%Y')} to {end_date.strftime('%d-%m-%Y')}"
                else:
                    start_date = end_date = parsed_dates[0]
                    is_range = False
                    date_label = start_date.strftime("%d-%m-%Y")

            else:
                day_month_year = re.findall(r"\b(\d{1,2})(?:st|nd|rd|th)?[\s\-_]+([a-zA-Z]+)[\s\-_]+(\d{4})\b", lower_text)
                if day_month_year:
                    d_num, m_name, y_num = day_month_year[0]
                    m_val = MONTH_MAP.get(m_name.lower())
                    if m_val:
                        try:
                            parsed_d = date(int(y_num), m_val, int(d_num))
                            start_date = end_date = parsed_d
                            is_range = False
                            date_label = parsed_d.strftime("%d-%m-%Y")
                        except ValueError:
                            return {
                                "intent": "INVALID_DATE",
                                "error": f"Invalid date: {d_num} {m_name} {y_num}",
                                "start_date": today,
                                "end_date": today,
                                "is_range": False,
                                "date_label": "invalid",
                                "raw_text": text,
                            }
                    else:
                        return {
                            "intent": "INVALID_DATE",
                            "error": f"Invalid month name: {m_name}",
                            "start_date": today,
                            "end_date": today,
                            "is_range": False,
                            "date_label": "invalid",
                            "raw_text": text,
                        }
                else:
                    month_year = re.findall(r"\b([a-zA-Z]+)[\s\-_]+(\d{4})\b", lower_text)
                    matched_my = False
                    if month_year:
                        m_name, y_num = month_year[0]
                        m_val = MONTH_MAP.get(m_name.lower())
                        if m_val:
                            matched_my = True
                            y_val = int(y_num)
                            last_day = calendar.monthrange(y_val, m_val)[1]
                            start_date = date(y_val, m_val, 1)
                            end_date = date(y_val, m_val, last_day)
                            is_range = True
                            date_label = f"{m_name.capitalize()} {y_val}"

                    if not matched_my:
                        if any(k in lower_text for k in ["kal", "yesterday", "pichla din"]):
                            start_date = yesterday
                            end_date = yesterday
                            is_range = False
                            date_label = f"Kal / Yesterday ({yesterday.strftime('%d-%m-%Y')})"
                        elif any(k in lower_text for k in ["aaj", "today"]):
                            start_date = today
                            end_date = today
                            is_range = False
                            date_label = f"Aaj / Today ({today.strftime('%d-%m-%Y')})"
                        elif any(k in lower_text for k in ["this month", "iss mahine", "current month", "monthly"]):
                            start_date = today.replace(day=1)
                            end_date = today
                            is_range = True
                            date_label = f"This Month ({start_date.strftime('%d-%m-%Y')} to {end_date.strftime('%d-%m-%Y')})"
                        elif any(k in lower_text for k in ["last month", "pichle mahine", "prev month", "previous month"]):
                            first_this_month = today.replace(day=1)
                            last_month_end = first_this_month - timedelta(days=1)
                            start_date = last_month_end.replace(day=1)
                            end_date = last_month_end
                            is_range = True
                            date_label = f"Last Month ({start_date.strftime('%d-%m-%Y')} to {end_date.strftime('%d-%m-%Y')})"
                        elif any(k in lower_text for k in ["this week", "iss hafte", "current week", "weekly"]):
                            start_date = today - timedelta(days=today.weekday())
                            end_date = today
                            is_range = True
                            date_label = f"This Week ({start_date.strftime('%d-%m-%Y')} to {end_date.strftime('%d-%m-%Y')})"
                        elif any(k in lower_text for k in ["daily"]):
                            start_date = today
                            end_date = today
                            is_range = False
                            date_label = f"Aaj / Today ({today.strftime('%d-%m-%Y')})"
                        elif any(k in lower_text for k in ["last week", "pichle hafte", "past week"]):
                            end_date = today - timedelta(days=today.weekday() + 1)
                            start_date = end_date - timedelta(days=6)
                            is_range = True
                            date_label = f"Last Week ({start_date.strftime('%d-%m-%Y')} to {end_date.strftime('%d-%m-%Y')})"
                        elif any(k in lower_text for k in ["7 days", "7d", "week", "past 7", "last 7"]):
                            start_date = today - timedelta(days=7)
                            end_date = today
                            is_range = True
                            date_label = f"{start_date.strftime('%d-%m-%Y')} to {end_date.strftime('%d-%m-%Y')} (Last 7 Days)"
                        elif any(k in lower_text for k in ["30 days", "30d", "last 30", "past 30", "month"]):
                            start_date = today - timedelta(days=30)
                            end_date = today
                            is_range = True
                            date_label = f"{start_date.strftime('%d-%m-%Y')} to {end_date.strftime('%d-%m-%Y')})"

        return {
            "intent": intent,
            "start_date": start_date,
            "end_date": end_date,
            "is_range": is_range,
            "date_label": date_label,
            "raw_text": text,
        }


def parse_report_command_text(text: str):
    """
    Public entrypoint for parsing Telegram reporting commands and natural dates.
    Delegates to NLPReportIntentParser.parse.
    """
    return NLPReportIntentParser.parse(text)


class TelegramReportingEngine:
    """
    Authoritative, deterministic database reporting engine for Telegram.
    Computes all numbers directly from the database using Django ORM.
    AI never invents financial figures.
    Appends the last sync timestamp to every report.
    """

    @classmethod
    def execute(cls, company, parsed_query):
        intent = parsed_query["intent"]
        start = parsed_query["start_date"]
        end = parsed_query["end_date"]
        date_label = parsed_query["date_label"]

        # ── 0. UNKNOWN / UNSUPPORTED COMMAND ──
        if intent == "UNKNOWN":
            return {
                "text": "Command samajh nahi aaya. Available commands ke liye /help bhejein.",
                "media_type": "TEXT"
            }

        sync_info = SyncService.get_sync_status(company)
        last_sync_ts = sync_info['last_sync_timestamp_display']
        now_dt = timezone.localtime(timezone.now())
        generated_ts = now_dt.strftime("%b %d, %Y %I:%M %p")
        is_store_online = sync_info["is_online"] and sync_info["pending_count"] == 0

        # Retrieve latest durable cloud snapshot for offline fallback and cloud-worker reporting
        cloud_snap = CompanyCloudSnapshot.objects.filter(company=company, snapshot_date=start).first()
        if not cloud_snap:
            cloud_snap = CompanyCloudSnapshot.objects.filter(company=company).order_by("-snapshot_date").first()

        use_snapshot_data = (not is_store_online) or (str(getattr(settings, "CLOUD_SYNC_ROLE", "edge")).strip().lower() in ["cloud", "cloud_worker"])

        if is_store_online:
            sync_footer = (
                f"\n────────────────────────\n"
                f"🕒 *Report Generated:* `{generated_ts}`\n"
                f"🔄 *Data Through (Last Sync):* `{last_sync_ts}`\n"
                f"🟢 *Live Data* (Store online, synchronized)\n"
                f"🔄 Cloud Sync: Synchronized ({last_sync_ts})"
            )
        else:
            pending_cnt = sync_info['pending_count']
            freshness = sync_info.get("freshness_status", "CACHED")
            sync_footer = (
                f"\n────────────────────────\n"
                f"🕒 *Report Generated:* `{generated_ts}`\n"
                f"🔄 *Data Through (Last Sync):* `{last_sync_ts}`\n"
                f"⚠️ *Report may be stale — Store Offline Snapshot*\n"
                f"⚠️ Store Offline: {pending_cnt} local pending items. Newer offline sales or stock activity may be missing from this report.\n"
                f"📡 *Freshness:* `{freshness}` (Last Cloud Sync: {last_sync_ts})\n"
                f"Your store is currently disconnected or has not confirmed a newer sync. Recent local transactions may not be included."
            )

        # ── 0. INVALID DATE ──
        if intent == "INVALID_DATE":
            return {
                "text": (
                    f"❌ *Invalid or Unrecognized Date Format*\n"
                    f"The date provided in your request could not be recognized as a valid calendar date.\n\n"
                    f"💡 *Supported Date Formats:*\n"
                    f"• *Today* / *Aaj* / *Yesterday* / *Kal*\n"
                    f"• *YYYY-MM-DD* (e.g. `/report 2026-10-02`)\n"
                    f"• *DD-MM-YYYY* (e.g. `/report 02-10-2026` or `02/10/2026`)\n"
                    f"• *DD-MM* (e.g. `/report 02-10` for 2 Oct this year)\n"
                    f"• *Day of Month* (e.g. `/report 2` for 2nd of this month)\n"
                    f"• *Date Range* (e.g. `/report 2026-10-01 2026-10-11` or `01-10-2026 to 11-10-2026`)\n"
                    f"• *Month Year* (e.g. `/report September 2026`)"
                ),
                "media_type": "TEXT"
            }

        # ── 1. PDF REPORT EXPORT ──
        if intent == "PDF_REPORT":
            file_path, filename, _ = generate_executive_pdf(company, start, end)
            return {
                "text": (
                    f"📄 *Executive PDF Report Generated*\n"
                    f"Store: {company.company_name}\n"
                    f"Period: {date_label}\n"
                    f"File: `{filename}`\n"
                    f"{sync_footer}"
                ),
                "media_type": "DOCUMENT",
                "media_path": file_path,
                "media_filename": filename,
            }

        # ── 2. CHART EXPORTS (1600x1000px High-Res PNG) ──
        if intent == "CHART_SALES":
            file_path, filename, _ = generate_sales_chart(company, start, end)
            return {
                "text": (
                    f"📊 *Sales & Revenue Analysis Chart ({date_label})*\n"
                    f"Store: {company.company_name}\n"
                    f"{sync_footer}"
                ),
                "media_type": "PHOTO",
                "media_path": file_path,
                "media_filename": filename,
            }

        if intent == "CHART_RETURNS":
            file_path, filename, _ = generate_returns_chart(company, start, end)
            return {
                "text": (
                    f"🔄 *Store Returns & Refunds Analysis Chart ({date_label})*\n"
                    f"Store: {company.company_name}\n"
                    f"{sync_footer}"
                ),
                "media_type": "PHOTO",
                "media_path": file_path,
                "media_filename": filename,
            }

        if intent == "CHART_PAYMENTS":
            file_path, filename, _ = generate_payments_chart(company, start, end)
            return {
                "text": (
                    f"💳 *Payment Tender Distribution Chart ({date_label})*\n"
                    f"Store: {company.company_name}\n"
                    f"{sync_footer}"
                ),
                "media_type": "PHOTO",
                "media_path": file_path,
                "media_filename": filename,
            }

        if intent == "CHART_INVENTORY":
            file_path, filename, _ = generate_inventory_chart(company, start, end)
            return {
                "text": (
                    f"📦 *Inventory Health & Stock Velocity Chart ({date_label})*\n"
                    f"Store: {company.company_name}\n"
                    f"{sync_footer}"
                ),
                "media_type": "PHOTO",
                "media_path": file_path,
                "media_filename": filename,
            }

        if intent in ["CHART_ALL", "CHART_IMAGE"]:
            file_path, filename, _ = generate_all_chart(company, start, end)
            return {
                "text": (
                    f"📊 *Complete Executive Overview Chart ({date_label})*\n"
                    f"Store: {company.company_name}\n"
                    f"{sync_footer}"
                ),
                "media_type": "PHOTO",
                "media_path": file_path,
                "media_filename": filename,
            }

        # ── 3. CASH REPORT ──
        if intent == "CASH":
            if use_snapshot_data and cloud_snap:
                total_cash = cloud_snap.cash_total
                gross_all = cloud_snap.total_revenue
                count_cash = cloud_snap.invoice_count
                share = (total_cash / gross_all * 100) if gross_all > 0 else 0
            else:
                sales_qs = Sale.objects.filter(company=company, sale_status="COMPLETED", created_at__date__gte=start, created_at__date__lte=end)
                payments = Payment.objects.filter(sale__company=company, sale__in=sales_qs, payment_method="CASH")
                total_cash = payments.aggregate(t=Sum("amount"))["t"] or Decimal("0.00")
                count_cash = payments.count()
                gross_all = sales_qs.aggregate(t=Sum("grand_total"))["t"] or Decimal("0.00")
                share = (total_cash / gross_all * 100) if gross_all > 0 else 0

            return {
                "text": (
                    f"💵 *Cash Collections ({date_label})*\n"
                    f"Store: {company.company_name}\n"
                    f"────────────────────────\n"
                    f"• Total Cash Collected: *₹{total_cash:,.2f}*\n"
                    f"• Cash Payments: *{count_cash} transactions*\n"
                    f"• Share of Total Sales: *{share:.1f}%*\n"
                    f"{sync_footer}"
                ),
                "media_type": "TEXT"
            }

        # ── 4. UPI REPORT ──
        if intent == "UPI":
            if use_snapshot_data and cloud_snap:
                total_upi = cloud_snap.upi_total
                gross_all = cloud_snap.total_revenue
                count_upi = cloud_snap.invoice_count
                share = (total_upi / gross_all * 100) if gross_all > 0 else 0
            else:
                sales_qs = Sale.objects.filter(company=company, sale_status="COMPLETED", created_at__date__gte=start, created_at__date__lte=end)
                payments = Payment.objects.filter(sale__company=company, sale__in=sales_qs, payment_method="UPI")
                total_upi = payments.aggregate(t=Sum("amount"))["t"] or Decimal("0.00")
                count_upi = payments.count()
                gross_all = sales_qs.aggregate(t=Sum("grand_total"))["t"] or Decimal("0.00")
                share = (total_upi / gross_all * 100) if gross_all > 0 else 0

            return {
                "text": (
                    f"📱 *UPI / QR Collections ({date_label})*\n"
                    f"Store: {company.company_name}\n"
                    f"────────────────────────\n"
                    f"• Total UPI Collected: *₹{total_upi:,.2f}*\n"
                    f"• Verified UPI Scans: *{count_upi} transactions*\n"
                    f"• Share of Total Sales: *{share:.1f}%*\n"
                    f"{sync_footer}"
                ),
                "media_type": "TEXT"
            }

        # ── 5. CARD REPORT ──
        if intent == "CARD":
            if use_snapshot_data and cloud_snap:
                total_card = cloud_snap.card_total
                gross_all = cloud_snap.total_revenue
                count_card = cloud_snap.invoice_count
                share = (total_card / gross_all * 100) if gross_all > 0 else 0
            else:
                sales_qs = Sale.objects.filter(company=company, sale_status="COMPLETED", created_at__date__gte=start, created_at__date__lte=end)
                payments = Payment.objects.filter(sale__company=company, sale__in=sales_qs, payment_method="CARD")
                total_card = payments.aggregate(t=Sum("amount"))["t"] or Decimal("0.00")
                count_card = payments.count()
                gross_all = sales_qs.aggregate(t=Sum("grand_total"))["t"] or Decimal("0.00")
                share = (total_card / gross_all * 100) if gross_all > 0 else 0

            return {
                "text": (
                    f"💳 *Card POS Collections ({date_label})*\n"
                    f"Store: {company.company_name}\n"
                    f"────────────────────────\n"
                    f"• Total Card Settled: *₹{total_card:,.2f}*\n"
                    f"• Card Swipes/Taps: *{count_card} transactions*\n"
                    f"• Share of Total Sales: *{share:.1f}%*\n"
                    f"{sync_footer}"
                ),
                "media_type": "TEXT"
            }

        # ── 6. CREDIT / ACCOUNTS RECEIVABLE REPORT ──
        if intent == "CREDIT":
            if use_snapshot_data and cloud_snap:
                total_receivable = cloud_snap.total_credit_receivable
                top_balances = cloud_snap.customer_balances
                debtors_count = len(top_balances)

                lines = [
                    f"📒 *Customer Credit & Accounts Receivable*",
                    f"Store: {company.company_name}",
                    f"────────────────────────",
                    f"• Total Outstanding Debt: *₹{total_receivable:,.2f}*",
                    f"• Customers with Credit Balance: *{debtors_count}*",
                ]
                if top_balances:
                    lines.append("\nTop Outstanding Balances:")
                    for c in top_balances:
                        bal = Decimal(str(c.get("balance", "0")))
                        lines.append(f"• *{c.get('name', 'Customer')}*: ₹{bal:,.2f}")
                lines.append(sync_footer)

                return {"text": "\n".join(lines), "media_type": "TEXT"}
            else:
                customers_credit = Customer.objects.filter(company=company, credit_balance__gt=0).order_by("-credit_balance")
                total_receivable = Customer.objects.filter(company=company).aggregate(t=Sum("credit_balance"))["t"] or Decimal("0.00")
                debtors_count = customers_credit.count()

                lines = [
                    f"📒 *Customer Credit & Accounts Receivable*",
                    f"Store: {company.company_name}",
                    f"────────────────────────",
                    f"• Total Outstanding Debt: *₹{total_receivable:,.2f}*",
                    f"• Customers with Credit Balance: *{debtors_count}*",
                ]
                if debtors_count > 0:
                    lines.append("\nTop Outstanding Balances:")
                    for c in customers_credit[:5]:
                        lines.append(f"• *{c.name}* (`{c.phone}`): ₹{c.credit_balance:,.2f} (Limit: ₹{c.credit_limit:,.2f})")
                lines.append(sync_footer)

                return {"text": "\n".join(lines), "media_type": "TEXT"}

        # ── 7. PURCHASES REPORT ──
        if intent == "PURCHASES":
            purchases_qs = Purchase.objects.filter(company=company, purchase_date__gte=start, purchase_date__lte=end)
            po_count = purchases_qs.count()
            received_count = purchases_qs.filter(status="RECEIVED").count()
            draft_count = purchases_qs.filter(status="DRAFT").count()

            total_purchase_val = sum((p.subtotal for p in purchases_qs), Decimal("0.00"))
            suppliers_count = purchases_qs.values("supplier_id").distinct().count()

            return {
                "text": (
                    f"🛒 *Supplier Purchases Report ({date_label})*\n"
                    f"Store: {company.company_name}\n"
                    f"────────────────────────\n"
                    f"• Total Purchase Orders: *{po_count}*\n"
                    f"• Received Deliveries: *{received_count} POs*\n"
                    f"• Pending Drafts: *{draft_count} POs*\n"
                    f"• Total Purchase Expense: *₹{total_purchase_val:,.2f}*\n"
                    f"• Active Suppliers: *{suppliers_count}*\n"
                    f"{sync_footer}"
                ),
                "media_type": "TEXT"
            }

        # ── 8. CUSTOMERS REPORT ──
        if intent == "CUSTOMERS":
            all_cust = Customer.objects.filter(company=company)
            total_customers = all_cust.count()
            active_customers = all_cust.filter(sales__company=company, sales__sale_status="COMPLETED").distinct().count()
            total_credit = all_cust.aggregate(t=Sum("credit_balance"))["t"] or Decimal("0.00")

            top_customers = all_cust.annotate(
                total_spent=Sum("sales__grand_total", filter=Q(sales__sale_status="COMPLETED"))
            ).filter(total_spent__gt=0).order_by("-total_spent")[:3]

            lines = [
                f"👥 *Customer Intelligence & Directory*",
                f"Store: {company.company_name}",
                f"────────────────────────",
                f"• Total Registered Customers: *{total_customers}*",
                f"• Active Transacting Buyers: *{active_customers}*",
                f"• Total Credit Outstanding: *₹{total_credit:,.2f}*",
            ]
            if top_customers:
                lines.append("\nTop Customers by Lifetime Spend:")
                for idx, c in enumerate(top_customers, 1):
                    lines.append(f"{idx}. *{c.name}* (`{c.phone}`): ₹{c.total_spent:,.2f}")
            lines.append(sync_footer)

            return {"text": "\n".join(lines), "media_type": "TEXT"}

        # ── 9. RETURNS REPORT ──
        if intent == "RETURNS":
            if use_snapshot_data and cloud_snap:
                approved = cloud_snap.returns_count
                refunded = cloud_snap.refund_amount
                return {
                    "text": (
                        f"🔄 *Store Returns & Refunds Status ({date_label})*\n"
                        f"Store: {company.company_name}\n"
                        f"────────────────────────\n"
                        f"• ⏳ Pending Manager Audit: *0*\n"
                        f"• ✅ Approved & Restocked: *{approved}*\n"
                        f"• ❌ Rejected: *0*\n"
                        f"• Total Refunds Issued: *₹{refunded:,.2f}*\n"
                        f"{sync_footer}"
                    ),
                    "media_type": "TEXT"
                }
            else:
                returns_all = ReturnRequest.objects.filter(company=company, updated_at__date__gte=start, updated_at__date__lte=end)
                pending = returns_all.filter(status="PENDING").count()
                approved = returns_all.filter(status__in=["APPROVED", "COMPLETED"]).count()
                rejected = returns_all.filter(status="REJECTED").count()
                refunded = returns_all.filter(status__in=["APPROVED", "COMPLETED"]).aggregate(t=Sum("refund_amount"))["t"] or Decimal("0.00")

                return {
                    "text": (
                        f"🔄 *Store Returns & Refunds Status ({date_label})*\n"
                        f"Store: {company.company_name}\n"
                        f"────────────────────────\n"
                        f"• ⏳ Pending Manager Audit: *{pending}*\n"
                        f"• ✅ Approved & Restocked: *{approved}*\n"
                        f"• ❌ Rejected: *{rejected}*\n"
                        f"• Total Refunds Issued: *₹{refunded:,.2f}*\n"
                        f"{sync_footer}"
                    ),
                    "media_type": "TEXT"
                }

        # ── 10. INVENTORY / STOCK REPORT ──
        if intent == "INVENTORY":
            if use_snapshot_data and cloud_snap:
                total_prods = cloud_snap.active_products_count
                active_prods = cloud_snap.active_products_count
                low_prods = cloud_snap.low_stock_items
                low_stock = len(low_prods)
                out_prods = cloud_snap.out_of_stock_items
                out_of_stock = len(out_prods)
                total_units = sum(int(float(it.get("stock", 0))) for it in low_prods)

                lines = [
                    f"📦 *Store Inventory Overview & Low Stock Alert ({date_label})*",
                    f"Store: *{company.company_name}*",
                    f"────────────────────────",
                    f"• Total Catalog SKUs: *{total_prods}*",
                    f"• Active SKUs: *{active_prods}*",
                    f"• Total Stock Volume: *{total_units:,.0f} units*",
                    f"• ⚠️ Low Stock Alert (≤5 units): *{low_stock}*",
                    f"• 🚨 Out of Stock: *{out_of_stock}*",
                ]
                if low_stock > 0:
                    lines.append("\n⚠️ *Low Stock Items:*")
                    for p in low_prods[:8]:
                        lines.append(f"• *{p.get('name')}*: {p.get('stock')} pcs (Min Safety: {p.get('reorder')})")
                if out_of_stock > 0:
                    lines.append("\n🚨 *Out of Stock Items:*")
                    for p_name in out_prods[:5]:
                        lines.append(f"• *{p_name}*: 0 remaining")
                lines.append(sync_footer)

                return {
                    "text": "\n".join(lines),
                    "media_type": "TEXT"
                }
            else:
                products = Product.objects.filter(company=company)
                total_prods = products.count()
                active_prods = products.filter(status=True).count()
                low_prods = products.filter(status=True, current_stock__gt=0, current_stock__lte=5).order_by("current_stock")
                low_stock = low_prods.count()
                out_prods = products.filter(current_stock__lte=0).order_by("name")
                out_of_stock = out_prods.count()
                total_units = products.aggregate(t=Sum("current_stock"))["t"] or Decimal("0")

                lines = [
                    f"📦 *Store Inventory Overview & Low Stock Alert ({date_label})*",
                    f"Store: *{company.company_name}*",
                    f"────────────────────────",
                    f"• Total Catalog SKUs: *{total_prods}*",
                    f"• Active SKUs: *{active_prods}*",
                    f"• Total Stock Volume: *{total_units:,.0f} units*",
                    f"• ⚠️ Low Stock Alert (≤5 units): *{low_stock}*",
                    f"• 🚨 Out of Stock: *{out_of_stock}*",
                ]
                if low_stock > 0:
                    lines.append("\n⚠️ *Low Stock Items:*")
                    for p in low_prods[:8]:
                        lines.append(f"• *{p.name}* (`{p.product_code}`): {p.current_stock} {p.unit or 'pcs'} (Min Safety: {p.minimum_stock})")
                if out_of_stock > 0:
                    lines.append("\n🚨 *Out of Stock Items:*")
                    for p in out_prods[:5]:
                        lines.append(f"• *{p.name}* (`{p.product_code}`): 0 remaining")
                lines.append(sync_footer)

                return {
                    "text": "\n".join(lines),
                    "media_type": "TEXT"
                }

        # ── 11. TOP PRODUCTS REPORT ──
        if intent == "TOP_PRODUCTS":
            if use_snapshot_data and cloud_snap and cloud_snap.top_selling_products:
                top_items = [
                    {
                        "product_name": it.get("name", "Product"),
                        "product_code": "—",
                        "total_qty": it.get("qty", 0),
                        "total_rev": Decimal(str(it.get("revenue", "0.00"))),
                    }
                    for it in cloud_snap.top_selling_products
                ]
            else:
                top_items = SaleItem.objects.filter(
                    sale__company=company,
                    sale__sale_status="COMPLETED",
                    sale__created_at__date__gte=start,
                    sale__created_at__date__lte=end
                ).values("product_name", "product_code").annotate(
                    total_qty=Sum("quantity"),
                    total_rev=Sum("total")
                ).order_by("-total_qty")[:5]

            if not top_items:
                return {
                    "text": f"📊 No completed sales recorded for {date_label}.\n{sync_footer}",
                    "media_type": "TEXT"
                }

            lines = [f"🏆 *Top Best-Selling Products ({date_label})*", f"Store: {company.company_name}", "────────────────────────"]
            for idx, item in enumerate(top_items, 1):
                name = item["product_name"] or "Product"
                sku = item["product_code"] or "—"
                qty = item["total_qty"] or 0
                rev = item["total_rev"] or Decimal("0.00")
                lines.append(f"{idx}. *{name}* (`{sku}`)\n   Sold: {qty} units | Revenue: ₹{rev:,.2f}")
            lines.append(sync_footer)

            return {"text": "\n".join(lines), "media_type": "TEXT"}

        # ── 12. COMPARISONS REPORT ──
        if intent == "COMPARISON":
            today = timezone.now().date()
            d7_curr_start = today - timedelta(days=7)
            d7_prev_start = today - timedelta(days=14)

            curr_sales = Sale.objects.filter(company=company, sale_status="COMPLETED", created_at__date__gte=d7_curr_start)
            prev_sales = Sale.objects.filter(company=company, sale_status="COMPLETED", created_at__date__gte=d7_prev_start, created_at__date__lt=d7_curr_start)

            curr_rev = curr_sales.aggregate(t=Sum("grand_total"))["t"] or Decimal("0.00")
            prev_rev = prev_sales.aggregate(t=Sum("grand_total"))["t"] or Decimal("0.00")
            curr_count = curr_sales.count()
            prev_count = prev_sales.count()

            if prev_rev > 0:
                diff_pct = ((curr_rev - prev_rev) / prev_rev) * 100
                trend_str = f"+{diff_pct:.1f}% 📈" if diff_pct >= 0 else f"{diff_pct:.1f}% 📉"
            else:
                trend_str = "No baseline data for prior week"

            return {
                "text": (
                    f"📈 *Week-over-Week Performance Comparison*\n"
                    f"Store: {company.company_name}\n"
                    f"────────────────────────\n"
                    f"• *This Week (Last 7 Days):*\n"
                    f"  Revenue: ₹{curr_rev:,.2f} across {curr_count} orders\n"
                    f"• *Previous Week (Prior 7 Days):*\n"
                    f"  Revenue: ₹{prev_rev:,.2f} across {prev_count} orders\n"
                    f"• *Revenue Velocity:* {trend_str}\n"
                    f"• *Average Ticket:* ₹{(curr_rev / curr_count if curr_count > 0 else 0):,.2f}\n"
                    f"{sync_footer}"
                ),
                "media_type": "TEXT"
            }

        # ── 13. PAYMENTS & TENDER BREAKDOWN REPORT ──
        if intent == "PAYMENTS":
            if use_snapshot_data and cloud_snap:
                cash = cloud_snap.cash_total
                upi = cloud_snap.upi_total
                card = cloud_snap.card_total
                credit = cloud_snap.total_credit_receivable
                total_tender = cash + upi + card + credit
                sales_count = cloud_snap.invoice_count
            else:
                sales_qs = Sale.objects.filter(company=company, sale_status="COMPLETED", created_at__date__gte=start, created_at__date__lte=end)
                sales_count = sales_qs.count()
                gross_rev = sales_qs.aggregate(t=Sum("grand_total"))["t"] or Decimal("0.00")
                payments_qs = Payment.objects.filter(sale__company=company, sale__in=sales_qs)
                cash = payments_qs.filter(payment_method="CASH").aggregate(t=Sum("amount"))["t"] or Decimal("0.00")
                upi = payments_qs.filter(payment_method="UPI").aggregate(t=Sum("amount"))["t"] or Decimal("0.00")
                card = payments_qs.filter(payment_method="CARD").aggregate(t=Sum("amount"))["t"] or Decimal("0.00")
                credit = payments_qs.filter(payment_method="CREDIT").aggregate(t=Sum("amount"))["t"] or Decimal("0.00")
                total_tender = cash + upi + card + credit

            text = (
                f"💳 *Payment Breakdown ({date_label})*\n"
                f"Store: *{company.company_name}*\n"
                f"Period: `{start.isoformat()}`" + (f" to `{end.isoformat()}`" if parsed_query.get("is_range") else "") + "\n"
                f"────────────────────────\n"
                f"• Total Settled Payments: *₹{total_tender:,.2f}*\n"
                f"• Associated Orders: *{sales_count}*\n\n"
                f"  • Cash:   *₹{cash:,.2f}* ({(cash/total_tender*100 if total_tender>0 else 0):.1f}%)\n"
                f"  • UPI/QR: *₹{upi:,.2f}* ({(upi/total_tender*100 if total_tender>0 else 0):.1f}%)\n"
                f"  • Card:   *₹{card:,.2f}* ({(card/total_tender*100 if total_tender>0 else 0):.1f}%)\n"
                f"  • Credit: *₹{credit:,.2f}* ({(credit/total_tender*100 if total_tender>0 else 0):.1f}%)\n"
                f"{sync_footer}"
            )
            return {
                "text": text,
                "media_type": "TEXT"
            }

        # ── 14. SALES / EXECUTIVE SUMMARY REPORT (Default) ──
        if use_snapshot_data and cloud_snap:
            gross_rev = cloud_snap.total_revenue
            tax_total = cloud_snap.tax_amount
            discount_sum = cloud_snap.discount_amount
            net_rev = cloud_snap.net_revenue
            sales_count = cloud_snap.invoice_count
            subtotal_sum = gross_rev - tax_total + discount_sum
            aov = (gross_rev / Decimal(str(sales_count))) if sales_count > 0 else Decimal("0.00")

            cash = cloud_snap.cash_total
            upi = cloud_snap.upi_total
            card = cloud_snap.card_total
            credit = cloud_snap.total_credit_receivable

            refund_amount = cloud_snap.refund_amount
            refund_count = cloud_snap.returns_count

            low_stock_count = len(cloud_snap.low_stock_items)
            out_of_stock_count = len(cloud_snap.out_of_stock_items)

            top_item = cloud_snap.top_selling_products[0] if cloud_snap.top_selling_products else None
            top_str = f"{top_item['name']} ({top_item['qty']} sold)" if top_item else "None yet"
        else:
            sales_qs = Sale.objects.filter(
                company=company,
                sale_status="COMPLETED",
                created_at__date__gte=start,
                created_at__date__lte=end
            )
            sales_count = sales_qs.count()
            gross_rev = sales_qs.aggregate(t=Sum("grand_total"))["t"] or Decimal("0.00")
            subtotal_sum = sales_qs.aggregate(t=Sum("subtotal"))["t"] or Decimal("0.00")
            discount_sum = sales_qs.aggregate(t=Sum("discount_amount"))["t"] or Decimal("0.00")
            tax_total = sales_qs.aggregate(t=Sum("tax_amount"))["t"] or Decimal("0.00")

            aov = (gross_rev / Decimal(str(sales_count))) if sales_count > 0 else Decimal("0.00")

            payments_qs = Payment.objects.filter(sale__company=company, sale__in=sales_qs)
            cash = payments_qs.filter(payment_method="CASH").aggregate(t=Sum("amount"))["t"] or Decimal("0.00")
            upi = payments_qs.filter(payment_method="UPI").aggregate(t=Sum("amount"))["t"] or Decimal("0.00")
            card = payments_qs.filter(payment_method="CARD").aggregate(t=Sum("amount"))["t"] or Decimal("0.00")
            credit = payments_qs.filter(payment_method="CREDIT").aggregate(t=Sum("amount"))["t"] or Decimal("0.00")

            returns_qs = ReturnRequest.objects.filter(company=company, status__in=["APPROVED", "COMPLETED"], updated_at__date__gte=start, updated_at__date__lte=end)
            refund_amount = returns_qs.aggregate(t=Sum("refund_amount"))["t"] or Decimal("0.00")
            refund_count = returns_qs.count()
            net_rev = gross_rev - refund_amount

            # Low stock count
            low_stock_count = Product.objects.filter(company=company, status=True, current_stock__gt=0, current_stock__lte=5).count()
            out_of_stock_count = Product.objects.filter(company=company, current_stock__lte=0).count()

            top_item = SaleItem.objects.filter(
                sale__company=company,
                sale__in=sales_qs
            ).values("product_name").annotate(qty=Sum("quantity")).order_by("-qty").first()
            top_str = f"{top_item['product_name']} ({top_item['qty']} sold)" if top_item else "None yet"

        today_date = timezone.localdate()
        if parsed_query.get("is_range"):
            sales_title = f"Sales Report ({date_label})"
        elif start == today_date:
            sales_title = f"Today's Sales Report ({date_label})"
        else:
            sales_title = f"Daily Sales Report ({date_label})"

        if not is_store_online:
            sales_title = f"{sales_title} — Last Synced Snapshot"

        future_note = "ℹ️ *Future Date:* No business transactions recorded yet for this upcoming period.\n" if start > today_date else ""

        text_lines = [
            f"📊 *{sales_title}*",
            f"Store: *{company.company_name}*",
            f"Period: `{start.isoformat()}`" + (f" to `{end.isoformat()}`" if parsed_query.get("is_range") else ""),
            f"────────────────────────",
        ]
        if future_note:
            text_lines.append(future_note)

        text_lines.extend([
            f"• *Gross Sales (Invoiced):* ₹{gross_rev:,.2f}",
            f"  (Subtotal: ₹{subtotal_sum:,.2f} | Discount: ₹{discount_sum:,.2f} | GST: ₹{tax_total:,.2f})",
            f"• *Returns & Refunds:* ₹{refund_amount:,.2f} ({refund_count} returns)",
            f"• *Net Revenue:* ₹{net_rev:,.2f}",
            f"• *Completed Orders:* {sales_count}",
            f"• *Average Order Value (AOV):* ₹{aov:,.2f}",
            f"",
            f"💳 *Tender Breakdown:*",
            f"  • Cash:   ₹{cash:,.2f}",
            f"  • UPI/QR: ₹{upi:,.2f}",
            f"  • Card:   ₹{card:,.2f}",
            f"  • Credit: ₹{credit:,.2f}",
            f"",
            f"📦 *Inventory Alerts:*",
            f"  • Low Stock (≤5 units): {low_stock_count} SKUs | Out of Stock: {out_of_stock_count} SKUs",
            f"⭐ *Best Seller:* {top_str}",
            f"",
            f"📐 *Formulas:* Gross Sales = Invoiced Orders | Net Revenue = Gross Sales - Refunds | AOV = Gross Sales / Orders",
            sync_footer
        ])

        result = {
            "text": "\n".join(text_lines),
            "media_type": "TEXT"
        }

        if sales_count > 0:
            chart_path, chart_filename, _ = generate_chart_image(company, start, end)
            if chart_path:
                result["has_chart"] = True
                result["chart_path"] = chart_path
                result["chart_filename"] = chart_filename
                result["chart_caption"] = f"📊 Sales & Payment Chart ({date_label}) — {company.company_name}"

        return result


def handle_telegram_command(chat_id, raw_text):
    """
    Authoritative processing of Telegram bot messages.
    Strictly tenant-isolated to the verified owner's company.
    Guarantees:
    - Real-time /start response confirming bot is reachable and providing pairing instructions.
    - AI interprets natural language, but database remains the source of truth.
    - AI never invents financial figures.
    - AI never directly executes arbitrary SQL.
    - AI never modifies business records through Telegram.
    - Direct, immediate delivery back to the user via send_telegram_direct_message.
    """
    text = (raw_text or "").strip()
    chat_id_str = str(chat_id).strip()
    parts = text.split()
    lower_text = text.lower()

    # Track that update was received
    record_update_received(text)

    # 1. Check if chat is already linked to a verified store owner
    link = TelegramOwnerLink.objects.filter(telegram_chat_id=chat_id_str, is_verified=True).first()
    if not link:
        from dashboard.models import MerchantTelegramBot
        m_bot = MerchantTelegramBot.objects.filter(verified_chat_id=chat_id_str, status="CONNECTED").first()
        if m_bot:
            link, _ = TelegramOwnerLink.objects.get_or_create(
                company=m_bot.company,
                owner=m_bot.owner,
                defaults={"is_verified": True, "telegram_chat_id": chat_id_str}
            )
            link.is_verified = True
            link.telegram_chat_id = chat_id_str
            link.save()

    # 2. Handle /unlink or /disconnect command
    if lower_text in ["/unlink", "unlink", "/disconnect", "disconnect"]:
        if link:
            store_name = link.company.company_name
            company_obj = link.company
            link.is_verified = False
            link.telegram_chat_id = ""
            link.save()

            from dashboard.models import MerchantTelegramBot
            m_bot = MerchantTelegramBot.objects.filter(company=company_obj).first()
            if m_bot:
                m_bot.status = "DISCONNECTED"
                m_bot.is_active = False
                m_bot.verified_chat_id = ""
                m_bot.save()

            unlinked_msg = (
                f"🔌 *Store Disconnected*\n\n"
                f"Your Telegram chat has been successfully disconnected from *{store_name}*.\n"
                f"You will no longer receive alerts or reports for this store.\n\n"
                f"To reconnect, open POS *Settings > Telegram Reporting*, click *Generate Pairing Code*, and send `/link <CODE>`."
            )
            send_telegram_direct_message(chat_id_str, unlinked_msg)
            return unlinked_msg
        else:
            from dashboard.models import MerchantTelegramBot
            m_bot = MerchantTelegramBot.objects.filter(verified_chat_id=chat_id_str, status="CONNECTED").first()
            if m_bot:
                store_name = m_bot.company.company_name
                m_bot.status = "DISCONNECTED"
                m_bot.is_active = False
                m_bot.verified_chat_id = ""
                m_bot.save()
                unlinked_msg = (
                    f"🔌 *Store Disconnected*\n\n"
                    f"Your Telegram chat has been successfully disconnected from *{store_name}*.\n"
                    f"You will no longer receive alerts or reports for this store.\n\n"
                    f"To reconnect, open POS *Settings > Telegram Reporting*, click *Generate Pairing Code*, and send `/link <CODE>`."
                )
                send_telegram_direct_message(chat_id_str, unlinked_msg)
                return unlinked_msg

            not_linked_msg = (
                "ℹ️ *No Connected Store*\n\n"
                "This Telegram chat is not currently connected to any store.\n"
                "Send `/start` to view pairing instructions."
            )
            send_telegram_direct_message(chat_id_str, not_linked_msg)
            return not_linked_msg

    # 3. Handle /pair, /link, /connect, or /start <CODE> (or bare 6-char pairing code)
    pair_commands = ["/pair", "pair", "/link", "link", "/connect", "connect", "/start", "start"]
    is_pair_cmd = len(parts) >= 2 and parts[0].lower() in pair_commands
    is_bare_code = len(parts) == 1 and len(parts[0]) == 6 and not parts[0].startswith("/")

    if is_pair_cmd or is_bare_code:
        possible_code = parts[1] if is_pair_cmd else parts[0]
        success, new_link, msg = verify_pairing_code(possible_code, chat_id_str)
        if success:
            welcome = (
                f"{msg}\n\n"
                f"🏪 *Store:* {new_link.company.company_name}\n"
                f"👤 *Authorized Owner:* `{new_link.owner.username}`\n"
                f"🟢 *Database Connection:* Verified & Online\n\n"
                "🤖 *Retail POS Assistant is Ready!*\n\n"
                "📊 *Report Commands & Examples:*\n"
                "• `/report` or `/report today` — Today's complete financial report\n"
                "• `/report 2026-10-02` or `/report 02-10-2026` — Report for 2 Oct 2026\n"
                "• `/report 02-10` — Report for 2 Oct this year\n"
                "• `/report 2` — Report for 2nd of the current month\n"
                "• `/report 2026-10-01 2026-10-11` — Date range report\n"
                "• `/sales` — Revenue breakdown (Cash, UPI, Card)\n"
                "• `/stock` — Low inventory & out of stock alerts\n"
                "• `/chart` — Visual financial & tender chart\n"
                "• `/pdf` — Download executive summary PDF\n"
                "• `/unlink` — Disconnect this chat from store\n\n"
                "Or ask questions naturally:\n"
                "_\"how much cash did we collect today?\"_, _\"show upi collections\"_, _\"top selling products\"_"
            )
            ok, err = send_telegram_direct_message(chat_id_str, welcome)
            TelegramOutboundMessage.objects.create(
                company=new_link.company,
                chat_id=chat_id_str,
                message_text=welcome,
                media_type="TEXT",
                status="SENT" if ok else "FAILED",
                sent_at=timezone.now() if ok else None,
                error_message="" if ok else (err or "")[:250]
            )
            return welcome
        else:
            err_msg = (
                f"❌ {msg}\n\n"
                "To generate a valid pairing code:\n"
                "1. Sign in to your POS terminal as Store Owner.\n"
                "2. Go to *Settings > Telegram Reporting*.\n"
                "3. Click *Generate Pairing Code* and send `/pair <CODE>` or `/link <CODE>` here."
            )
            send_telegram_direct_message(chat_id_str, err_msg)
            return err_msg

    # 4. Handle /start command (single word)
    if lower_text in ["/start", "start"]:
        if not link:
            # Unlinked /start: Truthful reachable greeting + pairing instructions
            greeting = (
                "👋 *Welcome to Retail POS & Billing Assistant!* (@Brisksmith_bot)\n\n"
                "🟢 *Bot Status:* Online & Reachable\n"
                "🔒 *Store Connection:* Not yet paired\n\n"
                "This bot provides secure, remote financial reporting and inventory summaries for store owners.\n\n"
                "📋 *How to connect your store:*\n"
                "1. Sign in to your POS terminal as Store Owner.\n"
                "2. Navigate to *Settings > Telegram Reporting*.\n"
                "3. Click *Generate Pairing Code* (6-character code).\n"
                "4. Send `/pair <YOUR-CODE>` to this chat.\n\n"
                "💡 *Quick Commands:*\n"
                "• `/start` — Check bot connectivity\n"
                "• `/help` — Setup guide and commands\n"
                "• `/pair <CODE>` or `/link <CODE>` — Link your store account\n\n"
                "🛡️ _Note: We will never ask for your application password._"
            )
            send_telegram_direct_message(chat_id_str, greeting)
            return greeting
        else:
            # Linked /start: Truthful verified store greeting
            company = link.company
            sync_info = SyncService.get_sync_status(company)
            sync_line = f"🔄 *Sync Status:* `{sync_info['last_sync_timestamp_display']}`"
            connected_greeting = (
                f"🏪 *{company.company_name} — Remote Executive Bot*\n"
                f"👤 *Authorized Owner:* `{link.owner.username}`\n"
                f"🟢 *POS Database:* Verified & Connected\n"
                f"{sync_line}\n\n"
                "💡 *Available Commands & Examples:*\n"
                "• `/report` or `/report today` — Today's sales & revenue\n"
                "• `/report 2026-10-02` — Specific date (YYYY-MM-DD)\n"
                "• `/report 02-10-2026` — Specific date (DD-MM-YYYY)\n"
                "• `/report 02-10` — 2 October this year\n"
                "• `/report 2` — 2nd of this month\n"
                "• `/report 2026-10-01 2026-10-11` — Date range\n"
                "• `/sales` — Revenue breakdown (Cash, UPI, Card)\n"
                "• `/stock` — Low inventory & reorder alerts\n"
                "• `/chart` — Visual sales & tender chart\n"
                "• `/pdf` — Export executive PDF report\n"
                "• `/unlink` — Disconnect this chat\n"
                "• `/help` — Full command list\n\n"
                "Or ask in plain English:\n"
                "_\"how much cash today?\"_, _\"show upi collections\"_, _\"top selling products\"_"
            )
            ok, err = send_telegram_direct_message(chat_id_str, connected_greeting)
            TelegramOutboundMessage.objects.create(
                company=company,
                chat_id=chat_id_str,
                message_text=connected_greeting,
                media_type="TEXT",
                status="SENT" if ok else "FAILED",
                sent_at=timezone.now() if ok else None,
                error_message="" if ok else (err or "")[:250]
            )
            return connected_greeting

    # 5. Handle /help command
    if lower_text in ["/help", "help"]:
        if not link:
            help_unlinked = (
                "ℹ️ *Retail POS Bot Help*\n\n"
                "This bot is currently *unlinked* to any store.\n\n"
                "🔐 *How Pairing Works:*\n"
                "1. Log in to POS as Store Owner.\n"
                "2. Go to *Settings > Telegram Reporting*.\n"
                "3. Click *Generate Pairing Code*.\n"
                "4. Send `/pair <CODE>` or `/link <CODE>` here.\n\n"
                "Each code expires in 15 minutes and can only be used once. Tenant isolation ensures your data remains strictly confidential."
            )
            send_telegram_direct_message(chat_id_str, help_unlinked)
            return help_unlinked
        else:
            help_linked = (
                f"📖 *Executive Bot Commands — {link.company.company_name}*\n\n"
                "• `/report` or `/report today` — Today's financial summary\n"
                "• `/report 2026-10-02` or `02-10-2026` — Specific date report\n"
                "• `/report 02-10` — Date in current year (DD-MM)\n"
                "• `/report 2` — Day of current month\n"
                "• `/report 2026-10-01 2026-10-11` — Date range report\n"
                "• `/sales` — Revenue and tender breakdown\n"
                "• `/stock` — Inventory alerts and items below reorder level\n"
                "• `/chart` — Visual graphic chart of sales & tenders\n"
                "• `/pdf` — Download executive summary PDF\n"
                "• `/status` — Gateway and connection health\n"
                "• `/unlink` — Disconnect this chat from store\n\n"
                "💡 *Natural Language Reporting:*\n"
                "• _\"how much cash did we collect today?\"_\n"
                "• _\"show upi payments\"_\n"
                "• _\"customer credit balances\"_\n"
                "• _\"supplier purchases this month\"_\n"
                "• _\"top 5 selling products\"_"
            )
            ok, err = send_telegram_direct_message(chat_id_str, help_linked)
            TelegramOutboundMessage.objects.create(
                company=link.company,
                chat_id=chat_id_str,
                message_text=help_linked,
                media_type="TEXT",
                status="SENT" if ok else "FAILED",
                sent_at=timezone.now() if ok else None,
                error_message="" if ok else (err or "")[:250]
            )
            return help_linked

    # 6. Handle /status command
    if lower_text in ["/status", "status"]:
        if not link:
            status_reply = (
                "📊 *Bot Status Report*\n\n"
                "• Bot Identity: `@Brisksmith_bot`\n"
                "• API Status: Online & Reachable\n"
                "• Chat Pairing: ❌ Unpaired\n"
                "• Database Access: Restricted\n\n"
                "Send `/pair <CODE>` with code from POS Settings to pair."
            )
            send_telegram_direct_message(chat_id_str, status_reply)
            return status_reply
        else:
            company = link.company
            sync_info = SyncService.get_sync_status(company)
            status_reply = (
                f"📊 *Bot Status Report — {company.company_name}*\n\n"
                f"• Bot Identity: `@Brisksmith_bot`\n"
                f"• Authorized Owner: `{link.owner.username}`\n"
                f"• Chat ID: `{chat_id_str}`\n"
                f"• Database Connection: 🟢 Connected\n"
                f"• Last Sync: `{sync_info['last_sync_timestamp_display']}`\n"
                f"• Offline Buffer: {sync_info['pending_count']} pending"
            )
            ok, err = send_telegram_direct_message(chat_id_str, status_reply)
            TelegramOutboundMessage.objects.create(
                company=company,
                chat_id=chat_id_str,
                message_text=status_reply,
                media_type="TEXT",
                status="SENT" if ok else "FAILED",
                sent_at=timezone.now() if ok else None,
                error_message="" if ok else (err or "")[:250]
            )
            return status_reply

    # 7. If NOT linked and sent any other message:
    if not link:
        unauth_msg = (
            "🔒 *Unauthorized Access*\n\n"
            "This chat ID is not paired with any Retail POS store.\n\n"
            "To connect:\n"
            "1. Log in to your POS terminal as Owner.\n"
            "2. Navigate to *Settings > Telegram Reporting*.\n"
            "3. Generate a one-time pairing code.\n"
            "4. Send `/pair <CODE>` or `/link <CODE>` here.\n\n"
            "Send `/start` to view the welcome guide."
        )
        send_telegram_direct_message(chat_id_str, unauth_msg)
        return unauth_msg

    # 8. LINKED: Business Queries and Reporting
    company = link.company
    parsed_query = NLPReportIntentParser.parse(text)
    engine_result = TelegramReportingEngine.execute(company, parsed_query)

    reply_text = engine_result["text"]
    media_type = engine_result.get("media_type", "TEXT")
    media_path = engine_result.get("media_path", "")
    media_filename = engine_result.get("media_filename", "")

    if media_type == "PHOTO" and media_path and os.path.exists(media_path):
        photo_ok, photo_err = send_telegram_photo(
            chat_id_str,
            media_path,
            caption=reply_text,
            filename=media_filename or "chart.png"
        )
        if not photo_ok:
            send_telegram_direct_message(chat_id_str, reply_text)

        TelegramOutboundMessage.objects.create(
            company=company,
            chat_id=chat_id_str,
            message_text=reply_text,
            media_type="PHOTO",
            media_path=media_path,
            media_filename=media_filename or "chart.png",
            status="SENT" if photo_ok else "FAILED",
            sent_at=timezone.now() if photo_ok else None,
            error_message="" if photo_ok else (photo_err or "")[:250]
        )
    elif media_type == "DOCUMENT" and media_path and os.path.exists(media_path):
        doc_ok, doc_err = send_telegram_document(
            chat_id_str,
            media_path,
            caption=reply_text,
            filename=media_filename or "document.pdf"
        )
        if not doc_ok:
            send_telegram_direct_message(chat_id_str, reply_text)

        TelegramOutboundMessage.objects.create(
            company=company,
            chat_id=chat_id_str,
            message_text=reply_text,
            media_type="DOCUMENT",
            media_path=media_path,
            media_filename=media_filename or "document.pdf",
            status="SENT" if doc_ok else "FAILED",
            sent_at=timezone.now() if doc_ok else None,
            error_message="" if doc_ok else (doc_err or "")[:250]
        )
    else:
        text_ok, text_err = send_telegram_direct_message(chat_id_str, reply_text)
        TelegramOutboundMessage.objects.create(
            company=company,
            chat_id=chat_id_str,
            message_text=reply_text,
            media_type="TEXT",
            status="SENT" if text_ok else "FAILED",
            sent_at=timezone.now() if text_ok else None,
            error_message="" if text_ok else (text_err or "")[:250]
        )

    return reply_text
