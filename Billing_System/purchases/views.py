from django.shortcuts import render

# Create your views here.

def purchases(request):
    return render(request,"purchases/purchases.html")
