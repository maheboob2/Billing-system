from django.shortcuts import render, get_object_or_404
from django.db.models import Q
from accounts.tenancy import company_required, role_required
from .models import Sale, Invoice


@company_required
@role_required(["Admin", "Manager", "Cashier"])
def sales(request):
    company = request.company
    sales_list = (
        Sale.objects.filter(company=company)
        .select_related("customer", "cashier", "invoice")
        .prefetch_related("items")
        .order_by("-created_at")
    )
    return render(
        request,
        "sales/sales.html",
        {
            "company": company,
            "sales": sales_list,
            "user_role": request.user_role,
        }
    )


@company_required
@role_required(["Admin", "Manager", "Cashier"])
def invoice_detail(request, invoice_number):
    company = request.company
    invoice = get_object_or_404(
        Invoice.objects.select_related("sale").prefetch_related("sale__items"),
        invoice_number=invoice_number,
        company=company
    )
    return render(
        request,
        "sales/invoice_detail.html",
        {
            "company": company,
            "invoice": invoice,
            "sale": invoice.sale,
            "items": invoice.sale.items.all(),
        }
    )


@company_required
@role_required(["Admin", "Manager", "Cashier"])
def cart(request):
    """POS Cart / Checkout page."""
    return render(
        request,
        "sales/cart.html",
        {
            "company": request.company,
            "user_role": request.user_role,
        }
    )


def pos_phone_scanner(request):
    """
    Mobile companion scanner client.
    Pairs with an active POS terminal session via session token or 6-character pairing code.
    """
    from sales.models import POSScannerSession
    from django.utils import timezone

    session_token = request.GET.get("session", "").strip()
    code = request.GET.get("code", "").strip().upper()
    session = None
    company = None

    if session_token:
        session = POSScannerSession.objects.filter(
            session_token=session_token,
            status="ACTIVE",
            expires_at__gt=timezone.now()
        ).select_related("company", "user").first()
    elif code:
        session = POSScannerSession.objects.filter(
            pairing_code=code,
            status="ACTIVE",
            expires_at__gt=timezone.now()
        ).select_related("company", "user").first()

    if session:
        company = session.company

    return render(
        request,
        "sales/phone_scanner.html",
        {
            "session": session,
            "company": company,
            "terminal_id": session.device_info if session else "POS-001",
        }
    )


@company_required
@role_required(["Admin", "Manager", "Cashier"])
def returns_view(request):
    """
    Returns and Refund Management Screen.
    Cashiers can submit return requests; Managers/Admin can review and approve/reject.
    """
    from sales.models import ReturnRequest
    company = request.company
    returns_qs = ReturnRequest.objects.filter(company=company).select_related(
        "sale", "invoice", "customer", "created_by", "approved_by"
    ).prefetch_related("items__product").order_by("-created_at")

    status_filter = request.GET.get("status", "").strip().upper()
    if status_filter in ["PENDING", "APPROVED", "REJECTED", "COMPLETED"]:
        returns_qs = returns_qs.filter(status=status_filter)

    search = request.GET.get("search", "").strip()
    if search:
        returns_qs = returns_qs.filter(
            Q(return_number__icontains=search) |
            Q(sale__sale_number__icontains=search) |
            Q(customer__name__icontains=search)
        )

    return render(
        request,
        "sales/returns.html",
        {
            "company": company,
            "returns": returns_qs,
            "user_role": request.user_role,
            "is_manager": request.user_role in ["Admin", "Manager"],
            "selected_status": status_filter,
            "search": search,
        }
    )

