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

    def __str__(self):
                return f"{self.employee_id}"



