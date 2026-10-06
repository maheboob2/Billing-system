
from django.db import models
from django.db.models import Sum
from django.core.validators import MinValueValidator
from django.utils import timezone

from accounts.models import companyRegistration
from inventory.models import Product


class Customer(models.Model):
    company = models.ForeignKey(
        companyRegistration,
        on_delete=models.CASCADE,
        related_name="sales_customers"
    )
    name = models.CharField(max_length=150)
    phone = models.CharField(max_length=15)
    email = models.EmailField(blank=True, null=True)
    address = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class Sale(models.Model):
    STATUS_CHOICES = [
        ("DRAFT", "Draft"),
        ("COMPLETED", "Completed"),
        ("CANCELLED", "Cancelled"),
    ]

    PAYMENT_CHOICES = [
        ("CASH", "Cash"),
        ("UPI", "UPI"),
        ("CARD", "Card"),
        ("CREDIT", "Credit"),
    ]

    company = models.ForeignKey(
        companyRegistration,
        on_delete=models.CASCADE,
        related_name="sales_records"
    )
    customer = models.ForeignKey(
        Customer,
        on_delete=models.PROTECT,
        related_name="sales",
        null=True,
        blank=True
    )
    invoice_number = models.CharField(max_length=50)
    sale_date = models.DateField(default=timezone.localdate)
    status = models.CharField(
        max_length=15,
        choices=STATUS_CHOICES,
        default="DRAFT"
    )
    payment_method = models.CharField(
        max_length=10,
        choices=PAYMENT_CHOICES,
        default="CASH"
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["company", "invoice_number"],
                name="unique_sale_invoice_per_company"
            )
        ]
        ordering = ["-sale_date", "-id"]

    def __str__(self):
        return self.invoice_number

    @property
    def subtotal(self):
        return sum(
            (item.subtotal for item in self.items.all()),
            start=0
        )

    @property
    def gst_amount(self):
        return sum(
            (item.gst_amount for item in self.items.all()),
            start=0
        )

    @property
    def total_amount(self):
        return self.subtotal + self.gst_amount


class SaleItem(models.Model):
    sale = models.ForeignKey(
        Sale,
        on_delete=models.CASCADE,
        related_name="items"
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name="sale_items"
    )
    quantity = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(0.01)]
    )
    selling_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(0)]
    )
    gst_percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        validators=[MinValueValidator(0)]
    )

    @property
    def subtotal(self):
        return self.quantity * self.selling_price

    @property
    def gst_amount(self):
        return self.subtotal * self.gst_percentage / 100

    @property
    def total_amount(self):
        return self.subtotal + self.gst_amount

    def __str__(self):
        return f"{self.product.name} - {self.quantity}"
