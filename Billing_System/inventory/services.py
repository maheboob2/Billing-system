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
