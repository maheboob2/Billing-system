from django.urls import path, include
from rest_framework.routers import DefaultRouter

from accounts.api_views import CurrentProfileAPIView, LoginAPIView, LogoutAPIView
from inventory.api_views import (
    CategoryViewSet,
    SupplierViewSet,
    ProductViewSet,
    StockMovementViewSet,
    StockAdjustmentAPIView,
)
from purchases.api_views import PurchaseViewSet, ReceivePurchaseAPIView
from sales.api_views import (
    CustomerViewSet,
    SaleViewSet,
    InvoiceViewSet,
    CheckoutAPIView,
    RecordPaymentAPIView,
    DashboardMetricsAPIView,
    SalesQuoteAPIView,
    ReturnRequestViewSet,
    POSScannerSessionCreateAPIView,
    POSScannerSessionActiveAPIView,
    POSScannerSessionPollAPIView,
    POSScannerSessionScanAPIView,
    POSScannerSessionDisconnectAPIView,
    POSScannerSessionRevokeAPIView,
    POSScannerNetworkDiagnosticsAPIView,
)
from reports.urls import report_api_urlpatterns
from dashboard.api_views import (
    AIAssistantAskAPIView,
    AIAssistantInsightsAPIView,
    TelegramWebhookAPIView,
    SyncStatusAPIView,
    SyncTriggerAPIView,
    NotificationListCreateAPIView,
    NotificationMarkReadAPIView,
    NotificationMarkAllReadAPIView,
    MerchantBotConfigureAPIView,
    MerchantBotStatusAPIView,
    MerchantBotDisconnectAPIView,
    CloudSyncPushAPIView,
    CloudSyncStatusAPIView,
)
from config.api_docs import openapi_schema_view, swagger_docs_view
from employee.views import postal_pincode_lookup

router = DefaultRouter()
router.register(r"products", ProductViewSet, basename="api-products")
router.register(r"categories", CategoryViewSet, basename="api-categories")
router.register(r"suppliers", SupplierViewSet, basename="api-suppliers")
router.register(r"customers", CustomerViewSet, basename="api-customers")
router.register(r"sales/returns", ReturnRequestViewSet, basename="api-returns")
router.register(r"sales", SaleViewSet, basename="api-sales")
router.register(r"invoices", InvoiceViewSet, basename="api-invoices")
router.register(r"purchases", PurchaseViewSet, basename="api-purchases")
router.register(r"inventory/movements", StockMovementViewSet, basename="api-stock-movements")

urlpatterns = [
    # Authentication APIs
    path("auth/login/", LoginAPIView.as_view(), name="api-login"),
    path("auth/logout/", LogoutAPIView.as_view(), name="api-logout"),
    path("auth/me/", CurrentProfileAPIView.as_view(), name="api-me"),

    # Sales & Billing APIs
    path("sales/checkout/", CheckoutAPIView.as_view(), name="api-checkout"),
    path("sales/quote/", SalesQuoteAPIView.as_view(), name="api-sales-quote"),
    path("sales/payments/", RecordPaymentAPIView.as_view(), name="api-record-payment"),

    # Inventory APIs
    path("inventory/adjust/", StockAdjustmentAPIView.as_view(), name="api-stock-adjust"),

    # Purchases APIs
    path("purchases/<int:pk>/receive/", ReceivePurchaseAPIView.as_view(), name="api-purchase-receive"),

    # Dashboard Metrics API
    path("dashboard/metrics/", DashboardMetricsAPIView.as_view(), name="api-dashboard-metrics"),

    # POS Phone Scanner APIs
    path("pos/scanner-session/", POSScannerSessionCreateAPIView.as_view()),
    path("pos/scanner-session/create/", POSScannerSessionCreateAPIView.as_view(), name="api-scanner-session-create"),
    path("pos/scanner-session/active/", POSScannerSessionActiveAPIView.as_view(), name="api-scanner-session-active"),
    path("pos/scanner-session/<str:token>/poll/", POSScannerSessionPollAPIView.as_view(), name="api-scanner-session-poll"),
    path("pos/scanner-session/<str:token>/scan/", POSScannerSessionScanAPIView.as_view(), name="api-scanner-session-scan"),
    path("pos/scanner-session/<str:token>/heartbeat/", POSScannerSessionScanAPIView.as_view(), name="api-scanner-session-heartbeat"),
    path("pos/scanner-session/<str:token>/disconnect/", POSScannerSessionDisconnectAPIView.as_view(), name="api-scanner-session-disconnect"),
    path("pos/scanner-session/<str:token>/revoke/", POSScannerSessionRevokeAPIView.as_view(), name="api-scanner-session-revoke"),
    path("pos/scanner-session/network-diagnostics/", POSScannerNetworkDiagnosticsAPIView.as_view(), name="api-scanner-network-diagnostics"),

    # Report APIs
    *report_api_urlpatterns,

    # AI Business Assistant APIs
    path("ai/ask/", AIAssistantAskAPIView.as_view(), name="api-ai-ask"),
    path("ai/insights/", AIAssistantInsightsAPIView.as_view(), name="api-ai-insights"),

    # Telegram Webhook / Gateway API
    path("telegram/webhook/", TelegramWebhookAPIView.as_view(), name="api-telegram-webhook"),
    path("telegram/merchant-bot/configure/", MerchantBotConfigureAPIView.as_view(), name="api-merchant-bot-configure"),
    path("telegram/merchant-bot/status/", MerchantBotStatusAPIView.as_view(), name="api-merchant-bot-status"),
    path("telegram/merchant-bot/disconnect/", MerchantBotDisconnectAPIView.as_view(), name="api-merchant-bot-disconnect"),

    # Cloud Sync Status & Trigger APIs
    path("sync/status/", SyncStatusAPIView.as_view(), name="api-sync-status"),
    path("sync/trigger/", SyncTriggerAPIView.as_view(), name="api-sync-trigger"),
    path("cloud-sync/push/", CloudSyncPushAPIView.as_view(), name="api-cloud-sync-push"),
    path("cloud-sync/status/", CloudSyncStatusAPIView.as_view(), name="api-cloud-sync-status"),

    # Internal Staff Notification & Messaging APIs
    path("notifications/", NotificationListCreateAPIView.as_view(), name="api-notifications"),
    path("notifications/<int:pk>/read/", NotificationMarkReadAPIView.as_view(), name="api-notification-read"),
    path("notifications/mark-all-read/", NotificationMarkAllReadAPIView.as_view(), name="api-notifications-mark-all-read"),

    # Postal PIN code lookup API
    path("postal/lookup/", postal_pincode_lookup, name="api-postal-lookup"),

    # OpenAPI Schema & Swagger Documentation
    path("schema/", openapi_schema_view, name="api-schema"),
    path("docs/", swagger_docs_view, name="api-docs"),

    # ViewSet routes
    path("", include(router.urls)),
]
