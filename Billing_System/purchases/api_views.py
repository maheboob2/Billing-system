from rest_framework import viewsets, status
from rest_framework.views import APIView
from rest_framework.response import Response
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.core.exceptions import ValidationError

from accounts.tenancy import IsManagerOrAbove
from purchases.models import Purchase
from purchases.serializers import PurchaseSerializer
from inventory.services import StockService


class PurchaseViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = PurchaseSerializer
    permission_classes = [IsManagerOrAbove]

    def get_queryset(self):
        qs = Purchase.objects.filter(company=self.request.company).select_related(
            "supplier"
        ).prefetch_related("items__product")

        status_param = self.request.query_params.get("status")
        if status_param in ["DRAFT", "RECEIVED"]:
            qs = qs.filter(status=status_param)

        supplier_id = self.request.query_params.get("supplier")
        if supplier_id:
            qs = qs.filter(supplier_id=supplier_id)

        return qs


class ReceivePurchaseAPIView(APIView):
    """
    POST /api/purchases/<id>/receive/
    Transitions purchase order from DRAFT to RECEIVED,
    atomically increments stock, and writes StockMovement records.
    """
    permission_classes = [IsManagerOrAbove]

    def post(self, request, pk):
        company = request.company

        with transaction.atomic():
            purchase = get_object_or_404(
                Purchase.objects.select_for_update(),
                id=pk,
                company=company
            )

            if purchase.status == "RECEIVED":
                return Response(
                    {"error": "This purchase order has already been received."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            items = list(purchase.items.select_related("product"))
            if not items:
                return Response(
                    {"error": "Cannot receive purchase with no items."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            for item in items:
                if item.product.company_id != company.id:
                    return Response(
                        {"error": "Purchase item does not belong to company."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )

                StockService.adjust_stock(
                    company=company,
                    product=item.product,
                    movement_type="PURCHASE",
                    quantity=item.quantity,
                    user=request.user,
                    reference=f"PO #{purchase.invoice_number}",
                    reason="Purchase intake via API",
                )

            purchase.status = "RECEIVED"
            purchase.save(update_fields=["status"])

        return Response(PurchaseSerializer(purchase).data, status=status.HTTP_200_OK)
