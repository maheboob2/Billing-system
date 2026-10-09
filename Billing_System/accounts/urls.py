from django.urls import path

from . import views

urlpatterns = [
    path("accounts/",views.user_login,name="accounts"),
    path("registration/",views.registration_form,name="registration"),
    path("logout/",views.user_logout,name="logout"),
    path("accounts/logout/",views.user_logout,name="accounts_logout"),
    
]
