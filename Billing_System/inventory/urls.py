from django.urls import path

from . import views

urlpatterns = [
    path("category/",views.category_list,name="category"),
    path("category/add",views.add_category,name="add_category"),
    path(
    "category/edit/<int:id>/",
    views.edit_category,
    name="edit_category"
),

path(
    "category/deactivate/<int:id>/",
    views.deactivate_category,
    name="deactivate_category"
),
path("supplier/add/", views.add_supplier, name="add_supplier"),
path("supplier/", views.supplier_list, name="supplier"),
path(
    "supplier/edit/<int:id>/",
    views.edit_supplier,
    name="edit_supplier"
),

path(
    "supplier/deactivate/<int:id>/",
    views.deactivate_supplier,
    name="deactivate_supplier"
),

path("product/add/", views.add_product, name="add_product"),
path("product/", views.product_list, name="product"),
path(
    "product/edit/<int:id>/",
    views.edit_product,
    name="edit_product"
),
path(
    "product/deactivate/<int:id>/",
    views.deactivate_product,
    name="deactivate_product"
),
]


