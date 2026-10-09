from decimal import Decimal
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.models import User
from django.db import transaction, IntegrityError
from django.db.models import Q
from django.utils import timezone
from accounts.tenancy import company_required, role_required
from .models import user_registration, SalaryPayment


@company_required
@role_required(["Admin", "Manager"])
def employee(request):
    """
    Add Team Member.
    Both Owner/Admin and Manager can add team members.
    Owner can configure salary and payroll status.
    Manager cannot see or set salary amounts (defaults to 0 / Unpaid).
    """
    company = request.company
    is_owner = request.user_role in ["Admin", "Owner"]
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
        raw_role = request.POST.get("Role", "Worker").strip()
        
        # Normalize role
        if raw_role.lower() in ["stock manager", "stock", "inventory staff"]:
            role = "Stock Manager"
        elif raw_role.lower() == "manager":
            role = "Manager"
        elif raw_role.lower() == "cashier":
            role = "Cashier"
        else:
            role = "Worker"

        joining_date = request.POST.get("joining_date")
        if joining_date:
            from datetime import datetime
            try:
                parsed_join = datetime.strptime(str(joining_date), "%Y-%m-%d").date()
                from django.utils import timezone
                if parsed_join > timezone.now().date():
                    return render(request, "employee/employee.html", {
                        "error": "Joining date cannot be in the future.",
                        "company": company,
                        "is_owner": is_owner,
                        "user_role": request.user_role,
                    })
            except ValueError:
                pass

        status = request.POST.get("status", "active").lower()

        # Salary & Payroll (Owner only)
        if is_owner:
            try:
                monthaly_salary = Decimal(str(request.POST.get("monthaly_salary", 0) or 0))
            except Exception:
                monthaly_salary = Decimal("0.00")
            payment = request.POST.get("payment", "Paid")
        else:
            monthaly_salary = Decimal("0.00")
            payment = "Unpaid"

        if password != confirm_pass:
            return render(request, "employee/employee.html", {
                "error": "Passwords do not match.",
                "company": company,
                "is_owner": is_owner,
                "user_role": request.user_role,
            })

        if User.objects.filter(username=username).exists():
            return render(request, "employee/employee.html", {
                "error": f"Username '{username}' is already taken.",
                "company": company,
                "is_owner": is_owner,
                "user_role": request.user_role,
            })

        if user_registration.objects.filter(employee_id=employee_id).exists():
            return render(request, "employee/employee.html", {
                "error": f"Employee ID '{employee_id}' is already registered.",
                "company": company,
                "is_owner": is_owner,
                "user_role": request.user_role,
            })

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
            return render(request, "employee/employee.html", {
                "error": f"Registration failed: {str(e)}",
                "company": company,
                "is_owner": is_owner,
                "user_role": request.user_role,
            })

    return render(request, "employee/employee.html", {
        "company": company,
        "is_owner": is_owner,
        "user_role": request.user_role,
        "error": error,
    })


@company_required
@role_required(["Admin", "Manager"])
def view_employees(request):
    """
    Team Management Screen.
    Accessible to Owner/Admin and Manager.
    Owner sees team list + salary + payroll controls.
    Manager sees operational team members only (no salary values or payroll fields).
    """
    company = request.company
    is_owner = request.user_role in ["Admin", "Owner"]
    search = request.GET.get("search", "").strip()
    role_filter = request.GET.get("role", "").strip()
    status_filter = request.GET.get("status", "").strip()

    employees = user_registration.objects.filter(
        company=company
    ).select_related("user", "company")

    if search:
        employees = employees.filter(
            Q(employee_id__icontains=search) |
            Q(user__username__icontains=search) |
            Q(user__first_name__icontains=search) |
            Q(user__last_name__icontains=search) |
            Q(Phone__icontains=search)
        )

    if role_filter:
        employees = employees.filter(Role__iexact=role_filter)

    if status_filter:
        employees = employees.filter(status__iexact=status_filter)

    all_company_employees = user_registration.objects.filter(company=company)
    total_employees = all_company_employees.count()
    active_employees = all_company_employees.filter(status__iexact="active").count()
    inactive_employees = total_employees - active_employees

    cashiers_count = all_company_employees.filter(Role__iexact="Cashier").count()
    stock_count = all_company_employees.filter(Q(Role__icontains="Stock") | Q(Role__icontains="Inventory")).count()
    managers_count = all_company_employees.filter(Role__iexact="Manager").count()
    workers_count = all_company_employees.filter(Role__iexact="Worker").count()

    # Payroll metrics - strictly OWNER ONLY
    if is_owner:
        total_monthly_salary = sum(emp.monthaly_salary or Decimal("0.00") for emp in all_company_employees.filter(status__iexact="active"))
        paid_monthly_salary = sum(emp.monthaly_salary or Decimal("0.00") for emp in all_company_employees.filter(status__iexact="active", payment__iexact="Paid"))
        pending_monthly_salary = total_monthly_salary - paid_monthly_salary
    else:
        total_monthly_salary = None
        paid_monthly_salary = None
        pending_monthly_salary = None

    context = {
        'company': company,
        'employees': employees,
        'Total_Employees': total_employees,
        'Active_Employees': active_employees,
        'In_active_Employees': inactive_employees,
        'cashiers_count': cashiers_count,
        'stock_count': stock_count,
        'managers_count': managers_count,
        'workers_count': workers_count,
        'Total_monthly_salary': total_monthly_salary,
        'paid_monthly_salary': paid_monthly_salary,
        'pending_monthly_salary': pending_monthly_salary,
        'search': search,
        'role_filter': role_filter,
        'status_filter': status_filter,
        'user_role': request.user_role,
        'is_owner': is_owner,
    }

    return render(request, "employee/view_employee.html", context)


@company_required
@role_required(["Admin", "Manager"])
def edit_employee(request, employee_id):
    """
    Edit Team Member.
    Owner can edit all details including salary and payroll status.
    Manager can edit operational details, but cannot edit or see salary/payroll status.
    """
    company = request.company
    is_owner = request.user_role in ["Admin", "Owner"]
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
                    "is_owner": is_owner,
                    "user_role": request.user_role,
                    "error": f"Username '{new_username}' is already taken."
                })
            employee_obj.user.username = new_username

        employee_obj.user.first_name = request.POST.get("first_name", "").strip()
        employee_obj.user.last_name = request.POST.get("last_name", "").strip()
        employee_obj.user.email = request.POST.get("email", "").strip()

        new_emp_id = request.POST.get("employee_id", "").strip()
        if new_emp_id and new_emp_id != employee_obj.employee_id:
            if user_registration.objects.filter(employee_id=new_emp_id).exclude(pk=employee_obj.pk).exists():
                return render(request, "employee/edit_employee.html", {
                    "employee": employee_obj,
                    "company": company,
                    "is_owner": is_owner,
                    "user_role": request.user_role,
                    "error": f"Employee ID '{new_emp_id}' is already registered."
                })
            employee_obj.employee_id = new_emp_id

        employee_obj.Phone = request.POST.get("Phone", "").strip()
        employee_obj.Alternate_phone = request.POST.get("Alternate_phone", "").strip()
        employee_obj.address = request.POST.get("address", "").strip()
        employee_obj.Gender = request.POST.get("Gender", "Male")
        
        raw_role = request.POST.get("Role", employee_obj.Role).strip()
        if raw_role.lower() in ["stock manager", "stock", "inventory staff"]:
            employee_obj.Role = "Stock Manager"
        elif raw_role.lower() == "manager":
            employee_obj.Role = "Manager"
        elif raw_role.lower() == "cashier":
            employee_obj.Role = "Cashier"
        else:
            employee_obj.Role = "Worker"

        joining_date = request.POST.get("joining_date")
        if joining_date:
            from datetime import datetime
            try:
                parsed_join = datetime.strptime(str(joining_date), "%Y-%m-%d").date()
                from django.utils import timezone
                if parsed_join > timezone.now().date():
                    return render(request, "employee/edit_employee.html", {
                        "employee": employee_obj,
                        "company": company,
                        "is_owner": is_owner,
                        "user_role": request.user_role,
                        "error": "Joining date cannot be in the future."
                    })
                employee_obj.joining_date = parsed_join
            except ValueError:
                pass

        employee_obj.status = request.POST.get("status", "active").lower()

        # Profile pic update if uploaded
        if request.FILES.get("profile_pic"):
            employee_obj.profile_pic = request.FILES.get("profile_pic")

        # Salary & Payroll: Only OWNER can modify
        if is_owner:
            try:
                employee_obj.monthaly_salary = Decimal(str(request.POST.get("monthaly_salary", employee_obj.monthaly_salary) or 0))
            except Exception:
                pass
            employee_obj.payment = request.POST.get("payment", employee_obj.payment)

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
            "is_owner": is_owner,
            "user_role": request.user_role,
        }
    )


@company_required
@role_required(["Admin"])
def delete_employee(request, employee_id):
    """
    Delete Team Member.
    Strictly OWNER / ADMIN ONLY.
    """
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
            "user_role": request.user_role,
        }
    )


@company_required
@role_required(["Admin", "Manager"])
def change_credentials(request, employee_id):
    """
    Secure password/credential change for employee accounts.
    Owner has full authority across all company staff.
    Manager may manage staff but cannot alter Owner or Admin credentials.
    Cashier has zero access (role_required blocks).
    """
    company = request.company
    is_owner = request.user_role in ["Admin", "Owner"]
    employee_obj = get_object_or_404(
        user_registration,
        employee_id=employee_id,
        company=company
    )

    if request.method == "POST":
        # Role boundary enforcement
        if not is_owner:
            if company.owner == employee_obj.user or employee_obj.Role in ["Admin", "Owner"]:
                messages.error(request, "Permission denied: Managers cannot alter Owner or Admin credentials.")
                return redirect("edit_employee", employee_id=employee_id)

        new_email = request.POST.get("email", "").strip()
        new_password = request.POST.get("new_password", "")
        confirm_password = request.POST.get("confirm_password", "")

        if new_password:
            if len(new_password) < 6:
                messages.error(request, "Password must be at least 6 characters long.")
                return redirect("edit_employee", employee_id=employee_id)
            if new_password != confirm_password:
                messages.error(request, "New password and Confirm password do not match.")
                return redirect("edit_employee", employee_id=employee_id)

            employee_obj.user.set_password(new_password)

        if new_email and new_email != employee_obj.user.email:
            employee_obj.user.email = new_email

        employee_obj.user.save()
        messages.success(request, f"Credentials for user '{employee_obj.user.username}' updated successfully.")
        return redirect("edit_employee", employee_id=employee_id)

    return redirect("edit_employee", employee_id=employee_id)


@company_required
@role_required(["Admin", "Owner"])
def payroll_history(request):
    """
    Authoritative Payroll History & Monthly Disbursement Report.
    Strictly OWNER / ADMIN ONLY.
    Supports historical period April 2025 - April 2026 and dynamic future months/years.
    Calculates eligible headcount, paid count, unpaid count, payable, paid, pending,
    completion percentage, and employee-wise status for the selected period.
    """
    from .models import SalaryPayment
    import calendar
    from datetime import date, datetime
    from django.db.models import Sum

    company = request.company
    is_owner = True
    today = timezone.now().date()

    min_year = 2024
    max_year = max(today.year + 1, 2026)
    available_years = list(range(min_year, max_year + 1))

    try:
        selected_year = int(request.GET.get("year", today.year))
    except (ValueError, TypeError):
        selected_year = today.year

    try:
        selected_month = int(request.GET.get("month", today.month))
    except (ValueError, TypeError):
        selected_month = today.month

    if selected_month < 1 or selected_month > 12:
        selected_month = today.month

    _, last_day_of_month = calendar.monthrange(selected_year, selected_month)
    period_end_date = date(selected_year, selected_month, last_day_of_month)

    # Active employees joining on or before the period end date are eligible
    eligible_employees = user_registration.objects.filter(
        company=company,
        status__iexact="active",
        joining_date__lte=period_end_date
    ).select_related("user").order_by("employee_id")

    post_msg = None
    post_error = None
    if request.method == "POST" and request.POST.get("action") == "record_payment":
        target_emp_id = request.POST.get("employee_id", "").strip()
        target_emp = user_registration.objects.filter(company=company, employee_id=target_emp_id).first()
        if not target_emp:
            post_error = "Invalid employee selected."
        else:
            try:
                basic_salary = target_emp.monthaly_salary or Decimal("0.00")
                allowances = Decimal(str(request.POST.get("allowances", 0) or 0))
                deductions = Decimal(str(request.POST.get("deductions", 0) or 0))
                paid_amount = Decimal(str(request.POST.get("paid_amount", 0) or 0))
                gross_salary = basic_salary + allowances
                net_payable = max(Decimal("0.00"), gross_salary - deductions)

                if paid_amount < Decimal("0.00"):
                    paid_amount = Decimal("0.00")

                if paid_amount >= net_payable and net_payable > Decimal("0.00"):
                    status_choice = "Paid"
                elif paid_amount > Decimal("0.00"):
                    status_choice = "Partial"
                else:
                    status_choice = "Unpaid"

                payment_method = request.POST.get("payment_method", "Bank Transfer").strip()
                payment_ref = request.POST.get("payment_reference", "").strip()
                notes = request.POST.get("notes", "").strip()
                pay_date_str = request.POST.get("payment_date")
                payment_date = datetime.strptime(pay_date_str, "%Y-%m-%d").date() if pay_date_str else today

                SalaryPayment.objects.update_or_create(
                    company=company,
                    employee=target_emp,
                    year=selected_year,
                    month=selected_month,
                    defaults={
                        "basic_salary": basic_salary,
                        "allowances": allowances,
                        "deductions": deductions,
                        "gross_salary": gross_salary,
                        "net_payable": net_payable,
                        "paid_amount": paid_amount,
                        "payment_status": status_choice,
                        "payment_method": payment_method,
                        "payment_reference": payment_ref,
                        "payment_date": payment_date,
                        "notes": notes,
                    }
                )
                post_msg = f"Salary record for {target_emp.user.get_full_name() or target_emp.employee_id} saved successfully."
            except Exception as e:
                post_error = f"Failed to record payment: {str(e)}"

    existing_payments = {
        sp.employee_id: sp
        for sp in SalaryPayment.objects.filter(
            company=company,
            year=selected_year,
            month=selected_month
        ).select_related("employee")
    }

    employee_roster = []
    total_payable = Decimal("0.00")
    total_paid = Decimal("0.00")
    paid_count = 0
    partial_count = 0
    unpaid_count = 0

    for emp in eligible_employees:
        record = existing_payments.get(emp.id)
        if record:
            gross = record.gross_salary
            net = record.net_payable
            paid = record.paid_amount
            pending = record.pending_amount
            status = record.payment_status
            method = record.payment_method
            ref = record.payment_reference
            pdate = record.payment_date
            notes = record.notes
            rec_id = record.id
        else:
            gross = emp.monthaly_salary or Decimal("0.00")
            net = gross
            paid = Decimal("0.00")
            pending = net
            status = "Unpaid"
            method = "—"
            ref = ""
            pdate = None
            notes = ""
            rec_id = None

        total_payable += net
        total_paid += paid

        if status == "Paid":
            paid_count += 1
        elif status == "Partial":
            partial_count += 1
        else:
            unpaid_count += 1

        employee_roster.append({
            "employee": emp,
            "record_id": rec_id,
            "gross_salary": gross,
            "net_payable": net,
            "paid_amount": paid,
            "pending_amount": pending,
            "status": status,
            "payment_method": method,
            "payment_reference": ref,
            "payment_date": pdate,
            "notes": notes,
        })

    total_pending = max(Decimal("0.00"), total_payable - total_paid)
    completion_pct = round((total_paid / total_payable * 100), 1) if total_payable > Decimal("0.00") else (100.0 if len(eligible_employees) == 0 else 0.0)

    # ── Month-by-Month History Table (April 2025 through April 2026 + beyond) ──
    start_tuple = (2025, 4)
    end_year = max(2026, today.year)
    end_month = max(4, today.month) if end_year == today.year else 12
    end_tuple = (end_year, end_month)
    if end_tuple < (2026, 4):
        end_tuple = (2026, 4)

    curr_y, curr_m = start_tuple
    month_tuples = []
    while (curr_y, curr_m) <= end_tuple:
        month_tuples.append((curr_y, curr_m))
        curr_m += 1
        if curr_m > 12:
            curr_m = 1
            curr_y += 1

    all_company_payments = SalaryPayment.objects.filter(company=company)
    all_active_emps = user_registration.objects.filter(company=company, status__iexact="active")
    history_table = []

    for y, m in reversed(month_tuples):
        _, m_last_day = calendar.monthrange(y, m)
        m_end_date = date(y, m, m_last_day)
        m_eligible_count = all_active_emps.filter(joining_date__lte=m_end_date).count()
        m_payments = all_company_payments.filter(year=y, month=m)

        m_paid_recs = m_payments.filter(payment_status="Paid").count()
        m_partial_recs = m_payments.filter(payment_status="Partial").count()
        m_unpaid_recs = max(0, m_eligible_count - (m_paid_recs + m_partial_recs))

        m_paid_amount = m_payments.aggregate(t=Sum("paid_amount"))["t"] or Decimal("0.00")
        m_recorded_payable = m_payments.aggregate(t=Sum("net_payable"))["t"] or Decimal("0.00")

        paid_emp_ids = set(m_payments.values_list("employee_id", flat=True))
        missing_payable = sum(
            (e.monthaly_salary or Decimal("0.00"))
            for e in all_active_emps.filter(joining_date__lte=m_end_date)
            if e.id not in paid_emp_ids
        )
        m_payable_amount = m_recorded_payable + missing_payable
        m_pending_amount = max(Decimal("0.00"), m_payable_amount - m_paid_amount)
        m_comp_pct = round((m_paid_amount / m_payable_amount * 100), 1) if m_payable_amount > 0 else (100.0 if m_eligible_count == 0 else 0.0)

        history_table.append({
            "year": y,
            "month": m,
            "month_name": calendar.month_name[m],
            "period_display": f"{calendar.month_name[m]} {y}",
            "eligible_count": m_eligible_count,
            "paid_count": m_paid_recs,
            "partial_count": m_partial_recs,
            "unpaid_count": m_unpaid_recs,
            "payable_amount": m_payable_amount,
            "paid_amount": m_paid_amount,
            "pending_amount": m_pending_amount,
            "completion_pct": m_comp_pct,
            "is_selected": (y == selected_year and m == selected_month),
        })

    months_list = [(i, calendar.month_name[i]) for i in range(1, 13)]

    context = {
        "company": company,
        "is_owner": is_owner,
        "user_role": request.user_role,
        "selected_year": selected_year,
        "selected_month": selected_month,
        "selected_month_name": calendar.month_name[selected_month],
        "selected_period_display": f"{calendar.month_name[selected_month]} {selected_year}",
        "available_years": available_years,
        "months_list": months_list,
        "eligible_count": len(eligible_employees),
        "paid_count": paid_count,
        "partial_count": partial_count,
        "unpaid_count": unpaid_count,
        "total_payable": total_payable,
        "total_paid": total_paid,
        "total_pending": total_pending,
        "completion_pct": completion_pct,
        "employee_roster": employee_roster,
        "history_table": history_table,
        "post_msg": post_msg,
        "post_error": post_error,
    }

    return render(request, "employee/payroll_history.html", context)


@company_required
def employee_payslip(request, employee_id):
    """
    Individual Payslip View & Printable Voucher.
    Role access: Owner/Admin can view any employee's payslip in their company.
    Employees can ONLY view their own payslip. Unauthorized access returns 403 Forbidden.
    """
    from django.http import HttpResponseForbidden
    from .models import SalaryPayment
    import calendar
    from datetime import date

    company = request.company
    today = timezone.now().date()
    employee_obj = get_object_or_404(
        user_registration,
        employee_id=employee_id,
        company=company
    )

    is_owner_or_admin = (
        request.user_role in ["Admin", "Owner"] or 
        getattr(company, "owner", None) == request.user or
        request.user.is_superuser
    )

    if not is_owner_or_admin:
        return HttpResponseForbidden("You do not have authorization to view this employee's salary details.")

    try:
        year = int(request.GET.get("year", today.year))
    except (ValueError, TypeError):
        year = today.year

    try:
        month = int(request.GET.get("month", today.month))
    except (ValueError, TypeError):
        month = today.month

    if month < 1 or month > 12:
        month = today.month

    record = SalaryPayment.objects.filter(
        company=company,
        employee=employee_obj,
        year=year,
        month=month
    ).first()

    if record:
        basic_salary = record.basic_salary
        allowances = record.allowances
        deductions = record.deductions
        gross_salary = record.gross_salary
        net_payable = record.net_payable
        paid_amount = record.paid_amount
        pending_amount = record.pending_amount
        status = record.payment_status
        payment_method = record.payment_method
        payment_ref = record.payment_reference
        payment_date = record.payment_date
        notes = record.notes
    else:
        basic_salary = employee_obj.monthaly_salary or Decimal("0.00")
        allowances = Decimal("0.00")
        deductions = Decimal("0.00")
        gross_salary = basic_salary
        net_payable = basic_salary
        paid_amount = Decimal("0.00")
        pending_amount = net_payable
        status = "Unpaid"
        payment_method = "Pending"
        payment_ref = "—"
        payment_date = None
        notes = ""

    period_display = f"{calendar.month_name[month]} {year}"

    # ── Dedicated Month-by-Month History Specific to this Employee (April 2025 onwards) ──
    start_tuple = (2025, 4)
    end_year = max(2026, today.year)
    end_month = max(4, today.month) if end_year == today.year else 12
    end_tuple = (end_year, end_month)
    if end_tuple < (2026, 4):
        end_tuple = (2026, 4)

    curr_y, curr_m = start_tuple
    month_tuples = []
    while (curr_y, curr_m) <= end_tuple:
        month_tuples.append((curr_y, curr_m))
        curr_m += 1
        if curr_m > 12:
            curr_m = 1
            curr_y += 1

    emp_payment_records = {
        (sp.year, sp.month): sp
        for sp in SalaryPayment.objects.filter(
            company=company,
            employee=employee_obj
        )
    }

    employee_monthly_history = []
    for y, m in reversed(month_tuples):
        _, m_last_day = calendar.monthrange(y, m)
        m_end_date = date(y, m, m_last_day)
        is_employed = employee_obj.joining_date <= m_end_date
        sp_rec = emp_payment_records.get((y, m))

        if not is_employed:
            employee_monthly_history.append({
                "year": y,
                "month": m,
                "month_name": calendar.month_name[m],
                "period_display": f"{calendar.month_name[m]} {y}",
                "payable": Decimal("0.00"),
                "paid": Decimal("0.00"),
                "outstanding": Decimal("0.00"),
                "status": "Not Employed",
                "payment_date": None,
                "reference": "—",
                "method": "—",
                "is_selected": (y == year and m == month),
                "is_active_row": False,
            })
        elif sp_rec:
            employee_monthly_history.append({
                "year": y,
                "month": m,
                "month_name": calendar.month_name[m],
                "period_display": f"{calendar.month_name[m]} {y}",
                "payable": sp_rec.net_payable,
                "paid": sp_rec.paid_amount,
                "outstanding": sp_rec.pending_amount,
                "status": sp_rec.payment_status,
                "payment_date": sp_rec.payment_date,
                "reference": sp_rec.payment_reference or "—",
                "method": sp_rec.payment_method or "—",
                "is_selected": (y == year and m == month),
                "is_active_row": True,
            })
        else:
            std_payable = employee_obj.monthaly_salary or Decimal("0.00")
            employee_monthly_history.append({
                "year": y,
                "month": m,
                "month_name": calendar.month_name[m],
                "period_display": f"{calendar.month_name[m]} {y}",
                "payable": std_payable,
                "paid": Decimal("0.00"),
                "outstanding": std_payable,
                "status": "Unpaid",
                "payment_date": None,
                "reference": "No payment recorded",
                "method": "—",
                "is_selected": (y == year and m == month),
                "is_active_row": True,
            })

    available_years = sorted(list({y for y, _ in month_tuples}), reverse=True)
    months_list = [(i, calendar.month_name[i]) for i in range(1, 13)]

    context = {
        "company": company,
        "employee": employee_obj,
        "year": year,
        "month": month,
        "period_display": period_display,
        "basic_salary": basic_salary,
        "allowances": allowances,
        "deductions": deductions,
        "gross_salary": gross_salary,
        "net_payable": net_payable,
        "paid_amount": paid_amount,
        "pending_amount": pending_amount,
        "status": status,
        "payment_method": payment_method,
        "payment_ref": payment_ref,
        "payment_date": payment_date,
        "notes": notes,
        "generated_on": today,
        "record": record,
        "employee_monthly_history": employee_monthly_history,
        "available_years": available_years,
        "months_list": months_list,
        "is_owner_or_admin": is_owner_or_admin,
    }

    return render(request, "employee/payslip.html", context)

