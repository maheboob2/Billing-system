from django.shortcuts import render, redirect
from django.contrib.auth.models import User
from .models import user_registration
from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponse
from django.db.models import Q


def employee(request):
    if request.method == "POST":

        # User details
        username = request.POST.get("username")
        first_name = request.POST.get("first_name")
        last_name = request.POST.get("last_name")
        email = request.POST.get("email")  # Optional if you add it to HTML
        password = request.POST.get("password")
        confirm_pass=request.POST.get("confirm_pass")

        # Employee details
        profile_pic = request.FILES.get("profile_pic")
        gender = request.POST.get("Gender")
        phone = request.POST.get("Phone")
        alternate_phone = request.POST.get("Alternate_phone")
        address = request.POST.get("address")
        employee_id = request.POST.get("employee_id")
        role = request.POST.get("Role")
        joining_date = request.POST.get("joining_date")
        monthaly_salary = request.POST.get("monthaly_salary")
        payment = request.POST.get("payment")
        status = request.POST.get("status")

        # Create Django User
        if password != confirm_pass:
            return HttpResponse("password do not match")

        user = User.objects.create_user(
            username=username,
            first_name=first_name,
            last_name=last_name,
            email=email,
            password=password,
        )

        company = request.user.owned_companies


        # Create Employee Profile
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

        return redirect("employee")

    return render(request, "employee/employee.html")

def view_employees(request):

    company=request.user.owned_companies

    search=request.GET.get("search","")


    employees=user_registration.objects.filter(
        company=company
    ).select_related("user","company")

    #Total Employees
    
    if search:
        employees=employees.filter(
            Q(employee_id__icontains=search) |
            Q(user__username__icontains=search) |
            Q(user__first_name__icontains=search) |
            Q(user__last_name__icontains=search) 
        )

    Total_Employees=employees.count()
    Active_Employees=employees.filter(status="active").count()
    In_active_Employees=employees.filter(status="inactive").count()
    Total_monthly_salary=sum(employee.monthaly_salary or 0
                              for employee in employees)


    context={
        'company':company,
        'employees':employees,
        'Total_Employees':Total_Employees,
        'Active_Employees': Active_Employees,
        'In_active_Employees':In_active_Employees,
        'Total_monthly_salary':Total_monthly_salary,
        'search':search
    }

    return render(
        request,
        "employee/view_employee.html",
        
        context
    )

def edit_employee(request,employee_id):

    company=request.user.owned_companies

    employee=user_registration.objects.get(
        employee_id=employee_id,
        company=company
    )

    if request.method=="POST":
          employee.user.username = request.POST.get("username")
          employee.user.first_name = request.POST.get("first_name")
          employee.user.last_name = request.POST.get("last_name")
          employee.user.email = request.POST.get("email")

          employee.user.save()


          employee.employee_id = request.POST.get("employee_id")
          employee.Phone = request.POST.get("Phone")
          employee.Alternate_phone = request.POST.get("Alternate_phone")
          employee.address = request.POST.get("address")
          employee.Gender = request.POST.get("Gender")
          employee.Role = request.POST.get("Role")
          employee.joining_date = request.POST.get("joining_date")
          employee.monthaly_salary = request.POST.get("monthaly_salary")
          employee.payment = request.POST.get("payment")
          employee.status = request.POST.get("status")

          employee.save()

          return redirect("view_employee")

    return render(
        request,
        "employee/edit_employee.html",
        {
            "employee": employee,
            "company": company,
        }
    )

def delete_employee(request, employee_id):
    company = request.user.owned_companies

    employee = user_registration.objects.get(
        employee_id=employee_id,
        company=company
    )
    user = employee.user
    if request.method=="POST":
        

      
        employee.delete()
        user.delete()

        return redirect("view_employee")
    return render(request,"employee/delete_employee.html",{"employee":employee,"user":user})
