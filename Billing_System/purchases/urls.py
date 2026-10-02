from django.urls import path

from . import views

urlpatterns = [
     path("add/",views.add_purchase,name="add_purchase"),
    path("", views.purchase_list, name="purchase_list"),
      path(
        "receive/<int:purchase_id>/",
        views.receive_purchase,
        name="receive_purchase"
    ),

    path("<int:purchase_id>/", views.purchase_detail, name="purchase_detail"),
    path("edit/<int:purchase_id>/", views.edit_purchase, name="edit_purchase"),
]