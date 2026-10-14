import os
import re
import calendar
from decimal import Decimal
from datetime import datetime, date, timedelta
from django.utils import timezone
from django.db.models import Sum, Count, F, Q

from sales.models import Sale, SaleItem, ReturnRequest, Payment, Invoice
from inventory.models import Product


def generate_sales_insights(company):
    """
    Generate data-backed AI sales insights for the store.
    Read-only, tenant-isolated analysis of revenue, velocity, and payment distribution.
    """
    now = timezone.now()
    today = now.date()

    # Current 7-day window vs previous 7-day window
    d7_start = today - timedelta(days=7)
    d14_start = today - timedelta(days=14)

    curr_sales = Sale.objects.filter(
        company=company,
        sale_status="COMPLETED",
        created_at__date__gte=d7_start
    )
    prev_sales = Sale.objects.filter(
        company=company,
        sale_status="COMPLETED",
        created_at__date__gte=d14_start,
        created_at__date__lt=d7_start
    )

    curr_rev = curr_sales.aggregate(total=Sum("grand_total"))["total"] or Decimal("0.00")
    prev_rev = prev_sales.aggregate(total=Sum("grand_total"))["total"] or Decimal("0.00")
    curr_count = curr_sales.count()
    prev_count = prev_sales.count()

    insights = []

    # 1. Revenue trajectory
    if prev_rev > 0:
        pct_change = ((curr_rev - prev_rev) / prev_rev) * 100
        if pct_change >= 5:
            insights.append({
                "type": "positive",
                "icon": "bi-graph-up-arrow",
                "title": f"Revenue Momentum (+{pct_change:.1f}%)",
                "text": f"Last 7-day revenue (₹{curr_rev:,.2f}) is outperforming previous week (₹{prev_rev:,.2f}) by {pct_change:+.1f}% across {curr_count} orders."
            })
        elif pct_change <= -5:
            insights.append({
                "type": "warning",
                "icon": "bi-graph-down-arrow",
                "title": f"Revenue Dip ({pct_change:.1f}%)",
                "text": f"Sales decreased from ₹{prev_rev:,.2f} to ₹{curr_rev:,.2f}. Consider reviewing footfall or stock availability."
            })
        else:
            insights.append({
                "type": "neutral",
                "icon": "bi-dash-circle",
                "title": "Steady Revenue Pace",
                "text": f"7-day sales remain steady at ₹{curr_rev:,.2f} ({pct_change:+.1f}% change WoW)."
            })
    else:
        insights.append({
            "type": "neutral",
            "icon": "bi-bar-chart",
            "title": "Baseline Performance",
            "text": f"7-day revenue stands at ₹{curr_rev:,.2f} across {curr_count} completed orders."
        })

    # 2. Payment Method Insight
    if curr_count > 0:
        payments_qs = Payment.objects.filter(sale__company=company, sale__in=curr_sales)
        upi_rev = payments_qs.filter(payment_method="UPI").aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
        cash_rev = payments_qs.filter(payment_method="CASH").aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
        card_rev = payments_qs.filter(payment_method="CARD").aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
        credit_rev = payments_qs.filter(payment_method="CREDIT").aggregate(total=Sum("amount"))["total"] or Decimal("0.00")

        dominant = "UPI"
        dom_val = upi_rev
        if cash_rev > dom_val:
            dominant = "Cash"
            dom_val = cash_rev
        if card_rev > dom_val:
            dominant = "Card"
            dom_val = card_rev
        if credit_rev > dom_val:
            dominant = "Store Credit"
            dom_val = credit_rev

        dom_pct = (dom_val / curr_rev * 100) if curr_rev > 0 else 0
        insights.append({
            "type": "info",
            "icon": "bi-credit-card-2-front",
            "title": f"Primary Tender: {dominant} ({dom_pct:.0f}%)",
            "text": f"Customers predominantly paid via {dominant} (₹{dom_val:,.2f}). Cash: ₹{cash_rev:,.2f}, UPI: ₹{upi_rev:,.2f}, Card: ₹{card_rev:,.2f}, Credit: ₹{credit_rev:,.2f}."
        })

    # 3. Top item velocity
    top_item = SaleItem.objects.filter(
        sale__company=company,
        sale__sale_status="COMPLETED",
        sale__created_at__date__gte=d7_start
    ).values("product_name").annotate(qty=Sum("quantity")).order_by("-qty").first()

    if top_item:
        insights.append({
            "type": "star",
            "icon": "bi-award",
            "title": "Star Product of the Week",
            "text": f"'{top_item['product_name']}' is leading demand with {top_item['qty']} units sold in the last 7 days."
        })

    return {
        "curr_rev": float(curr_rev),
        "prev_rev": float(prev_rev),
        "curr_count": curr_count,
        "insights": insights
    }


def generate_inventory_intelligence(company):
    """
    Inventory demand estimation and stock depletion projection.
    Estimates daily burn rate using past 30 days of sales history.
    """
    today = timezone.now().date()
    d30_start = today - timedelta(days=30)

    # Calculate 30-day unit sales per product
    recent_item_sales = SaleItem.objects.filter(
        sale__company=company,
        sale__sale_status="COMPLETED",
        sale__created_at__date__gte=d30_start
    ).values("product_id").annotate(units_sold=Sum("quantity"))

    sales_map = {item["product_id"]: float(item["units_sold"] or 0) for item in recent_item_sales}

    # Fetch active products with low or critical stock
    active_prods = list(Product.objects.filter(company=company, status=True).order_by("current_stock")[:25])

    recommendations = []
    for prod in active_prods:
        current_stock = float(prod.current_stock or 0)
        min_stock = float(prod.minimum_stock or 5)
        if current_stock > min_stock + 10:
            continue

        units_30d = sales_map.get(prod.id, 0.0)
        daily_velocity = units_30d / 30.0

        if daily_velocity > 0:
            days_runway = current_stock / daily_velocity
            urgency = "CRITICAL" if days_runway <= 3 else ("HIGH" if days_runway <= 7 else "MEDIUM")
            suggested_order = max(10, int((daily_velocity * 30) - current_stock + min_stock))
            recommendations.append({
                "product_id": prod.id,
                "product_name": prod.name,
                "sku": prod.product_code,
                "current_stock": current_stock,
                "daily_velocity": round(daily_velocity, 2),
                "estimated_runway_days": round(days_runway, 1),
                "suggested_order": suggested_order,
                "urgency": urgency,
                "reason": f"At {daily_velocity:.1f} units/day, current stock will run out in ~{days_runway:.1f} days."
            })
        elif current_stock <= 0:
            recommendations.append({
                "product_id": prod.id,
                "product_name": prod.name,
                "sku": prod.product_code,
                "current_stock": 0,
                "daily_velocity": 0.0,
                "estimated_runway_days": 0.0,
                "suggested_order": int(min_stock or 10),
                "urgency": "CRITICAL",
                "reason": "Completely out of stock. Customers cannot purchase this item."
            })

        if len(recommendations) >= 8:
            break

    return recommendations


MONTH_NAMES = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}


def parse_date_or_range(text):
    """
    Attempts to extract a specific date or date range from user's natural text.
    Returns:
      ('single', date_obj, 'Formatted String') or
      ('range', start_date, end_date, 'Formatted String') or
      None
    """
    t = (text or "").lower().strip()
    today = timezone.now().date()

    # Relative words
    if "yesterday" in t:
        d = today - timedelta(days=1)
        return ("single", d, f"Yesterday ({d.strftime('%d %B %Y')})")
    if "today" in t and not any(w in t for w in ["yesterday", "last week", "this month"]):
        return ("single", today, f"Today ({today.strftime('%d %B %Y')})")

    # Range: last X days
    m_days = re.search(r"last\s+(\d+)\s+days?", t)
    if m_days:
        num = int(m_days.group(1))
        num = min(num, 365)
        st = today - timedelta(days=num)
        return ("range", st, today, f"Last {num} Days ({st.strftime('%d %b')} – {today.strftime('%d %b %Y')})")

    # Range: between DATE and DATE or from DATE to DATE
    # Pattern: 2026-09-01 to 2026-09-10 or 1 Sep to 10 Sep
    # Let's check ISO dates: YYYY-MM-DD
    iso_dates = re.findall(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", t)
    if len(iso_dates) >= 2:
        try:
            d1 = date(int(iso_dates[0][0]), int(iso_dates[0][1]), int(iso_dates[0][2]))
            d2 = date(int(iso_dates[1][0]), int(iso_dates[1][1]), int(iso_dates[1][2]))
            st, en = min(d1, d2), max(d1, d2)
            return ("range", st, en, f"{st.strftime('%d %b %Y')} to {en.strftime('%d %b %Y')}")
        except ValueError:
            pass
    elif len(iso_dates) == 1:
        try:
            d1 = date(int(iso_dates[0][0]), int(iso_dates[0][1]), int(iso_dates[0][2]))
            return ("single", d1, d1.strftime('%d %B %Y'))
        except ValueError:
            pass

    # Pattern: DD-MM-YYYY or DD/MM/YYYY
    dmy_dates = re.findall(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b", t)
    if len(dmy_dates) >= 2:
        try:
            d1 = date(int(dmy_dates[0][2]), int(dmy_dates[0][1]), int(dmy_dates[0][0]))
            d2 = date(int(dmy_dates[1][2]), int(dmy_dates[1][1]), int(dmy_dates[1][0]))
            st, en = min(d1, d2), max(d1, d2)
            return ("range", st, en, f"{st.strftime('%d %b %Y')} to {en.strftime('%d %b %Y')}")
        except ValueError:
            pass
    elif len(dmy_dates) == 1:
        try:
            d1 = date(int(dmy_dates[0][2]), int(dmy_dates[0][1]), int(dmy_dates[0][0]))
            return ("single", d1, d1.strftime('%d %B %Y'))
        except ValueError:
            pass

    # Pattern with month names: "20 September 2026", "20th Sept 2026", "September 20, 2026", "20 Sep"
    # Match: (day)(st|nd|rd|th)? (month_name) (year)?
    m_name_pattern = r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([a-zA-Z]{3,9})(?:\s+(\d{4}))?\b"
    matches = re.findall(m_name_pattern, t)
    valid_dates = []
    for m in matches:
        day_str, month_str, year_str = m
        month_val = MONTH_NAMES.get(month_str.lower())
        if month_val:
            year_val = int(year_str) if year_str else today.year
            try:
                dt = date(year_val, month_val, int(day_str))
                valid_dates.append(dt)
            except ValueError:
                pass

    # Reverse pattern: "September 20 2026"
    if not valid_dates:
        m_rev_pattern = r"\b([a-zA-Z]{3,9})\s+(\d{1,2})(?:st|nd|rd|th)?(?:\s*,?\s*(\d{4}))?\b"
        rev_matches = re.findall(m_rev_pattern, t)
        for m in rev_matches:
            month_str, day_str, year_str = m
            month_val = MONTH_NAMES.get(month_str.lower())
            if month_val:
                year_val = int(year_str) if year_str else today.year
                try:
                    dt = date(year_val, month_val, int(day_str))
                    valid_dates.append(dt)
                except ValueError:
                    pass

    if len(valid_dates) >= 2:
        st, en = min(valid_dates[0], valid_dates[1]), max(valid_dates[0], valid_dates[1])
        return ("range", st, en, f"{st.strftime('%d %b %Y')} to {en.strftime('%d %b %Y')}")
    elif len(valid_dates) == 1:
        return ("single", valid_dates[0], valid_dates[0].strftime('%d %B %Y'))

    return None


def format_payment_breakdown(payments_qs):
    """Calculates breakdown by CASH, UPI, CARD, CREDIT."""
    cash = payments_qs.filter(payment_method="CASH").aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
    upi = payments_qs.filter(payment_method="UPI").aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
    card = payments_qs.filter(payment_method="CARD").aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
    credit = payments_qs.filter(payment_method="CREDIT").aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
    total = cash + upi + card + credit
    return {
        "cash": cash,
        "upi": upi,
        "card": card,
        "credit": credit,
        "total": total
    }


def resolve_staff_recipient(company, target_raw):
    """
    Resolves a recipient user or role within the authenticated store company.
    Handles disambiguation when multiple active staff members match the query.
    """
    from employee.models import user_registration

    target_clean = target_raw.strip()
    target_lower = target_clean.lower()

    # 1. Broadcast roles
    if "all manager" in target_lower or target_lower == "managers":
        return {
            "recipient": None,
            "recipient_role": "Manager",
            "target_display": "All Managers",
            "emp": None
        }
    if "all" in target_lower or "everyone" in target_lower or "all staff" in target_lower:
        return {
            "recipient": None,
            "recipient_role": "ALL",
            "target_display": "All Store Staff",
            "emp": None
        }

    # 2. Owner / Admin
    if target_lower in ["owner", "admin", "store owner"]:
        return {
            "recipient": company.owner,
            "recipient_role": "Admin",
            "target_display": f"Store Owner ({company.owner.username})",
            "emp": None
        }

    # 3. Role-qualified query: e.g. "cashier billo", "manager rahul"
    role_filter = None
    if "manager" in target_lower:
        role_filter = "Manager"
    elif "cashier" in target_lower:
        role_filter = "Cashier"
    elif "worker" in target_lower:
        role_filter = "Worker"

    name_words = [w for w in target_lower.split() if w not in ["the", "cashier", "manager", "worker", "mr", "mrs", "staff", "employee", "to", "for"]]
    name_query = " ".join(name_words).strip()

    if name_query:
        qs = user_registration.objects.filter(company=company, status="active").select_related("user")
        if role_filter:
            qs = qs.filter(Role__iexact=role_filter)

        matches = qs.filter(
            Q(user__first_name__icontains=name_query) |
            Q(user__last_name__icontains=name_query) |
            Q(user__username__icontains=name_query) |
            Q(employee_id__iexact=name_query)
        )
        count = matches.count()
        if count > 1:
            return {"ambiguous": True, "matches": list(matches), "query": name_query}
        elif count == 1:
            emp = matches.first()
            return {
                "recipient": emp.user,
                "recipient_role": emp.Role,
                "target_display": f"{emp.user.get_full_name() or emp.user.username} ({emp.Role})",
                "emp": emp
            }
        else:
            if company.owner and (name_query in company.owner.username.lower() or name_query in (company.owner.first_name or "").lower()):
                return {
                    "recipient": company.owner,
                    "recipient_role": "Admin",
                    "target_display": f"Store Owner ({company.owner.username})",
                    "emp": None
                }
            return {"not_found": True, "query": target_raw}
    else:
        # Generic role specified without a name (e.g. "the cashier")
        if role_filter:
            emps = user_registration.objects.filter(company=company, Role__iexact=role_filter, status="active").select_related("user")
            c = emps.count()
            if c > 1:
                return {"ambiguous": True, "matches": list(emps), "query": role_filter}
            elif c == 1:
                emp = emps.first()
                return {
                    "recipient": emp.user,
                    "recipient_role": emp.Role,
                    "target_display": f"{role_filter} ({emp.user.get_full_name() or emp.user.username})",
                    "emp": emp
                }
            else:
                return {
                    "recipient": None,
                    "recipient_role": role_filter,
                    "target_display": f"{role_filter}",
                    "emp": None
                }
        return {"not_found": True, "query": target_raw}


def handle_staff_communication(company, user, raw_q):
    """
    Handles internal staff messaging commands via conversational assistant.
    Examples:
      - 'Tell cashier to speak to me in the office'
      - 'Ask Manager Rahul to check the pending returns'
      - 'Tell the cashier to verify the stock'
      - 'Notify all managers that the store meeting is at 5 PM'
      - 'Call Billo'
      - 'Tell Billo to meet me in the office'
    """
    from accounts.tenancy import get_user_role
    from .models import InternalNotification

    sender_user = user or getattr(company, "owner", None)
    if not sender_user:
        return None

    sender_role = get_user_role(sender_user) or "Staff"
    q_str = raw_q.strip()

    # 1. Handle "Call <recipient>"
    call_m = re.match(
        r"^(?:please\s+)?call\s+(?:the\s+)?(.+?)(?:\s+(?:to|and|about)\s+(.+))?$",
        q_str,
        re.IGNORECASE
    )
    if call_m:
        target_raw = call_m.group(1).strip()
        call_topic = (call_m.group(2) or "").strip()

        res = resolve_staff_recipient(company, target_raw)
        if res.get("ambiguous"):
            matches = res["matches"]
            return {
                "intent": "staff_communication",
                "answer": (
                    f"Multiple active staff members match '{res['query']}':\n"
                    + "\n".join(f"• **{e.user.get_full_name() or e.user.username}** ({e.Role}, ID: {e.employee_id})" for e in matches)
                    + "\n\nPlease specify which person you want to contact (e.g. by full name or employee ID)."
                ),
                "data": {"ambiguous": True}
            }
        elif res.get("not_found"):
            return {
                "intent": "staff_communication",
                "answer": f"Could not find staff member '{res['query']}' in your store. You can specify 'the cashier', 'the manager', or an employee name.",
                "data": {"not_found": True}
            }

        target_display = res["target_display"]
        emp = res.get("emp")
        phone_str = emp.Phone if (emp and emp.Phone) else ""
        dialer_opt = f"\n2. **Open phone dialer**: [{phone_str}](tel:{phone_str})" if phone_str else ""

        return {
            "intent": "staff_call_inquiry",
            "answer": (
                f"📞 **Phone Call Request for {target_display}**\n\n"
                f"Automatic telephony dialing is not integrated directly into this POS installation.\n\n"
                f"Would you like to:\n"
                f"1. **Send an in-app message** requesting a call? You can say: *\"Tell {target_raw} to call me immediately\"*."
                + dialer_opt
            ),
            "data": {
                "recipient": res["recipient"].username if res["recipient"] else None,
                "phone": phone_str,
                "target_display": target_display
            }
        }

    # 2. Handle "Tell / Ask / Notify / Message / Inform / Alert <recipient> [to/that/about/:] <message>"
    m = re.match(
        r"^(?:please\s+)?(?:tell|ask|notify|message|inform|alert)\s+(?:the\s+)?(.+?)\s*(?:to\s+|that\s+|about\s+|regarding\s+|:\s*|\s*-\s*)(.+)$",
        q_str,
        re.IGNORECASE
    )
    if not m:
        return None

    target_raw = m.group(1).strip()
    msg_text = m.group(2).strip()

    if not msg_text:
        return {
            "intent": "staff_communication",
            "answer": "What message would you like me to deliver to the staff member?",
            "data": {}
        }

    target_lower = target_raw.lower()

    # RBAC check: Cashier cannot broadcast or command management
    if sender_role.lower() == "cashier":
        if any(b in target_lower for b in ["all", "everyone", "staff", "managers"]):
            return {
                "intent": "staff_communication",
                "answer": "⚠️ As a Cashier, you cannot send broadcast announcements. You may message your Manager or Store Owner for assistance.",
                "data": {"permitted": False}
            }
        if "cashier" in target_lower:
            return {
                "intent": "staff_communication",
                "answer": "⚠️ As a Cashier, you cannot assign tasks or issue directives to fellow cashiers.",
                "data": {"permitted": False}
            }

    # Resolve recipient
    res = resolve_staff_recipient(company, target_raw)
    if res.get("ambiguous"):
        matches = res["matches"]
        return {
            "intent": "staff_communication",
            "answer": (
                f"Multiple active staff members match '{res['query']}':\n"
                + "\n".join(f"• **{e.user.get_full_name() or e.user.username}** ({e.Role}, ID: {e.employee_id})" for e in matches)
                + "\n\nPlease specify which person you want to contact (e.g. by full name or employee ID)."
            ),
            "data": {"ambiguous": True}
        }
    elif res.get("not_found"):
        return {
            "intent": "staff_communication",
            "answer": f"Could not find staff member '{res['query']}' in your store. You can specify 'the cashier', 'the manager', or an employee name.",
            "data": {"not_found": True}
        }

    recipient = res["recipient"]
    recipient_role = res["recipient_role"]
    target_display = res["target_display"]

    if recipient_role == "ALL" and sender_role not in ["Admin", "Manager"]:
        return {
            "intent": "staff_communication",
            "answer": "⚠️ Only Store Owners and Managers have permission to broadcast announcements to all staff.",
            "data": {"permitted": False}
        }

    # Save auditable notification in database
    notif = InternalNotification.objects.create(
        company=company,
        sender=sender_user,
        sender_role=sender_role,
        recipient=recipient,
        recipient_role=recipient_role,
        title=f"Message from {sender_user.username} ({sender_role})",
        message=msg_text,
        priority="NORMAL",
        status="DELIVERED"
    )

    return {
        "intent": "staff_communication",
        "answer": f"✅ Delivered notification to {target_display}: \"{msg_text}\". Message saved and notification queued for their inbox.",
        "data": {
            "notification_id": notif.id,
            "recipient": recipient.username if recipient else None,
            "recipient_role": recipient_role,
            "message": msg_text
        }
    }


def answer_business_question(company, question_text, user=None):
    """
    Answer natural-language business questions using authoritative,
    read-only queries against the tenant's data.
    Enforces tenant isolation, RBAC, and safe responses.
    """
    raw_q = (question_text or "").strip()
    if not raw_q:
        return {
            "intent": "empty_query",
            "answer": "Please ask a question regarding your store's sales, revenue, payments, stock levels, or returns.",
            "data": {}
        }

    # Normalize hyphens and multiple spaces
    normalized_q = re.sub(r"[-_]+", " ", raw_q).lower()
    q_clean = re.sub(r"[^\w\s]", "", normalized_q)
    q_clean = re.sub(r"\s+", " ", q_clean).strip()
    today = timezone.now().date()

    # 1. Staff communication workflow
    staff_action = handle_staff_communication(company, user, raw_q)
    if staff_action:
        return staff_action

    # 2. Greetings
    if q_clean in ["hi", "hello", "hey", "namaste", "good morning", "good evening", "good afternoon"] or any(q_clean.startswith(w + " ") for w in ["hi", "hello", "hey"]):
        return {
            "intent": "greeting",
            "answer": f"👋 **Hello! I am your POS Business Intelligence Assistant.**\n\nI provide authoritative analytics for **{company.company_name}**.\n\n**Here are questions you can ask me:**\n• *\"Today's sales\"* — Real-time revenue & orders completed today\n• *\"Show UPI collections\"* — Tender breakdown for UPI, Cash, & Cards\n• *\"Who is our best-selling product?\"* — Star performing products\n• *\"Low stock\"* — Items needing immediate reorder\n• *\"Tell me today's returns\"* — Today's return requests\n• *\"Tell cashier to speak to me in the office\"* — Dispatch in-app staff notification",
            "data": {"store": company.company_name}
        }

    # 3. Identity & Name
    if any(k in q_clean for k in ["your name", "ur name", "who are you", "who r u", "whats your name", "what is your name", "what is ur name"]):
        return {
            "intent": "identity",
            "answer": f"I am your **Retail Business Assistant** for **{company.company_name}**. I track real-time POS sales, monitor stock runways, reconcile UPI/cash tenders, and help dispatch internal staff notifications.",
            "data": {"store": company.company_name}
        }

    # 4. Thanks & Acknowledgements
    if any(k in q_clean for k in ["thank you", "thanks", "thx", "appreciate it", "great job", "thank u"]):
        return {
            "intent": "thanks",
            "answer": "You're very welcome! Let me know if you need any other business data or assistance.",
            "data": {}
        }

    # 5. Capabilities / Help
    if any(k in q_clean for k in ["what can you do", "help", "commands", "what are your capabilities", "how do you work", "how to use"]):
        answer = (
            f"🤖 **Capabilities for {company.company_name}:**\n\n"
            f"• **Sales Performance:** *\"How much did we sell today?\"*, *\"This month's sales\"*, *\"Compare this week with last week\"*\n"
            f"• **Tender Collections:** *\"Show UPI collections\"*, *\"Cash sales\"*, *\"Payment breakdown\"*\n"
            f"• **Products & Stock:** *\"Who is our best-selling product?\"*, *\"Low stock\"*, *\"Reorder suggestions\"*\n"
            f"• **Returns:** *\"Tell me today's returns\"*, *\"Returns summary\"*\n"
            f"• **Telegram Assistant:** *\"Telegram connect nahi ho raha\"*, *\"How to connect Telegram bot\"*\n"
            f"• **Historical Sales:** *\"Sales for 20 September 2026\"*\n"
            f"• **Staff Communication:** *\"Tell cashier to speak to me in the office\"*, *\"Notify all managers that meeting is at 5 PM\"*"
        )
        return {"intent": "capabilities", "answer": answer, "data": {}}

    # ── TELEGRAM BOT SETUP & DIAGNOSTICS WORKFLOW ──────────────────────
    if any(k in q_clean for k in [
        "telegram", "bot", "botfather", "connect telegram", "telegram connect",
        "telegram connect nahi ho raha", "bot connect nahi ho raha", "telegram not working",
        "telegram problem", "telegram issue", "telegram status", "telegram kaise connect kare"
    ]):
        from .models import MerchantTelegramBot

        bot_obj = MerchantTelegramBot.objects.filter(company=company).first()
        status_code = bot_obj.status if bot_obj else "NOT_CONFIGURED"

        # Diagnose safe status without exposing secrets
        if status_code == "CONNECTED" and bot_obj and bot_obj.is_active:
            ans = (
                f"🟢 **Telegram Executive Bot is Connected and Active!**\n\n"
                f"• **Store:** {company.company_name}\n"
                f"• **Bot Username:** @{bot_obj.bot_username}\n"
                f"• **Verified Owner Chat:** @{bot_obj.verified_username or 'Owner'}\n"
                f"• **Status:** Live & Operating Normally\n\n"
                f"You can message @{bot_obj.bot_username} directly on Telegram to receive live reports.\n"
                f"💡 Try sending `/report` or `/sales` in your Telegram chat!"
            )
            return {
                "intent": "telegram_status",
                "answer": ans,
                "data": {"status": "CONNECTED", "bot_username": bot_obj.bot_username}
            }
        elif status_code == "PENDING_VERIFICATION" and bot_obj:
            bot_link = f"https://t.me/{bot_obj.bot_username}?start={bot_obj.verification_challenge}"
            ans = (
                f"🟡 **Telegram Bot Token Verified — Pending One-Time Owner Verification**\n\n"
                f"Aapka bot token valid hai, lekin owner account verify hona baaki hai.\n\n"
                f"**Aage ka Step (How to Complete):**\n"
                f"1. Open your bot: [@{bot_obj.bot_username}]({bot_link})\n"
                f"2. Telegram me **Start** dabayein ya `/verify {bot_obj.verification_challenge}` bhejein.\n"
                f"3. Verification code: `{bot_obj.verification_challenge}` (Valid for 15 minutes).\n\n"
                f"Jaise hi aap start karenge, aapka store chat se bind ho jayega aur live reports aane lagenge!"
            )
            return {
                "intent": "telegram_pending",
                "answer": ans,
                "data": {"status": "PENDING_VERIFICATION", "challenge": bot_obj.verification_challenge}
            }
        elif status_code in ["INVALID_TOKEN", "REAUTH_REQUIRED"]:
            ans = (
                f"🔴 **Telegram Bot Token Error / Revoked**\n\n"
                f"Telegram API ne token reject kar diya hai (Token invalid ya revoke ho gaya hai).\n\n"
                f"**Aise Fix Karein (How to Fix):**\n"
                f"1. Telegram par `@BotFather` open karein.\n"
                f"2. `/mybots` bhejein aur apna bot select karke **API Token** copy karein, ya `/newbot` se naya bot banayein.\n"
                f"3. POS me **Settings > Telegram Reporting** me jaakar **Update Bot Token** me naya token paste karein.\n"
                f"4. Save karne ke baad verification step complete karein.\n\n"
                f"Aapka purana sales aur business data 100% safe rahega."
            )
            return {
                "intent": "telegram_invalid_token",
                "answer": ans,
                "data": {"status": status_code, "action": "REPLACE_TOKEN"}
            }
        elif status_code == "DISCONNECTED":
            ans = (
                f"⚪ **Telegram Bot is Currently Disconnected**\n\n"
                f"Aapka store bot disconnected state me hai.\n\n"
                f"**Reconnect karne ke liye:**\n"
                f"1. POS **Settings > Telegram Reporting** me jayein.\n"
                f"2. Apne bot ka token verify karein aur start code send karein.\n"
                f"3. Agar naya bot banana chahte hain, toh `@BotFather` par `/newbot` karke naya token daalein."
            )
            return {
                "intent": "telegram_disconnected",
                "answer": ans,
                "data": {"status": "DISCONNECTED"}
            }
        else:
            ans = (
                f"ℹ️ **Telegram Business Bot Set Up Nahi Hai**\n\n"
                f"Apne phone par daily sales, payments aur stock alerts paane ke liye apna personal Telegram bot connect karein.\n\n"
                f"**Simple 3 Steps (BotFather Setup Guide):**\n"
                f"1. Telegram app me search karein **@BotFather** aur `/newbot` command bhejein.\n"
                f"2. Bot ka naam aur username choose karein (e.g. `MyStore_sales_bot`).\n"
                f"3. BotFather aapko ek **API Token** dega (e.g. `123456789:ABCdef...`).\n"
                f"4. POS me **Settings > Telegram Reporting** me jaakar token paste karein aur connect karein!\n\n"
                f"Aapko kisi coding ki zaroorat nahi hai, 2 minute me connect ho jata hai."
            )
            return {
                "intent": "telegram_not_configured",
                "answer": ans,
                "data": {"status": "NOT_CONFIGURED"}
            }

    # 6. Returns & Refunds (Specific Today vs Overall)
    if any(k in q_clean for k in ["return", "returns", "refund", "refunds"]):
        is_today_return = "today" in q_clean or "todays" in q_clean
        if is_today_return:
            returns_today = ReturnRequest.objects.filter(company=company, created_at__date=today)
            cnt = returns_today.count()
            pend = returns_today.filter(status="PENDING").count()
            appr = returns_today.filter(status__in=["APPROVED", "COMPLETED"]).count()
            rej = returns_today.filter(status="REJECTED").count()
            ref_amt = returns_today.filter(status__in=["APPROVED", "COMPLETED"]).aggregate(total=Sum("refund_amount"))["total"] or Decimal("0.00")

            if cnt == 0:
                ans = f"🔄 **Today's Returns ({today.strftime('%b %d, %Y')}):**\n\nNo return requests have been logged today for {company.company_name}."
            else:
                ans = (
                    f"🔄 **Today's Returns ({today.strftime('%b %d, %Y')}):**\n\n"
                    f"• **Total Return Requests Today:** **{cnt}**\n"
                    f"• **Pending Manager Approval:** {pend}\n"
                    f"• **Approved & Restocked:** {appr}\n"
                    f"• **Rejected:** {rej}\n"
                    f"• **Total Refunded Today:** **₹{ref_amt:,.2f}**"
                )
            return {
                "intent": "returns_today",
                "answer": ans,
                "data": {"count": cnt, "pending": pend, "approved": appr, "rejected": rej, "refunded": float(ref_amt)}
            }
        else:
            returns_all = ReturnRequest.objects.filter(company=company)
            pending = returns_all.filter(status="PENDING").count()
            approved = returns_all.filter(status__in=["APPROVED", "COMPLETED"]).count()
            rejected = returns_all.filter(status="REJECTED").count()
            refunded = returns_all.filter(status__in=["APPROVED", "COMPLETED"]).aggregate(total=Sum("refund_amount"))["total"] or Decimal("0.00")

            answer = (
                f"🔄 **Returns & Refund Lifecycle Summary:**\n\n"
                f"• **Pending Manager Approval:** **{pending} requests**\n"
                f"• **Approved & Restocked:** **{approved} requests**\n"
                f"• **Rejected Requests:** **{rejected} requests**\n"
                f"• **Total Refunded Amount:** **₹{refunded:,.2f}**"
            )
            return {"intent": "returns_summary", "answer": answer, "data": {"pending": pending, "approved": approved, "rejected": rejected, "refunded": float(refunded)}}

    # 7. Today's Sales
    is_today_query = ("today" in q_clean and not any(w in q_clean for w in ["yesterday", "last week", "this month"])) or any(
        k in q_clean for k in ["sell today", "sales today", "today sales", "revenue today", "today revenue", "sold today", "today collection", "todays sales", "sales for today", "did we sell today", "how much did we sell today"]
    )
    if is_today_query:
        sales_qs = Sale.objects.filter(company=company, sale_status="COMPLETED", created_at__date=today)
        count = sales_qs.count()
        rev = sales_qs.aggregate(total=Sum("grand_total"))["total"] or Decimal("0.00")
        payments_qs = Payment.objects.filter(sale__company=company, sale__in=sales_qs)
        pb = format_payment_breakdown(payments_qs)
        avg_ticket = rev / Decimal(count) if count > 0 else Decimal("0.00")
        items_sold = SaleItem.objects.filter(sale__company=company, sale__in=sales_qs).aggregate(total=Sum("quantity"))["total"] or Decimal("0.00")

        answer = (
            f"🛒 **Today's POS Performance ({today.strftime('%b %d, %Y')}):**\n\n"
            f"• **Completed Invoices:** **{count} sales**\n"
            f"• **Gross Revenue:** **₹{rev:,.2f}**\n"
            f"• **Units Sold:** {items_sold:,.0f} items\n"
            f"• **Average Order Value:** ₹{avg_ticket:,.2f}\n\n"
            f"**Payment Breakdown:**\n"
            f"• UPI: ₹{pb['upi']:,.2f}\n"
            f"• Cash: ₹{pb['cash']:,.2f}\n"
            f"• Card: ₹{pb['card']:,.2f}\n"
            f"• Store Credit: ₹{pb['credit']:,.2f}"
        )
        return {
            "intent": "sales_today",
            "answer": answer,
            "data": {
                "count": count,
                "revenue": float(rev),
                "items_sold": float(items_sold),
                "upi": float(pb["upi"]),
                "cash": float(pb["cash"]),
                "card": float(pb["card"]),
                "credit": float(pb["credit"])
            }
        }

    # 8. Specific Date or Date Range Query
    parsed_date_info = parse_date_or_range(q_clean)
    if parsed_date_info:
        mode = parsed_date_info[0]
        if mode == "single":
            target_date = parsed_date_info[1]
            date_label = parsed_date_info[2]
            sales_qs = Sale.objects.filter(company=company, sale_status="COMPLETED", created_at__date=target_date)
            count = sales_qs.count()
            rev = sales_qs.aggregate(total=Sum("grand_total"))["total"] or Decimal("0.00")
            payments_qs = Payment.objects.filter(sale__company=company, sale__in=sales_qs)
            pb = format_payment_breakdown(payments_qs)

            if count == 0:
                answer = f"📅 **Sales for {date_label}:**\n\nNo completed transactions were recorded on this date."
            else:
                avg_ticket = rev / Decimal(count) if count > 0 else Decimal("0.00")
                answer = (
                    f"📅 **Sales Report for {date_label}:**\n\n"
                    f"• **Completed Invoices:** {count} transactions\n"
                    f"• **Grand Total Revenue:** **₹{rev:,.2f}**\n"
                    f"• **Average Ticket Size:** ₹{avg_ticket:,.2f}\n\n"
                    f"**Tender Breakdown:**\n"
                    f"• UPI: ₹{pb['upi']:,.2f}\n"
                    f"• Cash: ₹{pb['cash']:,.2f}\n"
                    f"• Card: ₹{pb['card']:,.2f}\n"
                    f"• Store Credit: ₹{pb['credit']:,.2f}"
                )

            return {
                "intent": "sales_date",
                "answer": answer,
                "data": {
                    "date": target_date.isoformat(),
                    "count": count,
                    "revenue": float(rev),
                    "upi": float(pb["upi"]),
                    "cash": float(pb["cash"]),
                    "card": float(pb["card"]),
                    "credit": float(pb["credit"])
                }
            }
        elif mode == "range":
            st_date, en_date, range_label = parsed_date_info[1], parsed_date_info[2], parsed_date_info[3]
            sales_qs = Sale.objects.filter(company=company, sale_status="COMPLETED", created_at__date__gte=st_date, created_at__date__lte=en_date)
            count = sales_qs.count()
            rev = sales_qs.aggregate(total=Sum("grand_total"))["total"] or Decimal("0.00")
            payments_qs = Payment.objects.filter(sale__company=company, sale__in=sales_qs)
            pb = format_payment_breakdown(payments_qs)

            avg_ticket = rev / Decimal(count) if count > 0 else Decimal("0.00")
            answer = (
                f"📊 **Sales Summary ({range_label}):**\n\n"
                f"• **Completed Orders:** {count} transactions\n"
                f"• **Total Period Revenue:** **₹{rev:,.2f}**\n"
                f"• **Average Order Value:** ₹{avg_ticket:,.2f}\n\n"
                f"**Collected Tender:**\n"
                f"• UPI: ₹{pb['upi']:,.2f}\n"
                f"• Cash: ₹{pb['cash']:,.2f}\n"
                f"• Card: ₹{pb['card']:,.2f}\n"
                f"• Store Credit: ₹{pb['credit']:,.2f}"
            )
            return {
                "intent": "sales_range",
                "answer": answer,
                "data": {
                    "start_date": st_date.isoformat(),
                    "end_date": en_date.isoformat(),
                    "count": count,
                    "revenue": float(rev),
                    "upi": float(pb["upi"]),
                    "cash": float(pb["cash"]),
                    "card": float(pb["card"]),
                    "credit": float(pb["credit"])
                }
            }

    # 9. Payment Breakdown / Specific Tender
    if any(k in q_clean for k in ["upi collection", "upi collections", "show upi", "upi sales", "cash collection", "cash collections", "show cash", "cash sales", "card collection", "card sales", "store credit collection", "payment breakdown", "payment methods", "tender breakdown", "collections"]):
        is_upi = "upi" in q_clean
        is_cash = "cash" in q_clean
        is_card = "card" in q_clean
        is_credit = "credit" in q_clean

        today_payments = Payment.objects.filter(company=company, payment_date__date=today)
        month_payments = Payment.objects.filter(company=company, payment_date__year=today.year, payment_date__month=today.month)
        all_payments = Payment.objects.filter(company=company)

        if is_upi and not (is_cash or is_card or is_credit):
            upi_today = today_payments.filter(payment_method="UPI").aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
            upi_month = month_payments.filter(payment_method="UPI").aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
            upi_all = all_payments.filter(payment_method="UPI").aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
            answer = (
                f"📱 **UPI / QR Collections Summary:**\n\n"
                f"• **Today's UPI:** **₹{upi_today:,.2f}**\n"
                f"• **This Month's UPI ({today.strftime('%B %Y')}):** **₹{upi_month:,.2f}**\n"
                f"• **All-Time Total UPI:** **₹{upi_all:,.2f}**"
            )
            return {
                "intent": "payment_upi",
                "answer": answer,
                "data": {"upi_today": float(upi_today), "upi_month": float(upi_month), "upi_all": float(upi_all)}
            }
        elif is_cash and not (is_upi or is_card or is_credit):
            cash_today = today_payments.filter(payment_method="CASH").aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
            cash_month = month_payments.filter(payment_method="CASH").aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
            cash_all = all_payments.filter(payment_method="CASH").aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
            answer = (
                f"💵 **Cash Tender Collections Summary:**\n\n"
                f"• **Today's Cash:** **₹{cash_today:,.2f}**\n"
                f"• **This Month's Cash ({today.strftime('%B %Y')}):** **₹{cash_month:,.2f}**\n"
                f"• **All-Time Total Cash:** **₹{cash_all:,.2f}**"
            )
            return {
                "intent": "payment_cash",
                "answer": answer,
                "data": {"cash_today": float(cash_today), "cash_month": float(cash_month), "cash_all": float(cash_all)}
            }
        elif is_card and not (is_upi or is_cash or is_credit):
            card_today = today_payments.filter(payment_method="CARD").aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
            card_month = month_payments.filter(payment_method="CARD").aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
            card_all = all_payments.filter(payment_method="CARD").aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
            answer = (
                f"💳 **Card Collections Summary:**\n\n"
                f"• **Today's Card:** **₹{card_today:,.2f}**\n"
                f"• **This Month's Card ({today.strftime('%B %Y')}):** **₹{card_month:,.2f}**\n"
                f"• **All-Time Total Card:** **₹{card_all:,.2f}**"
            )
            return {
                "intent": "payment_card",
                "answer": answer,
                "data": {"card_today": float(card_today), "card_month": float(card_month), "card_all": float(card_all)}
            }
        else:
            pb_today = format_payment_breakdown(today_payments)
            pb_month = format_payment_breakdown(month_payments)
            answer = (
                f"💳 **Payment Breakdown by Tender Method:**\n\n"
                f"**Today ({today.strftime('%b %d')}):**\n"
                f"• UPI: ₹{pb_today['upi']:,.2f}\n"
                f"• Cash: ₹{pb_today['cash']:,.2f}\n"
                f"• Card: ₹{pb_today['card']:,.2f}\n"
                f"• Store Credit: ₹{pb_today['credit']:,.2f}\n"
                f"• **Today Total:** ₹{pb_today['total']:,.2f}\n\n"
                f"**This Month ({today.strftime('%B %Y')}):**\n"
                f"• UPI: ₹{pb_month['upi']:,.2f}\n"
                f"• Cash: ₹{pb_month['cash']:,.2f}\n"
                f"• Card: ₹{pb_month['card']:,.2f}\n"
                f"• Store Credit: ₹{pb_month['credit']:,.2f}\n"
                f"• **Month Total:** ₹{pb_month['total']:,.2f}"
            )
            return {
                "intent": "payment_breakdown",
                "answer": answer,
                "data": {"today": {k: float(v) for k, v in pb_today.items()}, "month": {k: float(v) for k, v in pb_month.items()}}
            }

    # 10. Month's Sales
    if any(k in q_clean for k in ["this month", "monthly sales", "current month revenue", "month sales", "month revenue", "this months sales", "sales this month"]):
        curr_year = today.year
        curr_month = today.month

        curr_m_sales = Sale.objects.filter(company=company, sale_status="COMPLETED", created_at__year=curr_year, created_at__month=curr_month)
        curr_m_rev = curr_m_sales.aggregate(total=Sum("grand_total"))["total"] or Decimal("0.00")
        curr_m_count = curr_m_sales.count()

        prev_m_date = today.replace(day=1) - timedelta(days=1)
        prev_m_sales = Sale.objects.filter(company=company, sale_status="COMPLETED", created_at__year=prev_m_date.year, created_at__month=prev_m_date.month)
        prev_m_rev = prev_m_sales.aggregate(total=Sum("grand_total"))["total"] or Decimal("0.00")
        prev_m_count = prev_m_sales.count()

        if prev_m_rev > 0:
            diff_pct = ((curr_m_rev - prev_m_rev) / prev_m_rev) * 100
            diff_str = f"+{diff_pct:.1f}% compared to last month" if diff_pct >= 0 else f"{diff_pct:.1f}% compared to last month"
        else:
            diff_str = "no baseline data for previous month"

        month_name = today.strftime("%B %Y")
        prev_month_name = prev_m_date.strftime("%B %Y")

        answer = (
            f"📅 **Monthly Sales Performance ({month_name}):**\n\n"
            f"• **Current Month Revenue:** **₹{curr_m_rev:,.2f}** ({curr_m_count} orders)\n"
            f"• **Previous Month ({prev_month_name}):** ₹{prev_m_rev:,.2f} ({prev_m_count} orders)\n"
            f"• **Growth Pace:** {diff_str}\n\n"
            f"*(Days elapsed: {today.day} of {calendar.monthrange(curr_year, curr_month)[1]} days)*"
        )
        return {
            "intent": "sales_month",
            "answer": answer,
            "data": {
                "curr_month_rev": float(curr_m_rev),
                "curr_month_count": curr_m_count,
                "prev_month_rev": float(prev_m_rev),
                "prev_month_count": prev_m_count
            }
        }

    # 11. Week over Week Comparison
    if any(k in q_clean for k in ["compare this week with last week", "this week with last week", "week comparison", "weekly sales", "week over week", "last week comparison", "weekly comparison"]):
        d7_start = today - timedelta(days=7)
        d14_start = today - timedelta(days=14)

        this_week = Sale.objects.filter(company=company, sale_status="COMPLETED", created_at__date__gte=d7_start)
        last_week = Sale.objects.filter(company=company, sale_status="COMPLETED", created_at__date__gte=d14_start, created_at__date__lt=d7_start)

        rev_this = this_week.aggregate(total=Sum("grand_total"))["total"] or Decimal("0.00")
        rev_last = last_week.aggregate(total=Sum("grand_total"))["total"] or Decimal("0.00")
        count_this = this_week.count()
        count_last = last_week.count()

        if rev_last > 0:
            diff_pct = ((rev_this - rev_last) / rev_last) * 100
            trend_str = f"an increase of +{diff_pct:.1f}%" if diff_pct >= 0 else f"a decline of {diff_pct:.1f}%"
        else:
            trend_str = "no prior baseline for last week"

        answer = (
            f"📊 **Weekly Sales Comparison:**\n\n"
            f"• **This Week (Last 7 Days):** ₹{rev_this:,.2f} across {count_this} orders\n"
            f"• **Last Week (Prior 7 Days):** ₹{rev_last:,.2f} across {count_last} orders\n"
            f"• **Trend:** Revenue shows {trend_str}."
        )
        return {
            "intent": "weekly_comparison",
            "answer": answer,
            "data": {"this_week_rev": float(rev_this), "last_week_rev": float(rev_last), "this_week_count": count_this, "last_week_count": count_last}
        }

    # 12. Top Products / Best-Sellers
    if any(k in q_clean for k in ["top product", "top products", "best seller", "best sellers", "best selling", "bestselling", "most popular", "highest selling", "fast moving", "who is our best", "who is the best"]):
        top_items = SaleItem.objects.filter(
            sale__company=company,
            sale__sale_status="COMPLETED"
        ).values("product_name", "product_code").annotate(
            total_qty=Sum("quantity"),
            total_rev=Sum("total")
        ).order_by("-total_qty")[:5]

        if not top_items:
            return {"intent": "top_products", "answer": "No completed sales found yet to rank top products.", "data": []}

        lines = ["🏆 **Top 5 Best-Selling Products by Volume:**\n"]
        for idx, item in enumerate(top_items, 1):
            name = item["product_name"] or "Unknown"
            sku = item["product_code"] or "—"
            qty = item["total_qty"] or 0
            rev = item["total_rev"] or Decimal("0.00")
            lines.append(f"{idx}. **{name}** (`{sku}`) — **{qty:,.0f} units** sold (₹{rev:,.2f})")

        return {"intent": "top_products", "answer": "\n".join(lines), "data": list(top_items)}

    # 13. Low Stock / Out of Stock
    if any(k in q_clean for k in ["low on stock", "low stock", "running out", "out of stock", "reorder stock", "stock alert", "products low on stock", "depleted stock"]):
        low_prods = Product.objects.filter(
            company=company,
            status=True,
            current_stock__lte=F("minimum_stock")
        ).order_by("current_stock")[:10]

        if not low_prods.exists():
            low_prods = Product.objects.filter(
                company=company,
                status=True,
                current_stock__lte=5
            ).order_by("current_stock")[:10]

        if not low_prods.exists():
            return {
                "intent": "low_stock",
                "answer": "✅ **All Healthy!** There are currently no active products at or below low-stock threshold in your store inventory.",
                "data": []
            }

        lines = [f"⚠️ **Found {len(low_prods)} Products Needing Stock Replenishment:**\n"]
        for item in low_prods:
            status_str = "🚨 **OUT OF STOCK**" if item.current_stock <= 0 else f"⚠️ **{item.current_stock} units left**"
            lines.append(f"• **{item.name}** (`{item.product_code}`): {status_str} (Min threshold: {item.minimum_stock})")
        lines.append("\n*Recommendation: Contact suppliers to replenish these items to prevent POS checkout disruptions.*")

        return {"intent": "low_stock", "answer": "\n".join(lines), "data": [{"sku": p.product_code, "name": p.name, "stock": float(p.current_stock)} for p in low_prods]}

    # 14. Demand Estimation & Reorder Projections
    if any(k in q_clean for k in ["reorder", "reorder suggestions", "inventory suggestions", "estimate demand", "demand", "forecast", "runway", "depletion", "velocity"]):
        recs = generate_inventory_intelligence(company)
        if not recs:
            return {
                "intent": "demand_estimation",
                "answer": "✅ **Inventory Runway:** All active stock levels are in healthy equilibrium; no products are projected to stock out within the next 14 days.",
                "data": []
            }

        lines = ["📈 **AI Inventory Reorder Suggestions & Runway Projections:**\n"]
        for r in recs[:6]:
            lines.append(f"• **{r['product_name']}** (`{r['sku']}`): {r['reason']} **Suggested order: {r['suggested_order']} units** ({r['urgency']} priority).")

        return {"intent": "demand_estimation", "answer": "\n".join(lines), "data": recs}

    # 15. Safe Contextual Fallback for unsupported questions
    answer = (
        f"🤖 **Store Overview for {company.company_name}:**\n\n"
        f"I couldn't match that query directly to an internal store metric or command.\n\n"
        f"Try asking:\n"
        f"• *\"Today's sales\"*\n"
        f"• *\"Show UPI collections\"*\n"
        f"• *\"Who is our best-selling product?\"*\n"
        f"• *\"Tell me today's returns\"*\n"
        f"• *\"Low stock\"*\n"
        f"• *\"Tell cashier to speak to me in the office\"*"
    )
    return {
        "intent": "general_overview",
        "answer": answer,
        "data": {}
    }
