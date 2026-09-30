from django.shortcuts import render,redirect
from django.http import HttpResponse
from django.contrib.auth import authenticate,login,logout
from django.contrib.auth.models import User
from .models import companyRegistration
from employee.models import user_registration
# Create your views here.

def user_login(request):
  if request.method=="POST":
    username=request.POST.get("username")
    password=request.POST.get("password")

    user=authenticate(request,username=username,
                              password=password,
                      )
   
    if user is not None:

        login(request,user)

        if companyRegistration.objects.filter(owner=user).exists():
            return redirect("dashboard")
        try:
             employee = user_registration.objects.get(user=user)
             if employee.Role == "Manager":
                     return redirect("manager_dashboard") 
             elif employee.Role == "Cashier":
                    return redirect("cashier_dashboard")
        except user_registration.DoesNotExist:
                pass

    return HttpResponse("invalid username or passowrd")    
  
  return render(request,"accounts/login.html")

def registration_form(request):
    if request.method=="POST":

        #admin details
        admin_first_name=request.POST.get("admin_first_name")
        admin_last_name=request.POST.get("admin_last_name")
        username=request.POST.get("username")
        password=request.POST.get("password")
        confirm_pass=request.POST.get("confirm_pass")
        email=request.POST.get("email")

        #company details
        company_name=request.POST.get("company_name")
        address_line=request.POST.get("address_line")
        city=request.POST.get("city")
        state=request.POST.get("state")
        country=request.POST.get("country")
        pincode=request.POST.get("pincode")
        company_email=request.POST.get("company_email")
        phone=request.POST.get("phone")
        alternate_phone=request.POST.get("alternate_phone")
        gst_number=request.POST.get("gst_number")
        
        pan_number=request.POST.get("pan_number")
        business_info=request.POST.get("business_info")

        #user
        if password !=confirm_pass:
            return HttpResponse("password do not match")
        
        user=User.objects.create_user(
              first_name=admin_first_name,
              last_name= admin_last_name,
              username=username,
              password=password,
              email=email
          )

        #company
        companyRegistration.objects.create(
           
            owner=user,
            company_name= company_name,
            address_line= address_line,
            city=city,
            state=state,
            country=country,
            pincode=pincode,
            company_email= company_email,
            phone= phone,
            alternate_phone=alternate_phone,
            gst_number=gst_number,
            pan_number= pan_number,
            business_info=business_info


            
        )

        return redirect("accounts")


    return render(request,'accounts/Registration.html',{})

def user_logout(request):
    logout(request)

    return redirect("accounts")
