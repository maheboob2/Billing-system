from django.urls import path
from . import views

urlpatterns = [
    path("sales/", views.sales, name="sales"),
    path("sales/returns/", views.returns_view, name="returns"),
    path("returns/", views.returns_view, name="returns_alias"),
    path("cart/", views.cart, name="cart"),
    path("pos/", views.cart, name="pos"),
    path("pos/scanner/", views.pos_phone_scanner, name="pos_phone_scanner"),
    path("invoice/<str:invoice_number>/", views.invoice_detail, name="invoice_detail"),
]