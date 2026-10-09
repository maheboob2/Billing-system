from django.db import models
from django.utils import timezone 
from django.contrib.auth.models import User
from accounts.models import companyRegistration

# Create your models here.

class user_registration(models.Model):
    

    user=models.OneToOneField(User,on_delete=models.CASCADE)

    company=models.ForeignKey( companyRegistration,on_delete=models.CASCADE,related_name="employees")

    profile_pic=models.ImageField(upload_to='profile_pics',blank=True)
    Gender=[
        ("Male","Male"),
        ("Female","Female"),
        ("Other","Other")
    ]
    Gender=models.CharField(max_length=20,choices=Gender)

    Phone=models.CharField(max_length=15,blank=False,null=False)

    Alternate_phone=models.CharField(max_length=15)

    address=models.TextField()
    employee_id=models.CharField(max_length=10,unique=True)
    Role=[
        ("Manager","Manager"),
        ("Cashier","Cashier"),
        ("Stock Manager","Stock Manager"),
        ("Worker","Worker")
    ]

    Role=models.CharField(max_length=30,choices=Role)

    joining_date=models.DateField()

    monthaly_salary = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        blank=False,
        null=False
    )
    payment=[
        ("Paid","Paid"),
        ("Unpaid","Unpaid"),
        ("Due","Due")
    ]

    payment=models.CharField(max_length=20,choices=payment)

    status=[
        ("active","active"),
        ("inactive","inactive")
    ]

    status=models.CharField(max_length=20,choices=status)

    @property
    def department(self):
        role_map = {
            "Manager": "Store Management",
            "Cashier": "Front Billing & POS",
            "Stock Manager": "Inventory & Logistics",
            "Worker": "Store Operations",
        }
        return role_map.get(self.Role, "Store Operations")

    def __str__(self):
        return f"{self.employee_id}"


class SalaryPayment(models.Model):
    """
    Authoritative monthly salary disbursement and payroll record.
    Tracks each payment made to an employee for a specific month and year.
    """
    STATUS_CHOICES = [
        ("Paid", "Paid"),
        ("Partial", "Partially Paid"),
        ("Unpaid", "Unpaid"),
    ]

    company = models.ForeignKey(
        companyRegistration,
        on_delete=models.CASCADE,
        related_name="salary_payments"
    )
    employee = models.ForeignKey(
        user_registration,
        on_delete=models.CASCADE,
        related_name="salary_records"
    )
    year = models.PositiveIntegerField()
    month = models.PositiveSmallIntegerField()  # 1 to 12

    basic_salary = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    allowances = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    deductions = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    gross_salary = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    net_payable = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    paid_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    payment_status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="Unpaid")
    payment_method = models.CharField(max_length=30, blank=True, default="Bank Transfer")
    payment_reference = models.CharField(max_length=100, blank=True)
    payment_date = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("company", "employee", "year", "month")
        ordering = ["-year", "-month", "employee__employee_id"]

    @property
    def pending_amount(self):
        from decimal import Decimal
        return max(Decimal("0.00"), self.net_payable - self.paid_amount)

    @property
    def period_display(self):
        import calendar
        month_name = calendar.month_name[self.month] if 1 <= self.month <= 12 else str(self.month)
        return f"{month_name} {self.year}"

    def __str__(self):
        return f"{self.employee.employee_id} - {self.period_display} - {self.payment_status}"



