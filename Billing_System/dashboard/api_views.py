from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated

from accounts.tenancy import get_user_company
from .ai_assistant import answer_business_question, generate_sales_insights, generate_inventory_intelligence
from .telegram_service import handle_telegram_command


class AIAssistantAskAPIView(APIView):
    """
    API for natural-language business queries.
    Strictly read-only, tenant-isolated to the caller's active store.
    Never executes arbitrary SQL.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        company = getattr(request, "company", None) or get_user_company(request.user)
        if not company:
            return Response({"error": "Store context required."}, status=status.HTTP_400_BAD_REQUEST)

        question = request.data.get("question", "").strip()
        if not question:
            return Response({"error": "Question parameter is required."}, status=status.HTTP_400_BAD_REQUEST)

        result = answer_business_question(company, question)
        return Response({
            "status": "success",
            "question": question,
            "intent": result["intent"],
            "answer": result["answer"],
            "data": result.get("data", {})
        })


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
    """
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
