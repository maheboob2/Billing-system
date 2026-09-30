from django.db import models
from accounts.models import companyRegistration

# Create your models here.

class Category(models.Model):
    company = models.ForeignKey(
        companyRegistration,
        on_delete=models.CASCADE,
        related_name="categories"
    )

    name = models.CharField(max_length=100)

    description = models.TextField(
        blank=True,
        null=True
    )

    status = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

class Supplier(models.Model):

    company = models.ForeignKey(
        companyRegistration,
        on_delete=models.CASCADE,
        related_name="suppliers"
    )

    name = models.CharField(max_length=100)

    contact_person = models.CharField(
        max_length=100,
        blank=True
    )

    phone = models.CharField(max_length=15)

    alternate_phone = models.CharField(
        max_length=15,
        blank=True
    )

    email = models.EmailField(
        blank=True
    )

    address = models.TextField(
        blank=True
    )

    city = models.CharField(
        max_length=50,
        blank=True
    )

    state = models.CharField(
        max_length=50,
        blank=True
    )

    pincode = models.CharField(
        max_length=10,
        blank=True
    )

    gst_number = models.CharField(
        max_length=20,
        blank=True
    )

    status = models.BooleanField(default=True)

    created_at = models.DateTimeField(
        auto_now_add=True
    )

    def __str__(self):
        return self.name        

