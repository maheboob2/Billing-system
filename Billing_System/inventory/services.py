"""
Inventory and Stock Service Layer.
Encapsulates all stock mutations to ensure no silent overwrites,
strict validation against negative stock, and an unbroken audit ledger.
"""

from decimal import Decimal
from django.db import transaction
from django.core.exceptions import ValidationError
from inventory.models import Product, StockMovement


class StockService:
    @staticmethod
    def adjust_stock(company, product, movement_type, quantity, user=None, reference="", reason=""):
        """
        Adjusts product stock and records an audit log in StockMovement.
        Must be executed within or will wrap in an atomic transaction.

        quantity: Decimal. Positive value indicates intake/restoration;
                  Negative or positive based on movement_type.
        """
        qty = Decimal(str(quantity))
        if qty == Decimal("0.00"):
            raise ValidationError("Stock movement quantity cannot be zero.")

        # Determine stock delta based on movement type
        deduction_types = ["SALE", "DAMAGE", "WASTAGE"]
        intake_types = ["PURCHASE", "RETURN"]

        if movement_type in deduction_types:
            delta = -abs(qty)
            logged_qty = -abs(qty)
        elif movement_type in intake_types:
            delta = abs(qty)
            logged_qty = abs(qty)
        elif movement_type == "ADJUSTMENT":
            delta = qty
            logged_qty = qty
        else:
            raise ValidationError(f"Invalid movement type: {movement_type}")

        with transaction.atomic():
            # Lock the product row for update to prevent concurrency race conditions
            locked_product = Product.objects.select_for_update().get(id=product.id, company=company)

            previous_stock = Decimal(str(locked_product.current_stock))
            new_stock = previous_stock + delta

            if new_stock < Decimal("0.00"):
                raise ValidationError(
                    f"Insufficient stock for '{locked_product.name}'. "
                    f"Available: {previous_stock} {locked_product.unit}, Requested: {abs(delta)} {locked_product.unit}."
                )

            locked_product.current_stock = new_stock
            locked_product.save(update_fields=["current_stock"])

            movement = StockMovement.objects.create(
                company=company,
                product=locked_product,
                movement_type=movement_type,
                quantity=logged_qty,
                previous_stock=previous_stock,
                new_stock=new_stock,
                reference=reference,
                user=user,
                reason=reason,
            )

        return locked_product, movement


def generate_internal_barcode(company):
    """
    Generate a tenant-safe unique internal barcode in Code 128 standard.
    Uses GS1 in-store retail prefix '20' + 3-digit company code + 6-digit sequence.
    Example: 20001000001
    Guaranteed unique across all products within the tenant.
    """
    company_id_mod = company.id % 1000 if hasattr(company, "id") else 1
    prefix = f"20{company_id_mod:03d}"

    existing = Product.objects.filter(
        company=company,
        barcode__startswith=prefix
    ).order_by("-barcode").first()

    seq = 1
    if existing and existing.barcode and len(existing.barcode) > len(prefix):
        suffix = existing.barcode[len(prefix):]
        if suffix.isdigit():
            seq = int(suffix) + 1

    candidate = f"{prefix}{seq:06d}"
    while Product.objects.filter(company=company, barcode=candidate).exists():
        seq += 1
        candidate = f"{prefix}{seq:06d}"

    return candidate
