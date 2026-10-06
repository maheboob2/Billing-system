
from django import forms
from django.forms import inlineformset_factory, BaseInlineFormSet
from django.core.exceptions import ValidationError

from .models import Customer, Sale, SaleItem
from inventory.models import Product



class SaleForm(forms.ModelForm):
    class Meta:
        model = Sale
        fields = [
            "customer",
            "invoice_number",
            "sale_date",
            "payment_method",
            "notes",
        ]
        widgets = {
            "sale_date": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        self.company = kwargs.pop("company", None)
        super().__init__(*args, **kwargs)

        self.fields["customer"].required = False

        if self.company:
            self.fields["customer"].queryset = Customer.objects.filter(
                company=self.company
            )


class SaleItemForm(forms.ModelForm):
    class Meta:
        model = SaleItem
        fields = [
            "product",
            "quantity",
            "selling_price",
            "gst_percentage",
        ]

    def __init__(self, *args, **kwargs):
        self.company = kwargs.pop("company", None)
        super().__init__(*args, **kwargs)

        if self.company:
            self.fields["product"].queryset = Product.objects.filter(
                company=self.company
            )


class BaseSaleItemFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()

        if any(self.errors):
            return

        has_items = False

        for form in self.forms:
            if not hasattr(form, "cleaned_data"):
                continue

            if form.cleaned_data.get("DELETE", False):
                continue

            product = form.cleaned_data.get("product")
            quantity = form.cleaned_data.get("quantity")

            if product:
                has_items = True

                if quantity is None or quantity <= 0:
                    raise ValidationError(
                        "Product quantity must be greater than zero."
                    )

        if not has_items:
            raise ValidationError(
                "Add at least one product to the sale."
            )


SaleItemFormSet = inlineformset_factory(
    Sale,
    SaleItem,
    form=SaleItemForm,
    formset=BaseSaleItemFormSet,
    extra=1,
    can_delete=True,
)



class CustomerForm(forms.ModelForm):
    class Meta:
        model = Customer
        fields = ["name", "phone", "email", "address"]
        widgets = {
            "name": forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "Enter customer name",
            }),
            "phone": forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "Enter phone number",
            }),
            "email": forms.EmailInput(attrs={
                "class": "form-control",
                "placeholder": "Enter email (optional)",
            }),
            "address": forms.Textarea(attrs={
                "class": "form-control",
                "placeholder": "Enter address (optional)",
                "rows": 3,
            }),
        }