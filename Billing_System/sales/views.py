
from django.shortcuts import render, redirect,get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import transaction

from .models import Sale
from .forms import SaleForm, SaleItemFormSet
from inventory.models import Product
from django.db.models import Q

from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.contrib.auth.decorators import login_required
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
)
from .models import Customer
from .forms import CustomerForm


@login_required
def add_sale(request):
    company = request.user.owned_companies

    if request.method == "POST":
        sale_form = SaleForm(request.POST, company=company)

        sale = Sale()
        item_formset = SaleItemFormSet(
            request.POST,
            instance=sale,
            prefix="items",
            form_kwargs={"company": company},
        )

        if sale_form.is_valid() and item_formset.is_valid():
            with transaction.atomic():
                sale = sale_form.save(commit=False)
                sale.company = company
                sale.save()

                item_formset.instance = sale
                item_formset.save()

            messages.success(request, "Sale draft saved successfully.")
            return redirect("sales:sale_detail", pk=sale.pk)

    else:
        sale_form = SaleForm(company=company)
        item_formset = SaleItemFormSet(
            instance=Sale(),
            prefix="items",
            form_kwargs={"company": company},
        )

    return render(request, "sales/add_sale.html", {
        "sale_form": sale_form,
        "item_formset": item_formset,
    })

@login_required
def sale_detail(request, pk):
    company = request.user.owned_companies

    sale = get_object_or_404(
        Sale.objects.prefetch_related("items__product"),
        pk=pk,
        company=company,
    )

    return render(request, "sales/sale_detail.html", {
        "sale": sale,
    })

@login_required
@transaction.atomic
def complete_sale(request, pk):
    if request.method != "POST":
        return redirect("sales:sale_detail", pk=pk)

    company = request.user.owned_companies

    with transaction.atomic():
        sale = get_object_or_404(
            Sale.objects.select_for_update(),
            pk=pk,
            company=company,
        )

        if sale.status != "DRAFT":
            messages.error(request, "This sale is not a draft.")
            return redirect("sales:sale_detail", pk=pk)

        items = list(sale.items.all())

        if not items:
            messages.error(request, "Add at least one product.")
            return redirect("sales:sale_detail", pk=pk)

        # Combine quantities if a product is listed more than once.
        required_stock = {}
        for item in items:
            required_stock[item.product_id] = (
                required_stock.get(item.product_id, 0)
                + item.quantity
            )

        # Lock products while checking and updating stock.
        products = Product.objects.select_for_update().filter(
            company=company,
            pk__in=required_stock.keys(),
        )
        product_map = {p.pk: p for p in products}

        if len(product_map) != len(required_stock):
            messages.error(request, "A product is unavailable.")
            return redirect("sales:sale_detail", pk=pk)

        # Validate every product before changing stock.
        for product_id, quantity in required_stock.items():
            product = product_map[product_id]

            if product.current_stock < quantity:
                messages.error(
                    request,
                    f"Insufficient stock for {product.name}. "
                    f"Available: {product.current_stock}, "
                    f"required: {quantity}.",
                )
                return redirect("sales:sale_detail", pk=pk)

        # Deduct stock only after all checks pass.
        for product_id, quantity in required_stock.items():
            product = product_map[product_id]
            product.current_stock -= quantity
            product.save(update_fields=["current_stock"])

        sale.status = "COMPLETED"
        sale.save(update_fields=["status"])

    messages.success(request, "Bill completed successfully.")
    return redirect("sales:sale_detail", pk=pk)


@login_required
def sale_list(request):
    company = request.user.owned_companies

    sales = Sale.objects.filter(
        company=company
    ).select_related(
        "customer"
    ).order_by("-sale_date", "-id")

    search = request.GET.get("search", "").strip()
    status = request.GET.get("status", "")
    date_from = request.GET.get("date_from", "")
    date_to = request.GET.get("date_to", "")

    if search:
        sales = sales.filter(
            Q(invoice_number__icontains=search) |
            Q(customer__name__icontains=search)
        )

    if status:
        sales = sales.filter(status=status)

    if date_from:
        sales = sales.filter(sale_date__gte=date_from)

    if date_to:
        sales = sales.filter(sale_date__lte=date_to)

    context = {
        "sales": sales,
        "search": search,
        "selected_status": status,
        "date_from": date_from,
        "date_to": date_to,
    }

    return render(request, "sales/sale_list.html", context)




@login_required
def download_invoice_pdf(request, pk):
    company = request.user.owned_companies

    sale = get_object_or_404(
        Sale.objects.select_related("company", "customer")
        .prefetch_related("items__product"),
        pk=pk,
        company=company,
        status="COMPLETED",
    )

    response = HttpResponse(content_type="application/pdf")
    response["Content-Disposition"] = (
        f'attachment; filename="invoice_{sale.pk}.pdf"'
    )

    doc = SimpleDocTemplate(
        response,
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
    )

    styles = getSampleStyleSheet()
    elements = []

    def company_field(*field_names):
        for field in field_names:
            value = getattr(sale.company, field, None)
            if value:
                return str(value)
        return ""

    def safe(value):
        from django.utils.html import escape
        return escape(str(value or ""))

    # Company heading
    company_name = company_field("company_name", "name") or str(sale.company)
    elements.append(Paragraph(safe(company_name), styles["Title"]))

    company_address = company_field("address")
    company_phone = company_field("phone")
    company_gst = company_field("GST", "gst_number", "gstin")

    if company_address:
        elements.append(Paragraph(safe(company_address), styles["Normal"]))
    if company_phone:
        elements.append(Paragraph(f"Phone: {safe(company_phone)}", styles["Normal"]))
    if company_gst:
        elements.append(Paragraph(f"GSTIN: {safe(company_gst)}", styles["Normal"]))

    elements.append(Spacer(1, 8 * mm))
    elements.append(Paragraph("TAX INVOICE", styles["Heading1"]))
    elements.append(Spacer(1, 3 * mm))

    customer_name = sale.customer.name if sale.customer else "Walk-in Customer"
    customer_phone = sale.customer.phone if sale.customer else ""
    customer_address = sale.customer.address if sale.customer else ""

    invoice_info = [
        ["Invoice Number:", safe(sale.invoice_number),
         "Date:", safe(sale.sale_date)],
        ["Customer:", safe(customer_name),
         "Payment:", safe(sale.get_payment_method_display())],
        ["Phone:", safe(customer_phone),
         "Status:", safe(sale.get_status_display())],
    ]

    if customer_address:
        invoice_info.append(["Address:", safe(customer_address), "", ""])

    info_table = Table(invoice_info, colWidths=[28*mm, 62*mm, 25*mm, 45*mm])
    info_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    elements.append(info_table)
    elements.append(Spacer(1, 8 * mm))

    # Product rows
    rows = [[
        "No.", "Product", "Qty", "Price (Rs.)",
        "GST %", "GST (Rs.)", "Total (Rs.)"
    ]]

    for index, item in enumerate(sale.items.all(), start=1):
        rows.append([
            str(index),
            safe(item.product.name),
            str(item.quantity),
            f"{item.selling_price:.2f}",
            f"{item.gst_percentage:.2f}%",
            f"{item.gst_amount:.2f}",
            f"{item.total_amount:.2f}",
        ])

    product_table = Table(
        rows,
        repeatRows=1,
        colWidths=[10*mm, 48*mm, 16*mm, 25*mm, 18*mm, 24*mm, 25*mm],
    )
    product_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#26364a")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    elements.append(product_table)
    elements.append(Spacer(1, 8 * mm))

    # Invoice totals
    totals = [
        ["Subtotal:", f"Rs. {sale.subtotal:.2f}"],
        ["GST:", f"Rs. {sale.gst_amount:.2f}"],
        ["Grand Total:", f"Rs. {sale.total_amount:.2f}"],
    ]

    total_table = Table(totals, colWidths=[120*mm, 45*mm], hAlign="RIGHT")
    total_table.setStyle(TableStyle([
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("FONTNAME", (0, 2), (-1, 2), "Helvetica-Bold"),
        ("LINEABOVE", (0, 2), (-1, 2), 1, colors.black),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    elements.append(total_table)
    elements.append(Spacer(1, 12 * mm))
    elements.append(Paragraph("Thank you for your business!", styles["Normal"]))

    doc.build(elements)
    return response



@login_required
def customer_list(request):
    company = request.user.owned_companies

    customers = Customer.objects.filter(
        company=company
    ).order_by("-id")

    search = request.GET.get("search", "").strip()

    if search:
        customers = customers.filter(
            Q(name__icontains=search) |
            Q(phone__icontains=search) |
            Q(email__icontains=search)
        )

    context = {
        "customers": customers,
        "search": search,
    }

    return render(request, "sales/customer_list.html", context)


@login_required
def add_customer(request):
    company = request.user.owned_companies

    if request.method == "POST":
        form = CustomerForm(request.POST)

        if form.is_valid():
            customer = form.save(commit=False)
            customer.company = company
            customer.save()

            messages.success(request, "Customer added successfully.")
            return redirect("sales:customer_list")
    else:
        form = CustomerForm()

    return render(
        request,
        "sales/add_customer.html",
        {"form": form},
    )  


@login_required
def edit_customer(request, pk):
    company = request.user.owned_companies

    customer = get_object_or_404(
        Customer,
        pk=pk,
        company=company
    )

    if request.method == "POST":
        form = CustomerForm(request.POST, instance=customer)

        if form.is_valid():
            form.save()
            messages.success(request, "Customer updated successfully.")
            return redirect("sales:customer_list")
    else:
        form = CustomerForm(instance=customer)

    return render(
        request,
        "sales/edit_customer.html",
        {"form": form, "customer": customer},
    )

@login_required
def customer_purchase_history(request, pk):
    company = request.user.owned_companies

    customer = get_object_or_404(
        Customer,
        pk=pk,
        company=company
    )

    sales = Sale.objects.filter(
        company=company,
        customer=customer
    ).order_by("-sale_date", "-id")

    total_purchases = sales.count()

    context = {
        "customer": customer,
        "sales": sales,
        "total_purchases": total_purchases,
    }

    return render(
        request,
        "sales/customer_purchase_history.html",
        context
    )




@login_required
def delete_customer(request, pk):
    company = request.user.owned_companies

    customer = get_object_or_404(
        Customer,
        pk=pk,
        company=company
    )

    if request.method == "POST":
        if customer.sales.exists():
            messages.error(
                request,
                "This customer has sales records and cannot be deleted."
            )
            return redirect("sales:customer_list")

        customer.delete()
        messages.success(request, "Customer deleted successfully.")
        return redirect("sales:customer_list")

    return render(
        request,
        "sales/delete_customer.html",
        {"customer": customer},
    )    