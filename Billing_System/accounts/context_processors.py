"""
Context processors for Tenancy and Role information across all templates.
"""
from django.conf import settings
from accounts.tenancy import get_user_company, get_user_role


def tenancy_context(request):
    """
    Injects `company`, `user_role`, `user_display_role`, and `is_owner` into template context
    for all authenticated requests.
    """
    college_demo_mode = getattr(settings, "COLLEGE_DEMO_MODE", False)
    if not request.user.is_authenticated:
        return {
            "company": None,
            "user_role": None,
            "user_display_role": None,
            "is_owner": False,
            "college_demo_mode": college_demo_mode,
        }

    company = getattr(request, "company", None) or get_user_company(request.user)
    user_role = getattr(request, "user_role", None) or get_user_role(request.user)

    # Determine whether the authenticated user is the actual business owner
    is_owner = bool(
        company and hasattr(company, "owner_id") and company.owner_id == request.user.id
    )

    if is_owner:
        user_display_role = "Owner"
    elif user_role in ["Manager", "Cashier", "Worker"]:
        user_display_role = user_role
    elif user_role == "Admin":
        user_display_role = "Owner" if is_owner else "Manager"
    else:
        user_display_role = user_role or "Worker"

    cloud_sync_info = None
    if company:
        try:
            from dashboard.sync_service import SyncService
            cloud_sync_info = SyncService.get_sync_status(company)
        except Exception:
            cloud_sync_info = None

    return {
        "company": company,
        "user_role": user_role,
        "user_display_role": user_display_role,
        "is_owner": is_owner,
        "cloud_sync_info": cloud_sync_info,
        "college_demo_mode": college_demo_mode,
    }

