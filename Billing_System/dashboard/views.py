from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.db.models import F
from inventory.models import Product, Supplier

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

    context = {
        "total_products": total_products,
        "low_stock_products": low_stock_products,
        "out_of_stock_products": out_of_stock_products,
        "total_suppliers": total_suppliers,
    }

    return render(request, "dashboard/dashboard.html", context)

@login_required

def manager_dashboard(request):
     return render(request,"dashboard/manager_dash.html")

@login_required

def cashier_dashboard(request):
     return render(request,"dashboard/cashier_dash.html")






