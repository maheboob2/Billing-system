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
        self.company = kwargs.pop("company", None)
        super().__init__(*args, **kwargs)

        if self.company:
            self.fields["category"].queryset = Category.objects.filter(
                company=self.company,
                status=True
            )

            self.fields["supplier"].queryset = Supplier.objects.filter(
                company=self.company,
                status=True
            )

    def clean(self):
        cleaned_data = super().clean()
        product_code = cleaned_data.get("product_code")
        if product_code and self.company:
            qs = Product.objects.filter(company=self.company, product_code=product_code)
            if self.instance and self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                self.add_error("product_code", f"Product code '{product_code}' is already in use by another product in this company.")

        barcode = (cleaned_data.get("barcode") or "").strip()
        if barcode and self.company:
            qs = Product.objects.filter(company=self.company, barcode=barcode)
            if self.instance and self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                self.add_error("barcode", f"Barcode '{barcode}' is already in use by another product in this company.")

        return cleaned_data