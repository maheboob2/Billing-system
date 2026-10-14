"""
Multi-Identifier Authentication Backend for Retail POS & Billing System.
Allows users (Store Owners, Managers, Cashiers, Staff) to log in using:
1. Username
2. Employee ID (e.g., EMP-101)
3. Email address

Enforces active status for both the Django User and the employee profile.
"""

from django.contrib.auth.backends import ModelBackend
from django.contrib.auth.models import User
from django.db.models import Q


class UsernameOrEmployeeIdOrEmailBackend(ModelBackend):
    """
    Authenticates against django.contrib.auth.models.User using
    username, employee_id, or email address.
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        if not username or password is None:
            return None

        identifier = str(username).strip()
        if not identifier:
            return None

        # 1. Match by exact username or case-insensitive username
        user = User.objects.filter(username__iexact=identifier).first()

        # 2. Match by linked employee_id
        if not user:
            user = User.objects.filter(
                user_registration__employee_id__iexact=identifier
            ).select_related("user_registration").first()

        # 3. Match by email address (if valid email provided)
        if not user and "@" in identifier:
            user = User.objects.filter(email__iexact=identifier).first()

        if user and user.check_password(password):
            # Verify user account is active
            if not user.is_active:
                return None

            # If user is an employee, verify their employee status is active
            if hasattr(user, "user_registration"):
                try:
                    emp = user.user_registration
                    if emp.status and emp.status.strip().lower() == "inactive":
                        return None
                except Exception:
                    pass

            return user

        return None

    def get_user(self, user_id):
        try:
            return User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return None
