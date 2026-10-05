import hashlib
import json
import time
from decimal import Decimal
from django.db import IntegrityError, transaction
from django.db.models import Sum, Count, Q, F
from django.utils import timezone
from rest_framework import viewsets, status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.decorators import action
from django.core.exceptions import ValidationError

from accounts.tenancy import (
    IsCompanyMember,
    IsCashierOrAbove,
    IsManagerOrAbove,
    IsCompanyAdmin,
)
from sales.models import Customer, Sale, Payment, Invoice, CheckoutIdempotency
from inventory.models import Product
from sales.serializers import (
    CustomerSerializer,
    SaleSerializer,
    InvoiceSerializer,
    PaymentSerializer,
    CheckoutRequestSerializer,
)
from sales.services import SalesService


class CustomerViewSet(viewsets.ModelViewSet):
    """
    CRUD API for customer directory with search by name and phone.
    Access: Cashier and above.
    """
    serializer_class = CustomerSerializer
    permission_classes = [IsCashierOrAbove]

    def get_queryset(self):
        qs = Customer.objects.filter(company=self.request.company)
        search = self.request.query_params.get("search", "").strip()
        if search:
            qs = qs.filter(Q(name__icontains=search) | Q(phone__icontains=search))
        return qs

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        ctx["company"] = self.request.company
        return ctx

    def perform_create(self, serializer):
        serializer.save(company=self.request.company)


class SaleViewSet(viewsets.ReadOnlyModelViewSet):
    """
    API for viewing sales history with filters.
    Access: Cashier and above.
    """
    serializer_class = SaleSerializer
    permission_classes = [IsCashierOrAbove]

    def get_queryset(self):
        qs = Sale.objects.filter(company=self.request.company).select_related(
            "customer", "cashier", "invoice"
        ).prefetch_related("items", "payments")

        status_param = self.request.query_params.get("status")
        if status_param:
            qs = qs.filter(sale_status=status_param.upper())

        payment_status = self.request.query_params.get("payment_status")
        if payment_status:
            qs = qs.filter(payment_status=payment_status.upper())

        start_date = self.request.query_params.get("start_date")
        if start_date:
            qs = qs.filter(created_at__date__gte=start_date)

        end_date = self.request.query_params.get("end_date")
        if end_date:
            qs = qs.filter(created_at__date__lte=end_date)

        return qs


class InvoiceViewSet(viewsets.ReadOnlyModelViewSet):
    """
    API for retrieving invoices by ID or invoice_number.
    Access: Cashier and above.
    """
    serializer_class = InvoiceSerializer
    permission_classes = [IsCashierOrAbove]

    def get_queryset(self):
        qs = Invoice.objects.filter(company=self.request.company).select_related(
            "sale"
        ).prefetch_related("sale__items")

        number = self.request.query_params.get("number")
        if number:
            qs = qs.filter(invoice_number__icontains=number)

        phone = self.request.query_params.get("phone")
        if phone:
            qs = qs.filter(customer_phone__icontains=phone)

        return qs


class CheckoutAPIView(APIView):
    """
    Single atomic checkout endpoint:
    POST /api/sales/checkout/
    Validates items, stock, taxes, calculates authoritative totals,
    decrements stock, records sale, payments, and creates invoice.
    Supports Idempotency-Key header to guarantee at-most-once execution.
    """
    permission_classes = [IsCashierOrAbove]

    @staticmethod
    def _compute_payload_hash(data_dict):
        def default_serializer(obj):
            if isinstance(obj, Decimal):
                return str(obj)
            return str(obj)
        try:
            serialized = json.dumps(data_dict, default=default_serializer, sort_keys=True)
            return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        except Exception:
            return hashlib.sha256(str(data_dict).encode("utf-8")).hexdigest()

    def post(self, request):
        company = request.company
        cashier = request.user

        idempotency_key = (
            request.headers.get("Idempotency-Key")
            or request.META.get("HTTP_IDEMPOTENCY_KEY")
            or request.data.get("idempotency_key")
        )
        if idempotency_key:
            idempotency_key = str(idempotency_key).strip()

        idempotency_record = None
        req_hash = ""

        if idempotency_key:
            req_hash = self._compute_payload_hash(request.data)
            existing_rec = CheckoutIdempotency.objects.filter(
                company=company,
                idempotency_key=idempotency_key
            ).first()

            if existing_rec:
                if existing_rec.status == "COMPLETED":
                    if existing_rec.request_hash == req_hash:
                        resp = Response(
                            existing_rec.response_body,
                            status=existing_rec.response_status_code or status.HTTP_200_OK
                        )
                        resp["X-Idempotent-Replay"] = "true"
                        return resp
                    else:
                        return Response(
                            {"error": "Idempotency key reused with a different request payload."},
                            status=status.HTTP_409_CONFLICT
                        )
                elif existing_rec.status == "PROCESSING":
                    for _ in range(20):
                        time.sleep(0.1)
                        existing_rec.refresh_from_db()
                        if existing_rec.status == "COMPLETED":
                            if existing_rec.request_hash == req_hash:
                                resp = Response(
                                    existing_rec.response_body,
                                    status=existing_rec.response_status_code or status.HTTP_200_OK
                                )
                                resp["X-Idempotent-Replay"] = "true"
                                return resp
                            else:
                                return Response(
                                    {"error": "Idempotency key reused with a different request payload."},
                                    status=status.HTTP_409_CONFLICT
                                )
                        elif existing_rec.status == "FAILED":
                            break

                    if existing_rec.status == "PROCESSING":
                        return Response(
                            {"error": "A checkout request with this Idempotency-Key is currently being processed. Please retry shortly."},
                            status=status.HTTP_409_CONFLICT
                        )

                # Retry on previously failed attempt
                existing_rec.status = "PROCESSING"
                existing_rec.request_hash = req_hash
                existing_rec.user = cashier
                existing_rec.save(update_fields=["status", "request_hash", "user", "updated_at"])
                idempotency_record = existing_rec
            else:
                try:
                    with transaction.atomic():
                        idempotency_record = CheckoutIdempotency.objects.create(
                            company=company,
                            idempotency_key=idempotency_key,
                            user=cashier,
                            request_hash=req_hash,
                            status="PROCESSING"
                        )
                except IntegrityError:
                    winning_rec = CheckoutIdempotency.objects.filter(
                        company=company,
                        idempotency_key=idempotency_key
                    ).first()
                    if winning_rec and winning_rec.status == "COMPLETED" and winning_rec.request_hash == req_hash:
                        resp = Response(
                            winning_rec.response_body,
                            status=winning_rec.response_status_code or status.HTTP_200_OK
                        )
                        resp["X-Idempotent-Replay"] = "true"
                        return resp
                    return Response(
                        {"error": "A concurrent checkout request with this Idempotency-Key is in progress."},
                        status=status.HTTP_409_CONFLICT
                    )

        serializer = CheckoutRequestSerializer(data=request.data)
        if not serializer.is_valid():
            if idempotency_record:
                idempotency_record.status = "FAILED"
                idempotency_record.response_status_code = status.HTTP_400_BAD_REQUEST
                idempotency_record.response_body = serializer.errors
                idempotency_record.save(update_fields=["status", "response_status_code", "response_body", "updated_at"])
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data

        items_input = [
            {"product_id": item["product_id"], "quantity": item["quantity"]}
            for item in data["items"]
        ]

        try:
            sale, invoice = SalesService.checkout(
                company=company,
                cashier=cashier,
                items_data=items_input,
                customer_id=data.get("customer_id"),
                discount_type=data.get("discount_type", "FLAT"),
                discount_value=data.get("discount_value", Decimal("0.00")),
                payments_data=data.get("payments", []),
                is_interstate=data.get("is_interstate", False),
                notes=data.get("notes", ""),
            )

            response_data = {
                "message": "Checkout completed successfully.",
                "sale": SaleSerializer(sale).data,
                "invoice": InvoiceSerializer(invoice).data,
            }

            if idempotency_record:
                idempotency_record.status = "COMPLETED"
                idempotency_record.response_status_code = status.HTTP_201_CREATED
                idempotency_record.response_body = response_data
                idempotency_record.sale = sale
                idempotency_record.save(update_fields=["status", "response_status_code", "response_body", "sale", "updated_at"])

            return Response(response_data, status=status.HTTP_201_CREATED)
        except ValidationError as e:
            err_msg = str(e.message if hasattr(e, "message") else e)
            if idempotency_record:
                idempotency_record.status = "FAILED"
                idempotency_record.response_status_code = status.HTTP_400_BAD_REQUEST
                idempotency_record.response_body = {"error": err_msg}
                idempotency_record.save(update_fields=["status", "response_status_code", "response_body", "updated_at"])
            return Response(
                {"error": err_msg},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as e:
            err_msg = f"Checkout processing error: {str(e)}"
            if idempotency_record:
                idempotency_record.status = "FAILED"
                idempotency_record.response_status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
                idempotency_record.response_body = {"error": err_msg}
                idempotency_record.save(update_fields=["status", "response_status_code", "response_body", "updated_at"])
            return Response(
                {"error": err_msg},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )



class RecordPaymentAPIView(APIView):
    """
    Endpoint to record supplemental payments against a sale with balance due.
    POST /api/sales/payments/
    """
    permission_classes = [IsCashierOrAbove]

    def post(self, request):
        sale_id = request.data.get("sale_id")
        amount = request.data.get("amount")
        method = request.data.get("payment_method", "CASH").upper()
        ref = request.data.get("transaction_reference", "")
        notes = request.data.get("notes", "")

        if not sale_id or not amount:
            return Response({"error": "sale_id and amount are required."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            pay_amt = Decimal(str(amount))
            if pay_amt <= Decimal("0.00"):
                raise ValueError()
        except Exception:
            return Response({"error": "Amount must be a positive decimal."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            sale = Sale.objects.get(id=sale_id, company=request.company)
        except Sale.DoesNotExist:
            return Response({"error": "Sale not found."}, status=status.HTTP_404_NOT_FOUND)

        if sale.balance_due <= Decimal("0.00"):
            return Response({"error": "This sale is already fully paid."}, status=status.HTTP_400_BAD_REQUEST)

        if pay_amt > sale.balance_due:
            return Response(
                {"error": f"Payment amount (₹{pay_amt}) exceeds balance due (₹{sale.balance_due})."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        payment = Payment.objects.create(
            company=request.company,
            sale=sale,
            payment_method=method,
            amount=pay_amt,
            transaction_reference=ref,
            received_by=request.user,
            notes=notes,
        )

        sale.paid_amount += pay_amt
        sale.balance_due -= pay_amt
        if sale.balance_due == Decimal("0.00"):
            sale.payment_status = "PAID"
        else:
            sale.payment_status = "PARTIALLY_PAID"
        sale.save(update_fields=["paid_amount", "balance_due", "payment_status"])

        # Also update invoice
        if hasattr(sale, "invoice"):
            invoice = sale.invoice
            invoice.paid_amount = sale.paid_amount
            invoice.balance_due = sale.balance_due
            invoice.save(update_fields=["paid_amount", "balance_due"])

        return Response(PaymentSerializer(payment).data, status=status.HTTP_201_CREATED)


class DashboardMetricsAPIView(APIView):
    """
    Real dashboard metrics calculated dynamically from database tables.
    GET /api/dashboard/metrics/
    """
    permission_classes = [IsManagerOrAbove]

    def get(self, request):
        company = request.company
        today = timezone.now().date()

        # Sales metrics
        today_sales = Sale.objects.filter(company=company, created_at__date=today, sale_status="COMPLETED")
        today_revenue = today_sales.aggregate(total=Sum("grand_total"))["total"] or Decimal("0.00")
        today_invoices_count = today_sales.count()

        all_sales = Sale.objects.filter(company=company, sale_status="COMPLETED")
        total_revenue = all_sales.aggregate(total=Sum("grand_total"))["total"] or Decimal("0.00")
        total_sales_count = all_sales.count()

        # Product metrics
        total_products = Product.objects.filter(company=company).count()
        low_stock_products = Product.objects.filter(
            company=company,
            current_stock__gt=0,
            current_stock__lte=F("minimum_stock"),
        ).count()
        out_of_stock_products = Product.objects.filter(company=company, current_stock__lte=0).count()

        # Customer & Supplier metrics
        total_customers = Customer.objects.filter(company=company).count()
        from inventory.models import Supplier
        total_suppliers = Supplier.objects.filter(company=company).count()

        # Top 5 products by quantity sold
        from sales.models import SaleItem
        top_products = (
            SaleItem.objects.filter(sale__company=company, sale__sale_status="COMPLETED")
            .values("product_name", "product_code")
            .annotate(total_qty=Sum("quantity"), total_sales=Sum("total"))
            .order_by("-total_qty")[:5]
        )

        return Response({
            "today_revenue": today_revenue,
            "today_invoices_count": today_invoices_count,
            "total_revenue": total_revenue,
            "total_sales_count": total_sales_count,
            "total_products": total_products,
            "low_stock_products": low_stock_products,
            "out_of_stock_products": out_of_stock_products,
            "total_customers": total_customers,
            "total_suppliers": total_suppliers,
            "top_products": list(top_products),
        })
