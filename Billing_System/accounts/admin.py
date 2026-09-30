from django.contrib import admin
from .models import companyRegistration,CompanyUser

# Register your models here.

admin.site.register(companyRegistration)
admin.site.register(CompanyUser)
