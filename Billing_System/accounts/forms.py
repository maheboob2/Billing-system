from django import forms
from .models import companyRegistration
from django.contrib.auth.models import User


class CompanyRegistrationForm(forms.ModelForm):
    class Meta:
        model = companyRegistration
        fields ="__all__"

    def __init__(self, *args, **kwargs):
               super().__init__(*args, **kwargs)
       
               self.fields["owner"].queryset = User.objects.filter(
                   user_registration__isnull=True
               )