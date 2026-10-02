from django import forms
from .models import Category,Supplier,Product

class CategoryForm(forms.ModelForm):

    class Meta:
        model = Category

        fields = [
            "name",
            "description",
            "status",
        ]

class SupplierForm(forms.ModelForm):
    class Meta:
        model = Supplier
        fields = [
            "name",
            "contact_person",
            "phone",
            "alternate_phone",
            "email",
            "address",
            "city",
            "state",
            "pincode",
            "gst_number",
            "status",
        ]    

class ProductForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = [
            "name",
            "product_code",
            "barcode",
            "category",
            "supplier",
            "purchase_price",
            "selling_price",
            "gst_percentage",
            "current_stock",
            "minimum_stock",
            "unit",
            "status",
        ]

    def __init__(self, *args, **kwargs):
        company = kwargs.pop("company", None)
        super().__init__(*args, **kwargs)

        if company:
            self.fields["category"].queryset = Category.objects.filter(
                company=company,
                status=True
            )

            self.fields["supplier"].queryset = Supplier.objects.filter(
                company=company,
                status=True
            )            