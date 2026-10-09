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
    
from decimal import Decimal
from django.core.validators import MinValueValidator
from django.contrib.auth.models import User

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
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))]
    )

    selling_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))]
    )

    gst_percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(Decimal("0.00"))]
    )

    current_stock = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(Decimal("0.00"))]
    )

    minimum_stock = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(Decimal("0.00"))]
    )

    unit = models.CharField(
        max_length=20,
        default="pcs"
    )

    status = models.BooleanField(default=True)

    created_at = models.DateTimeField(
        auto_now_add=True
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["company", "product_code"],
                name="unique_company_product_code"
            ),
            models.UniqueConstraint(
                fields=["company", "barcode"],
                name="unique_company_barcode",
                condition=~models.Q(barcode="")
            ),
            models.CheckConstraint(
                check=models.Q(current_stock__gte=Decimal("0.00")),
                name="prevent_negative_stock"
            ),
        ]
        indexes = [
            models.Index(fields=["company", "product_code"]),
            models.Index(fields=["company", "barcode"]),
        ]

    def __str__(self):
        return f"{self.name} ({self.product_code})"


class StockMovement(models.Model):
    MOVEMENT_TYPES = [
        ("PURCHASE", "Purchase Intake"),
        ("SALE", "Sale Deduction"),
        ("RETURN", "Customer Return"),
        ("ADJUSTMENT", "Manual Adjustment"),
        ("DAMAGE", "Damaged Stock"),
        ("WASTAGE", "Wasted / Expired Stock"),
    ]

    company = models.ForeignKey(
        companyRegistration,
        on_delete=models.CASCADE,
        related_name="stock_movements"
    )

    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name="stock_movements"
    )

    movement_type = models.CharField(
        max_length=20,
        choices=MOVEMENT_TYPES
    )

    quantity = models.DecimalField(
        max_digits=10,
        decimal_places=2
    )

    previous_stock = models.DecimalField(
        max_digits=10,
        decimal_places=2
    )

    new_stock = models.DecimalField(
        max_digits=10,
        decimal_places=2
    )

    reference = models.CharField(
        max_length=100,
        blank=True
    )

    user = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="stock_movements"
    )

    reason = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["company", "created_at"]),
            models.Index(fields=["product", "created_at"]),
            models.Index(fields=["movement_type"]),
        ]

    def __str__(self):
        return f"{self.product.name} - {self.movement_type} ({self.quantity}) at {self.created_at}"
 


