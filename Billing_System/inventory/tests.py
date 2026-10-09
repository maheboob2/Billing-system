from decimal import Decimal
from django.test import TestCase
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from accounts.models import companyRegistration
from inventory.models import Category, Supplier, Product, StockMovement
from inventory.services import StockService


class InventoryAndStockTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="owner_inv", password="password123")
        self.company = companyRegistration.objects.create(
            owner=self.owner,
            company_name="Alpha Mart",
            address_line="100 Alpha Road",
            city="Delhi",
            state="Delhi",
            country="India",
            pincode="110001",
            company_email="alpha@example.com",
            phone="9111111111",
            alternate_phone="9222222222",
            gst_number="07AAAAA0000A1Z5",
            business_info="Retailer"
        )

        self.category = Category.objects.create(
            company=self.company,
            name="Groceries",
            description="Daily essentials"
        )

        self.supplier = Supplier.objects.create(
            company=self.company,
            name="Bulk Supplies Ltd",
            phone="9333333333",
            email="bulk@example.com"
        )

        self.product = Product.objects.create(
            company=self.company,
            category=self.category,
            supplier=self.supplier,
            name="Organic Basmati Rice 1kg",
            product_code="RICE-001",
            barcode="8901234567890",
            purchase_price=Decimal("80.00"),
            selling_price=Decimal("120.00"),
            gst_percentage=Decimal("5.00"),
            current_stock=Decimal("50.00"),
            minimum_stock=Decimal("10.00"),
            unit="kg"
        )

    def test_product_creation_and_attributes(self):
        self.assertEqual(self.product.name, "Organic Basmati Rice 1kg")
        self.assertEqual(self.product.current_stock, Decimal("50.00"))
        self.assertEqual(self.product.selling_price, Decimal("120.00"))

    def test_duplicate_product_code_in_same_company_rejected(self):
        with self.assertRaises(IntegrityError):
            Product.objects.create(
                company=self.company,
                category=self.category,
                supplier=self.supplier,
                name="Duplicate Rice",
                product_code="RICE-001",
                purchase_price=Decimal("80.00"),
                selling_price=Decimal("120.00"),
                gst_percentage=Decimal("5.00"),
                current_stock=Decimal("10.00"),
            )

    def test_duplicate_product_code_in_different_company_allowed(self):
        other_owner = User.objects.create_user(username="other_owner", password="password123")
        other_comp = companyRegistration.objects.create(
            owner=other_owner,
            company_name="Beta Mart",
            address_line="200 Beta St",
            city="Pune",
            state="Maharashtra",
            country="India",
            pincode="411001",
            company_email="beta@example.com",
            phone="9444444444",
            alternate_phone="9555555555",
            gst_number="27BBBBB0000B1Z6",
            business_info="Retailer"
        )
        other_cat = Category.objects.create(company=other_comp, name="Grains")
        other_sup = Supplier.objects.create(company=other_comp, name="Local Wholesaler", phone="9666666666")

        prod2 = Product.objects.create(
            company=other_comp,
            category=other_cat,
            supplier=other_sup,
            name="Different Company Rice",
            product_code="RICE-001",
            purchase_price=Decimal("75.00"),
            selling_price=Decimal("110.00"),
            current_stock=Decimal("20.00"),
        )
        self.assertEqual(prod2.product_code, "RICE-001")

    def test_stock_service_purchase_intake(self):
        updated_prod, movement = StockService.adjust_stock(
            company=self.company,
            product=self.product,
            movement_type="PURCHASE",
            quantity=Decimal("25.00"),
            user=self.owner,
            reference="PO-TEST-100",
            reason="Supplier shipment arrived"
        )
        self.assertEqual(updated_prod.current_stock, Decimal("75.00"))
        self.assertEqual(movement.previous_stock, Decimal("50.00"))
        self.assertEqual(movement.new_stock, Decimal("75.00"))
        self.assertEqual(movement.movement_type, "PURCHASE")
        self.assertEqual(movement.quantity, Decimal("25.00"))

    def test_stock_service_sale_deduction(self):
        updated_prod, movement = StockService.adjust_stock(
            company=self.company,
            product=self.product,
            movement_type="SALE",
            quantity=Decimal("15.00"),
            user=self.owner,
            reference="SALE-TEST-001",
            reason="Retail customer sale"
        )
        self.assertEqual(updated_prod.current_stock, Decimal("35.00"))
        self.assertEqual(movement.previous_stock, Decimal("50.00"))
        self.assertEqual(movement.new_stock, Decimal("35.00"))
        self.assertEqual(movement.quantity, Decimal("-15.00"))

    def test_stock_service_insufficient_stock_fails(self):
        with self.assertRaises(ValidationError):
            StockService.adjust_stock(
                company=self.company,
                product=self.product,
                movement_type="SALE",
                quantity=Decimal("100.00"),  # Only 50 in stock
                user=self.owner
            )
        # Ensure stock remained unchanged
        self.product.refresh_from_db()
        self.assertEqual(self.product.current_stock, Decimal("50.00"))
        self.assertEqual(StockMovement.objects.filter(product=self.product).count(), 0)

    def test_duplicate_barcode_in_same_company_rejected(self):
        with self.assertRaises(IntegrityError):
            Product.objects.create(
                company=self.company,
                category=self.category,
                supplier=self.supplier,
                name="Duplicate Barcode Item",
                product_code="DIFF-CODE-001",
                barcode="8901234567890",  # Same as self.product.barcode
                purchase_price=Decimal("50.00"),
                selling_price=Decimal("70.00"),
                current_stock=Decimal("10.00"),
            )

    def test_duplicate_barcode_in_different_company_allowed(self):
        other_owner = User.objects.create_user(username="other_owner_bar", password="password123")
        other_comp = companyRegistration.objects.create(
            owner=other_owner,
            company_name="Gamma Mart",
            address_line="300 Gamma St",
            city="Pune",
            state="Maharashtra",
            country="India",
            pincode="411002",
            company_email="gamma@example.com",
            phone="9777777777",
            alternate_phone="9888888888",
            gst_number="27CCCCC0000C1Z7",
            business_info="Retailer"
        )
        other_cat = Category.objects.create(company=other_comp, name="Snacks")
        other_sup = Supplier.objects.create(company=other_comp, name="Snack Supplier", phone="9999999999")

        other_prod = Product.objects.create(
            company=other_comp,
            category=other_cat,
            supplier=other_sup,
            name="Different Company Item",
            product_code="DIFF-CODE-OTHER",
            barcode="8901234567890",  # Same barcode as self.product in company A
            purchase_price=Decimal("50.00"),
            selling_price=Decimal("70.00"),
            current_stock=Decimal("10.00"),
        )
        self.assertEqual(other_prod.barcode, "8901234567890")

    def test_blank_barcode_multiple_allowed_in_same_company(self):
        p_blank1 = Product.objects.create(
            company=self.company,
            category=self.category,
            supplier=self.supplier,
            name="Blank Barcode 1",
            product_code="BLANK-001",
            barcode="",
            purchase_price=Decimal("10.00"),
            selling_price=Decimal("15.00"),
            current_stock=Decimal("5.00"),
        )
        p_blank2 = Product.objects.create(
            company=self.company,
            category=self.category,
            supplier=self.supplier,
            name="Blank Barcode 2",
            product_code="BLANK-002",
            barcode="",
            purchase_price=Decimal("20.00"),
            selling_price=Decimal("25.00"),
            current_stock=Decimal("5.00"),
        )
        self.assertEqual(p_blank1.barcode, "")
        self.assertEqual(p_blank2.barcode, "")

    def test_database_check_constraint_prevents_negative_stock(self):
        # 1. Normal service deduction passes
        StockService.adjust_stock(
            company=self.company,
            product=self.product,
            movement_type="SALE",
            quantity=Decimal("10.00"),
            user=self.owner,
            reference="Test Sale",
        )
        self.product.refresh_from_db()
        self.assertEqual(self.product.current_stock, Decimal("40.00"))

        # 2. Normal intake passes
        StockService.adjust_stock(
            company=self.company,
            product=self.product,
            movement_type="PURCHASE",
            quantity=Decimal("20.00"),
            user=self.owner,
            reference="Test PO",
        )
        self.product.refresh_from_db()
        self.assertEqual(self.product.current_stock, Decimal("60.00"))

        # 3. Direct DB insert with negative stock is strictly rejected by CheckConstraint
        with self.assertRaises(IntegrityError):
            Product.objects.create(
                company=self.company,
                category=self.category,
                supplier=self.supplier,
                name="Illegal Negative Stock Product",
                product_code="NEG-RAW-001",
                purchase_price=Decimal("10.00"),
                selling_price=Decimal("15.00"),
                current_stock=Decimal("-5.00"),
            )


