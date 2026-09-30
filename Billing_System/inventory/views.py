from django.shortcuts import render,redirect
from .models import Category,Supplier
from .forms import CategoryForm,SupplierForm


# Create your views here.

def category_list(request):
    company = request.user.owned_companies

    categories = Category.objects.filter(
        company=company
    )

    return render(
       request,
       "inventory/index.html", 
       {
            "categories": categories,
        }


    )

def add_category(request):

    company = request.user.owned_companies

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
        }
    )

def edit_category(request, id):

    company = request.user.owned_companies

    category = Category.objects.get(
        id=id,
        company=company
    )

    if request.method == "POST":

        form = CategoryForm(
            request.POST,
            instance=category
        )

        if form.is_valid():

            form.save()

            return redirect("category")

    else:

        form = CategoryForm(
            instance=category
        )

    return render(
        request,
        "inventory/edit_category.html",
        {
            "form": form,
            "category": category,
        }
    )

def deactivate_category(request, id):

    company = request.user.owned_companies

    category = Category.objects.get(
        id=id,
        company=company
    )

    category.status = False

    category.save()

    return redirect("category")

def add_supplier(request):
    company = request.user.owned_companies

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
        }
    )
def supplier_list(request):
    company = request.user.owned_companies

    suppliers = Supplier.objects.filter(
        company=company
    )

    return render(
        request,
        "inventory/supplier_list.html",
        {
            "suppliers": suppliers,
        }
    )
def edit_supplier(request, id):
    company = request.user.owned_companies

    supplier = Supplier.objects.get(
        id=id,
        company=company
    )

    if request.method == "POST":
        form = SupplierForm(
            request.POST,
            instance=supplier
        )

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
        }
    )

def deactivate_supplier(request, id):
    company = request.user.owned_companies

    supplier = Supplier.objects.get(
        id=id,
        company=company
    )

    supplier.status = False
    supplier.save()

    return redirect("supplier")
          