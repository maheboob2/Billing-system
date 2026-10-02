from django.shortcuts import render,redirect,get_object_or_404
from django.db import transaction
from django.db.models import F
from .models import Purchase
from .forms import PurchaseForm, PurchaseItemFormSet
from django.views.decorators.http import require_POST
from django.db.models import Count, Q, Sum, F, DecimalField, ExpressionWrapper
from django.shortcuts import render

# Create your views here.



from django.db.models import Count, Q, Sum, F, DecimalField, ExpressionWrapper
from django.shortcuts import render

def purchase_list(request):
    company = request.user.owned_companies

    purchases = (
        Purchase.objects.filter(company=company)
        .select_related("supplier")
        .prefetch_related("items__product")
        .order_by("-purchase_date", "-id")
    )

    search = request.GET.get("search", "").strip()
    status = request.GET.get("status", "").strip()
    start_date = request.GET.get("start_date", "").strip()
    end_date = request.GET.get("end_date", "").strip()

    if search:
        purchases = purchases.filter(
            Q(invoice_number__icontains=search) |
            Q(supplier__name__icontains=search)
        )

    if status in ["DRAFT", "RECEIVED"]:
        purchases = purchases.filter(status=status)

    if start_date:
        purchases = purchases.filter(purchase_date__gte=start_date)

    if end_date:
        purchases = purchases.filter(purchase_date__lte=end_date)

    item_total = ExpressionWrapper(
        F("items__quantity") * F("items__purchase_price") *
        (1 + F("items__gst_percentage") / 100),
        output_field=DecimalField(max_digits=20, decimal_places=2)
    )

    summary = purchases.aggregate(
        total_count=Count("id", distinct=True),
        draft_count=Count(
            "id", filter=Q(status="DRAFT"), distinct=True
        ),
        received_count=Count(
            "id", filter=Q(status="RECEIVED"), distinct=True
        ),
        total_value=Sum(item_total),
    )

    return render(
        request,
        "purchases/purchase_list.html",
        {
            "purchases": purchases,
            "search": search,
            "selected_status": status,
            "start_date": start_date,
            "end_date": end_date,
            "summary": summary,
        }
    )


def add_purchase(request):
    company = request.user.owned_companies

    if request.method == "POST":
        purchase_form = PurchaseForm(
            request.POST,
            company=company
        )

        item_formset = PurchaseItemFormSet(
            request.POST,
            instance=Purchase(),
            prefix="items",
            form_kwargs={"company": company}
        )

        if purchase_form.is_valid() and item_formset.is_valid():
            has_items = any(
                form.cleaned_data
                and not form.cleaned_data.get("DELETE", False)
                and form.cleaned_data.get("product")
                for form in item_formset.forms
            )

            if has_items:
                with transaction.atomic():
                    purchase = purchase_form.save(commit=False)
                    purchase.company = company
                    purchase.save()

                    item_formset.instance = purchase
                    item_formset.save()

                return redirect("purchase_list")
            else:
                  item_formset._non_form_errors = item_formset.error_class([
        "Add at least one product to the purchase."
    ])
                

    else:
        purchase_form = PurchaseForm(company=company)
        item_formset = PurchaseItemFormSet(
            instance=Purchase(),
            prefix="items",
            form_kwargs={"company": company}
        )

    return render(
        request,
        "purchases/add_purchase.html",
        {
            "purchase_form": purchase_form,
            "item_formset": item_formset,
        }
    )


@require_POST
def receive_purchase(request, purchase_id):
    company = request.user.owned_companies

    with transaction.atomic():
        purchase = get_object_or_404(
            Purchase.objects.select_for_update(),
            id=purchase_id,
            company=company
        )

        if purchase.status == "RECEIVED":
            return redirect("purchase_list")

        items = list(purchase.items.select_related("product"))

        if not items:
            return redirect("purchase_list")

        if purchase.supplier.company_id != company.id:
            return redirect("purchase_list")

        for item in items:
            if item.product.company_id != company.id:
                return redirect("purchase_list")

            updated = item.product.__class__.objects.filter(
                id=item.product_id,
                company=company
            ).update(
                current_stock=F("current_stock") + item.quantity
            )

            if updated != 1:
                return redirect("purchase_list")

        purchase.status = "RECEIVED"
        purchase.save(update_fields=["status"])

    return redirect("purchase_list")

def purchase_detail(request, purchase_id):
    company = request.user.owned_companies

    purchase = get_object_or_404(
        Purchase.objects
        .filter(company=company)
        .select_related("supplier")
        .prefetch_related("items__product"),
        id=purchase_id
    )

    return render(
        request,
        "purchases/purchase_detail.html",
        {"purchase": purchase}
    )


def edit_purchase(request, purchase_id):
    company = request.user.owned_companies

    purchase = get_object_or_404(
        Purchase,
        id=purchase_id,
        company=company,
        status="DRAFT"
    )

    if request.method == "POST":
        purchase_form = PurchaseForm(
            request.POST,
            instance=purchase,
            company=company
        )

        item_formset = PurchaseItemFormSet(
            request.POST,
            instance=purchase,
            prefix="items",
            form_kwargs={"company": company}
        )

        if purchase_form.is_valid() and item_formset.is_valid():
            with transaction.atomic():
                purchase_form.save()
                item_formset.save()

            return redirect("purchase_detail", purchase_id=purchase.id)

    else:
        purchase_form = PurchaseForm(
            instance=purchase,
            company=company
        )

        item_formset = PurchaseItemFormSet(
            instance=purchase,
            prefix="items",
            form_kwargs={"company": company}
        )

    return render(
        request,
        "purchases/add_purchase.html",
        {
            "purchase_form": purchase_form,
            "item_formset": item_formset,
            "is_edit": True,
            "purchase": purchase
        }
    )
    
