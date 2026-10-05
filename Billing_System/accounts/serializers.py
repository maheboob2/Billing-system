from rest_framework import serializers
from django.contrib.auth.models import User
from accounts.models import companyRegistration
from accounts.tenancy import get_user_company, get_user_role


class CompanySerializer(serializers.ModelSerializer):
    class Meta:
        model = companyRegistration
        fields = [
            "id",
            "company_name",
            "address_line",
            "city",
            "state",
            "country",
            "pincode",
            "company_email",
            "phone",
            "alternate_phone",
            "gst_number",
            "pan_number",
            "business_info",
            "created_at",
        ]
        read_only_fields = ["id", "created_at"]


class UserProfileSerializer(serializers.ModelSerializer):
    company = serializers.SerializerMethodField()
    role = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "username", "first_name", "last_name", "email", "role", "company"]

    def get_company(self, obj):
        comp = get_user_company(obj)
        if comp:
            return CompanySerializer(comp).data
        return None

    def get_role(self, obj):
        return get_user_role(obj)
