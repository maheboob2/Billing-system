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
)

router = DefaultRouter()
router.register(r"products", ProductViewSet, basename="api-products")
router.register(r"categories", CategoryViewSet, basename="api-categories")
router.register(r"suppliers", SupplierViewSet, basename="api-suppliers")
router.register(r"customers", CustomerViewSet, basename="api-customers")
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
    path("sales/payments/", RecordPaymentAPIView.as_view(), name="api-record-payment"),

    # Inventory APIs
    path("inventory/adjust/", StockAdjustmentAPIView.as_view(), name="api-stock-adjust"),

    # Purchases APIs
    path("purchases/<int:pk>/receive/", ReceivePurchaseAPIView.as_view(), name="api-purchase-receive"),

    # Dashboard Metrics API
    path("dashboard/metrics/", DashboardMetricsAPIView.as_view(), name="api-dashboard-metrics"),

    # ViewSet routes
    path("", include(router.urls)),
]
