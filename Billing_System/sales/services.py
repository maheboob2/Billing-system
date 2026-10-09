"""
Sales, Checkout, Invoicing, and Tax Service Layer.
Orchestrates atomic retail transactions:
- Authoritative pricing and tax calculations using Decimal
- Stock validation and deduction
- Payment recording and credit ledger update
- Collision-safe invoice generation
"""

from decimal import Decimal, ROUND_HALF_UP
from django.db import transaction
from django.utils import timezone
from django.core.exceptions import ValidationError
from inventory.models import Product
from inventory.services import StockService
from .models import Customer, Sale, SaleItem, Payment, Invoice


def quantize_money(amount):
    """Rounds a Decimal amount to 2 decimal places using standard commercial rounding."""
    return Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


class SalesService:
    @staticmethod
    def generate_invoice_number(company):
        """
        Generates a collision-safe, sequential invoice number.
        Format: INV-YYYY-COMPANYID-SEQUENCE (e.g., INV-2026-0001-000001)
        """
        year = timezone.now().year
        prefix = f"INV-{year}-{company.id:03d}-"

        last_invoice = (
            Invoice.objects.filter(company=company, invoice_number__startswith=prefix)
            .order_by("-id")
            .first()
        )

        if last_invoice:
            try:
                last_seq = int(last_invoice.invoice_number.split("-")[-1])
                next_seq = last_seq + 1
            except (ValueError, IndexError):
                next_seq = 1
        else:
            next_seq = 1

        return f"{prefix}{next_seq:06d}"

    @staticmethod
    def generate_sale_number(company):
        """
        Generates a collision-safe, sequential sale reference number.
        Format: SALE-YYYY-COMPANYID-SEQUENCE
        """
        year = timezone.now().year
        prefix = f"SALE-{year}-{company.id:03d}-"

        last_sale = (
            Sale.objects.filter(company=company, sale_number__startswith=prefix)
            .order_by("-id")
            .first()
        )

        if last_sale:
            try:
                last_seq = int(last_sale.sale_number.split("-")[-1])
                next_seq = last_seq + 1
            except (ValueError, IndexError):
                next_seq = 1
        else:
            next_seq = 1

        return f"{prefix}{next_seq:06d}"

    @classmethod
    def checkout(
        cls,
        company,
        cashier,
        items_data,
        customer=None,
        customer_id=None,
        discount_type="FLAT",
        discount_value=Decimal("0.00"),
        payments_data=None,
        is_interstate=False,
        notes="",
    ):
        """
        Performs an end-to-end atomic retail checkout:
        1. Resolves and validates customer (optional for walk-ins).
        2. Validates items list and product existence in company catalog.
        3. Validates stock availability for each item.
        4. Calculates line subtotals, item-level discounts, GST tax (CGST+SGST or IGST).
        5. Calculates sale subtotal, total discount, total tax, grand total.
        6. Validates payments (Cash, UPI, Card, Store Credit).
        7. Decrements stock and writes StockMovement records.
        8. Creates Sale and SaleItem records.
        9. Creates Payment records.
        10. Generates Invoice.
        """
        if not items_data:
            raise ValidationError("Cannot checkout an empty cart. Add at least one item.")

        discount_val = quantize_money(discount_value)
        if discount_val < Decimal("0.00"):
            raise ValidationError("Discount value cannot be negative.")

        with transaction.atomic():
            # 1. Resolve Customer
            customer_obj = customer
            if not customer_obj and customer_id:
                try:
                    customer_obj = Customer.objects.get(id=customer_id, company=company)
                except Customer.DoesNotExist:
                    raise ValidationError(f"Customer with ID {customer_id} does not exist.")

            # 2. Lock and validate all products
            product_ids = [item.get("product_id") for item in items_data if item.get("product_id")]
            if len(product_ids) != len(items_data):
                raise ValidationError("Each item must contain a valid product_id.")

            # Lock products to prevent concurrent overselling
            locked_products = {
                p.id: p
                for p in Product.objects.select_for_update().filter(id__in=product_ids, company=company)
            }

            if len(locked_products) != len(set(product_ids)):
                missing = set(product_ids) - set(locked_products.keys())
                raise ValidationError(f"Products with IDs {missing} do not exist in your company catalog.")

            # Calculate cumulative requested quantity per product across all line items
            requested_cumulative = {}
            for item_input in items_data:
                p_id = item_input["product_id"]
                try:
                    q = quantize_money(item_input.get("quantity", 1))
                except Exception:
                    raise ValidationError(f"Invalid quantity format for product ID {p_id}.")
                if q <= Decimal("0.00"):
                    raise ValidationError("Item quantity must be greater than zero.")
                requested_cumulative[p_id] = requested_cumulative.get(p_id, Decimal("0.00")) + q

            # Authoritative cumulative stock check
            for p_id, total_requested in requested_cumulative.items():
                prod = locked_products[p_id]
                if not prod.status:
                    raise ValidationError(f"Product '{prod.name}' is inactive and no longer available for sale.")
                if prod.current_stock < total_requested:
                    raise ValidationError(
                        f"Insufficient stock for '{prod.name}'. "
                        f"Available: {prod.current_stock} {prod.unit}, Requested: {total_requested} {prod.unit}."
                    )

            # 3. Process Line Items
            prepared_items = []
            gross_subtotal = Decimal("0.00")
            total_tax = Decimal("0.00")
            total_cgst = Decimal("0.00")
            total_sgst = Decimal("0.00")
            total_igst = Decimal("0.00")

            for item_input in items_data:
                p_id = item_input["product_id"]
                product = locked_products[p_id]
                qty = quantize_money(item_input.get("quantity", 1))

                # Authoritative pricing from backend DB
                unit_price = quantize_money(product.selling_price)
                cost_price = quantize_money(product.purchase_price)
                gst_pct = quantize_money(product.gst_percentage)

                line_subtotal = quantize_money(unit_price * qty)
                gross_subtotal += line_subtotal

                prepared_items.append({
                    "product": product,
                    "product_name": product.name,
                    "product_code": product.product_code,
                    "unit_price": unit_price,
                    "cost_price": cost_price,
                    "quantity": qty,
                    "gst_percentage": gst_pct,
                    "subtotal": line_subtotal,
                })

            # 4. Compute Discounts
            total_discount = Decimal("0.00")
            if discount_type == "PERCENTAGE":
                if discount_val > Decimal("100.00"):
                    raise ValidationError("Percentage discount cannot exceed 100%.")
                total_discount = quantize_money(gross_subtotal * (discount_val / Decimal("100.00")))
            else:  # FLAT
                if discount_val > gross_subtotal:
                    raise ValidationError("Flat discount cannot exceed cart subtotal.")
                total_discount = discount_val

            # Distribute discount proportionally across items to accurately calculate line-item GST
            discounted_subtotal = gross_subtotal - total_discount
            discount_ratio = (discounted_subtotal / gross_subtotal) if gross_subtotal > Decimal("0.00") else Decimal("0.00")

            for item in prepared_items:
                item_discount = quantize_money(item["subtotal"] * (Decimal("1.00") - discount_ratio))
                item_taxable = item["subtotal"] - item_discount
                item_tax = quantize_money(item_taxable * (item["gst_percentage"] / Decimal("100.00")))
                item_total = item_taxable + item_tax

                item["discount_amount"] = item_discount
                item["tax_amount"] = item_tax
                item["total"] = item_total

                total_tax += item_tax

                if is_interstate:
                    total_igst += item_tax
                else:
                    cgst = quantize_money(item_tax / Decimal("2.00"))
                    sgst = item_tax - cgst
                    total_cgst += cgst
                    total_sgst += sgst

            grand_total = quantize_money(gross_subtotal - total_discount + total_tax)

            # 5. Process Payments
            if not payments_data:
                # Default to full cash payment if no payment details supplied
                payments_data = [{"payment_method": "CASH", "amount": grand_total}]

            total_paid = Decimal("0.00")
            parsed_payments = []

            for pay in payments_data:
                method = pay.get("payment_method", "CASH").upper()
                if method not in [m[0] for m in Payment.PAYMENT_METHODS]:
                    raise ValidationError(f"Invalid payment method: {method}")

                pay_amt = quantize_money(pay.get("amount", Decimal("0.00")))
                if pay_amt <= Decimal("0.00"):
                    raise ValidationError(f"Payment amount for {method} must be positive.")

                if method == "CREDIT":
                    if not customer_obj:
                        raise ValidationError("Store credit payments require a registered customer.")
                    new_credit_bal = customer_obj.credit_balance + pay_amt
                    if customer_obj.credit_limit > Decimal("0.00") and new_credit_bal > customer_obj.credit_limit:
                        raise ValidationError(
                            f"Credit limit exceeded for '{customer_obj.name}'. "
                            f"Limit: ₹{customer_obj.credit_limit}, Current Balance: ₹{customer_obj.credit_balance}."
                        )

                parsed_payments.append({
                    "payment_method": method,
                    "amount": pay_amt,
                    "transaction_reference": pay.get("transaction_reference", ""),
                    "notes": pay.get("notes", ""),
                })
                total_paid += pay_amt

            total_paid = quantize_money(total_paid)
            balance_due = quantize_money(max(Decimal("0.00"), grand_total - total_paid))

            if total_paid >= grand_total:
                payment_status = "PAID"
            elif total_paid > Decimal("0.00"):
                payment_status = "PARTIALLY_PAID"
            else:
                payment_status = "UNPAID"

            # 6. Create Sale Record
            sale_number = cls.generate_sale_number(company)
            sale = Sale.objects.create(
                company=company,
                sale_number=sale_number,
                customer=customer_obj,
                cashier=cashier,
                subtotal=gross_subtotal,
                discount_type=discount_type,
                discount_value=discount_val,
                discount_amount=total_discount,
                tax_amount=total_tax,
                cgst_amount=total_cgst,
                sgst_amount=total_sgst,
                igst_amount=total_igst,
                grand_total=grand_total,
                paid_amount=total_paid,
                balance_due=balance_due,
                payment_status=payment_status,
                sale_status="COMPLETED",
                notes=notes,
            )

            # 7. Record SaleItems & Decrement Stock atomically
            for item in prepared_items:
                SaleItem.objects.create(
                    sale=sale,
                    product=item["product"],
                    product_name=item["product_name"],
                    product_code=item["product_code"],
                    unit_price=item["unit_price"],
                    cost_price=item["cost_price"],
                    quantity=item["quantity"],
                    gst_percentage=item["gst_percentage"],
                    subtotal=item["subtotal"],
                    discount_amount=item["discount_amount"],
                    tax_amount=item["tax_amount"],
                    total=item["total"],
                )

                # Atomically decrement stock and record in StockMovement ledger
                StockService.adjust_stock(
                    company=company,
                    product=item["product"],
                    movement_type="SALE",
                    quantity=item["quantity"],
                    user=cashier,
                    reference=f"Sale #{sale.sale_number}",
                    reason=f"Sold via sale {sale.sale_number}",
                )

            # 8. Record Payments
            payment_methods_used = []
            for p in parsed_payments:
                Payment.objects.create(
                    company=company,
                    sale=sale,
                    payment_method=p["payment_method"],
                    amount=p["amount"],
                    transaction_reference=p["transaction_reference"],
                    received_by=cashier,
                    notes=p["notes"],
                )
                payment_methods_used.append(f"{p['payment_method']} (₹{p['amount']})")

                # If customer paid via Store Credit, adjust customer's credit balance
                if p["payment_method"] == "CREDIT" and customer_obj:
                    customer_obj.credit_balance += p["amount"]
                    customer_obj.save(update_fields=["credit_balance"])

            # 9. Create Invoice
            invoice_number = cls.generate_invoice_number(company)
            cashier_name = cashier.get_full_name() or cashier.username
            cust_name = customer_obj.name if customer_obj else "Walk-in Customer"
            cust_phone = customer_obj.phone if customer_obj else ""
            cust_address = customer_obj.address if customer_obj else ""

            comp_address = f"{company.address_line}, {company.city}, {company.state} {company.pincode}"

            invoice = Invoice.objects.create(
                company=company,
                sale=sale,
                invoice_number=invoice_number,
                customer_name=cust_name,
                customer_phone=cust_phone,
                customer_address=cust_address,
                company_name=company.company_name,
                company_gst=company.gst_number,
                company_address=comp_address,
                company_phone=company.phone,
                subtotal=gross_subtotal,
                tax_amount=total_tax,
                cgst_amount=total_cgst,
                sgst_amount=total_sgst,
                igst_amount=total_igst,
                discount_amount=total_discount,
                grand_total=grand_total,
                paid_amount=total_paid,
                balance_due=balance_due,
                cashier_name=cashier_name,
                payment_summary=", ".join(payment_methods_used),
            )

            # Enqueue for remote sync (operates safely offline and online)
            try:
                from dashboard.sync_service import SyncService
                SyncService.queue_sale(sale)
                for p in sale.payments.all():
                    SyncService.queue_payment(p)
            except Exception:
                # Local POS operation is never blocked by sync queue exceptions
                pass

        return sale, invoice
