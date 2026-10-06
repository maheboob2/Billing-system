
from django.urls import path
from . import views

app_name = "sales"

urlpatterns = [
      path("", views.sale_list, name="sale_list"),
    path("add/", views.add_sale, name="add_sale"),
    path("<int:pk>/", views.sale_detail, name="sale_detail"),
     path("<int:pk>/complete/", views.complete_sale, name="complete_sale"),
     path(
    "<int:pk>/invoice/pdf/",
    views.download_invoice_pdf,
    name="download_invoice_pdf",
    
),

    path("customers/", views.customer_list, name="customer_list"),

    path("customers/add/", views.add_customer, name="add_customer"),
    path("customers/<int:pk>/edit/", views.edit_customer, name="edit_customer"),
    path(
    "customers/<int:pk>/history/",
    views.customer_purchase_history,
    name="customer_purchase_history",
),
path(
    "customers/<int:pk>/delete/",
    views.delete_customer,
    name="delete_customer",
),
]