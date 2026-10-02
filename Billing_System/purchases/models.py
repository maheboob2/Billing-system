from django.db import models

# Create your models here.


from django.db import models
from accounts.models import companyRegistration
from inventory.models import Supplier,Product


class Purchase(models.Model):
    STATUS_CHOICES = [
        ("DRAFT", "Draft"),
        ("RECEIVED", "Received"),
    ]

    company = models.ForeignKey(
        companyRegistration,
        on_delete=models.CASCADE,
        related_name="purchase_records"
    )

    supplier = models.ForeignKey(
        Supplier,
        on_delete=models.PROTECT,
        related_name="purchase_records"
    )

    invoice_number = models.CharField(max_length=100)

    purchase_date = models.DateField()

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="DRAFT"
    )

    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.invoice_number

    
    @property
    def subtotal(self):
        return sum((item.subtotal for item in self.items.all()), start=0)

    @property
    def gst_amount(self):
        return sum((item.gst_amount for item in self.items.all()), start=0)

    @property
    def total_amount(self):
        return self.subtotal + self.gst_amount


from inventory.models import Product

class PurchaseItem(models.Model):
    purchase = models.ForeignKey(
        Purchase,
        on_delete=models.CASCADE,
        related_name="items"
    )

    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name="purchase_items"
    )

    quantity = models.DecimalField(
        max_digits=10,
        decimal_places=2
    )

    purchase_price = models.DecimalField(
        max_digits=10,
        decimal_places=2
    )

    gst_percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0
    )

    def __str__(self):
        return f"{self.product.name} - {self.quantity}"
    
    @property
    def subtotal(self):
        return self.quantity * self.purchase_price

    @property
    def gst_amount(self):
        return self.subtotal * self.gst_percentage / 100

    @property
    def total_amount(self):
        return self.subtotal + self.gst_amount
