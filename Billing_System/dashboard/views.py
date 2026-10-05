from decimal import Decimal
from django.shortcuts import render
from django.db.models import F, Sum, Count, Q
from django.utils import timezone
from accounts.tenancy import company_required, role_required
from inventory.models import Product, Supplier
from sales.models import Sale, Customer, SaleItem
from purchases.models import Purchase


@role_required(["Admin", "Owner", "Manager"])
def dashboard(request):
    company = request.company
    today = timezone.now().date()
    current_year = today.year
    current_month = today.month

    # Inventory metrics
    total_products = Product.objects.filter(company=company).count()
    low_stock_products = Product.objects.filter(
        company=company,
        current_stock__gt=0,
        current_stock__lte=F("minimum_stock"),
    ).count()
    out_of_stock_products = Product.objects.filter(
        company=company,
        current_stock__lte=0,
    ).count()
    total_suppliers = Supplier.objects.filter(company=company).count()

    # Sales metrics
    completed_sales = Sale.objects.filter(company=company, sale_status="COMPLETED")

    today_sales_qs = completed_sales.filter(created_at__date=today)
    today_sales_amount = today_sales_qs.aggregate(total=Sum("grand_total"))["total"] or Decimal("0.00")
    today_sales_count = today_sales_qs.count()

    month_sales_qs = completed_sales.filter(
        created_at__year=current_year,
        created_at__month=current_month
    )
    this_month_sales_amount = month_sales_qs.aggregate(total=Sum("grand_total"))["total"] or Decimal("0.00")

    total_sales_amount = completed_sales.aggregate(total=Sum("grand_total"))["total"] or Decimal("0.00")
    total_sales_count = completed_sales.count()

    total_customers = Customer.objects.filter(company=company).count()

    # Purchase metrics
    purchases_qs = Purchase.objects.filter(company=company, status="RECEIVED")
    today_purchases_qs = purchases_qs.filter(purchase_date=today)

    from django.db.models import DecimalField, ExpressionWrapper
    purchase_item_total = ExpressionWrapper(
        F("items__quantity") * F("items__purchase_price") * (1 + F("items__gst_percentage") / 100),
        output_field=DecimalField(max_digits=20, decimal_places=2)
    )

    today_purchases_amount = today_purchases_qs.aggregate(total=Sum(purchase_item_total))["total"] or Decimal("0.00")

    # Financial / Profit calculation: Revenue (Total Sales) - Cost of Goods Sold
    total_revenue = total_sales_amount
    cost_of_goods_sold = (
        SaleItem.objects.filter(sale__company=company, sale__sale_status="COMPLETED")
        .aggregate(
            total_cogs=Sum(
                ExpressionWrapper(
                    F("cost_price") * F("quantity"),
                    output_field=DecimalField(max_digits=20, decimal_places=2)
                )
            )
        )["total_cogs"]
        or Decimal("0.00")
    )
    total_profit = total_revenue - cost_of_goods_sold

    # Recent sales
    recent_sales = completed_sales.select_related("customer", "cashier").order_by("-created_at")[:5]

    context = {
        "company": company,
        "user_role": request.user_role,
        "total_products": total_products,
        "low_stock_products": low_stock_products,
        "out_of_stock_products": out_of_stock_products,
        "total_suppliers": total_suppliers,
        "today_sales_amount": today_sales_amount,
        "today_sales_count": today_sales_count,
        "this_month_sales_amount": this_month_sales_amount,
        "total_sales_amount": total_sales_amount,
        "total_sales_count": total_sales_count,
        "total_customers": total_customers,
        "today_purchases_amount": today_purchases_amount,
        "total_revenue": total_revenue,
        "total_profit": total_profit,
        "recent_sales": recent_sales,
    }

    return render(request, "dashboard/dashboard.html", context)


@role_required(["Admin", "Owner", "Manager"])
def manager_dashboard(request):
    return dashboard(request)


@role_required(["Admin", "Owner", "Manager", "Cashier"])
def cashier_dashboard(request):
    return render(
        request,
        "dashboard/cashier_Dash.html",
        {
            "company": request.company,
            "user_role": request.user_role,
        },
    )

