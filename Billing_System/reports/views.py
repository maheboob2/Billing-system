from django.shortcuts import render
from accounts.tenancy import role_required


@role_required(["Admin", "Owner", "Manager"])
def reports(request):
    return render(
        request,
        "reports/reports.html",
        {
            "company": request.company,
            "user_role": request.user_role,
        },
    )

