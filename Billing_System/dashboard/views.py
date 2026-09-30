from django.shortcuts import render
from django.contrib.auth.decorators import login_required

# Create your views here.
@login_required
def dashboard(request):
    return render(request,"dashboard/dashboard.html")

@login_required

def manager_dashboard(request):
     return render(request,"dashboard/manager_dash.html")

@login_required

def cashier_dashboard(request):
     return render(request,"dashboard/cashier_dash.html")


