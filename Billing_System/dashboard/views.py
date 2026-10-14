from decimal import Decimal
from datetime import timedelta, datetime, date
from django.shortcuts import render, redirect, get_object_or_404
from django.db.models import F, Sum, Count, Q
from django.utils import timezone
from accounts.tenancy import company_required, role_required
from inventory.models import Product, Supplier
from sales.models import Sale, Customer, SaleItem
from purchases.models import Purchase
from employee.models import user_registration
from .ai_assistant import generate_sales_insights, generate_inventory_intelligence
from sales.network_utils import get_pos_scanner_base_url


@company_required
@role_required(["Admin", "Owner", "Manager"])
def dashboard(request):
    """
    Main Management Dashboard for Store Owner / Admin and Manager.
    Displays compact retail metrics, operational alerts, recent ledger,
    and a dedicated professional TEAM bar.
    Owner additionally receives PAYROLL THIS MONTH breakdown.
    Manager never sees salary/payroll amounts.
    """
    company = request.company
    is_owner = request.user_role in ["Admin", "Owner"]
    today = timezone.now().date()
    current_year = today.year
    current_month = today.month

    # Inventory metrics
    total_products = Product.objects.filter(company=company).count()
    low_stock_products = Product.objects.filter(
        company=company,
        current_stock__gt=0,
        current_stock__lte=F("minimum_stock"),
    ).count()
    out_of_stock_products = Product.objects.filter(
        company=company,
        current_stock__lte=0,
    ).count()
    total_suppliers = Supplier.objects.filter(company=company).count()

    # Sales metrics
    completed_sales = Sale.objects.filter(company=company, sale_status="COMPLETED")

    today_sales_qs = completed_sales.filter(created_at__date=today)
    today_sales_amount = today_sales_qs.aggregate(total=Sum("grand_total"))["total"] or Decimal("0.00")
    today_sales_count = today_sales_qs.count()

    month_sales_qs = completed_sales.filter(
        created_at__year=current_year,
        created_at__month=current_month
    )
    this_month_sales_amount = month_sales_qs.aggregate(total=Sum("grand_total"))["total"] or Decimal("0.00")

    total_sales_amount = completed_sales.aggregate(total=Sum("grand_total"))["total"] or Decimal("0.00")
    total_sales_count = completed_sales.count()

    total_customers = Customer.objects.filter(company=company).count()

    # Purchase metrics
    purchases_qs = Purchase.objects.filter(company=company, status="RECEIVED")
    today_purchases_qs = purchases_qs.filter(purchase_date=today)

    from django.db.models import DecimalField, ExpressionWrapper
    purchase_item_total = ExpressionWrapper(
        F("items__quantity") * F("items__purchase_price") * (1 + F("items__gst_percentage") / 100),
        output_field=DecimalField(max_digits=20, decimal_places=2)
    )

    today_purchases_amount = today_purchases_qs.aggregate(total=Sum(purchase_item_total))["total"] or Decimal("0.00")

    # Financial / Profit calculation: Revenue (Total Sales) - Cost of Goods Sold
    total_revenue = total_sales_amount
    cost_of_goods_sold = (
        SaleItem.objects.filter(sale__company=company, sale__sale_status="COMPLETED")
        .aggregate(
            total_cogs=Sum(
                ExpressionWrapper(
                    F("cost_price") * F("quantity"),
                    output_field=DecimalField(max_digits=20, decimal_places=2)
                )
            )
        )["total_cogs"]
        or Decimal("0.00")
    )
    total_profit = total_revenue - cost_of_goods_sold

    # Recent sales ledger
    recent_sales = completed_sales.select_related("customer", "cashier", "invoice").prefetch_related("payments").order_by("-created_at")[:6]

    # ── TEAM BAR METRICS ───────────────────────────────────────────────
    team_qs = user_registration.objects.filter(company=company)
    total_team = team_qs.count()
    cashiers_count = team_qs.filter(Role__iexact="Cashier").count()
    stock_team_count = team_qs.filter(Q(Role__icontains="Stock") | Q(Role__icontains="Inventory")).count()
    managers_count = team_qs.filter(Role__iexact="Manager").count()
    other_staff_count = total_team - (cashiers_count + stock_team_count + managers_count)
    if other_staff_count < 0:
        other_staff_count = 0

    active_team_count = team_qs.filter(status__iexact="active").count()
    inactive_team_count = total_team - active_team_count

    # ── PAYROLL THIS MONTH (OWNER ONLY) ────────────────────────────────
    payroll_data = None
    if is_owner:
        active_staff = team_qs.filter(status__iexact="active")
        total_payroll = sum((emp.monthaly_salary or Decimal("0.00")) for emp in active_staff)
        
        # Reconcile from actual persisted SalaryPayment records for current period
        from employee.models import SalaryPayment
        current_payments = SalaryPayment.objects.filter(
            company=company,
            year=current_year,
            month=current_month
        )
        if current_payments.exists():
            paid_payroll = current_payments.aggregate(total=Sum("paid_amount"))["total"] or Decimal("0.00")
        else:
            paid_payroll = sum((emp.monthaly_salary or Decimal("0.00")) for emp in active_staff.filter(payment__iexact="Paid"))
            
        pending_payroll = max(Decimal("0.00"), total_payroll - paid_payroll)
        payroll_data = {
            "total": total_payroll,
            "paid": paid_payroll,
            "pending": pending_payroll,
        }

    # ── TELEGRAM ONBOARDING STATE (OWNER ONLY) ────────────────────────
    telegram_onboarding = None
    if is_owner:
        from dashboard.models import TelegramOwnerLink, MerchantTelegramBot
        from dashboard.telegram_service import is_bot_token_configured, generate_pairing_code

        merchant_bot = MerchantTelegramBot.objects.filter(company=company).first()
        if merchant_bot and merchant_bot.encrypted_token:
            is_connected = merchant_bot.status == "CONNECTED"
            is_pending = merchant_bot.status == "PENDING_VERIFICATION"
            bot_username = merchant_bot.bot_username
            bot_link = f"https://t.me/{bot_username}" + (f"?start={merchant_bot.verification_challenge}" if (is_pending and merchant_bot.verification_challenge) else "")
            telegram_onboarding = {
                "is_custom_bot": True,
                "bot_configured": True,
                "is_connected": is_connected,
                "status": merchant_bot.status,
                "bot_username": bot_username,
                "bot_first_name": merchant_bot.bot_first_name,
                "bot_link": bot_link,
                "pairing_code": merchant_bot.verification_challenge,
                "linked_chat_id": merchant_bot.verified_chat_id,
                "verified_username": merchant_bot.verified_username,
                "linked_at": merchant_bot.verified_at,
                "last_error": merchant_bot.last_error,
            }
        else:
            bot_configured = is_bot_token_configured()
            link = TelegramOwnerLink.objects.filter(company=company, is_verified=True).first()
            pending_link = TelegramOwnerLink.objects.filter(
                company=company,
                owner=request.user,
                is_verified=False,
                pairing_code_expires_at__gte=timezone.now()
            ).first()

            if not link and not pending_link and bot_configured:
                pending_link = generate_pairing_code(company, request.user)

            telegram_onboarding = {
                "is_custom_bot": False,
                "bot_configured": bot_configured,
                "is_connected": bool(link),
                "status": "CONNECTED" if link else ("PENDING_VERIFICATION" if pending_link else "NOT_CONFIGURED"),
                "linked_chat_id": link.telegram_chat_id if link else "",
                "linked_at": link.updated_at if link else None,
                "pairing_code": pending_link.pairing_code if pending_link else "",
                "code_expires_at": pending_link.pairing_code_expires_at if pending_link else None,
                "bot_username": "Brisksmith_bot",
                "bot_link": f"https://t.me/Brisksmith_bot?start={pending_link.pairing_code}" if (pending_link and pending_link.pairing_code) else "https://t.me/Brisksmith_bot",
            }

    context = {
        "company": company,
        "user_role": request.user_role,
        "is_owner": is_owner,
        "total_products": total_products,
        "low_stock_products": low_stock_products,
        "out_of_stock_products": out_of_stock_products,
        "total_suppliers": total_suppliers,
        "today_sales_amount": today_sales_amount,
        "today_sales_count": today_sales_count,
        "this_month_sales_amount": this_month_sales_amount,
        "total_sales_amount": total_sales_amount,
        "total_sales_count": total_sales_count,
        "total_customers": total_customers,
        "today_purchases_amount": today_purchases_amount,
        "total_revenue": total_revenue,
        "total_profit": total_profit,
        "recent_sales": recent_sales,
        # Team Metrics
        "total_team": total_team,
        "cashiers_count": cashiers_count,
        "stock_team_count": stock_team_count,
        "managers_count": managers_count,
        "other_staff_count": other_staff_count,
        "active_team_count": active_team_count,
        "inactive_team_count": inactive_team_count,
        # Payroll Metrics (Owner only)
        "payroll_data": payroll_data,
        # Telegram Onboarding (Owner only)
        "telegram_onboarding": telegram_onboarding,
        # AI Business Intelligence
        "ai_sales_insights": generate_sales_insights(company),
        "ai_inventory_intelligence": generate_inventory_intelligence(company),
    }

    return render(request, "dashboard/dashboard.html", context)


@company_required
@role_required(["Admin", "Owner", "Manager"])
def manager_dashboard(request):
    """
    Manager Dashboard route.
    Delegates to main dashboard view where role-based security
    automatically suppresses payroll salary amounts.
    """
    return dashboard(request)


@company_required
@role_required(["Admin", "Owner", "Manager", "Cashier"])
def cashier_dashboard(request):
    """
    Cashier Portal / Terminal Hub.
    Billing-focused only. Shows today's terminal stats and quick launch buttons.
    Cashier cannot view products catalog, categories, inventory, reports, or team.
    """
    company = request.company
    today = timezone.now().date()

    # Cashier-specific session stats
    cashier_sales = Sale.objects.filter(
        company=company,
        cashier=request.user,
        sale_status="COMPLETED"
    )
    today_cashier_sales = cashier_sales.filter(created_at__date=today)
    today_count = today_cashier_sales.count()
    today_total = today_cashier_sales.aggregate(total=Sum("grand_total"))["total"] or Decimal("0.00")
    recent_cashier_bills = today_cashier_sales.select_related("customer", "invoice").prefetch_related("payments").order_by("-created_at")[:5]

    return render(
        request,
        "dashboard/cashier_Dash.html",
        {
            "company": company,
            "user_role": request.user_role,
            "today_count": today_count,
            "today_total": today_total,
            "recent_cashier_bills": recent_cashier_bills,
        },
    )


# ── SETTINGS VIEWS ─────────────────────────────────────────────────────

@company_required
@role_required(["Admin", "Manager"])
def settings_general(request):
    """
    Settings -> General page.
    Includes Week Starts On (Sunday / Monday) preference setting.
    """
    company = request.company
    is_owner = request.user_role in ["Admin", "Owner"]

    saved_msg = None
    if request.method == "POST":
        week_start = request.POST.get("week_start", "sunday").lower()
        request.session["pos_week_start"] = week_start
        saved_msg = "Preferences updated successfully."

    current_week_start = request.session.get("pos_week_start", "sunday")

    return render(
        request,
        "dashboard/settings_general.html",
        {
            "company": company,
            "user_role": request.user_role,
            "is_owner": is_owner,
            "current_week_start": current_week_start,
            "saved_msg": saved_msg,
        }
    )


@company_required
@role_required(["Admin", "Manager"])
def settings_appearance(request):
    """
    Settings -> Appearance page.
    Customization for themes (Ash, Forest, Ocean, Plum, Amber)
    with smooth animations, tokens and surface mode toggling.
    """
    company = request.company
    is_owner = request.user_role in ["Admin", "Owner"]

    return render(
        request,
        "dashboard/settings_appearance.html",
        {
            "company": company,
            "user_role": request.user_role,
            "is_owner": is_owner,
        }
    )


@company_required
@role_required(["Admin"])
def settings_company(request):
    """
    Settings -> Company Profile & Ownership settings.
    Strictly OWNER ONLY. Managers are blocked by backend RBAC.
    """
    company = request.company
    is_owner = True
    saved_msg = None

    if request.method == "POST":
        company.company_name = request.POST.get("company_name", company.company_name).strip()
        company.address_line = request.POST.get("address_line", company.address_line).strip()
        company.city = request.POST.get("city", company.city).strip()
        company.state = request.POST.get("state", company.state).strip()
        company.country = request.POST.get("country", company.country).strip()
        company.pincode = request.POST.get("pincode", company.pincode).strip()
        company.phone = request.POST.get("phone", company.phone).strip()
        company.alternate_phone = request.POST.get("alternate_phone", company.alternate_phone).strip()
        company.gst_number = request.POST.get("gst_number", company.gst_number).strip()
        company.pan_number = request.POST.get("pan_number", company.pan_number).strip()
        company.business_info = request.POST.get("business_info", company.business_info).strip()
        company.save()
        saved_msg = "Company details updated successfully."

    return render(
        request,
        "dashboard/settings_company.html",
        {
            "company": company,
            "user_role": request.user_role,
            "is_owner": is_owner,
            "saved_msg": saved_msg,
        }
    )


@company_required
@role_required(["Admin", "Manager", "Owner"])
def settings_scanner(request):
    """
    Settings -> Scanner & POS Devices page.
    Manages primary USB/Bluetooth HID scanner, backup phone scanner,
    and scan behaviors (auto-add, repeat increment, beep, confirm).
    """
    company = request.company
    is_owner = request.user_role in ["Admin", "Owner"]
    from sales.models import POSScannerSession
    from sales.network_utils import get_phone_scanner_full_url
    import secrets
    import random
    from datetime import timedelta

    terminal_id = request.session.get("pos_terminal_id") or "POS-001"
    request.session["pos_terminal_id"] = terminal_id
    saved_msg = None

    if request.method == "POST":
        if request.POST.get("action") == "regenerate_pairing":
            POSScannerSession.objects.filter(company=company, user=request.user, status="ACTIVE").update(status="EXPIRED")
            saved_msg = "New pairing token generated successfully."
        else:
            scanner_settings = {
                "auto_add": request.POST.get("auto_add") == "on",
                "repeat_increment": request.POST.get("repeat_increment") == "on",
                "show_confirmation": request.POST.get("show_confirmation") == "on",
                "audio_beep": request.POST.get("audio_beep") == "on",
                "ask_quantity": request.POST.get("ask_quantity") == "on",
            }
            request.session["pos_scanner_settings"] = scanner_settings
            saved_msg = "Scanner and POS device preferences updated successfully."

    scanner_settings = request.session.get("pos_scanner_settings", {
        "auto_add": True,
        "repeat_increment": True,
        "show_confirmation": True,
        "audio_beep": True,
        "ask_quantity": False,
    })

    # Invalidate expired sessions safely
    POSScannerSession.objects.filter(
        company=company,
        status="ACTIVE",
        expires_at__lte=timezone.now()
    ).update(status="EXPIRED")

    # Retrieve active short-lived pairing session
    active_session = POSScannerSession.objects.filter(
        company=company,
        user=request.user,
        status="ACTIVE",
        expires_at__gt=timezone.now()
    ).order_by("-created_at").first()

    if not active_session:
        session_token = secrets.token_urlsafe(24)
        pairing_code = f"{random.randint(100, 999)}-{random.randint(100, 999)}"
        expires_at = timezone.now() + timedelta(hours=2)
        active_session = POSScannerSession.objects.create(
            company=company,
            user=request.user,
            session_token=session_token,
            pairing_code=pairing_code,
            expires_at=expires_at,
            status="ACTIVE",
            device_info=f"Terminal {terminal_id}",
        )

    # Determine real phone connection evidence
    is_phone_paired = bool(
        active_session and
        active_session.device_info and
        not active_session.device_info.startswith("Terminal") and
        active_session.is_valid()
    )
    last_scan = active_session.scans.order_by("-created_at").first() if active_session else None
    if is_phone_paired:
        scanner_pair_state = f"Paired ({active_session.device_info})"
    elif active_session and active_session.is_valid():
        scanner_pair_state = "Pairing available"
    else:
        scanner_pair_state = "Not paired"

    url_info = get_phone_scanner_full_url(active_session.session_token, request=request)
    phone_scanner_url = url_info["phone_url"]
    expires_in_minutes = max(1, int((active_session.expires_at - timezone.now()).total_seconds() / 60))

    return render(
        request,
        "dashboard/settings_scanner.html",
        {
            "company": company,
            "user_role": request.user_role,
            "is_owner": is_owner,
            "scanner_settings": scanner_settings,
            "saved_msg": saved_msg,
            "network_info": url_info,
            "phone_scanner_url": phone_scanner_url,
            "terminal_id": terminal_id,
            "active_session": active_session,
            "is_phone_paired": is_phone_paired,
            "scanner_pair_state": scanner_pair_state,
            "last_scan": last_scan,
            "expires_in_minutes": expires_in_minutes,
        }
    )


@company_required
@role_required(["Admin", "Owner"])
def settings_telegram(request):
    """
    Settings -> Telegram Owner Reporting.
    Strictly OWNER / ADMIN ONLY.
    Allows generating one-time pairing codes, viewing paired status,
    testing bot commands, and reviewing the offline outbound queue.
    """
    from .models import TelegramOwnerLink, TelegramOutboundMessage
    from .telegram_service import (
        generate_pairing_code,
        verify_pairing_code,
        handle_telegram_command,
        test_telegram_connection,
        get_telegram_gateway_status,
        is_bot_token_configured
    )
    from .sync_service import SyncService

    company = request.company
    is_owner = True
    msg = None
    test_result = None

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "generate_code":
            generate_pairing_code(company, request.user)
            msg = "New pairing code generated. Valid for 15 minutes."
        elif action == "unlink":
            TelegramOwnerLink.objects.filter(company=company, owner=request.user).delete()
            msg = "Telegram account unlinked successfully."
        elif action == "simulate_pair":
            chat_id = request.POST.get("chat_id", "demo_owner_101")
            code = request.POST.get("pairing_code", "")
            success, link_obj, verify_msg = verify_pairing_code(code, chat_id, "StoreOwner")
            msg = verify_msg
        elif action == "test_connection":
            res = test_telegram_connection()
            if res["success"]:
                msg = res["message"]
            else:
                test_result = res["message"]
        elif action == "test_command":
            cmd = request.POST.get("command", "/sales today")
            active_link = TelegramOwnerLink.objects.filter(company=company, owner=request.user, is_verified=True).first()
            if active_link:
                test_result = handle_telegram_command(active_link.telegram_chat_id, cmd)
            else:
                test_result = "⚠️ Please pair your Telegram account first before testing commands."
        elif action == "trigger_sync":
            sync_res = SyncService.run_sync(company)
            msg = f"Cloud Sync Completed. Synced {sync_res['synced']} items. Pending: {sync_res['pending']}."
        elif action == "configure_merchant_bot":
            import secrets
            import urllib.request
            import ssl
            import json
            from .models import MerchantTelegramBot
            from .multi_bot_registry import get_supervised_bot_registry
            raw_token = request.POST.get("bot_token", "").strip()
            if not raw_token:
                test_result = "⚠️ Bot token cannot be empty."
            else:
                try:
                    ssl_ctx = ssl.create_default_context()
                    req = urllib.request.Request(
                        f"https://api.telegram.org/bot{raw_token}/getMe",
                        headers={"User-Agent": "BriskRetail/1.0"}
                    )
                    with urllib.request.urlopen(req, timeout=6, context=ssl_ctx) as resp:
                        resp_data = json.loads(resp.read().decode("utf-8"))
                    if resp_data.get("ok"):
                        b_info = resp_data.get("result", {})
                        b_obj, _ = MerchantTelegramBot.objects.get_or_create(company=company, defaults={"owner": request.user})
                        b_obj.owner = request.user
                        b_obj.set_and_encrypt_token(raw_token)
                        b_obj.bot_id = str(b_info.get("id", ""))
                        b_obj.bot_username = b_info.get("username", "")
                        b_obj.bot_first_name = b_info.get("first_name", "")
                        b_obj.status = "PENDING_VERIFICATION"
                        b_obj.verification_challenge = secrets.token_hex(4).upper()
                        b_obj.verification_expires_at = timezone.now() + timedelta(minutes=15)
                        b_obj.verified_chat_id = ""
                        b_obj.is_active = True
                        b_obj.last_error = ""
                        b_obj.save()
                        get_supervised_bot_registry().register_and_start(b_obj)
                        msg = f"Bot token verified for @{b_obj.bot_username}! Open the bot on Telegram and press Start to link your store."
                    else:
                        test_result = "⚠️ Telegram rejected token. Verify with @BotFather."
                except urllib.error.HTTPError as http_err:
                    if http_err.code in (401, 404):
                        test_result = "⚠️ Telegram rejected token (HTTP 401: Unauthorized). Please check the token provided by @BotFather."
                    else:
                        test_result = f"⚠️ Telegram API returned HTTP {http_err.code}. Please try again later."
                except urllib.error.URLError:
                    test_result = "⚠️ Could not connect to Telegram API. Please check your internet connection."
                except Exception as ex:
                    test_result = f"⚠️ Could not validate token with Telegram API: {str(ex)}"
        elif action == "disconnect_merchant_bot":
            from .models import MerchantTelegramBot
            from .multi_bot_registry import get_supervised_bot_registry
            b_obj = MerchantTelegramBot.objects.filter(company=company).first()
            if b_obj:
                b_obj.status = "DISCONNECTED"
                b_obj.is_active = False
                b_obj.verified_chat_id = ""
                b_obj.verification_challenge = ""
                b_obj.save()
                get_supervised_bot_registry().stop_bot(b_obj.pk)
                msg = "Custom Telegram bot disconnected successfully."

    from .models import MerchantTelegramBot
    from .token_encryption import mask_token_for_display
    merchant_bot = MerchantTelegramBot.objects.filter(company=company).first()
    decrypted_token = merchant_bot.get_decrypted_token() if merchant_bot else ""
    masked_token = mask_token_for_display(decrypted_token) if decrypted_token else ""

    link = TelegramOwnerLink.objects.filter(company=company, owner=request.user).first()
    outbound_messages = TelegramOutboundMessage.objects.filter(company=company).order_by("-created_at")[:10]
    sync_info = SyncService.get_sync_status(company)
    tg_gateway = get_telegram_gateway_status(company)

    return render(
        request,
        "dashboard/settings_telegram.html",
        {
            "company": company,
            "user_role": request.user_role,
            "is_owner": is_owner,
            "link": link,
            "merchant_bot": merchant_bot,
            "masked_token": masked_token,
            "msg": msg,
            "test_result": test_result,
            "outbound_messages": outbound_messages,
            "sync_info": sync_info,
            "tg_gateway": tg_gateway,
            "is_bot_configured": is_bot_token_configured() or (merchant_bot and merchant_bot.status == "CONNECTED"),
        }
    )

