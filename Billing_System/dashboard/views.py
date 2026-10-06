from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.db.models import F
from inventory.models import Product, Supplier
from django.utils import timezone
from purchases.models import Purchase
from django.db.models import Sum, F, DecimalField, ExpressionWrapper

# Create your views here.
@login_required
def dashboard(request):
    company = request.user.owned_companies

    total_products = Product.objects.filter(
        company=company
    ).count()

    low_stock_products = Product.objects.filter(
        company=company,
        current_stock__gt=0,
        current_stock__lte=F("minimum_stock")
    ).count()

    out_of_stock_products = Product.objects.filter(
        company=company,
        current_stock__lte=0
    ).count()

    total_suppliers = Supplier.objects.filter(
        company=company
    ).count()

    today = timezone.localdate()

    today_purchases = Purchase.objects.filter(
        company=company,
        status="RECEIVED",
        purchase_date=today
    )

    today_purchase_count = today_purchases.count()

    today_purchase_value = today_purchases.aggregate(
        total=Sum(
            ExpressionWrapper(
                F("items__quantity") * F("items__purchase_price") *
                (1 + F("items__gst_percentage") / 100),
                output_field=DecimalField(max_digits=20, decimal_places=2)
            )
        )
    )["total"] or 0

    low_stock_items = Product.objects.filter(
    company=company,
    current_stock__gt=0,
    current_stock__lte=F("minimum_stock")
).order_by("current_stock")[:10]

    recent_purchases = (
    Purchase.objects.filter(company=company)
    .select_related("supplier")
    .order_by("-purchase_date", "-id")[:5]
)



    context = {
        "total_products": total_products,
        "low_stock_products": low_stock_products,
        "out_of_stock_products": out_of_stock_products,
        "total_suppliers": total_suppliers,
        "today_purchase_count": today_purchase_count,
        "today_purchase_value": today_purchase_value,
        "low_stock_items": low_stock_items,
        "recent_purchases": recent_purchases,
    }

    return render(request, "dashboard/dashboard.html", context)

@login_required

def manager_dashboard(request):
     return render(request,"dashboard/manager_dash.html")

@login_required

def cashier_dashboard(request):
     return render(request,"dashboard/cashier_dash.html")








