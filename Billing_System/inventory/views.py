from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.http import require_http_methods
from accounts.tenancy import company_required, role_required
from .models import Category, Supplier, Product
from .forms import CategoryForm, SupplierForm, ProductForm


@company_required
@role_required(["Admin", "Manager"])
def category_list(request):
    company = request.company
    categories = Category.objects.filter(company=company)
    return render(
        request,
        "inventory/index.html",
        {
            "categories": categories,
            "company": company,
            "user_role": request.user_role,
        }
    )


@company_required
@role_required(["Admin", "Manager"])
def add_category(request):
    company = request.company
    if request.method == "POST":
        form = CategoryForm(request.POST)
        if form.is_valid():
            category = form.save(commit=False)
            category.company = company
            category.save()
            return redirect("category")
    else:
        form = CategoryForm()

    return render(
        request,
        "inventory/add_category.html",
        {
            "form": form,
            "company": company,
        }
    )


@company_required
@role_required(["Admin", "Manager"])
def edit_category(request, id):
    company = request.company
    category = get_object_or_404(Category, id=id, company=company)

    if request.method == "POST":
        form = CategoryForm(request.POST, instance=category)
        if form.is_valid():
            form.save()
            return redirect("category")
    else:
        form = CategoryForm(instance=category)

    return render(
        request,
        "inventory/edit_category.html",
        {
            "form": form,
            "category": category,
            "company": company,
        }
    )


@company_required
@role_required(["Admin", "Manager"])
@require_http_methods(["GET", "POST"])
def deactivate_category(request, id):
    company = request.company
    category = get_object_or_404(Category, id=id, company=company)
    category.status = False
    category.save(update_fields=["status"])
    return redirect("category")


@company_required
@role_required(["Admin", "Manager"])
def supplier_list(request):
    company = request.company
    suppliers = Supplier.objects.filter(company=company)
    return render(
        request,
        "inventory/supplier_list.html",
        {
            "suppliers": suppliers,
            "company": company,
            "user_role": request.user_role,
        }
    )


@company_required
@role_required(["Admin", "Manager"])
def add_supplier(request):
    company = request.company
    if request.method == "POST":
        form = SupplierForm(request.POST)
        if form.is_valid():
            supplier = form.save(commit=False)
            supplier.company = company
            supplier.save()
            return redirect("supplier")
    else:
        form = SupplierForm()

    return render(
        request,
        "inventory/add_supplier.html",
        {
            "form": form,
            "company": company,
        }
    )


@company_required
@role_required(["Admin", "Manager"])
def edit_supplier(request, id):
    company = request.company
    supplier = get_object_or_404(Supplier, id=id, company=company)

    if request.method == "POST":
        form = SupplierForm(request.POST, instance=supplier)
        if form.is_valid():
            form.save()
            return redirect("supplier")
    else:
        form = SupplierForm(instance=supplier)

    return render(
        request,
        "inventory/edit_supplier.html",
        {
            "form": form,
            "supplier": supplier,
            "company": company,
        }
    )


@company_required
@role_required(["Admin", "Manager"])
@require_http_methods(["GET", "POST"])
def deactivate_supplier(request, id):
    company = request.company
    supplier = get_object_or_404(Supplier, id=id, company=company)
    supplier.status = False
    supplier.save(update_fields=["status"])
    return redirect("supplier")


@company_required
@role_required(["Admin", "Manager", "Worker", "Stock Manager"])
def product_list(request):
    company = request.company
    products = Product.objects.filter(company=company).select_related("category", "supplier")
    return render(
        request,
        "inventory/product_list.html",
        {
            "products": products,
            "company": company,
            "user_role": request.user_role,
        }
    )


@company_required
@role_required(["Admin", "Manager"])
def add_product(request):
    company = request.company
    if request.method == "POST":
        form = ProductForm(request.POST, company=company)
        if form.is_valid():
            product = form.save(commit=False)
            product.company = company
            product.save()
            return redirect("product")
    else:
        form = ProductForm(company=company)

    return render(
        request,
        "inventory/add_product.html",
        {
            "form": form,
            "company": company,
        }
    )


@company_required
@role_required(["Admin", "Manager"])
def edit_product(request, id):
    company = request.company
    product = get_object_or_404(Product, id=id, company=company)

    if request.method == "POST":
        form = ProductForm(request.POST, instance=product, company=company)
        if form.is_valid():
            form.save()
            return redirect("product")
    else:
        form = ProductForm(instance=product, company=company)

    return render(
        request,
        "inventory/edit_product.html",
        {
            "form": form,
            "product": product,
            "company": company,
        }
    )


@company_required
@role_required(["Admin", "Manager"])
@require_http_methods(["GET", "POST"])
def deactivate_product(request, id):
    company = request.company
    product = get_object_or_404(Product, id=id, company=company)
    product.status = False
    product.save(update_fields=["status"])
    return redirect("product")
