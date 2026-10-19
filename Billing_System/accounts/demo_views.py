"""
Views for College Demonstration Session Isolation Hub.

Explains and orchestrates RFC 6265 Loopback Multi-Origin Host Isolation,
allowing independent Owner, Manager, and Cashier tabs in the exact same
browser window and profile without compromising security, tokens, or credentials.
"""

from django.shortcuts import render
from django.http import JsonResponse
from django.conf import settings
from accounts.models import companyRegistration
from employee.models import user_registration
from accounts.tenancy import get_user_role


def demo_hub_view(request):
    """
    Renders the interactive College Demonstration Launch Hub.
    Provides live isolation status, architecture explanation, 1-click launch buttons,
    and a step-by-step verification protocol for college presentations.
    """
    college_demo_mode = getattr(settings, "COLLEGE_DEMO_MODE", False)
    current_host = request.get_host()
    demo_role_hint = getattr(request, "demo_role_hint", "General")

    # Safe read-only lookup for sample roles to guide the presenter
    sample_owner = None
    sample_manager = None
    sample_cashier = None

    try:
        first_comp = companyRegistration.objects.select_related("owner").first()
        if first_comp and first_comp.owner:
            sample_owner = first_comp.owner.username
    except Exception:
        pass

    try:
        first_mgr = user_registration.objects.filter(Role__iexact="Manager").select_related("user").first()
        if first_mgr and first_mgr.user:
            sample_manager = first_mgr.user.username
    except Exception:
        pass

    try:
        first_cashier = user_registration.objects.filter(Role__iexact="Cashier").select_related("user").first()
        if first_cashier and first_cashier.user:
            sample_cashier = first_cashier.user.username
    except Exception:
        pass

    current_user_role = None
    if request.user.is_authenticated:
        current_user_role = get_user_role(request.user)
        if companyRegistration.objects.filter(owner=request.user).exists():
            current_user_role = "Owner"

    context = {
        "college_demo_mode": college_demo_mode,
        "current_host": current_host,
        "demo_role_hint": demo_role_hint,
        "owner_url": "http://localhost:8000/accounts/",
        "manager_url": "http://127.0.0.1:8000/accounts/",
        "cashier_url": "http://127.0.0.2:8000/accounts/",
        "sample_owner": sample_owner,
        "sample_manager": sample_manager,
        "sample_cashier": sample_cashier,
        "user_authenticated": request.user.is_authenticated,
        "current_user": request.user,
        "current_user_role": current_user_role,
    }
    return render(request, "accounts/demo_hub.html", context)


def demo_status_api(request):
    """
    JSON API returning active isolation configuration and diagnostic details.
    """
    college_demo_mode = getattr(settings, "COLLEGE_DEMO_MODE", False)
    user_role = None
    is_owner = False
    if request.user.is_authenticated:
        user_role = get_user_role(request.user)
        is_owner = companyRegistration.objects.filter(owner=request.user).exists()
        if is_owner:
            user_role = "Owner"

    return JsonResponse({
        "success": True,
        "college_demo_mode": college_demo_mode,
        "architecture": "Loopback Multi-Origin Host Isolation (RFC 6265)",
        "current_host": request.get_host(),
        "demo_role_hint": getattr(request, "demo_role_hint", "General"),
        "origins": {
            "owner": "http://localhost:8000",
            "manager": "http://127.0.0.1:8000",
            "cashier": "http://127.0.0.2:8000",
        },
        "session": {
            "authenticated": request.user.is_authenticated,
            "username": request.user.username if request.user.is_authenticated else None,
            "role": user_role,
            "is_owner": is_owner,
        },
        "browser_cookie_model": {
            "localhost": "Cookie Jar A (Owner Tab)",
            "127.0.0.1": "Cookie Jar B (Manager Tab)",
            "127.0.0.2": "Cookie Jar C (Cashier Tab)",
        }
    })
