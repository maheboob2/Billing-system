from django.db import models
from django.core.validators import MinLengthValidator
from django.contrib.auth.models import User


# Create your models here.

class companyRegistration(models.Model):
    owner = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="owned_companies"
    )

    company_name=models.CharField(max_length=100,blank=False,null=False,validators=[MinLengthValidator(1)])

    
    address_line = models.CharField(max_length=150,blank=False,null=False)
   
    city = models.CharField(max_length=50,blank=False,null=False)
    state = models.CharField(max_length=50,blank=False,null=False)
    country = models.CharField(max_length=50, default="India",blank=False,null=False)
    pincode = models.CharField(max_length=10,blank=False,null=False)

    company_email = models.EmailField()  
    phone=models.CharField(max_length=15,blank=False,null=False,validators=[MinLengthValidator(1)])
    alternate_phone=models.CharField(max_length=15,blank=False,null=False,validators=[MinLengthValidator(1)])
    gst_number=models.CharField(max_length=50)
    created_at=models.DateTimeField(auto_now_add=True)
    pan_number = models.CharField(
            max_length=10,
            blank=True,
            null=True
        )
    business_info=models.TextField(max_length=120)



    def __str__(self):
            return f"{self.company_name}-{self.id}"

class CompanyUser(models.Model):
    User = models.OneToOneField(User, on_delete=models.CASCADE)
    company = models.ForeignKey(
        companyRegistration,
        on_delete=models.CASCADE
    )
    role = models.CharField(max_length=20)
