from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.models import User
from django.db import transaction, IntegrityError
from django.db.models import Q
from django.http import HttpResponse
from accounts.tenancy import company_required, role_required
from .models import user_registration


@company_required
@role_required(["Admin"])
def employee(request):
    company = request.company
    error = None

    if request.method == "POST":
        # User details
        username = request.POST.get("username", "").strip()
        first_name = request.POST.get("first_name", "").strip()
        last_name = request.POST.get("last_name", "").strip()
        email = request.POST.get("email", "").strip()
        password = request.POST.get("password", "")
        confirm_pass = request.POST.get("confirm_pass", "")

        # Employee details
        profile_pic = request.FILES.get("profile_pic")
        gender = request.POST.get("Gender", "Male")
        phone = request.POST.get("Phone", "").strip()
        alternate_phone = request.POST.get("Alternate_phone", "").strip()
        address = request.POST.get("address", "").strip()
        employee_id = request.POST.get("employee_id", "").strip()
        role = request.POST.get("Role", "Worker").strip().capitalize()
        joining_date = request.POST.get("joining_date")
        monthaly_salary = request.POST.get("monthaly_salary", 0)
        payment = request.POST.get("payment", "Paid")
        status = request.POST.get("status", "active").lower()

        if password != confirm_pass:
            return render(request, "employee/employee.html", {"error": "Passwords do not match.", "company": company})

        if User.objects.filter(username=username).exists():
            return render(request, "employee/employee.html", {"error": f"Username '{username}' is already taken.", "company": company})

        if user_registration.objects.filter(employee_id=employee_id).exists():
            return render(request, "employee/employee.html", {"error": f"Employee ID '{employee_id}' is already registered.", "company": company})

        try:
            with transaction.atomic():
                user = User.objects.create_user(
                    username=username,
                    first_name=first_name,
                    last_name=last_name,
                    email=email,
                    password=password,
                )

                user_registration.objects.create(
                    user=user,
                    company=company,
                    profile_pic=profile_pic,
                    Gender=gender,
                    Phone=phone,
                    Alternate_phone=alternate_phone,
                    address=address,
                    employee_id=employee_id,
                    Role=role,
                    joining_date=joining_date,
                    monthaly_salary=monthaly_salary,
                    payment=payment,
                    status=status,
                )

            return redirect("view_employee")
        except IntegrityError as e:
            return render(request, "employee/employee.html", {"error": f"Registration failed: {str(e)}", "company": company})

    return render(request, "employee/employee.html", {"company": company, "error": error})


@company_required
@role_required(["Admin"])
def view_employees(request):
    company = request.company
    search = request.GET.get("search", "").strip()

    employees = user_registration.objects.filter(
        company=company
    ).select_related("user", "company")

    if search:
        employees = employees.filter(
            Q(employee_id__icontains=search) |
            Q(user__username__icontains=search) |
            Q(user__first_name__icontains=search) |
            Q(user__last_name__icontains=search)
        )

    total_employees = employees.count()
    active_employees = employees.filter(status__iexact="active").count()
    inactive_employees = employees.filter(status__iexact="inactive").count()
    total_monthly_salary = sum(emp.monthaly_salary or 0 for emp in employees)

    context = {
        'company': company,
        'employees': employees,
        'Total_Employees': total_employees,
        'Active_Employees': active_employees,
        'In_active_Employees': inactive_employees,
        'Total_monthly_salary': total_monthly_salary,
        'search': search,
        'user_role': request.user_role,
    }

    return render(request, "employee/view_employee.html", context)


@company_required
@role_required(["Admin"])
def edit_employee(request, employee_id):
    company = request.company
    employee_obj = get_object_or_404(
        user_registration,
        employee_id=employee_id,
        company=company
    )

    if request.method == "POST":
        new_username = request.POST.get("username", "").strip()
        if new_username and new_username != employee_obj.user.username:
            if User.objects.filter(username=new_username).exclude(pk=employee_obj.user.pk).exists():
                return render(request, "employee/edit_employee.html", {
                    "employee": employee_obj,
                    "company": company,
                    "error": f"Username '{new_username}' is already taken."
                })
            employee_obj.user.username = new_username

        employee_obj.user.first_name = request.POST.get("first_name", "")
        employee_obj.user.last_name = request.POST.get("last_name", "")
        employee_obj.user.email = request.POST.get("email", "")

        new_emp_id = request.POST.get("employee_id", "").strip()
        if new_emp_id and new_emp_id != employee_obj.employee_id:
            if user_registration.objects.filter(employee_id=new_emp_id).exclude(pk=employee_obj.pk).exists():
                return render(request, "employee/edit_employee.html", {
                    "employee": employee_obj,
                    "company": company,
                    "error": f"Employee ID '{new_emp_id}' is already registered."
                })
            employee_obj.employee_id = new_emp_id

        employee_obj.Phone = request.POST.get("Phone", "")
        employee_obj.Alternate_phone = request.POST.get("Alternate_phone", "")
        employee_obj.address = request.POST.get("address", "")
        employee_obj.Gender = request.POST.get("Gender", "Male")
        employee_obj.Role = request.POST.get("Role", "Worker").strip().capitalize()
        employee_obj.joining_date = request.POST.get("joining_date")
        employee_obj.monthaly_salary = request.POST.get("monthaly_salary", 0)
        employee_obj.payment = request.POST.get("payment", "Paid")
        employee_obj.status = request.POST.get("status", "active").lower()

        with transaction.atomic():
            employee_obj.user.save()
            employee_obj.save()

        return redirect("view_employee")

    return render(
        request,
        "employee/edit_employee.html",
        {
            "employee": employee_obj,
            "company": company,
        }
    )


@company_required
@role_required(["Admin"])
def delete_employee(request, employee_id):
    company = request.company
    employee_obj = get_object_or_404(
        user_registration,
        employee_id=employee_id,
        company=company
    )
    target_user = employee_obj.user

    if request.method == "POST":
        with transaction.atomic():
            employee_obj.delete()
            target_user.delete()
        return redirect("view_employee")

    return render(
        request,
        "employee/delete_employee.html",
        {
            "employee": employee_obj,
            "user": target_user,
            "company": company,
        }
    )
