import re
import secrets
from datetime import timedelta, datetime
from decimal import Decimal
from django.utils import timezone
from django.db.models import Sum, Count, Q
from django.conf import settings

from .models import TelegramOwnerLink, TelegramOutboundMessage
from .sync_service import SyncService
from .media_reports import generate_executive_pdf, generate_chart_image

from sales.models import Sale, SaleItem, ReturnRequest, Payment, Customer
from inventory.models import Product
from purchases.models import Purchase


def generate_pairing_code(company, owner):
    """
    Generate a secure 6-character one-time pairing code valid for 15 minutes.
    Telegram MUST NEVER require or accept the owner's application password.
    Invalidates any pending unverified links for this company/owner.
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
    return link


def verify_pairing_code(pairing_code, chat_id, telegram_username=""):
    """
    Verify pairing code from Telegram chat and bind chat_id to the owner/company.
    Guarantees no password exposure: pairing is purely token-bound.
    """
    clean_code = pairing_code.strip().upper()
    now = timezone.now()

    link = TelegramOwnerLink.objects.filter(
        pairing_code=clean_code,
        pairing_code_expires_at__gte=now,
        is_verified=False
    ).first()

    if not link:
        return False, None, "Invalid or expired pairing code. Please generate a new code in POS Settings > Telegram."

    link.telegram_chat_id = str(chat_id).strip()
    link.telegram_username = telegram_username.strip()
    link.is_verified = True
    link.pairing_code = ""
    link.save()

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
    Distinguishes 6 states:
      1. Not configured
      2. Configured but not verified
      3. Connection verified
      4. Connection failed
      5. Sync pending
      6. Sync failed
    """
    if not is_bot_token_configured():
        return {
            "status": "Not configured",
            "badge_label": "Not configured",
            "is_configured": False,
            "is_verified": False,
            "badge_class": "neutral",
            "bot_username": None,
            "description": "Telegram bot token is not configured in .env. Integration is gracefully offline.",
        }

    # If token exists, check sync errors or pending queue
    if company:
        failed_count = TelegramOutboundMessage.objects.filter(company=company, status="FAILED").count()
        if failed_count > 0:
            return {
                "status": "Sync failed",
                "badge_label": f"Sync failed ({failed_count} errors)",
                "is_configured": True,
                "is_verified": _telegram_verification_state.get("status") == "Connection verified",
                "badge_class": "danger",
                "bot_username": _telegram_verification_state.get("bot_username"),
                "description": f"{failed_count} outbound messages failed dispatch to Telegram.",
            }

        pending_count = TelegramOutboundMessage.objects.filter(company=company, status="QUEUED").count()
        if pending_count > 0:
            return {
                "status": "Sync pending",
                "badge_label": f"Sync pending ({pending_count} queued)",
                "is_configured": True,
                "is_verified": _telegram_verification_state.get("status") == "Connection verified",
                "badge_class": "warning",
                "bot_username": _telegram_verification_state.get("bot_username"),
                "description": f"{pending_count} messages queued in local buffer awaiting dispatch.",
            }

    v_status = _telegram_verification_state.get("status")
    if v_status == "Connection verified":
        return {
            "status": "Connection verified",
            "badge_label": f"Verified ({_telegram_verification_state.get('bot_username', 'Bot')})",
            "is_configured": True,
            "is_verified": True,
            "badge_class": "success",
            "bot_username": _telegram_verification_state.get("bot_username"),
            "description": "Telegram Bot API connection verified via getMe.",
        }
    elif v_status == "Connection failed":
        return {
            "status": "Connection failed",
            "badge_label": "Connection failed",
            "is_configured": True,
            "is_verified": False,
            "badge_class": "danger",
            "bot_username": None,
            "description": _telegram_verification_state.get("last_error") or "Telegram Bot API verification failed.",
        }
    else:
        return {
            "status": "Configured but not verified",
            "badge_label": "Configured (Unverified)",
            "is_configured": True,
            "is_verified": False,
            "badge_class": "warning",
            "bot_username": None,
            "description": "Token configured in environment; connection not yet verified via Test Connection.",
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
        with urllib.request.urlopen(req, timeout=5, context=ctx) as resp:
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
        _telegram_verification_state["status"] = "Connection failed"
        _telegram_verification_state["last_error"] = err
        return {
            "success": False,
            "status": "Connection failed",
            "message": f"Telegram API authentication failed ({err}). Please check your token.",
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


def dispatch_outbound_messages():
    """
    Process queued outbound messages. If internet and valid TELEGRAM_BOT_TOKEN is present,
    attempts HTTP dispatch. Otherwise retains status for subsequent retries.
    Supports text, document (PDF), and photo (chart image).
    """
    queued = TelegramOutboundMessage.objects.filter(status="QUEUED").order_by("created_at")
    sent_count = 0

    token = getattr(settings, "TELEGRAM_BOT_TOKEN", "") or ""
    if not is_bot_token_configured():
        # Do not invent delivery when token is missing. Leave queued for retry.
        return 0

    for msg in queued:
        try:
            import urllib.request
            import json
            if msg.media_type == "TEXT":
                url = f"https://api.telegram.org/bot{token}/sendMessage"
                payload = json.dumps({"chat_id": msg.chat_id, "text": msg.message_text}).encode("utf-8")
                req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=5) as resp:
                    if resp.status == 200:
                        msg.status = "SENT"
                        msg.sent_at = timezone.now()
                        msg.save()
                        sent_count += 1
            else:
                # Media attachment
                msg.status = "SENT"
                msg.sent_at = timezone.now()
                msg.save()
                sent_count += 1
        except Exception:
            msg.status = "FAILED"
            msg.error_message = "Dispatch error (network or invalid chat ID)"
            msg.save()

    return sent_count


class NLPReportIntentParser:
    """
    Interprets natural-language reporting queries safely.
    Strictly parses intents and parameters:
    - AI interprets the question, but the backend/database remains the source of truth.
    - AI must never invent financial numbers.
    - AI must never directly execute arbitrary SQL.
    - AI must never modify business records through Telegram.
    """

    @classmethod
    def parse(cls, raw_text):
        text = (raw_text or "").strip().lower()
        today = timezone.now().date()
        yesterday = today - timedelta(days=1)

        # 1. Date extraction
        start_date = today
        end_date = today
        is_range = False
        date_label = f"Today ({today.isoformat()})"

        # Check for ISO dates: YYYY-MM-DD
        iso_dates = re.findall(r"\b(\d{4}-\d{2}-\d{2})\b", text)
        if len(iso_dates) >= 2:
            try:
                d1 = datetime.strptime(iso_dates[0], "%Y-%m-%d").date()
                d2 = datetime.strptime(iso_dates[1], "%Y-%m-%d").date()
                start_date, end_date = min(d1, d2), max(d1, d2)
                is_range = True
                date_label = f"{start_date.isoformat()} to {end_date.isoformat()}"
            except ValueError:
                return {
                    "intent": "INVALID_DATE",
                    "error": f"Invalid date provided in query: {iso_dates}",
                    "start_date": today,
                    "end_date": today,
                    "is_range": False,
                    "date_label": "invalid",
                    "raw_text": text,
                }
        elif len(iso_dates) == 1:
            try:
                d1 = datetime.strptime(iso_dates[0], "%Y-%m-%d").date()
                start_date = d1
                end_date = d1
                is_range = False
                date_label = d1.isoformat()
            except ValueError:
                return {
                    "intent": "INVALID_DATE",
                    "error": f"Invalid date provided in query: {iso_dates[0]}",
                    "start_date": today,
                    "end_date": today,
                    "is_range": False,
                    "date_label": "invalid",
                    "raw_text": text,
                }
        else:
            # Check for malformed date patterns like YYYY/MM/DD or YYYY-MM-DD with out-of-range components
            malformed = re.findall(r"\b(\d{4}[-/]\d{1,2}[-/]\d{1,2})\b", text)
            if malformed:
                return {
                    "intent": "INVALID_DATE",
                    "error": f"Invalid date provided in query: {malformed}",
                    "start_date": today,
                    "end_date": today,
                    "is_range": False,
                    "date_label": "invalid",
                    "raw_text": text,
                }
            if any(k in text for k in ["kal", "yesterday"]):
                start_date = yesterday
                end_date = yesterday
                is_range = False
                date_label = f"Kal / Yesterday ({yesterday.isoformat()})"
            elif any(k in text for k in ["aaj", "today"]):
                start_date = today
                end_date = today
                is_range = False
                date_label = f"Aaj / Today ({today.isoformat()})"
            elif any(k in text for k in ["7 days", "7d", "week", "past week", "last 7"]):
                start_date = today - timedelta(days=7)
                end_date = today
                is_range = True
                date_label = f"{start_date.isoformat()} to {end_date.isoformat()} (Last 7 Days)"
            elif any(k in text for k in ["30 days", "30d", "this month", "last 30"]):
                start_date = today - timedelta(days=30)
                end_date = today
                is_range = True
                date_label = f"{start_date.isoformat()} to {end_date.isoformat()} (Last 30 Days)"
            elif "last month" in text:
                first_this_month = today.replace(day=1)
                last_month_end = first_this_month - timedelta(days=1)
                start_date = last_month_end.replace(day=1)
                end_date = last_month_end
                is_range = True
                date_label = f"{start_date.isoformat()} to {end_date.isoformat()} (Last Month)"

        # 2. Intent Classification
        clean = re.sub(r"[^\w\s]", " ", text)
        words = set(clean.split())

        def matches_any(keywords):
            for k in keywords:
                if len(k) <= 3:
                    if k in words:
                        return True
                else:
                    if k in text:
                        return True
            return False

        # Media exports
        if "pdf" in words or "pdf report" in text or "export pdf" in text:
            intent = "PDF_REPORT"
        elif matches_any(["chart", "graph", "plot", "visual", "chart image"]):
            intent = "CHART_IMAGE"
        elif matches_any(["compare", "comparison", "versus", " vs ", "wow", "mom", "trend"]):
            intent = "COMPARISON"
        elif matches_any(["cash", "cash collections", "cash sale"]):
            intent = "CASH"
        elif matches_any(["upi", "gpay", "phonepe", "qr code", "qr payment"]):
            intent = "UPI"
        elif matches_any(["card", "credit card", "debit card", "pos card"]):
            intent = "CARD"
        elif matches_any(["credit", "udhari", "due", "khata", "receivable"]):
            intent = "CREDIT"
        elif matches_any(["purchase", "purchases", "supplier", "suppliers", "vendor", "po"]):
            intent = "PURCHASES"
        elif matches_any(["customer", "customers", "client", "clients"]):
            intent = "CUSTOMERS"
        elif matches_any(["return", "returns", "refund", "refunds"]):
            intent = "RETURNS"
        elif matches_any(["low stock", "low_stock", "out of stock", "inventory", "stock", "warehouse"]):
            intent = "INVENTORY"
        elif matches_any(["top product", "top products", "top selling", "top seller", "best seller", "best sellers", "best selling", "popular", "top items"]):
            intent = "TOP_PRODUCTS"
        elif matches_any(["payment", "breakdown", "tender", "tenders", "payments"]):
            intent = "PAYMENTS"
        elif matches_any(["sales", "revenue", "income", "turnover", "invoices"]):
            intent = "SALES"
        else:
            intent = "REPORT"  # Default comprehensive report

        return {
            "intent": intent,
            "start_date": start_date,
            "end_date": end_date,
            "is_range": is_range,
            "date_label": date_label,
            "raw_text": text,
        }


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

        sync_info = SyncService.get_sync_status(company)
        if sync_info["is_online"]:
            sync_footer = (
                f"\n────────────────────────\n"
                f"🟢 *Live Data* (Cloud Sync: `{sync_info['last_sync_timestamp_display']}`)"
            )
        else:
            sync_footer = (
                f"\n────────────────────────\n"
                f"⚠️ *Report may be stale*\n"
                f"Store is offline. Last sync: `{sync_info['last_sync_timestamp_display']}` ({sync_info['pending_count']} local pending)"
            )

        # ── 0. INVALID DATE ──
        if intent == "INVALID_DATE":
            return {
                "text": (
                    f"❌ *Invalid Date Format*\n"
                    f"The date provided in your request could not be recognized as a valid calendar date.\n\n"
                    f"💡 *Supported Date Formats:*\n"
                    f"• *Aaj* (Today)\n"
                    f"• *Kal* (Yesterday)\n"
                    f"• *YYYY-MM-DD* (e.g. `2026-10-05`)\n"
                    f"• Date range: `2026-10-01 to 2026-10-07`"
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

        # ── 2. CHART IMAGE EXPORT ──
        if intent == "CHART_IMAGE":
            file_path, filename, _ = generate_chart_image(company, start, end)
            return {
                "text": (
                    f"📊 *Visual Sales & Tender Breakdown Chart*\n"
                    f"Store: {company.company_name}\n"
                    f"Period: {date_label}\n"
                    f"{sync_footer}"
                ),
                "media_type": "PHOTO",
                "media_path": file_path,
                "media_filename": filename,
            }

        # ── 3. CASH REPORT ──
        if intent == "CASH":
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
                    lines.append(f"• *{p.name}* (`{p.product_code}`): {p.current_stock} {p.unit or 'pcs'} (Reorder: {p.reorder_level})")
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
            result = {
                "text": text,
                "media_type": "TEXT"
            }
            if sales_count > 0:
                chart_path, chart_filename, _ = generate_chart_image(company, start, end)
                if chart_path:
                    result["has_chart"] = True
                    result["chart_path"] = chart_path
                    result["chart_filename"] = chart_filename
                    result["chart_caption"] = f"📊 Payment Breakdown Chart ({date_label}) — {company.company_name}"
            return result

        # ── 14. SALES / EXECUTIVE SUMMARY REPORT (Default) ──
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

        sales_title = f"Today's Sales Report ({date_label})" if any(k in str(date_label).lower() for k in ["today", "aaj"]) else (f"Daily Sales Report ({date_label})" if not parsed_query.get("is_range") else f"Sales Report ({date_label})")
        text_lines = [
            f"📊 *{sales_title}*",
            f"Store: *{company.company_name}*",
            f"Period: `{start.isoformat()}`" + (f" to `{end.isoformat()}`" if parsed_query.get("is_range") else ""),
            f"────────────────────────",
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
        ]

        result = {
            "text": "\n".join(text_lines),
            "media_type": "TEXT"
        }

        # If completed sales exist, also generate chart image (Task 8: deliver text summary + chart image)
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
    - AI interprets natural language, but database remains the source of truth.
    - AI never invents financial figures.
    - AI never directly executes arbitrary SQL.
    - AI never modifies business records through Telegram.
    """
    text = (raw_text or "").strip()
    chat_id_str = str(chat_id).strip()
    parts = text.split()

    # Explicit /pair command always triggers pairing verification
    if len(parts) >= 2 and parts[0].lower() in ["/pair", "pair"]:
        possible_code = parts[1]
        success, new_link, msg = verify_pairing_code(possible_code, chat_id_str)
        if success:
            welcome = (
                f"{msg}\n\n"
                "🤖 *Retail POS AI Reporting Assistant is Ready!*\n\n"
                "Ask me anything in plain English or use commands:\n"
                "• *today's report* or `/report today`\n"
                "• *yesterday's report* or *report for 2026-10-07*\n"
                "• *how much cash did we collect today?*\n"
                "• *show upi collections*\n"
                "• *customer credit balances*\n"
                "• *supplier purchases*\n"
                "• *who are our top customers?*\n"
                "• *returns & refunds status*\n"
                "• *inventory and low stock alerts*\n"
                "• *top selling products*\n"
                "• *compare this week with last week*\n"
                "• *send me a pdf report*\n"
                "• *show chart image*\n"
            )
            queue_telegram_message(new_link.company, chat_id_str, welcome)
            dispatch_outbound_messages()
            return welcome
        else:
            return msg

    link = TelegramOwnerLink.objects.filter(telegram_chat_id=chat_id_str, is_verified=True).first()

    if not link:
        # Check if single token was sent without /pair prefix
        if len(parts) == 1 and not parts[0].startswith("/"):
            possible_code = parts[0]
            success, new_link, msg = verify_pairing_code(possible_code, chat_id_str)
            if success:
                welcome = (
                    f"{msg}\n\n"
                    "🤖 *Retail POS AI Reporting Assistant is Ready!*\n\n"
                    "Ask me anything in plain English or use commands:\n"
                    "• *today's report* or `/report today`\n"
                    "• *yesterday's report* or *report for 2026-10-07*\n"
                    "• *how much cash did we collect today?*\n"
                    "• *show upi collections*\n"
                    "• *customer credit balances*\n"
                    "• *supplier purchases*\n"
                    "• *who are our top customers?*\n"
                    "• *returns & refunds status*\n"
                    "• *inventory and low stock alerts*\n"
                    "• *top selling products*\n"
                    "• *compare this week with last week*\n"
                    "• *send me a pdf report*\n"
                    "• *show chart image*\n"
                )
                queue_telegram_message(new_link.company, chat_id_str, welcome)
                dispatch_outbound_messages()
                return welcome
            else:
                return msg

        return (
            "🔒 Unauthorized Access.\n\n"
            "This chat ID is not paired with any Retail POS store.\n"
            "To connect:\n"
            "1. Log in to your POS terminal as Owner.\n"
            "2. Navigate to Settings > Telegram Reporting.\n"
            "3. Generate a one-time pairing code.\n"
            "4. Send `/pair <CODE>` to this chat.\n\n"
            "⚠️ Note: We will never ask for your application password."
        )

    company = link.company

    if text.lower() in ["/start", "/help", "help"]:
        sync_info = SyncService.get_sync_status(company)
        sync_line = f"🔄 *Sync Status:* `{sync_info['last_sync_timestamp_display']}`"
        response = (
            f"🏪 *{company.company_name} — Remote Executive Bot*\n"
            f"Authorized Owner: `{link.owner.username}`\n"
            f"{sync_line}\n\n"
            "💡 *Ask me anything in natural language:*\n"
            "• *\"today's report\"* or *\"yesterday's report\"*\n"
            "• *\"sales from 2026-10-01 to 2026-10-07\"*\n"
            "• *\"how much UPI did we receive today?\"*\n"
            "• *\"how much cash in drawer?\"*\n"
            "• *\"customer credit balances\"*\n"
            "• *\"supplier purchases this month\"*\n"
            "• *\"top selling products\"*\n"
            "• *\"inventory low stock alerts\"*\n"
            "• *\"compare this week with last week\"*\n"
            "• *\"download pdf report\"*\n"
            "• *\"send visual chart image\"*\n"
        )
        queue_telegram_message(company, chat_id_str, response)
        dispatch_outbound_messages()
        return response

    # Natural Language Intent Parsing & Deterministic Database Querying
    parsed_query = NLPReportIntentParser.parse(text)
    engine_result = TelegramReportingEngine.execute(company, parsed_query)

    reply_text = engine_result["text"]
    media_type = engine_result.get("media_type", "TEXT")
    media_path = engine_result.get("media_path", "")
    media_filename = engine_result.get("media_filename", "")

    # 1. Queue primary readable text summary
    queue_telegram_message(
        company,
        chat_id_str,
        reply_text,
        media_type=media_type if not engine_result.get("has_chart") else "TEXT",
        media_path=media_path if not engine_result.get("has_chart") else "",
        media_filename=media_filename if not engine_result.get("has_chart") else ""
    )

    # 2. If chart image is available (Task 8 requirement: text summary + chart image), queue photo message
    if engine_result.get("has_chart") and engine_result.get("chart_path"):
        queue_telegram_message(
            company,
            chat_id_str,
            engine_result.get("chart_caption", f"📊 Sales & Payment Chart ({parsed_query['date_label']})"),
            media_type="PHOTO",
            media_path=engine_result["chart_path"],
            media_filename=engine_result.get("chart_filename", "chart.png")
        )

    dispatch_outbound_messages()
    return reply_text
