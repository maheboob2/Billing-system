
from django import forms
from .models import Purchase, PurchaseItem
from inventory.models import Supplier, Product
from django.forms import inlineformset_factory
from django.forms import BaseInlineFormSet
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404

class PurchaseForm(forms.ModelForm):
    class Meta:
        model = Purchase
        fields = [
            "supplier",
            "invoice_number",
            "purchase_date",
            "notes",
        ]

        widgets = {
            "purchase_date": forms.DateInput(
                attrs={"type": "date"}
            ),
        }

    def __init__(self, *args, **kwargs):
        company = kwargs.pop("company", None)
        super().__init__(*args, **kwargs)

        if company:
            self.fields["supplier"].queryset = Supplier.objects.filter(
                company=company,
                status=True
            )

class BasePurchaseItemFormSet(BaseInlineFormSet):
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
                "Add at least one product to the purchase."
            )



class PurchaseItemForm(forms.ModelForm):
    class Meta:
        model = PurchaseItem
        fields = [
            "product",
            "quantity",
            "purchase_price",
            "gst_percentage",
        ]

    def __init__(self, *args, **kwargs):
        company = kwargs.pop("company", None)
        super().__init__(*args, **kwargs)

        if company:
            self.fields["product"].queryset = Product.objects.filter(
                company=company,
                status=True
            )

    

PurchaseItemFormSet = inlineformset_factory(
    Purchase,
    PurchaseItem,
    form=PurchaseItemForm,
    formset=BasePurchaseItemFormSet,
    extra=1,
    can_delete=True,
)        


