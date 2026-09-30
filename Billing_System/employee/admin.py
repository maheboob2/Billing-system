from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django import forms
from django.contrib.auth.models import User
from .models import user_registration


class user_registration_form(forms.ModelForm):
    class Meta:
        model = user_registration
        fields = "__all__"

class EmployeeInline(admin.StackedInline):
        model = user_registration
        extra = 0


class CustomUserAdmin(UserAdmin):
    inlines = [EmployeeInline]


admin.site.unregister(User)
admin.site.register(User, CustomUserAdmin)