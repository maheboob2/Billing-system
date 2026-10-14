from django.urls import path

from . import views
from . import demo_views

urlpatterns = [
    path("accounts/", views.user_login, name="accounts"),
    path("registration/", views.registration_form, name="registration"),
    path("accounts/register/", views.registration_form, name="accounts_register"),
    path("logout/", views.user_logout, name="logout"),
    path("accounts/logout/", views.user_logout, name="accounts_logout"),
    path("demo/", demo_views.demo_hub_view, name="demo_hub"),
    path("api/demo/status/", demo_views.demo_status_api, name="demo_status_api"),
]
