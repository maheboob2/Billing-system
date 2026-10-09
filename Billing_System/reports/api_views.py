"""
Reports API Views - Phase 9
Provides real aggregated reporting from live database data.
All endpoints require Manager or Admin role (tenant-isolated).
"""

from decimal import Decimal
from django.db.models import Sum, Count, F, DecimalField, ExpressionWrapper
from django.db.models.functions import TruncDate, TruncMonth
from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
import datetime

from accounts.tenancy import IsManagerOrAbove
from sales.models import Sale, SaleItem, Payment, Customer
from inventory.models import Product, StockMovement
from purchases.models import Purchase, PurchaseItem


def _parse_date_params(request):
    """
    Parse start_date and end_date from query params.
    Returns (start_date, end_date, error_response_or_None).
    Defaults to last 30 days if not provided.
    """
    start_str = request.query_params.get("start_date", "")
    end_str = request.query_params.get("end_date", "")

    today = timezone.now().date()
    start_date = today - datetime.timedelta(days=29)
    end_date = today

    if start_str:
        try:
            start_date = datetime.date.fromisoformat(start_str)
        except ValueError:
            return None, None, Response(
                {"error": "Invalid start_date. Expected ISO format: YYYY-MM-DD."},
                status=status.HTTP_400_BAD_REQUEST,
            )

    if end_str:
        try:
            end_date = datetime.date.fromisoformat(end_str)
        except ValueError:
            return None, None, Response(
                {"error": "Invalid end_date. Expected ISO format: YYYY-MM-DD."},
                status=status.HTTP_400_BAD_REQUEST,
            )

    if start_date > end_date:
        return None, None, Response(
            {"error": "start_date cannot be later than end_date."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    return start_date, end_date, None


class SalesReportAPIView(APIView):
    """
    GET /api/reports/sales/?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD

    Aggregated sales report: revenue, discount, tax, daily breakdown,
    top products, payment method breakdown, sale status distribution.
    Access: Manager and Admin only.
    """
    permission_classes = [IsManagerOrAbove]

    def get(self, request):
        company = request.company
        start_date, end_date, err = _parse_date_params(request)
        if err:
            return err

        completed_sales = Sale.objects.filter(
            company=company,
            sale_status="COMPLETED",
            created_at__date__gte=start_date,
            created_at__date__lte=end_date,
        )

        summary = completed_sales.aggregate(
            total_revenue=Sum("grand_total"),
            total_subtotal=Sum("subtotal"),
            total_discount=Sum("discount_amount"),
            total_tax=Sum("tax_amount"),
            total_cgst=Sum("cgst_amount"),
            total_sgst=Sum("sgst_amount"),
            total_igst=Sum("igst_amount"),
            total_paid=Sum("paid_amount"),
            total_balance_due=Sum("balance_due"),
            transaction_count=Count("id"),
        )

        total_revenue = summary["total_revenue"] or Decimal("0.00")
        transaction_count = summary["transaction_count"] or 0
        avg_order_value = (
            (total_revenue / transaction_count).quantize(Decimal("0.01"))
            if transaction_count > 0
            else Decimal("0.00")
        )

        daily = (
            completed_sales
            .annotate(date=TruncDate("created_at"))
            .values("date")
            .annotate(
                revenue=Sum("grand_total"),
                orders=Count("id"),
                discount=Sum("discount_amount"),
                tax=Sum("tax_amount"),
            )
            .order_by("date")
        )

        top_products = (
            SaleItem.objects.filter(
                sale__company=company,
                sale__sale_status="COMPLETED",
                sale__created_at__date__gte=start_date,
                sale__created_at__date__lte=end_date,
            )
            .values("product_name", "product_code")
            .annotate(
                total_revenue=Sum("total"),
                total_qty=Sum("quantity"),
                total_discount=Sum("discount_amount"),
                total_tax=Sum("tax_amount"),
            )
            .order_by("-total_revenue")[:10]
        )

        payment_breakdown = (
            Payment.objects.filter(
                company=company,
                sale__sale_status="COMPLETED",
                sale__created_at__date__gte=start_date,
                sale__created_at__date__lte=end_date,
            )
            .values("payment_method")
            .annotate(total_amount=Sum("amount"), count=Count("id"))
            .order_by("-total_amount")
        )

        by_payment_status = (
            Sale.objects.filter(
                company=company,
                sale_status="COMPLETED",
                created_at__date__gte=start_date,
                created_at__date__lte=end_date,
            )
            .values("payment_status")
            .annotate(count=Count("id"), total=Sum("grand_total"))
        )

        all_sales_status = (
            Sale.objects.filter(
                company=company,
                created_at__date__gte=start_date,
                created_at__date__lte=end_date,
            )
            .values("sale_status")
            .annotate(count=Count("id"), total=Sum("grand_total"))
        )

        return Response({
            "period": {
                "start_date": str(start_date),
                "end_date": str(end_date),
            },
            "summary": {
                "total_revenue": summary["total_revenue"] or "0.00",
                "total_subtotal": summary["total_subtotal"] or "0.00",
                "total_discount": summary["total_discount"] or "0.00",
                "total_tax": summary["total_tax"] or "0.00",
                "total_cgst": summary["total_cgst"] or "0.00",
                "total_sgst": summary["total_sgst"] or "0.00",
                "total_igst": summary["total_igst"] or "0.00",
                "total_paid": summary["total_paid"] or "0.00",
                "total_balance_due": summary["total_balance_due"] or "0.00",
                "transaction_count": transaction_count,
                "avg_order_value": avg_order_value,
            },
            "daily_breakdown": list(daily),
            "top_products": list(top_products),
            "payment_method_breakdown": list(payment_breakdown),
            "by_payment_status": list(by_payment_status),
            "by_sale_status": list(all_sales_status),
        })


class InventoryReportAPIView(APIView):
    """
    GET /api/reports/inventory/?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD

    Stock summary, valuation, movement analysis.
    Access: Manager and Admin only.
    """
    permission_classes = [IsManagerOrAbove]

    def get(self, request):
        company = request.company
        start_date, end_date, err = _parse_date_params(request)
        if err:
            return err

        all_products = Product.objects.filter(company=company)
        total_products = all_products.count()
        active_products = all_products.filter(status=True).count()
        out_of_stock = all_products.filter(current_stock__lte=0).count()
        low_stock = all_products.filter(
            current_stock__gt=0,
            current_stock__lte=F("minimum_stock"),
        ).count()

        valuation = all_products.filter(status=True).aggregate(
            total_cost_value=Sum(
                ExpressionWrapper(
                    F("current_stock") * F("purchase_price"),
                    output_field=DecimalField(max_digits=18, decimal_places=2),
                )
            ),
            total_selling_value=Sum(
                ExpressionWrapper(
                    F("current_stock") * F("selling_price"),
                    output_field=DecimalField(max_digits=18, decimal_places=2),
                )
            ),
        )

        movements = StockMovement.objects.filter(
            company=company,
            created_at__date__gte=start_date,
            created_at__date__lte=end_date,
        )

        movement_summary = (
            movements.values("movement_type")
            .annotate(total_qty=Sum("quantity"), count=Count("id"))
            .order_by("movement_type")
        )

        top_moved = (
            movements
            .values("product__name", "product__product_code")
            .annotate(total_movement=Count("id"))
            .order_by("-total_movement")[:10]
        )

        low_stock_list = list(
            all_products.filter(
                status=True,
                current_stock__lte=F("minimum_stock"),
            ).values(
                "id", "name", "product_code", "current_stock", "minimum_stock", "unit"
            ).order_by("current_stock")[:20]
        )

        out_of_stock_list = list(
            all_products.filter(status=True, current_stock__lte=0)
            .values("id", "name", "product_code", "unit")[:20]
        )

        return Response({
            "period": {
                "start_date": str(start_date),
                "end_date": str(end_date),
            },
            "summary": {
                "total_products": total_products,
                "active_products": active_products,
                "out_of_stock_count": out_of_stock,
                "low_stock_count": low_stock,
                "total_cost_value": valuation["total_cost_value"] or "0.00",
                "total_selling_value": valuation["total_selling_value"] or "0.00",
            },
            "movement_by_type": list(movement_summary),
            "top_moved_products": list(top_moved),
            "low_stock_products": low_stock_list,
            "out_of_stock_products": out_of_stock_list,
        })


class PurchaseReportAPIView(APIView):
    """
    GET /api/reports/purchases/?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD

    Purchase spend, supplier breakdown, product breakdown.
    Access: Manager and Admin only.
    """
    permission_classes = [IsManagerOrAbove]

    def get(self, request):
        company = request.company
        start_date, end_date, err = _parse_date_params(request)
        if err:
            return err

        purchases = Purchase.objects.filter(
            company=company,
            purchase_date__gte=start_date,
            purchase_date__lte=end_date,
        )

        total_purchases = purchases.count()
        received_count = purchases.filter(status="RECEIVED").count()
        draft_count = purchases.filter(status="DRAFT").count()

        # Aggregate from PurchaseItem (Purchase.subtotal is a Python property, not a DB field)
        purchase_items = PurchaseItem.objects.filter(
            purchase__company=company,
            purchase__purchase_date__gte=start_date,
            purchase__purchase_date__lte=end_date,
        )

        item_agg = purchase_items.aggregate(
            total_subtotal=Sum(
                ExpressionWrapper(
                    F("quantity") * F("purchase_price"),
                    output_field=DecimalField(max_digits=18, decimal_places=2),
                )
            ),
            total_gst=Sum(
                ExpressionWrapper(
                    F("quantity") * F("purchase_price") * F("gst_percentage") / 100,
                    output_field=DecimalField(max_digits=18, decimal_places=2),
                )
            ),
            total_items=Count("id"),
        )

        total_subtotal = item_agg["total_subtotal"] or Decimal("0.00")
        total_gst = item_agg["total_gst"] or Decimal("0.00")
        grand_total = total_subtotal + total_gst

        by_supplier = (
            purchase_items
            .values("purchase__supplier__name")
            .annotate(
                total_spend=Sum(
                    ExpressionWrapper(
                        F("quantity") * F("purchase_price"),
                        output_field=DecimalField(max_digits=18, decimal_places=2),
                    )
                ),
                order_count=Count("purchase", distinct=True),
            )
            .order_by("-total_spend")[:10]
        )

        top_products = (
            purchase_items
            .values("product__name", "product__product_code")
            .annotate(
                total_qty=Sum("quantity"),
                total_spend=Sum(
                    ExpressionWrapper(
                        F("quantity") * F("purchase_price"),
                        output_field=DecimalField(max_digits=18, decimal_places=2),
                    )
                ),
            )
            .order_by("-total_spend")[:10]
        )

        by_status = (
            purchases.values("status")
            .annotate(count=Count("id"))
        )

        return Response({
            "period": {
                "start_date": str(start_date),
                "end_date": str(end_date),
            },
            "summary": {
                "total_purchases": total_purchases,
                "received_count": received_count,
                "draft_count": draft_count,
                "total_subtotal": total_subtotal,
                "total_gst": total_gst,
                "grand_total": grand_total,
            },
            "by_supplier": list(by_supplier),
            "top_products": list(top_products),
            "by_status": list(by_status),
        })


class FinancialReportAPIView(APIView):
    """
    GET /api/reports/financial/?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD

    P&L overview: revenue, COGS, gross profit, outstanding receivables,
    monthly trend, cash flow by payment method.
    Access: Manager and Admin only.
    """
    permission_classes = [IsManagerOrAbove]

    def get(self, request):
        company = request.company
        start_date, end_date, err = _parse_date_params(request)
        if err:
            return err

        completed_sales = Sale.objects.filter(
            company=company,
            sale_status="COMPLETED",
            created_at__date__gte=start_date,
            created_at__date__lte=end_date,
        )

        revenue_agg = completed_sales.aggregate(
            total_revenue=Sum("grand_total"),
            total_discount=Sum("discount_amount"),
            total_tax=Sum("tax_amount"),
            total_paid=Sum("paid_amount"),
            total_balance_due=Sum("balance_due"),
            count=Count("id"),
        )

        sale_items = SaleItem.objects.filter(
            sale__company=company,
            sale__sale_status="COMPLETED",
            sale__created_at__date__gte=start_date,
            sale__created_at__date__lte=end_date,
        )

        cogs_agg = sale_items.aggregate(
            total_cogs=Sum(
                ExpressionWrapper(
                    F("cost_price") * F("quantity"),
                    output_field=DecimalField(max_digits=18, decimal_places=2),
                )
            ),
        )

        total_revenue = revenue_agg["total_revenue"] or Decimal("0.00")
        total_tax = revenue_agg["total_tax"] or Decimal("0.00")
        total_cogs = cogs_agg["total_cogs"] or Decimal("0.00")
        revenue_ex_tax = total_revenue - total_tax
        gross_profit = revenue_ex_tax - total_cogs
        gross_margin_pct = (
            (gross_profit / revenue_ex_tax * 100).quantize(Decimal("0.01"))
            if revenue_ex_tax > Decimal("0.00")
            else Decimal("0.00")
        )

        purchase_items = PurchaseItem.objects.filter(
            purchase__company=company,
            purchase__status="RECEIVED",
            purchase__purchase_date__gte=start_date,
            purchase__purchase_date__lte=end_date,
        )
        purchase_cost_agg = purchase_items.aggregate(
            total_purchase_cost=Sum(
                ExpressionWrapper(
                    F("quantity") * F("purchase_price"),
                    output_field=DecimalField(max_digits=18, decimal_places=2),
                )
            )
        )

        credit_outstanding = Customer.objects.filter(
            company=company,
            credit_balance__gt=0,
        ).aggregate(
            total_outstanding=Sum("credit_balance"),
            customer_count=Count("id"),
        )

        today = timezone.now().date()
        twelve_months_ago = today - datetime.timedelta(days=365)

        monthly_trend = (
            Sale.objects.filter(
                company=company,
                sale_status="COMPLETED",
                created_at__date__gte=twelve_months_ago,
            )
            .annotate(month=TruncMonth("created_at"))
            .values("month")
            .annotate(revenue=Sum("grand_total"), count=Count("id"))
            .order_by("month")
        )

        cash_flow = (
            Payment.objects.filter(
                company=company,
                sale__sale_status="COMPLETED",
                sale__created_at__date__gte=start_date,
                sale__created_at__date__lte=end_date,
            )
            .values("payment_method")
            .annotate(total=Sum("amount"), count=Count("id"))
            .order_by("-total")
        )

        return Response({
            "period": {
                "start_date": str(start_date),
                "end_date": str(end_date),
            },
            "profit_loss": {
                "total_revenue": total_revenue,
                "total_tax_collected": total_tax,
                "revenue_ex_tax": revenue_ex_tax,
                "total_cogs": total_cogs,
                "gross_profit": gross_profit,
                "gross_margin_pct": gross_margin_pct,
                "total_discount_given": revenue_agg["total_discount"] or "0.00",
                "total_purchase_cost": purchase_cost_agg["total_purchase_cost"] or "0.00",
            },
            "receivables": {
                "total_balance_due": revenue_agg["total_balance_due"] or "0.00",
                "credit_outstanding_total": credit_outstanding["total_outstanding"] or "0.00",
                "credit_customers_count": credit_outstanding["customer_count"] or 0,
            },
            "monthly_revenue_trend": list(monthly_trend),
            "cash_flow_by_method": list(cash_flow),
            "transactions": {
                "total_completed_sales": revenue_agg["count"] or 0,
                "total_paid_collected": revenue_agg["total_paid"] or "0.00",
            },
        })


class TaxReportAPIView(APIView):
    """
    GET /api/reports/tax/?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD

    GST/Tax report: CGST, SGST, IGST collected, tax by rate slab,
    monthly trend, input vs output tax.
    Access: Manager and Admin only.
    """
    permission_classes = [IsManagerOrAbove]

    def get(self, request):
        company = request.company
        start_date, end_date, err = _parse_date_params(request)
        if err:
            return err

        completed_sales = Sale.objects.filter(
            company=company,
            sale_status="COMPLETED",
            created_at__date__gte=start_date,
            created_at__date__lte=end_date,
        )

        tax_agg = completed_sales.aggregate(
            total_tax=Sum("tax_amount"),
            total_cgst=Sum("cgst_amount"),
            total_sgst=Sum("sgst_amount"),
            total_igst=Sum("igst_amount"),
            total_revenue=Sum("grand_total"),
            transaction_count=Count("id"),
        )

        tax_by_slab = (
            SaleItem.objects.filter(
                sale__company=company,
                sale__sale_status="COMPLETED",
                sale__created_at__date__gte=start_date,
                sale__created_at__date__lte=end_date,
            )
            .values("gst_percentage")
            .annotate(
                taxable_amount=Sum(
                    ExpressionWrapper(
                        F("subtotal") - F("discount_amount"),
                        output_field=DecimalField(max_digits=18, decimal_places=2),
                    )
                ),
                total_tax=Sum("tax_amount"),
                items_count=Count("id"),
            )
            .order_by("gst_percentage")
        )

        monthly_tax = (
            completed_sales
            .annotate(month=TruncMonth("created_at"))
            .values("month")
            .annotate(
                cgst=Sum("cgst_amount"),
                sgst=Sum("sgst_amount"),
                igst=Sum("igst_amount"),
                total_tax=Sum("tax_amount"),
            )
            .order_by("month")
        )

        purchase_tax = PurchaseItem.objects.filter(
            purchase__company=company,
            purchase__status="RECEIVED",
            purchase__purchase_date__gte=start_date,
            purchase__purchase_date__lte=end_date,
        ).aggregate(
            input_tax=Sum(
                ExpressionWrapper(
                    F("quantity") * F("purchase_price") * F("gst_percentage") / 100,
                    output_field=DecimalField(max_digits=18, decimal_places=2),
                )
            ),
            total_purchases=Count("id"),
        )

        output_tax = tax_agg["total_tax"] or Decimal("0.00")
        input_tax = purchase_tax["input_tax"] or Decimal("0.00")
        net_gst_payable = output_tax - input_tax

        return Response({
            "period": {
                "start_date": str(start_date),
                "end_date": str(end_date),
            },
            "output_tax": {
                "total_tax_collected": output_tax,
                "total_cgst": tax_agg["total_cgst"] or "0.00",
                "total_sgst": tax_agg["total_sgst"] or "0.00",
                "total_igst": tax_agg["total_igst"] or "0.00",
                "total_revenue": tax_agg["total_revenue"] or "0.00",
                "transaction_count": tax_agg["transaction_count"] or 0,
            },
            "input_tax": {
                "total_purchase_tax": input_tax,
                "purchase_count": purchase_tax["total_purchases"] or 0,
            },
            "net_gst_payable": net_gst_payable,
            "tax_by_slab": list(tax_by_slab),
            "monthly_tax_trend": list(monthly_tax),
        })
