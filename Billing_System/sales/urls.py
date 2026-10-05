from django.urls import path
from . import views

urlpatterns = [
    path("sales/", views.sales, name="sales"),
    path("invoice/<str:invoice_number>/", views.invoice_detail, name="invoice_detail"),
]