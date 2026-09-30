from django.urls import path

from . import views

urlpatterns = [
    path("employee/",views.employee,name="employee"),
    path("view_employee",views.view_employees,name="view_employee"),
    path("edit_employee/<str:employee_id>/",  views.edit_employee,
    name="edit_employee"),

    path("delete_employee/<str:employee_id>",views.delete_employee,name="delete_employee")
]
