from decimal import Decimal
from django.test import TestCase
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from rest_framework.test import APIClient
from rest_framework import status

from accounts.models import companyRegistration
from employee.models import user_registration
from inventory.models import Category, Supplier, Product, StockMovement
from sales.models import Customer, Sale, SaleItem, Payment, Invoice
from sales.services import SalesService


class SalesAndCheckoutTests(TestCase):
    def setUp(self):
        # 1. Company & Owner
        self.owner = User.objects.create_user(username="owner_sales", password="password123")
        self.company = companyRegistration.objects.create(
            owner=self.owner,
            company_name="Prime Retailers",
            address_line="10 Market Yard",
            city="Bengaluru",
            state="Karnataka",
            country="India",
            pincode="560001",
            company_email="prime@example.com",
            phone="9800000001",
            alternate_phone="9800000002",
            gst_number="29AAAAA1111A1Z1",
            business_info="Supermarket"
        )

        # 2. Cashier
        self.cashier_user = User.objects.create_user(username="cashier_bill", password="password123")
        self.cashier_emp = user_registration.objects.create(
            user=self.cashier_user,
            company=self.company,
            employee_id="CASH01",
            Role="Cashier",
            Phone="9800000003",
            Alternate_phone="9800000004",
            address="Cashier quarters",
            joining_date="2026-01-15",
            monthaly_salary=20000.00,
            payment="Paid",
            status="active"
        )

        # 3. Catalog
        self.category = Category.objects.create(company=self.company, name="Beverages")
        self.supplier = Supplier.objects.create(company=self.company, name="Beverage Dist", phone="9800000005")

        self.prod_cola = Product.objects.create(
            company=self.company,
            category=self.category,
            supplier=self.supplier,
            name="Cold Cola 500ml",
            product_code="COLA-500",
            purchase_price=Decimal("30.00"),
            selling_price=Decimal("50.00"),
            gst_percentage=Decimal("18.00"),
            current_stock=Decimal("100.00"),
            minimum_stock=Decimal("10.00"),
            unit="bottle"
        )

        self.prod_chips = Product.objects.create(
            company=self.company,
            category=self.category,
            supplier=self.supplier,
            name="Potato Chips 100g",
            product_code="CHIPS-100",
            purchase_price=Decimal("15.00"),
            selling_price=Decimal("30.00"),
            gst_percentage=Decimal("12.00"),
            current_stock=Decimal("50.00"),
            minimum_stock=Decimal("5.00"),
            unit="pkt"
        )

        # 4. Customer
        self.customer = Customer.objects.create(
            company=self.company,
            name="John Doe",
            phone="9988776655",
            credit_limit=Decimal("5000.00"),
            credit_balance=Decimal("0.00")
        )

        self.client = APIClient()

    def test_successful_checkout_single_item_cash(self):
        items_data = [{"product_id": self.prod_cola.id, "quantity": Decimal("2.00")}]

        sale, invoice = SalesService.checkout(
            company=self.company,
            cashier=self.cashier_user,
            items_data=items_data,
            customer=self.customer,
            payments_data=[{"payment_method": "CASH", "amount": Decimal("118.00")}]
        )

        # Subtotal: 2 * 50 = 100.00
        # Tax: 100 * 18% = 18.00 (CGST 9.00, SGST 9.00)
        # Grand total: 118.00
        self.assertEqual(sale.subtotal, Decimal("100.00"))
        self.assertEqual(sale.tax_amount, Decimal("18.00"))
        self.assertEqual(sale.cgst_amount, Decimal("9.00"))
        self.assertEqual(sale.sgst_amount, Decimal("9.00"))
        self.assertEqual(sale.grand_total, Decimal("118.00"))
        self.assertEqual(sale.paid_amount, Decimal("118.00"))
        self.assertEqual(sale.balance_due, Decimal("0.00"))
        self.assertEqual(sale.payment_status, "PAID")

        # Check stock deduction
        self.prod_cola.refresh_from_db()
        self.assertEqual(self.prod_cola.current_stock, Decimal("98.00"))

        # Check StockMovement logged
        movement = StockMovement.objects.filter(product=self.prod_cola, movement_type="SALE").first()
        self.assertIsNotNone(movement)
        self.assertEqual(movement.quantity, Decimal("-2.00"))
        self.assertEqual(movement.previous_stock, Decimal("100.00"))
        self.assertEqual(movement.new_stock, Decimal("98.00"))

        # Check Invoice generated
        self.assertIsNotNone(invoice)
        self.assertTrue(invoice.invoice_number.startswith("INV-"))
        self.assertEqual(invoice.grand_total, Decimal("118.00"))
        self.assertEqual(invoice.customer_name, "John Doe")

    def test_checkout_with_percentage_discount(self):
        items_data = [{"product_id": self.prod_cola.id, "quantity": Decimal("2.00")}]

        # 10% discount on 100.00 subtotal = 10.00 discount
        # Taxable: 90.00 -> 18% GST = 16.20
        # Grand Total: 90.00 + 16.20 = 106.20
        sale, invoice = SalesService.checkout(
            company=self.company,
            cashier=self.cashier_user,
            items_data=items_data,
            discount_type="PERCENTAGE",
            discount_value=Decimal("10.00"),
            payments_data=[{"payment_method": "CASH", "amount": Decimal("106.20")}]
        )

        self.assertEqual(sale.subtotal, Decimal("100.00"))
        self.assertEqual(sale.discount_amount, Decimal("10.00"))
        self.assertEqual(sale.tax_amount, Decimal("16.20"))
        self.assertEqual(sale.grand_total, Decimal("106.20"))

    def test_checkout_insufficient_stock_fails_atomically(self):
        items_data = [
            {"product_id": self.prod_cola.id, "quantity": Decimal("1.00")},
            {"product_id": self.prod_chips.id, "quantity": Decimal("100.00")},  # Only 50 in stock
        ]

        with self.assertRaises(ValidationError) as ctx:
            SalesService.checkout(
                company=self.company,
                cashier=self.cashier_user,
                items_data=items_data
            )

        self.assertIn("Insufficient stock", str(ctx.exception))

        # Ensure no stock was deducted from cola either (atomic rollback)
        self.prod_cola.refresh_from_db()
        self.assertEqual(self.prod_cola.current_stock, Decimal("100.00"))
        self.assertEqual(Sale.objects.filter(company=self.company).count(), 0)
        self.assertEqual(Invoice.objects.filter(company=self.company).count(), 0)
        self.assertEqual(StockMovement.objects.filter(company=self.company).count(), 0)

    def test_checkout_store_credit_updates_customer_balance(self):
        items_data = [{"product_id": self.prod_chips.id, "quantity": Decimal("1.00")}]
        # Subtotal: 30.00, Tax 12%: 3.60 -> Grand Total: 33.60

        sale, invoice = SalesService.checkout(
            company=self.company,
            cashier=self.cashier_user,
            items_data=items_data,
            customer=self.customer,
            payments_data=[{"payment_method": "CREDIT", "amount": Decimal("33.60")}]
        )

        self.assertEqual(sale.payment_status, "PAID")
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.credit_balance, Decimal("33.60"))

    def test_checkout_credit_limit_exceeded_fails(self):
        self.customer.credit_limit = Decimal("50.00")
        self.customer.credit_balance = Decimal("40.00")
        self.customer.save()

        # Grand total is 118.00, customer only has 10.00 credit left
        items_data = [{"product_id": self.prod_cola.id, "quantity": Decimal("2.00")}]

        with self.assertRaises(ValidationError) as ctx:
            SalesService.checkout(
                company=self.company,
                cashier=self.cashier_user,
                items_data=items_data,
                customer=self.customer,
                payments_data=[{"payment_method": "CREDIT", "amount": Decimal("118.00")}]
            )

        self.assertIn("Credit limit exceeded", str(ctx.exception))

    def test_checkout_rest_api_authenticated_cashier(self):
        self.client.force_authenticate(user=self.cashier_user)

        payload = {
            "items": [{"product_id": self.prod_cola.id, "quantity": 1}],
            "discount_type": "FLAT",
            "discount_value": "0.00",
            "payments": [{"payment_method": "CASH", "amount": "59.00"}]
        }

        response = self.client.post("/api/sales/checkout/", data=payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIn("sale", response.data)
        self.assertIn("invoice", response.data)
        self.assertEqual(response.data["sale"]["grand_total"], "59.00")

    def test_checkout_rest_api_unauthenticated_rejected(self):
        payload = {
            "items": [{"product_id": self.prod_cola.id, "quantity": 1}],
        }
        response = self.client.post("/api/sales/checkout/", data=payload, format="json")
        self.assertIn(response.status_code, [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN])

    def test_dashboard_metrics_api(self):
        # 1. Cashier must be DENIED from viewing financial metrics
        self.client.force_authenticate(user=self.cashier_user)
        res_cashier = self.client.get("/api/dashboard/metrics/")
        self.assertEqual(res_cashier.status_code, status.HTTP_403_FORBIDDEN)

        # 2. Complete one sale
        SalesService.checkout(
            company=self.company,
            cashier=self.cashier_user,
            items_data=[{"product_id": self.prod_cola.id, "quantity": Decimal("2.00")}],
            payments_data=[{"payment_method": "CASH", "amount": Decimal("118.00")}]
        )

        # 3. Owner/Admin must be ALLOWED
        self.client.force_authenticate(user=self.owner)
        response = self.client.get("/api/dashboard/metrics/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(Decimal(str(response.data["today_revenue"])), Decimal("118.00"))
        self.assertEqual(response.data["today_invoices_count"], 1)
        self.assertEqual(response.data["total_products"], 2)
        self.assertEqual(response.data["total_customers"], 1)

    def test_checkout_idempotency_same_key_replay(self):
        self.client.force_authenticate(user=self.cashier_user)
        payload = {
            "items": [{"product_id": self.prod_cola.id, "quantity": 2}],
            "payments": [{"payment_method": "CASH", "amount": 118.00}]
        }
        init_stock = Product.objects.get(id=self.prod_cola.id).current_stock

        # First request
        res1 = self.client.post("/api/sales/checkout/", data=payload, format="json", HTTP_IDEMPOTENCY_KEY="idemp-key-001")
        self.assertEqual(res1.status_code, status.HTTP_201_CREATED)
        sale_id = res1.data["sale"]["id"]
        inv_num = res1.data["invoice"]["invoice_number"]

        # Replay same request with same key
        res2 = self.client.post("/api/sales/checkout/", data=payload, format="json", HTTP_IDEMPOTENCY_KEY="idemp-key-001")
        self.assertIn(res2.status_code, [status.HTTP_200_OK, status.HTTP_201_CREATED])
        self.assertEqual(res2.data["sale"]["id"], sale_id)
        self.assertEqual(res2.data["invoice"]["invoice_number"], inv_num)

        # Database state: exactly 1 sale, 1 invoice, 1 payment, 1 stock deduction of 2 units
        self.assertEqual(Sale.objects.filter(company=self.company).count(), 1)
        self.assertEqual(Invoice.objects.filter(company=self.company).count(), 1)
        self.assertEqual(Payment.objects.filter(company=self.company).count(), 1)
        
        final_stock = Product.objects.get(id=self.prod_cola.id).current_stock
        self.assertEqual(final_stock, init_stock - Decimal("2.00"))

    def test_checkout_idempotency_payload_mismatch_conflict(self):
        self.client.force_authenticate(user=self.cashier_user)
        payload1 = {
            "items": [{"product_id": self.prod_cola.id, "quantity": 1}],
            "payments": [{"payment_method": "CASH", "amount": 59.00}]
        }
        payload2 = {
            "items": [{"product_id": self.prod_cola.id, "quantity": 5}],
            "payments": [{"payment_method": "CASH", "amount": 295.00}]
        }

        # First request succeeds
        res1 = self.client.post("/api/sales/checkout/", data=payload1, format="json", HTTP_IDEMPOTENCY_KEY="idemp-mismatch-key")
        self.assertEqual(res1.status_code, status.HTTP_201_CREATED)

        # Second request with SAME key but DIFFERENT payload must fail with 409 Conflict
        res2 = self.client.post("/api/sales/checkout/", data=payload2, format="json", HTTP_IDEMPOTENCY_KEY="idemp-mismatch-key")
        self.assertEqual(res2.status_code, status.HTTP_409_CONFLICT)
        self.assertIn("different request payload", res2.data.get("error", ""))

    def test_checkout_idempotency_distinct_keys(self):
        self.client.force_authenticate(user=self.cashier_user)
        payload = {
            "items": [{"product_id": self.prod_cola.id, "quantity": 1}],
            "payments": [{"payment_method": "CASH", "amount": 59.00}]
        }

        res1 = self.client.post("/api/sales/checkout/", data=payload, format="json", HTTP_IDEMPOTENCY_KEY="key-distinct-1")
        res2 = self.client.post("/api/sales/checkout/", data=payload, format="json", HTTP_IDEMPOTENCY_KEY="key-distinct-2")

        self.assertEqual(res1.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res2.status_code, status.HTTP_201_CREATED)
        self.assertNotEqual(res1.data["sale"]["id"], res2.data["sale"]["id"])
        self.assertEqual(Sale.objects.filter(company=self.company).count(), 2)

    def test_checkout_idempotency_failed_retry(self):
        self.client.force_authenticate(user=self.cashier_user)
        oversell_payload = {
            "items": [{"product_id": self.prod_cola.id, "quantity": 99999}],
            "payments": [{"payment_method": "CASH", "amount": 100000}]
        }

        # First attempt fails due to stock
        res_fail = self.client.post("/api/sales/checkout/", data=oversell_payload, format="json", HTTP_IDEMPOTENCY_KEY="key-retry-fail")
        self.assertEqual(res_fail.status_code, status.HTTP_400_BAD_REQUEST)

        # Client corrects quantity to valid amount and retries with the SAME key
        valid_payload = {
            "items": [{"product_id": self.prod_cola.id, "quantity": 1}],
            "payments": [{"payment_method": "CASH", "amount": 59.00}]
        }
        res_retry = self.client.post("/api/sales/checkout/", data=valid_payload, format="json", HTTP_IDEMPOTENCY_KEY="key-retry-fail")
        self.assertEqual(res_retry.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Sale.objects.filter(company=self.company).count(), 1)

    def test_checkout_inactive_product_rejected(self):
        # Create an inactive product
        prod_inactive = Product.objects.create(
            company=self.company,
            category=self.category,
            supplier=self.supplier,
            name="Discontinued Juice",
            product_code="DISC-JUICE-1",
            purchase_price=Decimal("20.00"),
            selling_price=Decimal("40.00"),
            current_stock=Decimal("50.00"),
            gst_percentage=Decimal("5.00"),
            status=False  # INACTIVE
        )

        self.client.force_authenticate(user=self.cashier_user)
        payload = {
            "items": [{"product_id": prod_inactive.id, "quantity": 1}],
            "payments": [{"payment_method": "CASH", "amount": 42.00}]
        }

        sales_count_before = Sale.objects.filter(company=self.company).count()
        sm_count_before = StockMovement.objects.filter(company=self.company).count()

        response = self.client.post("/api/sales/checkout/", data=payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("inactive and no longer available", response.data.get("error", ""))

        # Verify zero database mutations
        self.assertEqual(Sale.objects.filter(company=self.company).count(), sales_count_before)
        self.assertEqual(StockMovement.objects.filter(company=self.company).count(), sm_count_before)
        prod_inactive.refresh_from_db()
        self.assertEqual(prod_inactive.current_stock, Decimal("50.00"))

    # -----------------------------------------------------------------
    # POS Cart Page & Sales Quote & Store Credit Tests (Phase 9)
    # -----------------------------------------------------------------
    def test_cart_page_view_accessible_by_cashier(self):
        """Verify /cart/ loads 200 OK and renders cart.html for Cashier."""
        self.client.force_login(user=self.cashier_user)
        response = self.client.get("/cart/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "sales/cart.html")
        self.assertContains(response, "POS Checkout")

    def test_cart_page_view_unauthenticated_redirects(self):
        """Unauthenticated requests to /cart/ redirect to login."""
        self.client.logout()
        response = self.client.get("/cart/")
        self.assertEqual(response.status_code, 302)

    def test_sales_quote_endpoint_accuracy_and_non_mutating(self):
        """POST /api/sales/quote/ calculates authoritative totals without DB mutation."""
        self.client.force_authenticate(user=self.cashier_user)

        stock_before = self.prod_cola.current_stock
        sales_count_before = Sale.objects.filter(company=self.company).count()
        payments_count_before = Payment.objects.filter(company=self.company).count()
        invoices_count_before = Invoice.objects.filter(company=self.company).count()

        payload = {
            "items": [{"product_id": self.prod_cola.id, "quantity": 2}],
            "discount_type": "FLAT",
            "discount_value": 10.00,
            "payments": [{"payment_method": "CASH", "amount": 106.20}]
        }

        response = self.client.post("/api/sales/quote/", data=payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        data = response.data
        quote = data["quote"]
        # Subtotal: 2 * 50 = 100.00
        self.assertEqual(Decimal(str(quote["subtotal"])), Decimal("100.00"))
        # Discount: 10.00
        self.assertEqual(Decimal(str(quote["discount_amount"])), Decimal("10.00"))
        # Taxable: 90.00, 18% GST = 16.20 (CGST 8.10, SGST 8.10)
        self.assertEqual(Decimal(str(quote["tax_amount"])), Decimal("16.20"))
        self.assertEqual(Decimal(str(quote["cgst_amount"])), Decimal("8.10"))
        self.assertEqual(Decimal(str(quote["sgst_amount"])), Decimal("8.10"))
        # Grand total: 90 + 16.20 = 106.20
        self.assertEqual(Decimal(str(quote["grand_total"])), Decimal("106.20"))
        self.assertEqual(quote["payment_status_preview"], "PAID")

        # Zero DB mutations verified
        self.assertEqual(Sale.objects.filter(company=self.company).count(), sales_count_before)
        self.assertEqual(Payment.objects.filter(company=self.company).count(), payments_count_before)
        self.assertEqual(Invoice.objects.filter(company=self.company).count(), invoices_count_before)
        self.prod_cola.refresh_from_db()
        self.assertEqual(self.prod_cola.current_stock, stock_before)

    def test_sales_quote_insufficient_stock_error(self):
        """Quote fails with 400 when requested quantity exceeds available stock."""
        self.client.force_authenticate(user=self.cashier_user)
        payload = {
            "items": [{"product_id": self.prod_cola.id, "quantity": 99999}],
        }
        response = self.client.post("/api/sales/quote/", data=payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Insufficient stock", response.data.get("error", ""))

    def test_sales_quote_inactive_product_error(self):
        """Quote fails with 400 when product is inactive."""
        prod_inactive = Product.objects.create(
            company=self.company,
            category=self.category,
            supplier=self.supplier,
            name="Archived Item",
            product_code="ARCH-001",
            purchase_price=Decimal("10.00"),
            selling_price=Decimal("20.00"),
            current_stock=Decimal("10.00"),
            status=False
        )
        self.client.force_authenticate(user=self.cashier_user)
        payload = {
            "items": [{"product_id": prod_inactive.id, "quantity": 1}],
        }
        response = self.client.post("/api/sales/quote/", data=payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("inactive", response.data.get("error", ""))

    def test_sales_quote_credit_warning_when_limit_exceeded(self):
        """Quote returns a warning when projected credit exceeds customer credit limit."""
        self.client.force_authenticate(user=self.cashier_user)
        # Customer limit is 5000.00, balance is 0.00
        payload = {
            "customer_id": self.customer.id,
            "items": [{"product_id": self.prod_cola.id, "quantity": 1}],
            "payments": [{"payment_method": "CREDIT", "amount": 6000.00}]
        }
        response = self.client.post("/api/sales/quote/", data=payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("warnings", response.data)
        self.assertTrue(len(response.data["warnings"]) > 0)
        self.assertIn("Credit limit would be exceeded", response.data["warnings"][0])

    def test_checkout_credit_payment_requires_customer(self):
        """Checkout with payment method CREDIT without a customer returns 400."""
        self.client.force_authenticate(user=self.cashier_user)
        payload = {
            "items": [{"product_id": self.prod_cola.id, "quantity": 1}],
            "payments": [{"payment_method": "CREDIT", "amount": 59.00}],
            # No customer_id
        }
        response = self.client.post("/api/sales/checkout/", data=payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Store credit payments require a registered customer", response.data.get("error", ""))

    def test_checkout_credit_payment_success_and_balance_update(self):
        """Checkout with CREDIT updates customer credit_balance."""
        self.client.force_authenticate(user=self.cashier_user)
        balance_before = self.customer.credit_balance

        payload = {
            "customer_id": self.customer.id,
            "items": [{"product_id": self.prod_cola.id, "quantity": 1}],
            "payments": [{"payment_method": "CREDIT", "amount": 59.00}],
        }
        response = self.client.post("/api/sales/checkout/", data=payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        self.customer.refresh_from_db()
        self.assertEqual(self.customer.credit_balance, balance_before + Decimal("59.00"))

    def test_checkout_credit_limit_exceeded_rejected(self):
        """Checkout with CREDIT exceeding customer limit fails with 400."""
        self.client.force_authenticate(user=self.cashier_user)
        # customer credit limit is 5000.00
        self.customer.credit_balance = Decimal("4950.00")
        self.customer.save(update_fields=["credit_balance"])

        payload = {
            "customer_id": self.customer.id,
            "items": [{"product_id": self.prod_cola.id, "quantity": 2}],
            "payments": [{"payment_method": "CREDIT", "amount": 118.00}],
        }
        response = self.client.post("/api/sales/checkout/", data=payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Credit limit exceeded", response.data.get("error", ""))



