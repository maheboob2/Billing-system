from django.urls import path
from . import views

urlpatterns = [
    path("employee/", views.employee, name="employee"),
    path("view_employee/", views.view_employees, name="view_employee"),
    path("view_employee", views.view_employees),
    path("edit_employee/<str:employee_id>/", views.edit_employee, name="edit_employee"),
    path("employee/<str:employee_id>/credentials/", views.change_credentials, name="change_employee_credentials"),
    path("payroll/", views.payroll_history, name="payroll_history"),
    path("payslip/<str:employee_id>/", views.employee_payslip, name="employee_payslip"),
    path("delete_employee/<str:employee_id>/", views.delete_employee, name="delete_employee"),
    path("delete_employee/<str:employee_id>", views.delete_employee),
]
