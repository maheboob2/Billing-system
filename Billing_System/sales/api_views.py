import hashlib
import json
import secrets
import random
from datetime import timedelta
import time
from decimal import Decimal
from django.db import IntegrityError, transaction
from django.db.models import Sum, Count, Q, F
from django.utils import timezone
from rest_framework import viewsets, status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from django.core.exceptions import ValidationError

from accounts.tenancy import (
    IsCompanyMember,
    IsCashierOrAbove,
    IsManagerOrAbove,
    IsCompanyAdmin,
)
from sales.models import (
    Customer, Sale, SaleItem, Payment, Invoice, CheckoutIdempotency,
    POSScannerSession, POSScannerScan, ReturnRequest, ReturnItem
)
from inventory.models import Product, StockMovement
from inventory.services import StockService
from sales.serializers import (
    CustomerSerializer,
    SaleSerializer,
    InvoiceSerializer,
    PaymentSerializer,
    CheckoutRequestSerializer,
    POSScannerSessionSerializer,
    ReturnRequestSerializer,
    ReturnItemSerializer,
)
from sales.services import SalesService, quantize_money
from sales.network_utils import get_pos_scanner_base_url, get_phone_scanner_full_url

# Alias used in SalesQuoteAPIView for readability
sales_quantize = quantize_money



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
    throttle_scope = 'checkout'

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
                idempotency_record = None
                for lock_try in range(5):
                    try:
                        with transaction.atomic():
                            idempotency_record = CheckoutIdempotency.objects.create(
                                company=company,
                                idempotency_key=idempotency_key,
                                user=cashier,
                                request_hash=req_hash,
                                status="PROCESSING"
                            )
                        break
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
                    except Exception as e:
                        err_str = str(e).lower()
                        if ("database is locked" in err_str or "table is locked" in err_str or "locked" in err_str) and lock_try < 4:
                            time.sleep(0.15 * (lock_try + 1))
                            continue
                        raise e

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

        sale = None
        invoice = None
        for attempt in range(5):
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
                break
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
                err_str = str(e).lower()
                if ("database is locked" in err_str or "table is locked" in err_str or "locked" in err_str) and attempt < 4:
                    time.sleep(0.2 * (attempt + 1))
                    continue
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



class RecordPaymentAPIView(APIView):
    """
    Endpoint to record supplemental payments against a sale with balance due.
    POST /api/sales/payments/
    """
    permission_classes = [IsCashierOrAbove]
    throttle_scope = 'payment'

    VALID_PAYMENT_METHODS = {m[0] for m in Payment.PAYMENT_METHODS}
    MAX_REF_LENGTH = 100
    MAX_NOTES_LENGTH = 500

    def post(self, request):
        sale_id = request.data.get("sale_id")
        amount = request.data.get("amount")
        method = request.data.get("payment_method", "CASH").upper()
        ref = request.data.get("transaction_reference", "")[:self.MAX_REF_LENGTH]
        notes = request.data.get("notes", "")[:self.MAX_NOTES_LENGTH]

        if not sale_id or not amount:
            return Response({"error": "sale_id and amount are required."}, status=status.HTTP_400_BAD_REQUEST)

        if method not in self.VALID_PAYMENT_METHODS:
            return Response(
                {"error": f"Invalid payment method. Allowed: {', '.join(sorted(self.VALID_PAYMENT_METHODS))}."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            pay_amt = quantize_money(amount)
            if pay_amt <= Decimal("0.00"):
                raise ValueError()
        except Exception:
            return Response({"error": "Amount must be a positive decimal."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            with transaction.atomic():
                # Lock sale row to prevent concurrent payment race conditions
                sale = Sale.objects.select_for_update().get(id=sale_id, company=request.company)
        except Sale.DoesNotExist:
            return Response({"error": "Sale not found."}, status=status.HTTP_404_NOT_FOUND)

        if sale.balance_due <= Decimal("0.00"):
            return Response({"error": "This sale is already fully paid."}, status=status.HTTP_400_BAD_REQUEST)

        if pay_amt > sale.balance_due:
            return Response(
                {"error": f"Payment amount (₹{pay_amt}) exceeds balance due (₹{sale.balance_due})."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Validate CREDIT payment requires a customer on the sale
        if method == "CREDIT" and not sale.customer:
            return Response(
                {"error": "Store credit payments require a registered customer on the sale."},
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

        # If customer paid via Store Credit, adjust customer's credit balance
        if method == "CREDIT" and sale.customer:
            sale.customer.credit_balance += pay_amt
            sale.customer.save(update_fields=["credit_balance"])

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


class SalesQuoteAPIView(APIView):
    """
    Non-mutating sales quote/preview endpoint.
    POST /api/sales/quote/

    Returns authoritative subtotal, discount, tax, and grand total
    using the same calculation logic as checkout WITHOUT creating any
    Sale, Payment, Invoice, or changing any stock.

    Access: Cashier and above.
    """
    permission_classes = [IsCashierOrAbove]

    def post(self, request):
        from decimal import Decimal as D
        from django.core.exceptions import ValidationError as DjangoValidationError

        company = request.company

        # Deserialize using the existing checkout serializer (reuse validation)
        serializer = CheckoutRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data

        # Resolve customer if provided (read-only)
        customer_id = data.get("customer_id")
        customer_obj = None
        if customer_id:
            try:
                customer_obj = Customer.objects.get(id=customer_id, company=company)
            except Customer.DoesNotExist:
                return Response(
                    {"error": f"Customer with ID {customer_id} does not exist."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        items_data = [
            {"product_id": item["product_id"], "quantity": item["quantity"]}
            for item in data["items"]
        ]

        if not items_data:
            return Response(
                {"error": "At least one item is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        discount_type = data.get("discount_type", "FLAT")
        discount_value = data.get("discount_value", D("0.00"))
        is_interstate = data.get("is_interstate", False)
        payments_data = data.get("payments", [])

        # ----- Read-only calculation (mirrors SalesService.checkout but never persists) -----
        product_ids = [item["product_id"] for item in items_data]
        products_qs = Product.objects.filter(id__in=product_ids, company=company)
        locked_products = {p.id: p for p in products_qs}

        if len(locked_products) != len(set(product_ids)):
            missing = set(product_ids) - set(locked_products.keys())
            return Response(
                {"error": f"Products with IDs {missing} do not exist in your company catalog."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        discount_val = sales_quantize(discount_value)
        if discount_val < D("0.00"):
            return Response({"error": "Discount value cannot be negative."}, status=status.HTTP_400_BAD_REQUEST)

        line_items = []
        gross_subtotal = D("0.00")
        total_tax = D("0.00")
        total_cgst = D("0.00")
        total_sgst = D("0.00")
        total_igst = D("0.00")

        # Cumulative requested quantity check
        requested_cumulative = {}
        for item_input in items_data:
            p_id = item_input["product_id"]
            qty = sales_quantize(item_input.get("quantity", 1))
            if qty <= D("0.00"):
                return Response(
                    {"error": "Item quantity must be greater than zero."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            requested_cumulative[p_id] = requested_cumulative.get(p_id, D("0.00")) + qty

        for p_id, total_requested in requested_cumulative.items():
            prod = locked_products[p_id]
            if not prod.status:
                return Response(
                    {"error": f"Product '{prod.name}' is inactive and no longer available for sale."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if prod.current_stock < total_requested:
                return Response(
                    {
                        "error": f"Insufficient stock for '{prod.name}'. "
                        f"Available: {prod.current_stock} {prod.unit}, Requested: {total_requested} {prod.unit}."
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

        for item_input in items_data:
            p_id = item_input["product_id"]
            product = locked_products[p_id]
            qty = sales_quantize(item_input.get("quantity", 1))

            unit_price = sales_quantize(product.selling_price)
            gst_pct = sales_quantize(product.gst_percentage)
            line_subtotal = sales_quantize(unit_price * qty)
            gross_subtotal += line_subtotal

            line_items.append({
                "product_id": product.id,
                "product_name": product.name,
                "product_code": product.product_code,
                "unit_price": unit_price,
                "quantity": qty,
                "gst_percentage": gst_pct,
                "subtotal": line_subtotal,
            })

        # Discounts
        if discount_type == "PERCENTAGE":
            if discount_val > D("100.00"):
                return Response({"error": "Percentage discount cannot exceed 100%."}, status=status.HTTP_400_BAD_REQUEST)
            total_discount = sales_quantize(gross_subtotal * (discount_val / D("100.00")))
        else:
            if discount_val > gross_subtotal:
                return Response({"error": "Flat discount cannot exceed cart subtotal."}, status=status.HTTP_400_BAD_REQUEST)
            total_discount = discount_val

        discounted_subtotal = gross_subtotal - total_discount
        discount_ratio = (discounted_subtotal / gross_subtotal) if gross_subtotal > D("0.00") else D("0.00")

        for item in line_items:
            item_discount = sales_quantize(item["subtotal"] * (D("1.00") - discount_ratio))
            item_taxable = item["subtotal"] - item_discount
            item_tax = sales_quantize(item_taxable * (item["gst_percentage"] / D("100.00")))
            item_total = item_taxable + item_tax

            item["discount_amount"] = item_discount
            item["tax_amount"] = item_tax
            item["total"] = item_total

            total_tax += item_tax

            if is_interstate:
                total_igst += item_tax
            else:
                cgst = sales_quantize(item_tax / D("2.00"))
                sgst = item_tax - cgst
                total_cgst += cgst
                total_sgst += sgst

        grand_total = sales_quantize(gross_subtotal - total_discount + total_tax)

        # Payment computation for the quote (just show expected payment status)
        if payments_data:
            total_paid = sales_quantize(sum(
                sales_quantize(p.get("amount", D("0.00"))) for p in payments_data
            ))
        else:
            total_paid = grand_total

        balance_due = sales_quantize(max(D("0.00"), grand_total - total_paid))

        if total_paid >= grand_total:
            payment_status_preview = "PAID"
        elif total_paid > D("0.00"):
            payment_status_preview = "PARTIALLY_PAID"
        else:
            payment_status_preview = "UNPAID"

        # Credit check (informational only - no mutation)
        credit_warning = None
        if customer_obj and payments_data:
            for p in payments_data:
                if str(p.get("payment_method", "")).upper() == "CREDIT":
                    pay_amt = sales_quantize(p.get("amount", D("0.00")))
                    new_bal = customer_obj.credit_balance + pay_amt
                    if customer_obj.credit_limit > D("0.00") and new_bal > customer_obj.credit_limit:
                        credit_warning = (
                            f"Credit limit would be exceeded for '{customer_obj.name}'. "
                            f"Limit: ₹{customer_obj.credit_limit}, Current: ₹{customer_obj.credit_balance}, "
                            f"Would become: ₹{new_bal}."
                        )

        response_data = {
            "quote": {
                "subtotal": gross_subtotal,
                "discount_type": discount_type,
                "discount_value": discount_val,
                "discount_amount": total_discount,
                "tax_amount": total_tax,
                "cgst_amount": total_cgst,
                "sgst_amount": total_sgst,
                "igst_amount": total_igst,
                "grand_total": grand_total,
                "paid_amount": total_paid,
                "balance_due": balance_due,
                "payment_status_preview": payment_status_preview,
                "is_interstate": is_interstate,
            },
            "items": line_items,
        }

        if customer_obj:
            response_data["customer"] = {
                "id": customer_obj.id,
                "name": customer_obj.name,
                "phone": customer_obj.phone,
                "credit_balance": customer_obj.credit_balance,
                "credit_limit": customer_obj.credit_limit,
            }

        if credit_warning:
            response_data["warnings"] = [credit_warning]

        return Response(response_data, status=status.HTTP_200_OK)


class POSScannerSessionCreateAPIView(APIView):
    """
    Creates a temporary pairing session for phone camera scanner.
    Resolves LAN-accessible IP/URL with zero loopback exposure.
    Access: Cashier and above.
    """
    permission_classes = [IsCashierOrAbove]

    def post(self, request):
        company = request.company
        session_token = secrets.token_urlsafe(24)
        pairing_code = f"{random.randint(100, 999)}-{random.randint(100, 999)}"
        expires_at = timezone.now() + timedelta(minutes=30)

        session = POSScannerSession.objects.create(
            company=company,
            user=request.user,
            session_token=session_token,
            pairing_code=pairing_code,
            expires_at=expires_at,
        )

        url_info = get_phone_scanner_full_url(session.session_token, request=request)

        return Response(
            {
                "session_token": session.session_token,
                "pairing_code": session.pairing_code,
                "connect_url": f"/pos/scanner/?session={session.session_token}",
                "lan_connect_url": url_info["phone_url"],
                "lan_ip": url_info["lan_ip"],
                "base_url": url_info["base_url"],
                "url_source": url_info["source"],
                "is_configured": url_info["is_configured"],
                "is_https": url_info["is_https"],
                "all_lan_ips": url_info["all_lan_ips"],
                "expires_at": session.expires_at.isoformat(),
            },
            status=status.HTTP_201_CREATED,
        )


class POSScannerSessionPollAPIView(APIView):
    """
    Polls pending scans for an active POS scanner session.
    Tracks explicit connection state (WAITING vs CONNECTED vs DISCONNECTED).
    Access: Cashier and above.
    """
    permission_classes = [IsCashierOrAbove]

    def get(self, request, token):
        company = request.company
        session = POSScannerSession.objects.filter(
            session_token=token,
            company=company
        ).first()

        if not session or not session.is_valid():
            return Response(
                {
                    "active": False,
                    "status": session.status if session else "EXPIRED",
                    "connected": False,
                    "device_info": "",
                    "scans": [],
                },
                status=status.HTTP_200_OK,
            )

        # Phone is considered actively connected if it pinged within the last 40 seconds
        time_since_ping = (timezone.now() - session.last_ping).total_seconds()
        is_connected = bool(session.device_info and time_since_ping <= 40)

        unconsumed_scans = list(session.scans.filter(consumed=False).order_by("created_at"))
        codes = [s.barcode for s in unconsumed_scans]

        if unconsumed_scans:
            session.scans.filter(id__in=[s.id for s in unconsumed_scans]).update(consumed=True)

        return Response(
            {
                "active": True,
                "status": session.status,
                "connected": is_connected,
                "device_info": session.device_info if is_connected else "",
                "last_ping_seconds_ago": int(time_since_ping),
                "scans": codes,
            },
            status=status.HTTP_200_OK,
        )


class POSScannerSessionScanAPIView(APIView):
    """
    Endpoint called by phone camera client to submit a scanned barcode or ping heartbeat.
    Session-token authorized; safe for phone camera on local Wi-Fi.
    """
    permission_classes = [AllowAny]

    def post(self, request, token):
        session = POSScannerSession.objects.filter(session_token=token).first()
        if not session or not session.is_valid():
            return Response(
                {"error": "Scanner pairing session is invalid or expired."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        now = timezone.now()
        fields_to_update = ["last_ping"]
        session.last_ping = now

        device_info = request.data.get("device_info", "")
        if device_info and device_info != session.device_info:
            session.device_info = device_info
            fields_to_update.append("device_info")

        if session.status != "ACTIVE":
            session.status = "ACTIVE"
            fields_to_update.append("status")

        session.save(update_fields=fields_to_update)

        barcode = request.data.get("barcode", "").strip()

        # Allow handshake/heartbeat ping to establish device presence without creating dummy scan
        if request.data.get("heartbeat") or barcode in ("__PING__", "__HEARTBEAT__"):
            return Response(
                {
                    "success": True,
                    "connected": True,
                    "device_info": session.device_info,
                },
                status=status.HTTP_200_OK,
            )

        if not barcode:
            return Response(
                {"error": "Barcode value is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        scan = POSScannerScan.objects.create(session=session, barcode=barcode)
        return Response(
            {
                "success": True,
                "barcode": scan.barcode,
                "scanned_at": scan.created_at.isoformat(),
            },
            status=status.HTTP_201_CREATED,
        )


class POSScannerSessionDisconnectAPIView(APIView):
    """
    Disconnects an active phone scanner pairing session.
    AllowAny so both PC cashier and phone companion client can gracefully disconnect.
    """
    permission_classes = [AllowAny]

    def post(self, request, token):
        session = POSScannerSession.objects.filter(session_token=token).first()
        if session:
            session.status = "DISCONNECTED"
            session.device_info = ""
            session.save(update_fields=["status", "device_info"])

        return Response({"success": True, "status": "DISCONNECTED"}, status=status.HTTP_200_OK)


class POSScannerNetworkDiagnosticsAPIView(APIView):
    """
    Returns server network interface diagnostics for POS Scanner settings and companion setup.
    Access: Cashier and above.
    """
    permission_classes = [IsCashierOrAbove]

    def get(self, request):
        base_info = get_pos_scanner_base_url(request=request)
        return Response(
            {
                "base_url": base_info["base_url"],
                "lan_ip": base_info["lan_ip"],
                "port": base_info["port"],
                "url_source": base_info["source"],
                "is_configured": base_info["is_configured"],
                "is_https": base_info["is_https"],
                "all_lan_ips": base_info["all_lan_ips"],
                "server_host": request.get_host(),
                "windows_firewall_hint": "Ensure Python or port 8000 is allowed through Windows Defender Firewall for inbound connections.",
                "wifi_isolation_hint": "Phone and PC must be on the same Wi-Fi network with AP/Client Isolation disabled in router.",
            },
            status=status.HTTP_200_OK,
        )


class ReturnRequestViewSet(viewsets.ModelViewSet):
    """
    Return and Refund Management API.
    - Cashiers can view and submit return requests.
    - Only Managers and above can Approve or Reject return requests.
    - Approval restores inventory stock safely with row locking and audit trail.
    - Historical Sale and SaleItem records are permanently preserved.
    """
    serializer_class = ReturnRequestSerializer

    def get_permissions(self):
        if self.action in ["list", "retrieve", "create", "approve", "reject"]:
            return [IsCashierOrAbove()]
        return [IsManagerOrAbove()]

    def get_queryset(self):
        qs = ReturnRequest.objects.filter(company=self.request.company).select_related(
            "sale", "invoice", "customer", "created_by", "approved_by"
        ).prefetch_related("items__product")

        status_param = self.request.query_params.get("status")
        if status_param:
            qs = qs.filter(status=status_param.upper())

        search = self.request.query_params.get("search", "").strip()
        if search:
            qs = qs.filter(
                Q(return_number__icontains=search) |
                Q(sale__sale_number__icontains=search) |
                Q(customer__name__icontains=search)
            )

        return qs

    def create(self, request, *args, **kwargs):
        company = request.company
        sale_id = request.data.get("sale_id")
        sale_number = request.data.get("sale_number", "").strip()
        invoice_number = request.data.get("invoice_number", "").strip()

        sale = None
        if sale_id:
            sale = Sale.objects.filter(company=company, id=sale_id).first()
        elif sale_number:
            sale = Sale.objects.filter(company=company, sale_number=sale_number).first()
        elif invoice_number:
            sale = Sale.objects.filter(
                company=company
            ).filter(
                Q(invoice__invoice_number=invoice_number) | Q(sale_number=invoice_number)
            ).first()

        if not sale:
            return Response(
                {"error": "Original sale/invoice not found for this company."},
                status=status.HTTP_404_NOT_FOUND,
            )

        items_data = request.data.get("items", [])
        if not items_data:
            return Response(
                {"error": "At least one item must be selected for return."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        reason = request.data.get("reason", "CUSTOMER_CHANGED_MIND").upper()
        if reason not in dict(ReturnRequest.REASON_CHOICES):
            reason = "OTHER"

        notes = request.data.get("notes", "").strip()
        refund_method = request.data.get("refund_payment_method", "CASH").upper()
        if refund_method not in dict(Payment.PAYMENT_METHODS):
            refund_method = "CASH"

        total_refund_amount = Decimal("0.00")
        validated_items = []

        for item_entry in items_data:
            sale_item_id = item_entry.get("sale_item_id")
            return_qty = Decimal(str(item_entry.get("quantity", "0")))
            restock = bool(item_entry.get("restock", True))

            if return_qty <= Decimal("0.00"):
                continue

            sale_item = SaleItem.objects.filter(sale=sale, id=sale_item_id).first()
            if not sale_item:
                return Response(
                    {"error": f"Item {sale_item_id} does not belong to sale {sale.sale_number}."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Check already returned quantity for this sale item
            already_returned = ReturnItem.objects.filter(
                sale_item=sale_item,
                return_request__status__in=["PENDING", "APPROVED", "COMPLETED"]
            ).aggregate(total=Sum("quantity"))["total"] or Decimal("0.00")

            max_eligible_qty = sale_item.quantity - already_returned
            if return_qty > max_eligible_qty:
                return Response(
                    {
                        "error": f"Requested return quantity ({return_qty}) exceeds eligible remaining quantity ({max_eligible_qty}) for '{sale_item.product_name}'."
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            unit_refund = quantize_money(sale_item.total / sale_item.quantity)
            line_refund = quantize_money(unit_refund * return_qty)
            total_refund_amount += line_refund

            validated_items.append({
                "sale_item": sale_item,
                "product": sale_item.product,
                "quantity": return_qty,
                "refund_unit_price": unit_refund,
                "refund_total": line_refund,
                "restock": restock,
            })

        if not validated_items:
            return Response(
                {"error": "No valid return quantities specified."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return_number = f"RET-{company.id}-{int(time.time())}-{secrets.token_hex(4).upper()}"

        with transaction.atomic():
            ret_req = ReturnRequest.objects.create(
                company=company,
                return_number=return_number,
                sale=sale,
                invoice=getattr(sale, "invoice", None),
                customer=sale.customer,
                status="PENDING",
                reason=reason,
                refund_amount=total_refund_amount,
                refund_payment_method=refund_method,
                notes=notes,
                created_by=request.user,
            )

            for v in validated_items:
                ReturnItem.objects.create(
                    return_request=ret_req,
                    sale_item=v["sale_item"],
                    product=v["product"],
                    quantity=v["quantity"],
                    refund_unit_price=v["refund_unit_price"],
                    refund_total=v["refund_total"],
                    restock=v["restock"],
                )

        try:
            from dashboard.sync_service import SyncService
            SyncService.queue_return(ret_req)
        except Exception:
            pass

        serializer = ReturnRequestSerializer(ret_req)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="approve")
    def approve(self, request, pk=None):
        company = request.company
        return_req = ReturnRequest.objects.filter(company=company, pk=pk).first()
        if not return_req:
            return Response({"error": "Return request not found."}, status=status.HTTP_404_NOT_FOUND)

        if return_req.status != "PENDING":
            return Response(
                {"error": f"Cannot approve return in status '{return_req.status}'."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Manager Authorization Resolution:
        approver = request.user
        if request.user_role not in ["Admin", "Manager"]:
            # CASE B: Cashier is logged in, Manager is physically present entering PIN/credentials
            mgr_user_name = request.data.get("manager_username", "").strip()
            mgr_pin = request.data.get("manager_pin", "").strip()
            if not mgr_user_name or not mgr_pin:
                return Response(
                    {"error": "Manager approval required. Please provide manager credentials or approval PIN."},
                    status=status.HTTP_403_FORBIDDEN
                )

            from django.contrib.auth import authenticate
            authenticated_mgr = authenticate(username=mgr_user_name, password=mgr_pin)
            if not authenticated_mgr:
                return Response(
                    {"error": "Invalid manager credentials or PIN."},
                    status=status.HTTP_403_FORBIDDEN
                )

            from accounts.tenancy import get_user_role, get_user_company
            mgr_comp = get_user_company(authenticated_mgr)
            mgr_role = get_user_role(authenticated_mgr)
            if mgr_comp != company or mgr_role not in ["Admin", "Manager"]:
                return Response(
                    {"error": "Provided credentials do not belong to an authorized Manager for this company."},
                    status=status.HTTP_403_FORBIDDEN
                )
            approver = authenticated_mgr

        with transaction.atomic():
            # 1. Restock items that are flagged for restock
            for item in return_req.items.select_related("product"):
                if item.restock:
                    StockService.adjust_stock(
                        company=company,
                        product=item.product,
                        movement_type="RETURN",
                        quantity=item.quantity,
                        user=approver,
                        reference=return_req.return_number,
                        reason=f"Customer Return: {return_req.get_reason_display()}",
                    )
                else:
                    # Item is DAMAGED / DEFECTIVE:
                    # Must NOT be returned to sellable stock. Record quarantine audit trail.
                    StockMovement.objects.create(
                        company=company,
                        product=item.product,
                        movement_type="DAMAGE",
                        quantity=-abs(item.quantity),
                        previous_stock=item.product.current_stock,
                        new_stock=item.product.current_stock,
                        reference=return_req.return_number,
                        user=approver,
                        reason=f"Defective/Damaged Return (Quarantine - Not Sellable): {return_req.get_reason_display()}",
                    )

            # 2. Record refund transaction / payment audit record
            Payment.objects.create(
                company=company,
                sale=return_req.sale,
                payment_method=return_req.refund_payment_method,
                amount=-return_req.refund_amount,
                transaction_reference=f"REFUND-{return_req.return_number}",
                received_by=approver,
                notes=f"Approved refund for Return {return_req.return_number} (by {approver.username})",
            )

            # 3. Update return request status
            return_req.status = "APPROVED"
            return_req.approved_by = approver
            return_req.approved_at = timezone.now()
            return_req.save()

        serializer = ReturnRequestSerializer(return_req)
        return Response(
            {
                "message": "Return request approved successfully. Stock restored and refund recorded.",
                "return": serializer.data,
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["post"], url_path="reject")
    def reject(self, request, pk=None):
        company = request.company
        return_req = ReturnRequest.objects.filter(company=company, pk=pk).first()
        if not return_req:
            return Response({"error": "Return request not found."}, status=status.HTTP_404_NOT_FOUND)

        if return_req.status != "PENDING":
            return Response(
                {"error": f"Cannot reject return in status '{return_req.status}'."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Manager Authorization Resolution:
        approver = request.user
        if request.user_role not in ["Admin", "Manager"]:
            mgr_user_name = request.data.get("manager_username", "").strip()
            mgr_pin = request.data.get("manager_pin", "").strip()
            if not mgr_user_name or not mgr_pin:
                return Response(
                    {"error": "Manager approval required. Please provide manager credentials or approval PIN."},
                    status=status.HTTP_403_FORBIDDEN
                )

            from django.contrib.auth import authenticate
            authenticated_mgr = authenticate(username=mgr_user_name, password=mgr_pin)
            if not authenticated_mgr:
                return Response(
                    {"error": "Invalid manager credentials or PIN."},
                    status=status.HTTP_403_FORBIDDEN
                )

            from accounts.tenancy import get_user_role, get_user_company
            mgr_comp = get_user_company(authenticated_mgr)
            mgr_role = get_user_role(authenticated_mgr)
            if mgr_comp != company or mgr_role not in ["Admin", "Manager"]:
                return Response(
                    {"error": "Provided credentials do not belong to an authorized Manager for this company."},
                    status=status.HTTP_403_FORBIDDEN
                )
            approver = authenticated_mgr

        rejection_reason = request.data.get("reason", "").strip() or "Rejected by manager."

        return_req.status = "REJECTED"
        return_req.rejection_reason = rejection_reason
        return_req.approved_by = approver
        return_req.approved_at = timezone.now()
        return_req.save()

        serializer = ReturnRequestSerializer(return_req)
        return Response(
            {
                "message": "Return request rejected.",
                "return": serializer.data,
            },
            status=status.HTTP_200_OK,
        )

