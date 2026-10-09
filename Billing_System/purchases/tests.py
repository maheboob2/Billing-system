from decimal import Decimal
from django.test import TestCase
from django.contrib.auth.models import User
from accounts.models import companyRegistration
from inventory.models import Category, Supplier, Product, StockMovement
from purchases.models import Purchase, PurchaseItem
from inventory.services import StockService


class PurchaseIntakeTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="po_owner", password="password123")
        self.company = companyRegistration.objects.create(
            owner=self.owner,
            company_name="Gamma Supplies",
            address_line="500 Gamma Ave",
            city="Chennai",
            state="Tamil Nadu",
            country="India",
            pincode="600001",
            company_email="gamma@example.com",
            phone="9777777777",
            alternate_phone="9888888888",
            gst_number="33GGGGG0000G1Z7",
            business_info="Wholesaler"
        )

        self.category = Category.objects.create(company=self.company, name="Stationery")
        self.supplier = Supplier.objects.create(company=self.company, name="Paper Mills", phone="9999999999")

        self.product = Product.objects.create(
            company=self.company,
            category=self.category,
            supplier=self.supplier,
            name="A4 Paper Ream",
            product_code="A4-REAM-01",
            purchase_price=Decimal("200.00"),
            selling_price=Decimal("300.00"),
            current_stock=Decimal("10.00"),
        )

        self.purchase = Purchase.objects.create(
            company=self.company,
            supplier=self.supplier,
            invoice_number="SUPP-INV-999",
            purchase_date="2026-03-01",
            status="DRAFT"
        )

        self.purchase_item = PurchaseItem.objects.create(
            purchase=self.purchase,
            product=self.product,
            quantity=Decimal("40.00"),
            purchase_price=Decimal("200.00"),
            gst_percentage=Decimal("12.00")
        )

    def test_purchase_calculations(self):
        # Subtotal: 40 * 200 = 8000.00
        # GST: 8000 * 12% = 960.00
        # Total: 8960.00
        self.assertEqual(self.purchase.subtotal, Decimal("8000.00"))
        self.assertEqual(self.purchase.gst_amount, Decimal("960.00"))
        self.assertEqual(self.purchase.total_amount, Decimal("8960.00"))

    def test_purchase_receiving_increments_stock_and_logs_movement(self):
        # Initial stock: 10
        StockService.adjust_stock(
            company=self.company,
            product=self.product,
            movement_type="PURCHASE",
            quantity=self.purchase_item.quantity,
            user=self.owner,
            reference=f"PO #{self.purchase.invoice_number}",
            reason="Purchase order received",
        )
        self.purchase.status = "RECEIVED"
        self.purchase.save()

        self.product.refresh_from_db()
        self.assertEqual(self.product.current_stock, Decimal("50.00"))

        movement = StockMovement.objects.filter(product=self.product, movement_type="PURCHASE").first()
        self.assertIsNotNone(movement)
        self.assertEqual(movement.quantity, Decimal("40.00"))
        self.assertEqual(movement.new_stock, Decimal("50.00"))
