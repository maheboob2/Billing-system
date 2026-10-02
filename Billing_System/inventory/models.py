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
    
class Product(models.Model):
    company = models.ForeignKey(
        companyRegistration,
        on_delete=models.CASCADE,
        related_name="products"
    )

    name = models.CharField(max_length=150)

    product_code = models.CharField(max_length=50)

    barcode = models.CharField(
        max_length=100,
        blank=True
    )

    category = models.ForeignKey(
        Category,
        on_delete=models.PROTECT,
        related_name="products"
    )

    supplier = models.ForeignKey(
        Supplier,
        on_delete=models.PROTECT,
        related_name="products"
    )

    purchase_price = models.DecimalField(
        max_digits=10,
        decimal_places=2
    )

    selling_price = models.DecimalField(
        max_digits=10,
        decimal_places=2
    )

    gst_percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0
    )

    current_stock = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0
    )

    minimum_stock = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0
    )

    unit = models.CharField(
        max_length=20,
        default="pcs"
    )

    status = models.BooleanField(default=True)

    created_at = models.DateTimeField(
        auto_now_add=True
    )

    def __str__(self):
        return self.name 


