from django.urls import path

from . import views

urlpatterns = [
    path("dashboard/",views.dashboard,name="dashboard"),
    path("manager_dashboard/",views.manager_dashboard,name="manager_dashboard"),
    path("cashier_dashboard/",views.cashier_dashboard,name="cashier_dashboard")
]
