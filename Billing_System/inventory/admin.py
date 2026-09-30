from django.contrib import admin
from .models import Category,Supplier

# Register your models here.
@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "company",
        "status",
        "created_at",
    )

    list_filter = (
        "status",
        "company",
    )

    search_fields = (
        "name",
    )

@admin.register(Supplier)

class SupplierAdmin(admin.ModelAdmin):

    list_display = (
        "name",
        "company",
        "phone",
        "gst_number",
        "status",
        "created_at",
    )

    list_filter = (
        "status",
        "company",
    )

    search_fields = (
        "name",
        "phone",
        "gst_number",
    )