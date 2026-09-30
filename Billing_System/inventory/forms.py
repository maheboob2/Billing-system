from django import forms
from .models import Category,Supplier

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