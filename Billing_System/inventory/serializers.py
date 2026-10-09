from rest_framework import serializers
from decimal import Decimal
from inventory.models import Category, Supplier, Product, StockMovement


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ["id", "name", "description", "status", "created_at"]
        read_only_fields = ["id", "created_at"]


class SupplierSerializer(serializers.ModelSerializer):
    class Meta:
        model = Supplier
        fields = [
            "id",
            "name",
            "contact_person",
            "phone",
            "alternate_phone",
            "email",
            "address",
            "city",
            "state",
            "pincode",
            "gst_number",
            "status",
            "created_at",
        ]
        read_only_fields = ["id", "created_at"]


class ProductSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source="category.name", read_only=True)
    supplier_name = serializers.CharField(source="supplier.name", read_only=True)
    is_low_stock = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = [
            "id",
            "name",
            "product_code",
            "barcode",
            "category",
            "category_name",
            "supplier",
            "supplier_name",
            "purchase_price",
            "selling_price",
            "gst_percentage",
            "current_stock",
            "minimum_stock",
            "unit",
            "status",
            "is_low_stock",
            "created_at",
        ]
        read_only_fields = ["id", "created_at", "is_low_stock"]

    def get_is_low_stock(self, obj):
        return obj.current_stock <= obj.minimum_stock

    def validate_product_code(self, value):
        company = self.context.get("company")
        if company:
            qs = Product.objects.filter(company=company, product_code=value)
            if self.instance:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise serializers.ValidationError(f"Product code '{value}' already exists in your company.")
        return value

    def validate_barcode(self, value):
        val = (value or "").strip()
        if val:
            company = self.context.get("company")
            if company:
                qs = Product.objects.filter(company=company, barcode=val)
                if self.instance:
                    qs = qs.exclude(pk=self.instance.pk)
                if qs.exists():
                    raise serializers.ValidationError(f"Barcode '{val}' already exists in your company.")
        return val

    def validate_purchase_price(self, value):
        if value < Decimal("0.00"):
            raise serializers.ValidationError("Purchase price cannot be negative.")
        return value

    def validate_selling_price(self, value):
        if value < Decimal("0.00"):
            raise serializers.ValidationError("Selling price cannot be negative.")
        return value

    def validate_current_stock(self, value):
        if value < Decimal("0.00"):
            raise serializers.ValidationError("Current stock cannot be negative.")
        return value


class StockMovementSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)
    product_code = serializers.CharField(source="product.product_code", read_only=True)
    user_name = serializers.SerializerMethodField()

    class Meta:
        model = StockMovement
        fields = [
            "id",
            "product",
            "product_name",
            "product_code",
            "movement_type",
            "quantity",
            "previous_stock",
            "new_stock",
            "reference",
            "user",
            "user_name",
            "reason",
            "created_at",
        ]
        read_only_fields = fields

    def get_user_name(self, obj):
        if obj.user:
            return obj.user.get_full_name() or obj.user.username
        return "System"


class StockAdjustmentSerializer(serializers.Serializer):
    product_id = serializers.IntegerField()
    quantity = serializers.DecimalField(max_digits=10, decimal_places=2)
    movement_type = serializers.ChoiceField(
        choices=["ADJUSTMENT", "DAMAGE", "WASTAGE", "RETURN"]
    )
    reason = serializers.CharField(required=True)
