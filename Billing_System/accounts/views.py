from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.db import transaction, IntegrityError
from .models import companyRegistration
from employee.models import user_registration


def user_login(request):
    error = None
    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")

        user = authenticate(request, username=username, password=password)

        if user is not None:
            login(request, user)

            # Check if user is company owner
            if companyRegistration.objects.filter(owner=user).exists():
                return redirect("dashboard")

            # Check if user is an employee
            try:
                employee = user_registration.objects.get(user=user)
                role = (employee.Role or "").strip().capitalize()
                if role == "Manager":
                    return redirect("manager_dashboard")
                elif role == "Cashier":
                    return redirect("cashier_dashboard")
                elif role == "Worker":
                    return redirect("product")
                else:
                    return redirect("dashboard")
            except user_registration.DoesNotExist:
                return redirect("dashboard")

        error = "Invalid username or password."

    return render(request, "accounts/login.html", {"error": error})


def registration_form(request):
    error = None
    if request.method == "POST":
        admin_first_name = request.POST.get("admin_first_name", "").strip()
        admin_last_name = request.POST.get("admin_last_name", "").strip()
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")
        confirm_pass = request.POST.get("confirm_pass", "")
        email = request.POST.get("email", "").strip()

        company_name = request.POST.get("company_name", "").strip()
        address_line = request.POST.get("address_line", "").strip()
        city = request.POST.get("city", "").strip()
        state = request.POST.get("state", "").strip()
        country = request.POST.get("country", "India").strip()
        pincode = request.POST.get("pincode", "").strip()
        company_email = request.POST.get("company_email", "").strip()
        phone = request.POST.get("phone", "").strip()
        alternate_phone = request.POST.get("alternate_phone", "").strip()
        gst_number = request.POST.get("gst_number", "").strip()
        pan_number = request.POST.get("pan_number", "").strip()
        business_info = request.POST.get("business_info", "").strip()

        if password != confirm_pass:
            return render(request, "accounts/Registration.html", {"error": "Passwords do not match."})

        if User.objects.filter(username=username).exists():
            return render(request, "accounts/Registration.html", {"error": f"Username '{username}' is already taken."})

        try:
            with transaction.atomic():
                user = User.objects.create_user(
                    first_name=admin_first_name,
                    last_name=admin_last_name,
                    username=username,
                    password=password,
                    email=email
                )

                companyRegistration.objects.create(
                    owner=user,
                    company_name=company_name,
                    address_line=address_line,
                    city=city,
                    state=state,
                    country=country,
                    pincode=pincode,
                    company_email=company_email,
                    phone=phone,
                    alternate_phone=alternate_phone,
                    gst_number=gst_number,
                    pan_number=pan_number,
                    business_info=business_info
                )

            return redirect("accounts")
        except IntegrityError as e:
            return render(request, "accounts/Registration.html", {"error": f"Registration failed: {str(e)}"})

    return render(request, 'accounts/Registration.html', {"error": error})


def user_logout(request):
    logout(request)
    return redirect("accounts")
