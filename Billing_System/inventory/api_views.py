from rest_framework import viewsets, status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.decorators import action
from django.db.models import Q, F
from django.core.exceptions import ValidationError

from accounts.tenancy import (
    IsCompanyMember,
    IsCashierOrAbove,
    IsManagerOrAbove,
    IsCompanyAdmin,
)
from inventory.models import Category, Supplier, Product, StockMovement
from inventory.serializers import (
    CategorySerializer,
    SupplierSerializer,
    ProductSerializer,
    StockMovementSerializer,
    StockAdjustmentSerializer,
)
from inventory.services import StockService


class CategoryViewSet(viewsets.ModelViewSet):
    serializer_class = CategorySerializer

    def get_permissions(self):
        if self.action in ["list", "retrieve"]:
            return [IsCashierOrAbove()]
        return [IsManagerOrAbove()]

    def get_queryset(self):
        return Category.objects.filter(company=self.request.company)

    def perform_create(self, serializer):
        serializer.save(company=self.request.company)


class SupplierViewSet(viewsets.ModelViewSet):
    serializer_class = SupplierSerializer

    def get_permissions(self):
        if self.action in ["list", "retrieve"]:
            return [IsCashierOrAbove()]
        return [IsManagerOrAbove()]

    def get_queryset(self):
        return Supplier.objects.filter(company=self.request.company)

    def perform_create(self, serializer):
        serializer.save(company=self.request.company)


class ProductViewSet(viewsets.ModelViewSet):
    serializer_class = ProductSerializer

    def get_permissions(self):
        if self.action in ["list", "retrieve", "lookup"]:
            return [IsCashierOrAbove()]
        return [IsManagerOrAbove()]

    def get_queryset(self):
        qs = Product.objects.filter(company=self.request.company).select_related(
            "category", "supplier"
        )
        search = self.request.query_params.get("search", "").strip()
        if search:
            qs = qs.filter(
                Q(name__icontains=search) |
                Q(product_code__icontains=search) |
                Q(barcode__icontains=search)
            )

        category_id = self.request.query_params.get("category")
        if category_id:
            qs = qs.filter(category_id=category_id)

        low_stock = self.request.query_params.get("low_stock")
        if low_stock and low_stock.lower() in ["true", "1"]:
            qs = qs.filter(current_stock__lte=F("minimum_stock"))

        barcode = self.request.query_params.get("barcode")
        if barcode:
            qs = qs.filter(barcode=barcode)

        return qs

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        ctx["company"] = self.request.company
        return ctx

    def perform_create(self, serializer):
        serializer.save(company=self.request.company)

    @action(detail=False, methods=["get"], url_path="lookup")
    def lookup(self, request):
        code = request.query_params.get("code", "").strip()
        if not code:
            return Response(
                {"found": False, "error": "Barcode or SKU code is required."},
                status=status.HTTP_400_BAD_REQUEST
            )

        product = Product.objects.filter(company=request.company).filter(
            Q(barcode__iexact=code) | Q(product_code__iexact=code)
        ).first()

        if not product:
            return Response(
                {"found": False, "error": f"Product with code '{code}' not found."},
                status=status.HTTP_404_NOT_FOUND
            )

        if not product.status:
            return Response(
                {
                    "found": True,
                    "active": False,
                    "error": f"Product '{product.name}' is inactive and cannot be sold.",
                    "product": ProductSerializer(product).data,
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        return Response(
            {
                "found": True,
                "active": True,
                "product": ProductSerializer(product).data,
            },
            status=status.HTTP_200_OK
        )

    @action(detail=False, methods=["get", "post"], url_path="generate-barcode")
    def generate_barcode(self, request):
        from inventory.services import generate_internal_barcode
        barcode = generate_internal_barcode(request.company)
        return Response(
            {
                "success": True,
                "barcode": barcode,
                "type": "CODE128_INTERNAL",
            },
            status=status.HTTP_200_OK
        )

    @action(detail=False, methods=["get"], url_path="check-barcode")
    def check_barcode(self, request):
        code = request.query_params.get("barcode", "").strip() or request.query_params.get("code", "").strip()
        exclude_id = request.query_params.get("exclude_id", "").strip()
        if not code:
            return Response(
                {"available": False, "error": "Barcode parameter is required."},
                status=status.HTTP_400_BAD_REQUEST
            )

        qs = Product.objects.filter(company=request.company, barcode=code)
        if exclude_id and exclude_id.isdigit():
            qs = qs.exclude(id=int(exclude_id))

        exists = qs.exists()
        matched = qs.first() if exists else None
        return Response(
            {
                "available": not exists,
                "exists": exists,
                "barcode": code,
                "product_name": matched.name if matched else None,
                "conflict_product": matched.name if matched else None,
            },
            status=status.HTTP_200_OK
        )


class StockMovementViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Read-only audit ledger for stock movements.
    Access: Manager and above.
    """
    serializer_class = StockMovementSerializer
    permission_classes = [IsManagerOrAbove]

    def get_queryset(self):
        qs = StockMovement.objects.filter(company=self.request.company).select_related(
            "product", "user"
        )
        movement_type = self.request.query_params.get("movement_type")
        if movement_type:
            qs = qs.filter(movement_type=movement_type.upper())

        product_id = self.request.query_params.get("product_id")
        if product_id:
            qs = qs.filter(product_id=product_id)

        return qs


class StockAdjustmentAPIView(APIView):
    """
    Endpoint for authorized staff to adjust stock with audit reasons.
    POST /api/inventory/adjust/
    """
    permission_classes = [IsManagerOrAbove]

    def post(self, request):
        serializer = StockAdjustmentSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data
        company = request.company

        try:
            product = Product.objects.get(id=data["product_id"], company=company)
        except Product.DoesNotExist:
            return Response({"error": "Product not found."}, status=status.HTTP_404_NOT_FOUND)

        try:
            updated_product, movement = StockService.adjust_stock(
                company=company,
                product=product,
                movement_type=data["movement_type"],
                quantity=data["quantity"],
                user=request.user,
                reason=data["reason"],
                reference="Manual Adjustment",
            )

            return Response({
                "message": "Stock adjusted successfully.",
                "product": ProductSerializer(updated_product).data,
                "movement": StockMovementSerializer(movement).data,
            }, status=status.HTTP_200_OK)
        except ValidationError as e:
            return Response(
                {"error": str(e.message if hasattr(e, "message") else e)},
                status=status.HTTP_400_BAD_REQUEST,
            )
