import uuid
from decimal import Decimal
from django.test import TestCase
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from rest_framework.test import APIClient
from rest_framework import status

from accounts.models import companyRegistration
from employee.models import user_registration
from inventory.models import Category, Supplier, Product
from sales.models import Sale, SaleItem, Payment, Invoice
from sales.services import SalesService


class POSStockAndScannerRegressionTests(TestCase):
    """
    Regression Test Suite for POS Bug #1 (Stock Bounds) and Bug #2 (Scanner Separation):
    A. Cart cannot exceed stock.
    B. Repeated barcode scan cannot exceed stock.
    C. + button cannot exceed stock.
    D. Search selection cannot exceed stock.
    E. Backend checkout rejects insufficient stock.
    F. Exact barcode lookup does not perform product-name fuzzy matching.
    G. Partial text in barcode field does not return arbitrary products.
    H. Unknown barcode returns PRODUCT NOT FOUND (HTTP 404).
    I. Same product from barcode + search respects one cumulative cart quantity.
    J. Stock revalidation occurs during final checkout (concurrent stock reduction).
    """

    def setUp(self):
        self.owner = User.objects.create_user(username="owner_reg", password="password123")
        self.company = companyRegistration.objects.create(
            owner=self.owner,
            company_name="Hypermarket Central",
            address_line="Main Road",
            city="Mumbai",
            state="Maharashtra",
            country="India",
            pincode="400001",
            company_email="store@hypermarket.com",
            phone="9876543210",
            gst_number="27AAAAA0000A1Z5",
        )

        self.cashier_user = User.objects.create_user(username="cashier_reg", password="password123")
        self.cashier_emp = user_registration.objects.create(
            user=self.cashier_user,
            company=self.company,
            employee_id="CSH-REG-01",
            Role="Cashier",
            Phone="9876543211",
            Alternate_phone="9876543212",
            address="Staff Quarters",
            joining_date="2026-01-01",
            monthaly_salary=25000.00,
            payment="Paid",
            status="active"
        )

        self.category = Category.objects.create(company=self.company, name="Dairy")
        self.supplier = Supplier.objects.create(company=self.company, name="Amul Dairy Ltd", phone="9876543213")

        # Amul Taaza Milk 1L - Exactly matching the manual testing bug report
        self.amul_milk = Product.objects.create(
            company=self.company,
            category=self.category,
            supplier=self.supplier,
            name="Amul Taaza Milk 1L",
            product_code="MILK-001",
            barcode="890126201001",
            purchase_price=Decimal("45.00"),
            selling_price=Decimal("54.00"),
            current_stock=Decimal("2307.00"),
            minimum_stock=Decimal("50.00"),
            gst_percentage=Decimal("5.00"),
            unit="pcs",
            status=True
        )

        # Limited stock product for boundary testing
        self.low_stock_prod = Product.objects.create(
            company=self.company,
            category=self.category,
            supplier=self.supplier,
            name="Limited Butter 100g",
            product_code="BTR-100",
            barcode="890126209999",
            purchase_price=Decimal("40.00"),
            selling_price=Decimal("50.00"),
            current_stock=Decimal("5.00"),
            minimum_stock=Decimal("2.00"),
            gst_percentage=Decimal("5.00"),
            unit="pcs",
            status=True
        )

        self.api_client = APIClient()
        self.api_client.force_authenticate(user=self.cashier_user)

    # ── TEST A: Cart quote cannot exceed available stock ────────────────────
    def test_a_cart_cannot_exceed_available_stock(self):
        """Quote API must reject if requested quantity exceeds available stock."""
        payload = {
            "items": [
                {
                    "product_id": self.amul_milk.id,
                    "quantity": 2308,  # Stock is 2307
                    "unit_price": 54.00
                }
            ],
            "discount_type": "FLAT",
            "discount_value": 0
        }
        res = self.api_client.post("/api/sales/quote/", payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Insufficient stock", res.data["error"])
        self.assertIn("2307", res.data["error"])

    # ── TEST B: Cumulative requested quantity cannot exceed stock ───────────
    def test_b_cumulative_cart_quantity_cannot_exceed_stock(self):
        """Repeated additions of the same product across lines must be validated cumulatively."""
        payload = {
            "items": [
                {"product_id": self.low_stock_prod.id, "quantity": 3, "unit_price": 50.00},
                {"product_id": self.low_stock_prod.id, "quantity": 3, "unit_price": 50.00},  # Total 6, stock is 5
            ],
            "discount_type": "FLAT",
            "discount_value": 0
        }
        res = self.api_client.post("/api/sales/quote/", payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Insufficient stock", res.data["error"])

    # ── TEST C: Exact stock quantity is accepted ────────────────────────────
    def test_c_exact_max_stock_quantity_is_allowed(self):
        """Selling exactly the available stock (e.g., 2307) must be accepted."""
        payload = {
            "items": [
                {"product_id": self.amul_milk.id, "quantity": 2307, "unit_price": 54.00}
            ],
            "discount_type": "FLAT",
            "discount_value": 0
        }
        res = self.api_client.post("/api/sales/quote/", payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        quote_data = res.data.get("quote", res.data)
        self.assertEqual(Decimal(str(quote_data["grand_total"])), Decimal("2307") * Decimal("54.00") * Decimal("1.05"))

    # ── TEST D: Search selection cannot exceed stock in backend checkout ────
    def test_d_checkout_rejects_exceeded_stock_from_any_source(self):
        """SalesService and checkout API must reject if requested qty > available stock."""
        payload = {
            "items": [
                {"product_id": self.low_stock_prod.id, "quantity": 10, "unit_price": 50.00}
            ],
            "payment": {
                "payment_method": "CASH",
                "amount": 500.00
            }
        }
        res = self.api_client.post("/api/sales/checkout/", payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Insufficient stock", str(res.data))

        # Check stock unchanged
        self.low_stock_prod.refresh_from_db()
        self.assertEqual(self.low_stock_prod.current_stock, Decimal("5.00"))

    # ── TEST E: Backend service level validation ────────────────────────────
    def test_e_sales_service_checkout_raises_validation_error_on_excess_stock(self):
        """Direct call to SalesService.checkout raises ValidationError when stock is insufficient."""
        items_data = [
            {"product_id": self.low_stock_prod.id, "quantity": 6, "unit_price": Decimal("50.00")}
        ]
        with self.assertRaises(ValidationError) as ctx:
            SalesService.checkout(
                company=self.company,
                cashier=self.cashier_user,
                items_data=items_data,
                payments_data=[{"payment_method": "CASH", "amount": Decimal("300.00")}],
            )
        self.assertIn("Insufficient stock", str(ctx.exception))

    # ── TEST F: Exact barcode lookup does NOT do fuzzy product-name search ──
    def test_f_exact_barcode_lookup_does_not_match_product_name(self):
        """Looking up 'Amul' or 'Milk' in the barcode endpoint must return 404 Not Found."""
        # Searching by product name in barcode lookup endpoint
        res = self.api_client.get("/api/products/lookup/?code=Amul")
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(res.data.get("found", False))

        res_milk = self.api_client.get("/api/products/lookup/?code=Milk")
        self.assertEqual(res_milk.status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(res_milk.data.get("found", False))

    # ── TEST G: Partial text in barcode field returns 404 (no arbitrary match)
    def test_g_partial_text_in_barcode_lookup_returns_404(self):
        """Typing 'A' or 'AM' in barcode input must NOT return Amul Taaza Milk."""
        res_a = self.api_client.get("/api/products/lookup/?code=A")
        self.assertEqual(res_a.status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(res_a.data.get("found", False))

        res_am = self.api_client.get("/api/products/lookup/?code=AM")
        self.assertEqual(res_am.status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(res_am.data.get("found", False))

    # ── TEST H: Unknown barcode returns 404 Not Found ─────────────────────────
    def test_h_unknown_barcode_returns_404_not_found(self):
        """Scanning 999999999999 returns HTTP 404 and does not match any product."""
        res = self.api_client.get("/api/products/lookup/?code=999999999999")
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(res.data.get("found", False))
        self.assertIn("not found", res.data.get("error", "").lower())

    # ── TEST I: Exact barcode and exact SKU work correctly ──────────────────
    def test_i_exact_barcode_and_exact_sku_lookups_succeed(self):
        """Exact barcode '890126201001' and exact SKU 'MILK-001' return the product."""
        # Exact barcode
        res_bc = self.api_client.get(f"/api/products/lookup/?code={self.amul_milk.barcode}")
        self.assertEqual(res_bc.status_code, status.HTTP_200_OK)
        self.assertTrue(res_bc.data["found"])
        self.assertEqual(res_bc.data["product"]["id"], self.amul_milk.id)
        self.assertEqual(res_bc.data["product"]["name"], "Amul Taaza Milk 1L")

        # Exact SKU
        res_sku = self.api_client.get(f"/api/products/lookup/?code={self.amul_milk.product_code}")
        self.assertEqual(res_sku.status_code, status.HTTP_200_OK)
        self.assertTrue(res_sku.data["found"])
        self.assertEqual(res_sku.data["product"]["id"], self.amul_milk.id)

    # ── TEST J: Separate Product Search API allows name matching ─────────────
    def test_j_product_search_allows_name_search_separately(self):
        """The separate product search API (/api/products/?search=) allows searching by name."""
        res = self.api_client.get("/api/products/?search=Amul")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        results = res.data if isinstance(res.data, list) else res.data.get("results", [])
        found_names = [p["name"] for p in results]
        self.assertIn("Amul Taaza Milk 1L", found_names)

    # ── TEST K: Concurrent stock revalidation on final checkout ──────────────
    def test_k_stock_revalidation_occurs_during_final_checkout(self):
        """
        If available stock is reduced between cart assembly and checkout completion,
        the checkout must revalidate live locked stock and abort safely.
        """
        # Initial stock: 5 units. Cashier builds a cart for 4 units.
        items_payload = [
            {"product_id": self.low_stock_prod.id, "quantity": 4, "unit_price": 50.00}
        ]

        # Another counter sells 3 units concurrently before checkout
        self.low_stock_prod.current_stock = Decimal("2.00")
        self.low_stock_prod.save(update_fields=["current_stock"])

        # Cashier now attempts to checkout 4 units (now exceeds 2 available)
        checkout_payload = {
            "items": items_payload,
            "payment": {
                "payment_method": "CASH",
                "amount": 210.00
            }
        }
        res = self.api_client.post("/api/sales/checkout/", checkout_payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Insufficient stock", str(res.data))

        # Ensure stock never became negative
        self.low_stock_prod.refresh_from_db()
        self.assertEqual(self.low_stock_prod.current_stock, Decimal("2.00"))
        self.assertGreaterEqual(self.low_stock_prod.current_stock, Decimal("0.00"))
