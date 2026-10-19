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

    def clean_pincode(self):
        pincode = (self.cleaned_data.get("pincode") or "").strip()
        if pincode:
            import re
            if not re.match(r"^[1-9]\d{5}$", pincode):
                raise forms.ValidationError("Invalid PIN code. Must be a 6-digit Indian postal code beginning with digits 1–9.")
        return pincode


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

        # Allow user to leave SKU blank to auto-generate unique SKU
        self.fields["product_code"].required = False

        if self.company:
            self.fields["category"].queryset = Category.objects.filter(
                company=self.company,
                status=True
            )

            self.fields["supplier"].queryset = Supplier.objects.filter(
                company=self.company,
                status=True
            )

    def clean_product_code(self):
        code = (self.cleaned_data.get("product_code") or "").strip().upper()
        return code

    def clean(self):
        cleaned_data = super().clean()
        product_code = cleaned_data.get("product_code")
        name = cleaned_data.get("name", "")
        barcode = (cleaned_data.get("barcode") or "").strip()
        category = cleaned_data.get("category")

        # Auto-generate unique SKU if left blank
        if not product_code:
            if self.company:
                from inventory.services import generate_unique_sku
                product_code = generate_unique_sku(
                    self.company,
                    name=name,
                    barcode=barcode,
                    category=category
                )
                cleaned_data["product_code"] = product_code
                self.instance.product_code = product_code
            else:
                self.add_error("product_code", "Cannot generate SKU without company context.")
        else:
            # Enforce manual SKU uniqueness within company
            if self.company:
                qs = Product.objects.filter(company=self.company, product_code=product_code)
                if self.instance and self.instance.pk:
                    qs = qs.exclude(pk=self.instance.pk)
                if qs.exists():
                    self.add_error("product_code", f"Product code '{product_code}' is already in use by another product in this company.")

        if barcode and self.company:
            qs = Product.objects.filter(company=self.company, barcode=barcode)
            if self.instance and self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                self.add_error("barcode", f"Barcode '{barcode}' is already in use by another product in this company.")

        return cleaned_data
