from django.shortcuts import render, get_object_or_404
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
