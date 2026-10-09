from decimal import Decimal
from django.db import models
from django.core.validators import MinValueValidator
from django.contrib.auth.models import User
from accounts.models import companyRegistration
from inventory.models import Product


class Customer(models.Model):
    company = models.ForeignKey(
        companyRegistration,
        on_delete=models.CASCADE,
        related_name="customers"
    )
    name = models.CharField(max_length=150)
    phone = models.CharField(max_length=20, db_index=True)
    email = models.EmailField(blank=True, default="")
    address = models.TextField(blank=True, default="")
    credit_balance = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))]
    )
    credit_limit = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))]
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        indexes = [
            models.Index(fields=["company", "phone"]),
            models.Index(fields=["company", "name"]),
        ]

    def __str__(self):
        return f"{self.name} ({self.phone})"


class Sale(models.Model):
    DISCOUNT_TYPES = [
        ("FLAT", "Flat Amount"),
        ("PERCENTAGE", "Percentage"),
    ]

    PAYMENT_STATUS_CHOICES = [
        ("PAID", "Fully Paid"),
        ("PARTIALLY_PAID", "Partially Paid"),
        ("UNPAID", "Unpaid / Credit"),
    ]

    SALE_STATUS_CHOICES = [
        ("COMPLETED", "Completed"),
        ("CANCELLED", "Cancelled"),
        ("REFUNDED", "Refunded"),
    ]

    company = models.ForeignKey(
        companyRegistration,
        on_delete=models.CASCADE,
        related_name="sales"
    )
    sale_number = models.CharField(max_length=64, unique=True, db_index=True)
    customer = models.ForeignKey(
        Customer,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sales"
    )
    cashier = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="processed_sales"
    )

    subtotal = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))]
    )
    discount_type = models.CharField(
        max_length=20,
        choices=DISCOUNT_TYPES,
        default="FLAT"
    )
    discount_value = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))]
    )
    discount_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))]
    )

    tax_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))]
    )
    cgst_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))]
    )
    sgst_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))]
    )
    igst_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))]
    )

    grand_total = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))]
    )
    paid_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))]
    )
    balance_due = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))]
    )

    payment_status = models.CharField(
        max_length=20,
        choices=PAYMENT_STATUS_CHOICES,
        default="PAID"
    )
    sale_status = models.CharField(
        max_length=20,
        choices=SALE_STATUS_CHOICES,
        default="COMPLETED"
    )

    notes = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["company", "created_at"]),
            models.Index(fields=["company", "sale_number"]),
            models.Index(fields=["company", "payment_status"]),
        ]

    def __str__(self):
        return f"{self.sale_number} - ₹{self.grand_total}"


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

    # Historic snapshots of product data at moment of sale
    product_name = models.CharField(max_length=150)
    product_code = models.CharField(max_length=50)
    unit_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))]
    )
    cost_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))]
    )
    quantity = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))]
    )
    gst_percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))]
    )

    subtotal = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))]
    )
    discount_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))]
    )
    tax_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))]
    )
    total = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))]
    )

    def __str__(self):
        return f"{self.product_name} x {self.quantity} = ₹{self.total}"


class Payment(models.Model):
    PAYMENT_METHODS = [
        ("CASH", "Cash"),
        ("UPI", "UPI / QR Code"),
        ("CARD", "Debit / Credit Card"),
        ("CREDIT", "Store Credit"),
    ]

    company = models.ForeignKey(
        companyRegistration,
        on_delete=models.CASCADE,
        related_name="payments"
    )
    sale = models.ForeignKey(
        Sale,
        on_delete=models.CASCADE,
        related_name="payments"
    )
    payment_method = models.CharField(
        max_length=20,
        choices=PAYMENT_METHODS,
        default="CASH"
    )
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))]
    )
    transaction_reference = models.CharField(
        max_length=100,
        blank=True,
        default=""
    )
    received_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="received_payments"
    )
    payment_date = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-payment_date"]
        indexes = [
            models.Index(fields=["company", "payment_date"]),
            models.Index(fields=["sale", "payment_method"]),
        ]

    def __str__(self):
        return f"{self.payment_method}: ₹{self.amount} for {self.sale.sale_number}"


class Invoice(models.Model):
    company = models.ForeignKey(
        companyRegistration,
        on_delete=models.CASCADE,
        related_name="invoices"
    )
    sale = models.OneToOneField(
        Sale,
        on_delete=models.CASCADE,
        related_name="invoice"
    )
    invoice_number = models.CharField(
        max_length=64,
        unique=True,
        db_index=True
    )
    invoice_date = models.DateTimeField(auto_now_add=True)

    customer_name = models.CharField(max_length=150, blank=True, default="Walk-in Customer")
    customer_phone = models.CharField(max_length=20, blank=True, default="")
    customer_address = models.TextField(blank=True, default="")

    company_name = models.CharField(max_length=100)
    company_gst = models.CharField(max_length=50, blank=True, default="")
    company_address = models.TextField(blank=True, default="")
    company_phone = models.CharField(max_length=20, blank=True, default="")

    subtotal = models.DecimalField(max_digits=12, decimal_places=2)
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2)
    cgst_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    sgst_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    igst_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2)
    grand_total = models.DecimalField(max_digits=12, decimal_places=2)
    paid_amount = models.DecimalField(max_digits=12, decimal_places=2)
    balance_due = models.DecimalField(max_digits=12, decimal_places=2)

    cashier_name = models.CharField(max_length=150)
    payment_summary = models.CharField(max_length=100, default="CASH")

    class Meta:
        ordering = ["-invoice_date"]
        indexes = [
            models.Index(fields=["company", "invoice_date"]),
            models.Index(fields=["invoice_number"]),
        ]

    def __str__(self):
        return f"Invoice {self.invoice_number} (₹{self.grand_total})"


class CheckoutIdempotency(models.Model):
    STATUS_CHOICES = [
        ("PROCESSING", "Processing"),
        ("COMPLETED", "Completed"),
        ("FAILED", "Failed"),
    ]

    company = models.ForeignKey(
        companyRegistration,
        on_delete=models.CASCADE,
        related_name="checkout_idempotency_records"
    )
    idempotency_key = models.CharField(max_length=128, db_index=True)
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        null=True,
        blank=True
    )
    request_hash = models.CharField(max_length=64)
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="PROCESSING"
    )
    response_status_code = models.IntegerField(null=True, blank=True)
    response_body = models.JSONField(null=True, blank=True)
    sale = models.ForeignKey(
        Sale,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="idempotency_records"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["company", "idempotency_key"],
                name="unique_company_idempotency_key"
            )
        ]
        indexes = [
            models.Index(fields=["company", "idempotency_key"]),
        ]

    def __str__(self):
        return f"{self.company.company_name} - {self.idempotency_key} ({self.status})"


class POSScannerSession(models.Model):
    STATUS_CHOICES = [
        ("ACTIVE", "Active"),
        ("DISCONNECTED", "Disconnected"),
        ("EXPIRED", "Expired"),
    ]
    company = models.ForeignKey(
        companyRegistration,
        on_delete=models.CASCADE,
        related_name="scanner_sessions"
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="scanner_sessions"
    )
    session_token = models.CharField(max_length=64, unique=True, db_index=True)
    pairing_code = models.CharField(max_length=10, db_index=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="ACTIVE")
    device_info = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    last_ping = models.DateTimeField(auto_now=True)

    def is_valid(self):
        from django.utils import timezone
        return self.status == "ACTIVE" and self.expires_at > timezone.now()

    def __str__(self):
        return f"ScannerSession {self.pairing_code} ({self.status}) - {self.company.company_name}"


class POSScannerScan(models.Model):
    session = models.ForeignKey(
        POSScannerSession,
        on_delete=models.CASCADE,
        related_name="scans"
    )
    barcode = models.CharField(max_length=100)
    consumed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"Scan: {self.barcode} (consumed={self.consumed})"


class ReturnRequest(models.Model):
    STATUS_CHOICES = [
        ("PENDING", "Pending Approval"),
        ("APPROVED", "Approved"),
        ("REJECTED", "Rejected"),
        ("COMPLETED", "Completed"),
    ]
    REASON_CHOICES = [
        ("DAMAGED", "Damaged"),
        ("WRONG_PRODUCT", "Wrong Product"),
        ("EXPIRED", "Expired"),
        ("QUALITY_ISSUE", "Quality Issue"),
        ("CUSTOMER_CHANGED_MIND", "Customer Changed Mind"),
        ("OTHER", "Other"),
    ]

    company = models.ForeignKey(
        companyRegistration,
        on_delete=models.CASCADE,
        related_name="return_requests"
    )
    return_number = models.CharField(max_length=64, unique=True, db_index=True)
    sale = models.ForeignKey(
        Sale,
        on_delete=models.PROTECT,
        related_name="return_requests"
    )
    invoice = models.ForeignKey(
        Invoice,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="return_requests"
    )
    customer = models.ForeignKey(
        Customer,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="return_requests"
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="PENDING"
    )
    reason = models.CharField(
        max_length=30,
        choices=REASON_CHOICES,
        default="CUSTOMER_CHANGED_MIND"
    )
    refund_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))]
    )
    refund_payment_method = models.CharField(
        max_length=20,
        choices=Payment.PAYMENT_METHODS,
        default="CASH"
    )
    notes = models.TextField(blank=True, default="")
    created_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="created_returns"
    )
    approved_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_returns"
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["company", "created_at"]),
            models.Index(fields=["company", "status"]),
            models.Index(fields=["company", "return_number"]),
        ]

    def __str__(self):
        return f"{self.return_number} - {self.status} (₹{self.refund_amount})"


class ReturnItem(models.Model):
    return_request = models.ForeignKey(
        ReturnRequest,
        on_delete=models.CASCADE,
        related_name="items"
    )
    sale_item = models.ForeignKey(
        SaleItem,
        on_delete=models.PROTECT,
        related_name="return_records"
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name="returned_items"
    )
    quantity = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))]
    )
    refund_unit_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))]
    )
    refund_total = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))]
    )
    restock = models.BooleanField(
        default=True,
        help_text="If true, item is returned to sellable stock. If false (e.g. damaged/expired), stock is not restored."
    )

    def __str__(self):
        return f"ReturnItem: {self.product.name} x {self.quantity} (₹{self.refund_total})"

