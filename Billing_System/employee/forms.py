from django import forms
from .models import user_registration,User

class user_registration_form(forms.ModelForm):
    class Meta:
        model=user_registration
        fields = "__all__"

   