"""
Tenant and Role-Based Access Control (RBAC) helpers for Billing System.
Provides company context resolution and role enforcement for both Django views and DRF.
"""

from functools import wraps
from django.shortcuts import redirect
from django.http import HttpResponseForbidden, JsonResponse
from django.contrib.auth.decorators import login_required
from rest_framework import permissions


def get_user_company(user):
    """
    Resolves the companyRegistration associated with a user,
    whether they are the company owner or an employee.
    """
    if not user or not user.is_authenticated:
        return None

    # 1. Check if user is the company owner
    if hasattr(user, 'owned_companies'):
        try:
            return user.owned_companies
        except Exception:
            pass

    # 2. Check if user is an employee
    if hasattr(user, 'user_registration'):
        try:
            return user.user_registration.company
        except Exception:
            pass

    # 3. Fallback direct queries
    from accounts.models import companyRegistration
    from employee.models import user_registration

    owner_comp = companyRegistration.objects.filter(owner=user).first()
    if owner_comp:
        return owner_comp

    employee = user_registration.objects.filter(user=user).select_related('company').first()
    if employee:
        return employee.company

    return None


def get_user_role(user):
    """
    Resolves the functional role of the user within their company.
    Returns: 'Admin', 'Manager', 'Cashier', 'Worker', or None.
    """
    if not user or not user.is_authenticated:
        return None

    if user.is_superuser:
        return "Admin"

    from accounts.models import companyRegistration
    from employee.models import user_registration

    if companyRegistration.objects.filter(owner=user).exists():
        return "Admin"

    employee = user_registration.objects.filter(user=user).first()
    if employee:
        # Standardize capitalization
        role = employee.Role.strip().title()
        if role in ["Manager", "Cashier", "Worker", "Stock Manager"]:
            return role
        return employee.Role

    return None


def company_required(view_func):
    """
    Decorator ensuring user is authenticated and attached to an active company.
    Attaches `request.company` and `request.user_role` to the request object.
    """
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('accounts')

        company = get_user_company(request.user)
        if not company:
            return HttpResponseForbidden("Access Denied: No company associated with this account.")

        request.company = company
        request.user_role = get_user_role(request.user)
        return view_func(request, *args, **kwargs)

    return _wrapped_view


def role_required(allowed_roles):
    """
    Decorator ensuring the authenticated user has one of the allowed roles.
    Example: @role_required(['Admin', 'Manager'])
    """
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect('accounts')

            company = get_user_company(request.user)
            if not company:
                return HttpResponseForbidden("Access Denied: No company associated with this account.")

            role = get_user_role(request.user)
            request.company = company
            request.user_role = role

            # Normalize roles for comparison
            normalized_allowed = [r.lower() for r in allowed_roles]
            user_role_str = (role or "").lower()

            if user_role_str not in normalized_allowed:
                return HttpResponseForbidden(
                    f"Access Denied: Role '{role}' does not have permission to access this resource."
                )

            return view_func(request, *args, **kwargs)

        return _wrapped_view
    return decorator


# DRF Permissions

class IsCompanyMember(permissions.BasePermission):
    """Allows access only to authenticated users with a registered company."""
    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        company = get_user_company(request.user)
        if not company:
            return False
        request.company = company
        request.user_role = get_user_role(request.user)
        return True


class IsCompanyAdmin(permissions.BasePermission):
    """Allows access only to Company Owners / Superusers."""
    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        company = get_user_company(request.user)
        role = get_user_role(request.user)
        if not company or role != "Admin":
            return False
        request.company = company
        request.user_role = role
        return True


class IsManagerOrAbove(permissions.BasePermission):
    """Allows access to Admin and Manager roles."""
    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        company = get_user_company(request.user)
        role = get_user_role(request.user)
        if not company or role not in ["Admin", "Manager"]:
            return False
        request.company = company
        request.user_role = role
        return True


class IsCashierOrAbove(permissions.BasePermission):
    """Allows access to Admin, Manager, and Cashier roles."""
    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        company = get_user_company(request.user)
        role = get_user_role(request.user)
        if not company or role not in ["Admin", "Manager", "Cashier"]:
            return False
        request.company = company
        request.user_role = role
        return True
