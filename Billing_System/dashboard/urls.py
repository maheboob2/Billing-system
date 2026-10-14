from django.urls import path
from . import views

urlpatterns = [
    path("dashboard/", views.dashboard, name="dashboard"),
    path("manager_dashboard/", views.manager_dashboard, name="manager_dashboard"),
    path("manager/dashboard/", views.manager_dashboard, name="manager_dashboard_alias"),
    path("cashier_dashboard/", views.cashier_dashboard, name="cashier_dashboard"),
    path("cashier/dashboard/", views.cashier_dashboard, name="cashier_dashboard_alias"),
    path("settings/", views.settings_general, name="settings"),
    path("settings/general/", views.settings_general, name="settings_general"),
    path("settings/appearance/", views.settings_appearance, name="settings_appearance"),
    path("settings/company/", views.settings_company, name="settings_company"),
    path("settings/scanner/", views.settings_scanner, name="settings_scanner"),
    path("settings/telegram/", views.settings_telegram, name="settings_telegram"),
]

