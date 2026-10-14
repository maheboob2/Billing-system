from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated, AllowAny

import logging
from django.utils import timezone
from django.db.models import Q

from accounts.tenancy import get_user_company, get_user_role
from employee.models import user_registration
from .models import InternalNotification
from .ai_assistant import answer_business_question, generate_sales_insights, generate_inventory_intelligence
from .telegram_service import handle_telegram_command

logger = logging.getLogger("dashboard.ai_assistant")


class AIAssistantAskAPIView(APIView):
    """
    API for natural-language business queries.
    Strictly read-only for store analytics, tenant-isolated to caller's active store.
    Supports staff communication workflow with RBAC enforcement.
    Never exposes raw tracebacks, internal database records, or sensitive server errors to the client.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        company = getattr(request, "company", None) or get_user_company(request.user)
        if not company:
            return Response({"error": "Store context required."}, status=status.HTTP_400_BAD_REQUEST)

        question = request.data.get("question", "")
        if not isinstance(question, str):
            question = str(question or "")
        question = question.strip()

        if not question:
            return Response({"error": "Question parameter is required."}, status=status.HTTP_400_BAD_REQUEST)

        # Cap extremely long input safely
        if len(question) > 2000:
            question = question[:2000]

        try:
            result = answer_business_question(company, question, user=request.user)
            return Response({
                "status": "success",
                "question": question,
                "intent": result.get("intent", "general"),
                "answer": result.get("answer", ""),
                "data": result.get("data", {})
            }, status=status.HTTP_200_OK)
        except Exception as e:
            logger.exception("AI assistant query handling failure: %s", str(e))
            return Response({
                "status": "error",
                "intent": "error",
                "answer": "I couldn't process that request right now. Please try again or rephrase your question.",
                "data": {}
            }, status=status.HTTP_200_OK)


class AIAssistantInsightsAPIView(APIView):
    """
    API returning AI sales insights and inventory intelligence recommendations.
    Strictly read-only and tenant-isolated.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        company = getattr(request, "company", None) or get_user_company(request.user)
        if not company:
            return Response({"error": "Store context required."}, status=status.HTTP_400_BAD_REQUEST)

        sales_insights = generate_sales_insights(company)
        inventory_intel = generate_inventory_intelligence(company)

        return Response({
            "status": "success",
            "sales_insights": sales_insights,
            "inventory_intelligence": inventory_intel
        })


class TelegramWebhookAPIView(APIView):
    """
    Webhook / Gateway for incoming Telegram messages.
    Processes commands within the tenant scope of the verified chat ID.
    External webhook endpoint - receives callbacks directly from Telegram servers.
    """
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        # Can accept both raw Telegram bot webhook payloads and custom simulation payloads
        data = request.data
        chat_id = None
        text = None

        if "message" in data:
            # Standard Telegram Bot Webhook payload
            chat_id = data.get("message", {}).get("chat", {}).get("id")
            text = data.get("message", {}).get("text", "")
        else:
            # Direct API call
            chat_id = data.get("chat_id")
            text = data.get("text") or data.get("command")

        if not chat_id or text is None:
            return Response({"error": "chat_id and message text are required."}, status=status.HTTP_400_BAD_REQUEST)

        reply = handle_telegram_command(str(chat_id), str(text))
        return Response({"status": "processed", "reply": reply})


class SyncStatusAPIView(APIView):
    """
    Returns store cloud synchronization status, offline buffer count, and last sync timestamp.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        company = getattr(request, "company", None) or get_user_company(request.user)
        if not company:
            return Response({"error": "Store context required."}, status=status.HTTP_400_BAD_REQUEST)

        from .sync_service import SyncService
        status_info = SyncService.get_sync_status(company)
        return Response({
            "status": "success",
            "sync": status_info
        }, status=status.HTTP_200_OK)


class SyncTriggerAPIView(APIView):
    """
    Triggers an on-demand idempotent cloud sync upload for the active store.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        company = getattr(request, "company", None) or get_user_company(request.user)
        if not company:
            return Response({"error": "Store context required."}, status=status.HTTP_400_BAD_REQUEST)

        from .sync_service import SyncService
        simulate_offline = request.data.get("simulate_offline", False)
        result = SyncService.run_sync(company, simulate_offline=simulate_offline)
        return Response({
            "status": "success",
            "result": result
        }, status=status.HTTP_200_OK)


class NotificationListCreateAPIView(APIView):
    """
    List and dispatch internal staff notifications.
    Strictly tenant-isolated with RBAC permission enforcement.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        company = getattr(request, "company", None) or get_user_company(request.user)
        if not company:
            return Response({"error": "Store context required."}, status=status.HTTP_400_BAD_REQUEST)

        role = getattr(request, "user_role", None) or get_user_role(request.user) or "Staff"

        # Tenant isolated: query notifications directed to this user, their role, or "ALL"
        qs = InternalNotification.objects.filter(company=company).filter(
            Q(recipient=request.user) |
            Q(recipient_role__iexact=role) |
            Q(recipient_role__iexact="ALL")
        ).select_related("sender", "recipient").order_by("-created_at")

        unread_count = qs.filter(is_read=False).count()
        recent = qs[:30]

        items = []
        for n in recent:
            items.append({
                "id": n.id,
                "sender_username": n.sender.username,
                "sender_name": n.sender.get_full_name() or n.sender.username,
                "sender_role": n.sender_role,
                "recipient_role": n.recipient_role,
                "title": n.title,
                "message": n.message,
                "priority": n.priority,
                "status": n.status,
                "is_read": n.is_read,
                "created_at": n.created_at.strftime("%b %d, %H:%M"),
                "created_at_iso": n.created_at.isoformat(),
            })

        # Available staff recipients in this company
        staff_list = []
        if company.owner and company.owner != request.user:
            staff_list.append({
                "user_id": company.owner.id,
                "username": company.owner.username,
                "name": f"{company.owner.get_full_name() or company.owner.username} (Owner)",
                "role": "Admin"
            })
        employees = user_registration.objects.filter(company=company, status="active").select_related("user")
        for emp in employees:
            if emp.user != request.user:
                staff_list.append({
                    "user_id": emp.user.id,
                    "username": emp.user.username,
                    "name": f"{emp.user.get_full_name() or emp.user.username} ({emp.Role})",
                    "role": emp.Role
                })

        return Response({
            "status": "success",
            "unread_count": unread_count,
            "user_role": role,
            "notifications": items,
            "staff_members": staff_list
        }, status=status.HTTP_200_OK)

    def post(self, request):
        company = getattr(request, "company", None) or get_user_company(request.user)
        if not company:
            return Response({"error": "Store context required."}, status=status.HTTP_400_BAD_REQUEST)

        sender_role = getattr(request, "user_role", None) or get_user_role(request.user) or "Staff"
        message = (request.data.get("message") or "").strip()
        title = (request.data.get("title") or f"Message from {request.user.username}").strip()
        recipient_role = (request.data.get("recipient_role") or "").strip()
        recipient_user_id = request.data.get("recipient_user_id")
        priority = (request.data.get("priority") or "NORMAL").upper()

        if priority not in ["NORMAL", "HIGH", "URGENT"]:
            priority = "NORMAL"

        if not message:
            return Response({"error": "Message content cannot be blank."}, status=status.HTTP_400_BAD_REQUEST)

        # RBAC Enforcement: Cashier cannot broadcast to ALL or assign tasks to cashiers
        if sender_role.lower() == "cashier":
            if recipient_role.upper() == "ALL":
                return Response({
                    "error": "Cashiers are not permitted to send broadcast announcements to all staff."
                }, status=status.HTTP_403_FORBIDDEN)
            if recipient_role.lower() == "cashier":
                return Response({
                    "error": "Cashiers cannot issue directives to fellow cashiers."
                }, status=status.HTTP_403_FORBIDDEN)

        recipient_user = None
        if recipient_user_id:
            try:
                r_id = int(recipient_user_id)
            except (ValueError, TypeError):
                r_id = None

            is_owner = (company.owner and company.owner.id == r_id)
            emp = user_registration.objects.filter(company=company, user_id=r_id).first() if r_id else None
            if not is_owner and not emp:
                return Response({"error": "Recipient user not found in this company."}, status=status.HTTP_400_BAD_REQUEST)
            recipient_user = company.owner if is_owner else emp.user
            if not recipient_role:
                recipient_role = "Admin" if is_owner else emp.Role

        if not recipient_user and not recipient_role:
            return Response({"error": "Recipient role or specific staff member must be specified."}, status=status.HTTP_400_BAD_REQUEST)

        notif = InternalNotification.objects.create(
            company=company,
            sender=request.user,
            sender_role=sender_role,
            recipient=recipient_user,
            recipient_role=recipient_role,
            title=title,
            message=message,
            priority=priority,
            status="DELIVERED"
        )

        return Response({
            "status": "success",
            "message": "Staff notification delivered successfully.",
            "notification": {
                "id": notif.id,
                "sender": request.user.username,
                "sender_role": notif.sender_role,
                "recipient": recipient_user.username if recipient_user else None,
                "recipient_role": notif.recipient_role,
                "message": notif.message,
                "created_at": notif.created_at.strftime("%b %d, %H:%M")
            }
        }, status=status.HTTP_201_CREATED)


class NotificationMarkReadAPIView(APIView):
    """Marks a single notification as read."""
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        company = getattr(request, "company", None) or get_user_company(request.user)
        if not company:
            return Response({"error": "Store context required."}, status=status.HTTP_400_BAD_REQUEST)

        notif = InternalNotification.objects.filter(id=pk, company=company).first()
        if not notif:
            return Response({"error": "Notification not found."}, status=status.HTTP_404_NOT_FOUND)

        role = getattr(request, "user_role", None) or get_user_role(request.user) or "Staff"
        if notif.recipient and notif.recipient != request.user and role != "Admin":
            return Response({"error": "Permission denied."}, status=status.HTTP_403_FORBIDDEN)

        notif.is_read = True
        notif.status = "READ"
        notif.read_at = timezone.now()
        notif.save(update_fields=["is_read", "status", "read_at"])

        return Response({"status": "success", "message": "Marked as read."})


class NotificationMarkAllReadAPIView(APIView):
    """Marks all notifications for current user/role as read."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        company = getattr(request, "company", None) or get_user_company(request.user)
        if not company:
            return Response({"error": "Store context required."}, status=status.HTTP_400_BAD_REQUEST)

        role = getattr(request, "user_role", None) or get_user_role(request.user) or "Staff"
        now = timezone.now()

        InternalNotification.objects.filter(
            company=company,
            is_read=False
        ).filter(
            Q(recipient=request.user) |
            Q(recipient_role__iexact=role) |
            Q(recipient_role__iexact="ALL")
        ).update(is_read=True, status="READ", read_at=now)

        return Response({"status": "success", "message": "All notifications marked as read."})


class MerchantBotConfigureAPIView(APIView):
    """
    Dedicated endpoint for store Owner to configure a BotFather-created custom bot.
    Validates token against Telegram getMe before saving.
    Encrypts token at rest.
    Generates single-use cryptographic verification challenge.
    Access: Store Owner / Admin only.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        import secrets
        import urllib.request
        import json
        import ssl
        from datetime import timedelta
        from .models import MerchantTelegramBot
        from .token_encryption import mask_token_for_display
        from .multi_bot_registry import get_supervised_bot_registry

        company = getattr(request, "company", None) or get_user_company(request.user)
        role = getattr(request, "user_role", None) or get_user_role(request.user)
        if not company or role not in ["Admin", "Owner"]:
            return Response(
                {"error": "Only the authorized store Owner can configure Telegram bots."},
                status=status.HTTP_403_FORBIDDEN
            )

        raw_token = request.data.get("bot_token", "").strip()
        if not raw_token:
            return Response({"error": "Bot token is required."}, status=status.HTTP_400_BAD_REQUEST)

        # Validate with Telegram getMe API
        ssl_ctx = ssl.create_default_context()
        validate_url = f"https://api.telegram.org/bot{raw_token}/getMe"
        try:
            req = urllib.request.Request(
                validate_url,
                headers={"User-Agent": "BriskRetail-BotValidator/1.0"}
            )
            with urllib.request.urlopen(req, timeout=8, context=ssl_ctx) as resp:
                resp_data = json.loads(resp.read().decode("utf-8"))

            if not resp_data.get("ok"):
                return Response(
                    {"error": "Telegram rejected token. Please verify the token provided by @BotFather."},
                    status=status.HTTP_400_BAD_REQUEST
                )

            bot_info = resp_data.get("result", {})
            bot_id = str(bot_info.get("id", ""))
            bot_username = bot_info.get("username", "")
            bot_first_name = bot_info.get("first_name", "")

        except urllib.error.HTTPError as http_err:
            return Response(
                {"error": f"Invalid Telegram Bot Token (HTTP {http_err.code}). Please verify the token from @BotFather."},
                status=status.HTTP_400_BAD_REQUEST
            )
        except Exception as e:
            return Response(
                {"error": f"Could not reach Telegram API to validate token: {str(e)}"},
                status=status.HTTP_502_BAD_GATEWAY
            )

        # Generate single-use verification challenge
        challenge = secrets.token_hex(4).upper()
        expires_at = timezone.now() + timedelta(minutes=15)

        bot_obj, _ = MerchantTelegramBot.objects.get_or_create(
            company=company,
            defaults={"owner": request.user}
        )
        bot_obj.owner = request.user
        bot_obj.set_and_encrypt_token(raw_token)
        bot_obj.bot_id = bot_id
        bot_obj.bot_username = bot_username
        bot_obj.bot_first_name = bot_first_name
        bot_obj.status = "PENDING_VERIFICATION"
        bot_obj.verification_challenge = challenge
        bot_obj.verification_expires_at = expires_at
        bot_obj.verified_chat_id = ""
        bot_obj.verified_username = ""
        bot_obj.is_active = True
        bot_obj.last_error = ""
        bot_obj.save()

        # Start supervised polling worker for this merchant's bot
        registry = get_supervised_bot_registry()
        registry.register_and_start(bot_obj)

        bot_link = f"https://t.me/{bot_username}?start={challenge}"

        return Response({
            "success": True,
            "status": "PENDING_VERIFICATION",
            "bot_username": bot_username,
            "bot_first_name": bot_first_name,
            "masked_token": mask_token_for_display(raw_token),
            "challenge": challenge,
            "expires_at": expires_at.isoformat(),
            "bot_link": bot_link,
            "message": f"Token validated for @{bot_username}! Open the bot on Telegram and press Start to verify your chat."
        }, status=status.HTTP_200_OK)


class MerchantBotStatusAPIView(APIView):
    """
    Returns safe diagnostic status of merchant's custom Telegram bot.
    Never exposes raw or encrypted tokens.
    Access: Store Owner / Admin / Manager.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from .models import MerchantTelegramBot

        company = getattr(request, "company", None) or get_user_company(request.user)
        if not company:
            return Response({"error": "Store context required."}, status=status.HTTP_400_BAD_REQUEST)

        bot_obj = MerchantTelegramBot.objects.filter(company=company).first()
        if not bot_obj or bot_obj.status == "NOT_CONFIGURED" or not bot_obj.encrypted_token:
            return Response({
                "configured": False,
                "status": "NOT_CONFIGURED",
                "is_verified": False,
                "bot_username": "",
                "bot_first_name": "",
                "bot_link": "",
                "challenge": "",
                "verified_username": "",
                "verified_chat_id": "",
                "last_error": "",
            }, status=status.HTTP_200_OK)

        bot_link = f"https://t.me/{bot_obj.bot_username}"
        if bot_obj.status == "PENDING_VERIFICATION" and bot_obj.verification_challenge:
            bot_link += f"?start={bot_obj.verification_challenge}"

        return Response({
            "configured": True,
            "status": bot_obj.status,
            "is_verified": bot_obj.status == "CONNECTED",
            "bot_username": bot_obj.bot_username,
            "bot_first_name": bot_obj.bot_first_name,
            "bot_link": bot_link,
            "challenge": bot_obj.verification_challenge if bot_obj.status == "PENDING_VERIFICATION" else "",
            "verified_username": bot_obj.verified_username,
            "verified_chat_id": bot_obj.verified_chat_id,
            "last_error": bot_obj.last_error,
            "last_health_check": bot_obj.last_health_check.isoformat() if bot_obj.last_health_check else None,
        }, status=status.HTTP_200_OK)


class MerchantBotDisconnectAPIView(APIView):
    """
    Disconnects and unlinks merchant's custom Telegram bot.
    Stops worker polling and clears active chat binding.
    Preserves all existing sales, invoices, customers, and reports intact.
    Access: Store Owner only.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        from .models import MerchantTelegramBot
        from .multi_bot_registry import get_supervised_bot_registry

        company = getattr(request, "company", None) or get_user_company(request.user)
        role = getattr(request, "user_role", None) or get_user_role(request.user)
        if not company or role not in ["Admin", "Owner"]:
            return Response(
                {"error": "Only the authorized store Owner can disconnect Telegram bots."},
                status=status.HTTP_403_FORBIDDEN
            )

        bot_obj = MerchantTelegramBot.objects.filter(company=company).first()
        if bot_obj:
            bot_obj.status = "DISCONNECTED"
            bot_obj.is_active = False
            bot_obj.verified_chat_id = ""
            bot_obj.verified_username = ""
            bot_obj.verification_challenge = ""
            bot_obj.save()

            registry = get_supervised_bot_registry()
            registry.stop_bot(bot_obj.pk)

        return Response({
            "success": True,
            "status": "DISCONNECTED",
            "message": "Telegram bot disconnected successfully. You can reconnect anytime."
        }, status=status.HTTP_200_OK)


class CloudSyncPushAPIView(APIView):
    """
    Authoritative ingestion endpoint for shop-to-cloud synchronization.
    Called by local POS instances to push durable snapshots and pending queue transactions.
    Supports authenticated server-to-server communication via X-Cloud-Sync-Key.
    """
    permission_classes = [AllowAny]

    def post(self, request):
        from datetime import datetime
        from decimal import Decimal
        from accounts.models import companyRegistration
        from .models import CompanyCloudSnapshot, RemoteSyncedRecord, RemoteSyncStatus
        from django.conf import settings

        api_key = request.headers.get("X-Cloud-Sync-Key", "").strip()
        expected_key = getattr(settings, "CLOUD_SYNC_API_KEY", "").strip()
        data = request.data or {}

        company_id = data.get("company_id")
        company = None
        if company_id:
            company = companyRegistration.objects.filter(id=company_id).first()

        # Key verification: if CLOUD_SYNC_API_KEY is configured on server, it must match
        if expected_key and api_key != expected_key:
            # Also check if company has per-tenant api key
            has_status = hasattr(company, "sync_status") and company.sync_status
            if not company or not has_status or company.sync_status.cloud_sync_api_key != api_key:
                return Response(
                    {"error": "Unauthorized: Invalid or missing X-Cloud-Sync-Key."},
                    status=status.HTTP_401_UNAUTHORIZED
                )

        if not company:
            return Response({"error": "Invalid or missing company_id."}, status=status.HTTP_400_BAD_REQUEST)

        # Ingest Snapshot
        snapshot_date_str = data.get("snapshot_date")
        if snapshot_date_str:
            try:
                target_date = datetime.fromisoformat(snapshot_date_str).date()
            except Exception:
                target_date = timezone.localdate()
        else:
            target_date = timezone.localdate()

        snapshot, _ = CompanyCloudSnapshot.objects.update_or_create(
            company=company,
            snapshot_date=target_date,
            defaults={
                "last_sync_time": timezone.now(),
                "freshness_status": "LIVE",
                "pending_local_count": data.get("pending_local_count", 0),
                "total_revenue": Decimal(str(data.get("total_revenue", "0.00"))),
                "tax_amount": Decimal(str(data.get("tax_amount", "0.00"))),
                "discount_amount": Decimal(str(data.get("discount_amount", "0.00"))),
                "net_revenue": Decimal(str(data.get("net_revenue", "0.00"))),
                "invoice_count": data.get("invoice_count", 0),
                "cash_total": Decimal(str(data.get("cash_total", "0.00"))),
                "upi_total": Decimal(str(data.get("upi_total", "0.00"))),
                "card_total": Decimal(str(data.get("card_total", "0.00"))),
                "other_payment_total": Decimal(str(data.get("other_payment_total", "0.00"))),
                "returns_count": data.get("returns_count", 0),
                "refund_amount": Decimal(str(data.get("refund_amount", "0.00"))),
                "active_products_count": data.get("active_products_count", 0),
                "low_stock_items": data.get("low_stock_items", []),
                "out_of_stock_items": data.get("out_of_stock_items", []),
                "top_selling_products": data.get("top_selling_products", []),
                "total_credit_receivable": Decimal(str(data.get("total_credit_receivable", "0.00"))),
                "customer_balances": data.get("customer_balances", []),
                "raw_payload": data.get("raw_payload", {}),
            }
        )

        # Ingest any pending transactions idempotently
        pending_items = data.get("pending_items", [])
        synced_count = 0
        for it in pending_items:
            key = it.get("idempotency_key")
            if key:
                RemoteSyncedRecord.objects.get_or_create(
                    company=company,
                    idempotency_key=key,
                    defaults={
                        "entity_type": it.get("entity_type", "TRANSACTION"),
                        "entity_identifier": it.get("idempotency_key", ""),
                        "payload_summary": it.get("payload", {}),
                    }
                )
                synced_count += 1

        # Update RemoteSyncStatus on cloud
        status_obj, _ = RemoteSyncStatus.objects.get_or_create(
            company=company,
            defaults={"status": "ONLINE"}
        )
        status_obj.status = "ONLINE"
        status_obj.last_sync_success = timezone.now()
        status_obj.pending_count = 0
        status_obj.total_synced_count += synced_count
        status_obj.last_snapshot_time = timezone.now()
        status_obj.save()

        return Response({
            "success": True,
            "company": company.company_name,
            "snapshot_date": target_date.isoformat(),
            "synced_items_count": synced_count,
            "synced_at": timezone.now().isoformat(),
            "message": "Cloud snapshot and transactions synchronized successfully."
        }, status=status.HTTP_200_OK)


class CloudSyncStatusAPIView(APIView):
    """
    Health and status check for 24/7 cloud sync worker and snapshot availability.
    """
    permission_classes = [AllowAny]

    def get(self, request):
        from .models import CompanyCloudSnapshot
        total_snapshots = CompanyCloudSnapshot.objects.count()
        latest = CompanyCloudSnapshot.objects.order_by("-updated_at").first()

        return Response({
            "status": "ONLINE",
            "service": "24/7 Cloud Sync & Telegram Daemon Gateway",
            "server_time": timezone.now().isoformat(),
            "total_snapshots": total_snapshots,
            "latest_sync": latest.last_sync_time.isoformat() if latest else None,
        }, status=status.HTTP_200_OK)


