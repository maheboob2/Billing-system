from django.urls import path

from . import views
from .api_views import (
    SalesReportAPIView,
    InventoryReportAPIView,
    PurchaseReportAPIView,
    FinancialReportAPIView,
    TaxReportAPIView,
)

urlpatterns = [
    path("reports/", views.reports, name="reports"),
]

# Report API paths (also included in api_urls.py under /api/reports/)
report_api_urlpatterns = [
    path("reports/sales/", SalesReportAPIView.as_view(), name="api-report-sales"),
    path("reports/inventory/", InventoryReportAPIView.as_view(), name="api-report-inventory"),
    path("reports/purchases/", PurchaseReportAPIView.as_view(), name="api-report-purchases"),
    path("reports/financial/", FinancialReportAPIView.as_view(), name="api-report-financial"),
    path("reports/tax/", TaxReportAPIView.as_view(), name="api-report-tax"),
]