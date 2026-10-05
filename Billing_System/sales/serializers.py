from rest_framework import serializers
from decimal import Decimal
from sales.models import Customer, Sale, SaleItem, Payment, Invoice


class CustomerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Customer
        fields = [
            "id",
            "name",
            "phone",
            "email",
            "address",
            "credit_balance",
            "credit_limit",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate_phone(self, value):
        company = self.context.get("company")
        if company:
            qs = Customer.objects.filter(company=company, phone=value)
            if self.instance:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise serializers.ValidationError("A customer with this phone number already exists.")
        return value


class SaleItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = SaleItem
        fields = [
            "id",
            "product",
            "product_name",
            "product_code",
            "unit_price",
            "cost_price",
            "quantity",
            "gst_percentage",
            "subtotal",
            "discount_amount",
            "tax_amount",
            "total",
        ]
        read_only_fields = fields


class PaymentSerializer(serializers.ModelSerializer):
    received_by_name = serializers.CharField(source="received_by.username", read_only=True)

    class Meta:
        model = Payment
        fields = [
            "id",
            "sale",
            "payment_method",
            "amount",
            "transaction_reference",
            "received_by",
            "received_by_name",
            "payment_date",
            "notes",
        ]
        read_only_fields = ["id", "payment_date", "received_by", "received_by_name"]


class InvoiceSerializer(serializers.ModelSerializer):
    items = serializers.SerializerMethodField()

    class Meta:
        model = Invoice
        fields = [
            "id",
            "invoice_number",
            "invoice_date",
            "customer_name",
            "customer_phone",
            "customer_address",
            "company_name",
            "company_gst",
            "company_address",
            "company_phone",
            "subtotal",
            "discount_amount",
            "tax_amount",
            "cgst_amount",
            "sgst_amount",
            "igst_amount",
            "grand_total",
            "paid_amount",
            "balance_due",
            "cashier_name",
            "payment_summary",
            "items",
        ]
        read_only_fields = fields

    def get_items(self, obj):
        return SaleItemSerializer(obj.sale.items.all(), many=True).data


class SaleSerializer(serializers.ModelSerializer):
    items = SaleItemSerializer(many=True, read_only=True)
    payments = PaymentSerializer(many=True, read_only=True)
    customer_name = serializers.CharField(source="customer.name", read_only=True, default="Walk-in Customer")
    cashier_name = serializers.CharField(source="cashier.username", read_only=True)
    invoice_number = serializers.CharField(source="invoice.invoice_number", read_only=True, default=None)

    class Meta:
        model = Sale
        fields = [
            "id",
            "sale_number",
            "invoice_number",
            "customer",
            "customer_name",
            "cashier",
            "cashier_name",
            "subtotal",
            "discount_type",
            "discount_value",
            "discount_amount",
            "tax_amount",
            "cgst_amount",
            "sgst_amount",
            "igst_amount",
            "grand_total",
            "paid_amount",
            "balance_due",
            "payment_status",
            "sale_status",
            "notes",
            "items",
            "payments",
            "created_at",
        ]
        read_only_fields = fields


class CheckoutItemSerializer(serializers.Serializer):
    product_id = serializers.IntegerField(required=True)
    quantity = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal("0.01"))


class CheckoutPaymentSerializer(serializers.Serializer):
    payment_method = serializers.ChoiceField(choices=["CASH", "UPI", "CARD", "CREDIT"])
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal("0.01"))
    transaction_reference = serializers.CharField(required=False, allow_blank=True, default="")
    notes = serializers.CharField(required=False, allow_blank=True, default="")


class CheckoutRequestSerializer(serializers.Serializer):
    customer_id = serializers.IntegerField(required=False, allow_null=True, default=None)
    items = CheckoutItemSerializer(many=True, required=True)
    discount_type = serializers.ChoiceField(choices=["FLAT", "PERCENTAGE"], default="FLAT")
    discount_value = serializers.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00"), min_value=Decimal("0.00")
    )
    payments = CheckoutPaymentSerializer(many=True, required=False, default=[])
    is_interstate = serializers.BooleanField(default=False)
    notes = serializers.CharField(required=False, allow_blank=True, default="")

    def validate_items(self, value):
        if not value:
            raise serializers.ValidationError("At least one item is required for checkout.")
        return value
