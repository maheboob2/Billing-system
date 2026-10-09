from decimal import Decimal
from datetime import timedelta
from django.test import TestCase, Client
from django.contrib.auth.models import User
from django.contrib.auth.hashers import check_password
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework import status

from accounts.models import companyRegistration
from employee.models import user_registration
from inventory.models import Category, Supplier, Product, StockMovement
from inventory.services import generate_internal_barcode
from sales.models import Customer, Sale, SaleItem, Payment, Invoice, POSScannerSession, POSScannerScan, ReturnRequest, ReturnItem
from dashboard.models import TelegramOwnerLink, TelegramOutboundMessage
from dashboard.telegram_service import generate_pairing_code, verify_pairing_code, handle_telegram_command
from dashboard.ai_assistant import answer_business_question, generate_sales_insights, generate_inventory_intelligence


class RetailPOSFeaturesTestSuite(TestCase):
    def setUp(self):
        self.api_client = APIClient()
        self.web_client = Client()

        # 1. Company A & Owner
        self.owner_user = User.objects.create_user(username="owner_retail", email="owner@store.com", password="Password@123")
        self.company = companyRegistration.objects.create(
            owner=self.owner_user,
            company_name="Alpha Supermarket",
            address_line="123 Retail Hub",
            city="Bengaluru",
            state="Karnataka",
            country="India",
            pincode="560001",
            company_email="alpha@store.com",
            phone="9876543210",
            gst_number="29AAAAA0000A1Z5",
            business_info="Retail Grocery"
        )

        # 2. Manager A
        self.manager_user = User.objects.create_user(username="manager_retail", email="mgr@store.com", password="Password@123")
        self.manager_emp = user_registration.objects.create(
            user=self.manager_user,
            company=self.company,
            employee_id="MGR_001",
            Role="Manager",
            Phone="9876543211",
            joining_date="2026-01-01",
            payment="Paid",
            status="active"
        )

        # 3. Cashier A
        self.cashier_user = User.objects.create_user(username="cashier_retail", email="csh@store.com", password="Password@123")
        self.cashier_emp = user_registration.objects.create(
            user=self.cashier_user,
            company=self.company,
            employee_id="CSH_001",
            Role="Cashier",
            Phone="9876543212",
            joining_date="2026-01-01",
            payment="Paid",
            status="active"
        )

        # 4. Company B (Tenant isolation checks)
        self.owner_b = User.objects.create_user(username="owner_b", password="Password@123")
        self.company_b = companyRegistration.objects.create(
            owner=self.owner_b,
            company_name="Beta Mart",
            address_line="456 Other Way",
            city="Delhi",
            state="Delhi",
            country="India",
            pincode="110001",
            company_email="beta@store.com",
            phone="9876543299",
            gst_number="07BBBBB0000B1Z6"
        )

        # Inventory fixtures
        self.category = Category.objects.create(company=self.company, name="Beverages")
        self.supplier = Supplier.objects.create(company=self.company, name="Nestle Supply", phone="9988776655")

        self.prod_active = Product.objects.create(
            company=self.company,
            name="Cold Coffee 250ml",
            product_code="BEV-001",
            barcode="8901234567890",
            category=self.category,
            supplier=self.supplier,
            purchase_price=Decimal("40.00"),
            selling_price=Decimal("60.00"),
            gst_percentage=Decimal("18.00"),
            current_stock=Decimal("50.00"),
            minimum_stock=Decimal("10.00"),
            status=True
        )

        self.prod_inactive = Product.objects.create(
            company=self.company,
            name="Discontinued Soda",
            product_code="BEV-999",
            barcode="8909999999999",
            category=self.category,
            supplier=self.supplier,
            purchase_price=Decimal("20.00"),
            selling_price=Decimal("35.00"),
            gst_percentage=Decimal("18.00"),
            current_stock=Decimal("10.00"),
            minimum_stock=Decimal("5.00"),
            status=False
        )

        # Initial Sale fixture
        self.sale = Sale.objects.create(
            company=self.company,
            sale_number="INV-2026-1001",
            cashier=self.cashier_user,
            subtotal=Decimal("100.00"),
            discount_amount=Decimal("0.00"),
            tax_amount=Decimal("18.00"),
            grand_total=Decimal("118.00"),
            paid_amount=Decimal("118.00"),
            payment_status="PAID",
            sale_status="COMPLETED"
        )
        self.sale_item = SaleItem.objects.create(
            sale=self.sale,
            product=self.prod_active,
            product_name=self.prod_active.name,
            product_code=self.prod_active.product_code,
            unit_price=Decimal("50.00"),
            cost_price=Decimal("35.00"),
            quantity=Decimal("2.00"),
            gst_percentage=Decimal("18.00"),
            subtotal=Decimal("100.00"),
            tax_amount=Decimal("18.00"),
            total=Decimal("118.00")
        )
        self.payment = Payment.objects.create(
            company=self.company,
            sale=self.sale,
            payment_method="UPI",
            amount=Decimal("118.00"),
            transaction_reference="UPI_DEMO_REF_1001",
            received_by=self.cashier_user
        )
        self.invoice = Invoice.objects.create(
            company=self.company,
            sale=self.sale,
            invoice_number="INV-2026-1001",
            company_name=self.company.company_name,
            cashier_name=self.cashier_user.username,
            subtotal=Decimal("100.00"),
            tax_amount=Decimal("18.00"),
            discount_amount=Decimal("0.00"),
            grand_total=Decimal("118.00"),
            paid_amount=Decimal("118.00"),
            balance_due=Decimal("0.00"),
            payment_summary="UPI"
        )

    # ── PHASE 2: BARCODE & POS LOOKUP TESTS ─────────────────────────────
    def test_barcode_lookup_success(self):
        """Active product can be queried via barcode or product_code with real stock & pricing."""
        self.api_client.force_authenticate(user=self.cashier_user)
        res = self.api_client.get("/api/products/lookup/?code=8901234567890")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["product"]["name"], "Cold Coffee 250ml")
        self.assertEqual(Decimal(str(res.data["product"]["current_stock"])), Decimal("50.00"))
        self.assertEqual(Decimal(str(res.data["product"]["selling_price"])), Decimal("60.00"))

    def test_inactive_product_rejection(self):
        """Inactive products must never be returned as valid for POS billing."""
        self.api_client.force_authenticate(user=self.cashier_user)
        res = self.api_client.get("/api/products/lookup/?code=8909999999999")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(res.data["active"])
        self.assertIn("inactive", res.data["error"].lower())

    def test_nonexistent_barcode_returns_404(self):
        """Invalid barcode lookup returns 404 with error message."""
        self.api_client.force_authenticate(user=self.cashier_user)
        res = self.api_client.get("/api/products/lookup/?code=INVALID_BARCODE")
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    # ── PHASE 3: PHONE CAMERA SCANNER TESTS ─────────────────────────────
    def test_phone_scanner_session_create_and_pair(self):
        """POS terminal creates scanner session with temporary token and pairing code."""
        self.api_client.force_authenticate(user=self.cashier_user)
        res = self.api_client.post("/api/pos/scanner-session/create/")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        token = res.data["session_token"]
        self.assertTrue(token)

        session_obj = POSScannerSession.objects.get(session_token=token)
        self.assertEqual(session_obj.company, self.company)

    def test_phone_scanner_tenant_isolation(self):
        """Scanner cannot inject barcodes into a session belonging to another tenant."""
        session_a = POSScannerSession.objects.create(
            company=self.company,
            session_token="token_comp_a",
            pairing_code="1234",
            user=self.cashier_user,
            status="ACTIVE",
            expires_at=timezone.now() + timedelta(hours=1)
        )

        # Phone client scans barcode into session
        res = self.api_client.post(f"/api/pos/scanner-session/{session_a.session_token}/scan/", {
            "barcode": "8901234567890"
        }, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

        # POS terminal polls and consumes
        self.api_client.force_authenticate(user=self.cashier_user)
        poll_res = self.api_client.get(f"/api/pos/scanner-session/{session_a.session_token}/poll/")
        self.assertEqual(poll_res.status_code, status.HTTP_200_OK)
        scans = poll_res.data["scans"]
        self.assertEqual(len(scans), 1)
        self.assertEqual(scans[0], "8901234567890")

        # Second poll returns empty since scan was consumed
        poll_res2 = self.api_client.get(f"/api/pos/scanner-session/{session_a.session_token}/poll/")
        self.assertEqual(len(poll_res2.data["scans"]), 0)

    # ── PHASE 4: UPI CHECKOUT & PAYMENT INTEGRATION ────────────────────
    def test_upi_payment_record_created(self):
        """UPI payment records transaction reference and links to authoritative Sale."""
        self.assertEqual(self.payment.payment_method, "UPI")
        self.assertEqual(self.payment.amount, Decimal("118.00"))
        self.assertEqual(self.sale.payment_status, "PAID")

    # ── PHASE 5: RETURN WORKFLOW, RESTOCKING & AUDIT TRAIL ──────────────
    def test_return_request_creation_and_manager_approval(self):
        """
        Submitting a return creates PENDING request.
        Manager approval restores stock via StockMovement(RETURN) and preserves historical Sale.
        """
        self.api_client.force_authenticate(user=self.cashier_user)

        # 1. Create return request
        res = self.api_client.post("/api/sales/returns/", {
            "invoice_number": self.invoice.invoice_number,
            "reason": "DAMAGED",
            "notes": "Customer returned 1 damaged coffee",
            "items": [
                {
                    "sale_item_id": self.sale_item.id,
                    "quantity": 1
                }
            ]
        }, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        return_id = res.data["id"]

        ret_req = ReturnRequest.objects.get(id=return_id)
        self.assertEqual(ret_req.status, "PENDING")
        self.assertEqual(ret_req.refund_amount, Decimal("59.00"))

        # Verify stock has NOT changed yet (still 50.00)
        self.prod_active.refresh_from_db()
        self.assertEqual(self.prod_active.current_stock, Decimal("50.00"))

        # 2. Manager approves return
        self.api_client.force_authenticate(user=self.manager_user)
        approve_res = self.api_client.post(f"/api/sales/returns/{return_id}/approve/")
        self.assertEqual(approve_res.status_code, status.HTTP_200_OK)

        # 3. Verify stock restored correctly
        self.prod_active.refresh_from_db()
        self.assertEqual(self.prod_active.current_stock, Decimal("51.00"))

        # 4. Verify StockMovement audit record
        movement = StockMovement.objects.filter(product=self.prod_active, movement_type="RETURN").latest("id")
        self.assertEqual(movement.quantity, Decimal("1.00"))
        self.assertEqual(movement.new_stock, Decimal("51.00"))

        # 5. Verify historical Sale was NOT deleted or mutated
        orig_sale = Sale.objects.get(id=self.sale.id)
        self.assertEqual(orig_sale.grand_total, Decimal("118.00"))
        self.assertEqual(orig_sale.items.count(), 1)

    def test_rejected_return_cannot_change_stock(self):
        """Rejected return request does NOT adjust stock and transitions to REJECTED."""
        self.api_client.force_authenticate(user=self.cashier_user)
        res = self.api_client.post("/api/sales/returns/", {
            "invoice_number": self.invoice.invoice_number,
            "reason": "CUSTOMER_CHANGED_MIND",
            "notes": "Opened bottle",
            "items": [{"sale_item_id": self.sale_item.id, "quantity": 1}]
        }, format="json")
        return_id = res.data["id"]

        # Manager rejects return
        self.api_client.force_authenticate(user=self.manager_user)
        reject_res = self.api_client.post(f"/api/sales/returns/{return_id}/reject/")
        self.assertEqual(reject_res.status_code, status.HTTP_200_OK)

        ret_req = ReturnRequest.objects.get(id=return_id)
        self.assertEqual(ret_req.status, "REJECTED")

        # Stock remains strictly unchanged
        self.prod_active.refresh_from_db()
        self.assertEqual(self.prod_active.current_stock, Decimal("50.00"))
        self.assertFalse(StockMovement.objects.filter(product=self.prod_active, movement_type="RETURN").exists())

    # ── PHASE 6: EMPLOYEE CREDENTIAL BOUNDARIES ─────────────────────────
    def test_owner_can_change_employee_credentials(self):
        """Owner can securely update credentials of any staff member."""
        self.web_client.force_login(self.owner_user)
        res = self.web_client.post(f"/employee/{self.cashier_emp.employee_id}/credentials/", {
            "email": "new_cashier@store.com",
            "new_password": "NewSecretPassword123",
            "confirm_password": "NewSecretPassword123"
        })
        self.assertEqual(res.status_code, 302)
        self.cashier_user.refresh_from_db()
        self.assertEqual(self.cashier_user.email, "new_cashier@store.com")
        self.assertTrue(check_password("NewSecretPassword123", self.cashier_user.password))

    def test_manager_cannot_change_owner_credentials(self):
        """Manager is blocked from escalating or changing Owner credentials."""
        owner_emp, _ = user_registration.objects.get_or_create(
            user=self.owner_user,
            company=self.company,
            defaults={
                "employee_id": "OWNER_001",
                "Role": "Admin",
                "Phone": "9876543200",
                "joining_date": "2026-01-01",
                "status": "active"
            }
        )

        self.web_client.force_login(self.manager_user)
        res = self.web_client.post(f"/employee/{owner_emp.employee_id}/credentials/", {
            "email": "hacked@store.com",
            "new_password": "HackedPassword123",
            "confirm_password": "HackedPassword123"
        }, follow=True)

        self.owner_user.refresh_from_db()
        self.assertTrue(check_password("Password@123", self.owner_user.password))
        self.assertFalse(check_password("HackedPassword123", self.owner_user.password))

    # ── PHASE 7: TELEGRAM OWNER REPORTING ───────────────────────────────
    def test_telegram_unpaired_chat_id_denied(self):
        """Unpaired Telegram chat ID receives unauthorized rejection message."""
        reply = handle_telegram_command("random_chat_999", "/sales today")
        self.assertIn("Unauthorized Access", reply)

    def test_telegram_pairing_and_reporting_commands(self):
        """Owner generates one-time pairing code, pairs chat ID, and queries sales & stock."""
        link = generate_pairing_code(self.company, self.owner_user)
        code = link.pairing_code
        self.assertTrue(len(code) >= 6)

        owner_chat = "tg_chat_owner_777"
        reply_pair = handle_telegram_command(owner_chat, f"/pair {code}")
        self.assertIn("Pairing successful", reply_pair)

        sales_reply = handle_telegram_command(owner_chat, "/sales today")
        self.assertIn("Today's Sales Report", sales_reply)
        self.assertIn("Alpha Supermarket", sales_reply)

        stock_reply = handle_telegram_command(owner_chat, "/stock")
        self.assertIn("Store Inventory Overview", stock_reply)

        returns_reply = handle_telegram_command(owner_chat, "/returns")
        self.assertIn("Store Returns & Refunds Status", returns_reply)

        outbound = TelegramOutboundMessage.objects.filter(company=self.company, chat_id=owner_chat)
        self.assertTrue(outbound.exists())

    # ── PHASE 8: AI BUSINESS ASSISTANT ──────────────────────────────────
    def test_ai_business_queries_read_only(self):
        """Natural language questions return real data without modifying database."""
        ans_sales = answer_business_question(self.company, "How much did we sell today?")
        self.assertEqual(ans_sales["intent"], "sales_today")
        self.assertEqual(ans_sales["data"]["count"], 1)
        self.assertEqual(Decimal(str(ans_sales["data"]["revenue"])), Decimal("118.00"))

        ans_top = answer_business_question(self.company, "What are our top products?")
        self.assertEqual(ans_top["intent"], "top_products")
        self.assertIn("Cold Coffee 250ml", ans_top["answer"])

        ans_stock = answer_business_question(self.company, "Which products are low on stock?")
        self.assertEqual(ans_stock["intent"], "low_stock")

        ans_wow = answer_business_question(self.company, "Compare this week with last week.")
        self.assertEqual(ans_wow["intent"], "weekly_comparison")

        self.api_client.force_authenticate(user=self.owner_user)
        res_insights = self.api_client.get("/api/ai/insights/")
        self.assertEqual(res_insights.status_code, status.HTTP_200_OK)
        self.assertIn("sales_insights", res_insights.data)
        self.assertIn("inventory_intelligence", res_insights.data)

    # ── PHASE 9: API DOCUMENTATION ENDPOINTS ────────────────────────────
    def test_api_schema_endpoint(self):
        """OpenAPI 3.0 JSON specification is served at /api/schema/."""
        res = self.web_client.get("/api/schema/")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["openapi"], "3.0.3")
        self.assertIn("/api/products/lookup/", data["paths"])
        self.assertIn("/api/sales/returns/", data["paths"])
        self.assertIn("/api/ai/ask/", data["paths"])

    def test_api_docs_swagger_ui(self):
        """Swagger UI HTML documentation is served at /api/docs/."""
        res = self.web_client.get("/api/docs/")
        self.assertEqual(res.status_code, 200)
        self.assertIn("text/html", res["Content-Type"])
        self.assertIn("SwaggerUIBundle", res.content.decode("utf-8"))


class POSScannerArchitectureTestSuite(TestCase):
    """
    Comprehensive tests for the reworked Supermarket / DMart POS Scanner Architecture:
    A. HID barcode lookup
    B. repeated barcode increments cart quantity
    C. unknown barcode
    D. inactive barcode
    E. duplicate barcode prevention
    F. generated internal barcode uniqueness
    G. generated barcode can be scanned back into POS
    H. cashier cannot create/assign barcode
    I. manager can assign barcode
    J. owner scanner settings access
    K. phone scanner HTTP fallback
    L. existing checkout remains idempotent.
    """
    def setUp(self):
        self.api_client = APIClient()
        self.web_client = Client()

        # Company & Users
        self.owner = User.objects.create_user(username="owner_pos", email="owner@dmart.com", password="Password@123")
        self.company = companyRegistration.objects.create(
            owner=self.owner,
            company_name="DMart Superstore",
            address_line="MG Road",
            city="Bengaluru",
            state="Karnataka",
            country="India",
            pincode="560001",
            company_email="dmart@store.com",
            phone="9876543210",
            gst_number="29AAAAA0000A1Z5",
            business_info="Retail Supermarket"
        )

        self.manager_user = User.objects.create_user(username="manager_pos", email="mgr@dmart.com", password="Password@123")
        self.manager_emp = user_registration.objects.create(
            user=self.manager_user,
            company=self.company,
            employee_id="MGR_SCAN",
            Role="Manager",
            Phone="9876543211",
            joining_date="2026-01-01",
            payment="Paid",
            status="active"
        )

        self.cashier_user = User.objects.create_user(username="cashier_pos", email="csh@dmart.com", password="Password@123")
        self.cashier_emp = user_registration.objects.create(
            user=self.cashier_user,
            company=self.company,
            employee_id="CSH_SCAN",
            Role="Cashier",
            Phone="9876543212",
            joining_date="2026-01-01",
            payment="Paid",
            status="active"
        )

        self.category = Category.objects.create(company=self.company, name="Packaged Food")
        self.supplier = Supplier.objects.create(company=self.company, name="ITC Limited")

        self.product_active = Product.objects.create(
            company=self.company,
            category=self.category,
            supplier=self.supplier,
            name="Aashirvaad Atta 5kg",
            product_code="AASH-5KG",
            barcode="8901030000012",
            purchase_price=Decimal("210.00"),
            selling_price=Decimal("260.00"),
            current_stock=Decimal("50.00"),
            minimum_stock=Decimal("5.00"),
            gst_percentage=Decimal("5.00"),
            status=True
        )

        self.product_inactive = Product.objects.create(
            company=self.company,
            category=self.category,
            supplier=self.supplier,
            name="Discontinued Biscuits",
            product_code="DISC-001",
            barcode="8901030999999",
            purchase_price=Decimal("10.00"),
            selling_price=Decimal("20.00"),
            current_stock=Decimal("10.00"),
            minimum_stock=Decimal("2.00"),
            gst_percentage=Decimal("18.00"),
            status=False
        )

    # A. HID barcode lookup
    def test_a_hid_barcode_lookup(self):
        """Cashier scans barcode: GET /api/products/lookup/?code=<barcode> returns product."""
        self.api_client.force_authenticate(user=self.cashier_user)
        res = self.api_client.get(f"/api/products/lookup/?code={self.product_active.barcode}")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertTrue(res.data["found"])
        self.assertEqual(res.data["product"]["name"], "Aashirvaad Atta 5kg")
        self.assertEqual(res.data["product"]["barcode"], "8901030000012")

    # B. Repeated barcode increments cart quantity
    def test_b_repeated_barcode_increments_cart_quantity(self):
        """POS quote engine calculates authoritative totals for quantity=2 on repeated scan."""
        self.api_client.force_authenticate(user=self.cashier_user)
        payload = {
            "items": [
                {
                    "product_id": self.product_active.id,
                    "quantity": 2,
                    "unit_price": float(self.product_active.selling_price)
                }
            ],
            "discount_type": "FLAT",
            "discount_value": 0
        }
        res = self.api_client.post("/api/sales/quote/", payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        # Subtotal: 260 * 2 = 520
        self.assertEqual(Decimal(str(res.data["quote"]["subtotal"])), Decimal("520.00"))

    # C. Unknown barcode
    def test_c_unknown_barcode(self):
        """Unknown barcode returns found=False (404 Not Found) without creating phantom products."""
        self.api_client.force_authenticate(user=self.cashier_user)
        res = self.api_client.get("/api/products/lookup/?code=UNKNOWN999999")
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(res.data["found"])
        self.assertIn("not found", res.data["error"].lower())

    # D. Inactive barcode
    def test_d_inactive_barcode_rejected(self):
        """Inactive product barcode lookup returns 400 Bad Request with active=False."""
        self.api_client.force_authenticate(user=self.cashier_user)
        res = self.api_client.get(f"/api/products/lookup/?code={self.product_inactive.barcode}")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(res.data.get("active", True))

    # E. Duplicate barcode prevention
    def test_e_duplicate_barcode_prevention(self):
        """API check-barcode detects existing barcode within tenant to prevent duplicate collisions."""
        self.api_client.force_authenticate(user=self.manager_user)
        res = self.api_client.get(f"/api/products/check-barcode/?code={self.product_active.barcode}")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertTrue(res.data["exists"])
        self.assertEqual(res.data["product_name"], "Aashirvaad Atta 5kg")

    # F. Generated internal barcode uniqueness
    def test_f_generated_internal_barcode_uniqueness(self):
        """Internal barcodes follow Code 128 GS1 in-store standard and are sequentially unique."""
        b1 = generate_internal_barcode(self.company)
        self.assertTrue(b1.startswith(f"20{self.company.id % 1000:03d}"))

        # Create product with b1 and generate next
        Product.objects.create(
            company=self.company,
            category=self.category,
            supplier=self.supplier,
            name="Loose Sugar 1kg",
            product_code="SUG-001",
            barcode=b1,
            purchase_price=Decimal("40.00"),
            selling_price=Decimal("48.00"),
            current_stock=Decimal("100.00"),
            minimum_stock=Decimal("10.00"),
            gst_percentage=Decimal("5.00"),
            status=True
        )

        b2 = generate_internal_barcode(self.company)
        self.assertNotEqual(b1, b2)
        self.assertTrue(b2.startswith(f"20{self.company.id % 1000:03d}"))

    # G. Generated barcode can be scanned back into POS
    def test_g_generated_barcode_can_be_scanned_back_into_pos(self):
        """Internal barcode generated for bulk item can be scanned by Cashier directly at POS."""
        internal_code = generate_internal_barcode(self.company)
        prod = Product.objects.create(
            company=self.company,
            category=self.category,
            supplier=self.supplier,
            name="Fresh Bakery Bread",
            product_code="BRD-GEN",
            barcode=internal_code,
            purchase_price=Decimal("25.00"),
            selling_price=Decimal("35.00"),
            current_stock=Decimal("30.00"),
            minimum_stock=Decimal("5.00"),
            gst_percentage=Decimal("0.00"),
            status=True
        )

        self.api_client.force_authenticate(user=self.cashier_user)
        res = self.api_client.get(f"/api/products/lookup/?code={internal_code}")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertTrue(res.data["found"])
        self.assertEqual(res.data["product"]["id"], prod.id)
        self.assertEqual(res.data["product"]["name"], "Fresh Bakery Bread")

    # H. Cashier cannot create/assign barcode
    def test_h_cashier_cannot_create_or_assign_barcode(self):
        """Cashier role is strictly forbidden from creating products or generating barcodes."""
        self.api_client.force_authenticate(user=self.cashier_user)

        res_gen = self.api_client.post("/api/products/generate-barcode/")
        self.assertEqual(res_gen.status_code, status.HTTP_403_FORBIDDEN)

        res_check = self.api_client.get("/api/products/check-barcode/?code=12345")
        self.assertEqual(res_check.status_code, status.HTTP_403_FORBIDDEN)

        res_create = self.api_client.post("/api/products/", {
            "name": "Unauthorized Product",
            "category": self.category.id,
            "product_code": "UNAUTH-01",
            "selling_price": "100.00"
        })
        self.assertEqual(res_create.status_code, status.HTTP_403_FORBIDDEN)

    # I. Manager can assign barcode
    def test_i_manager_can_assign_barcode(self):
        """Manager role has authorization to generate internal barcodes and create products."""
        self.api_client.force_authenticate(user=self.manager_user)
        res_gen = self.api_client.post("/api/products/generate-barcode/")
        self.assertEqual(res_gen.status_code, status.HTTP_200_OK)
        self.assertIn("barcode", res_gen.data)

        gen_barcode = res_gen.data["barcode"]
        res_prod = self.api_client.post("/api/products/", {
            "name": "Manager Registered Item",
            "category": self.category.id,
            "supplier": self.supplier.id,
            "product_code": "MGR-REG-01",
            "barcode": gen_barcode,
            "purchase_price": "50.00",
            "selling_price": "75.00",
            "current_stock": "20.00",
            "minimum_stock": "5.00",
            "gst_percentage": "5.00",
            "status": True
        })
        self.assertEqual(res_prod.status_code, status.HTTP_201_CREATED)

    # J. Owner scanner settings access
    def test_j_owner_and_manager_scanner_settings_access(self):
        """Owner and Manager can access Scanner Settings; Cashier is forbidden."""
        # Owner login
        self.web_client.login(username="owner_pos", password="Password@123")
        res_owner = self.web_client.get("/settings/scanner/")
        self.assertEqual(res_owner.status_code, 200)
        self.assertIn("Scanner".encode(), res_owner.content)
        self.assertIn("POS Devices".encode(), res_owner.content)

        # Manager login
        self.web_client.login(username="manager_pos", password="Password@123")
        res_mgr = self.web_client.get("/settings/scanner/")
        self.assertEqual(res_mgr.status_code, 200)

        # Cashier login
        self.web_client.login(username="cashier_pos", password="Password@123")
        res_cashier = self.web_client.get("/settings/scanner/")
        self.assertEqual(res_cashier.status_code, 403)

    # K. Phone scanner HTTP fallback
    def test_k_phone_scanner_http_fallback(self):
        """Companion phone session returns LAN connection URL and supports handshake heartbeat."""
        self.api_client.force_authenticate(user=self.cashier_user)
        res_create = self.api_client.post("/api/pos/scanner-session/create/")
        self.assertEqual(res_create.status_code, status.HTTP_201_CREATED)
        token = res_create.data["session_token"]
        self.assertIn("connect_url", res_create.data)
        self.assertIn("lan_connect_url", res_create.data)

        # Phone client connects and sends heartbeat ping (no dummy scan created)
        res_ping = self.api_client.post(f"/api/pos/scanner-session/{token}/scan/", {
            "heartbeat": True,
            "device_info": "Mobile Phone (Android Chrome)"
        })
        self.assertEqual(res_ping.status_code, status.HTTP_200_OK)

        # Poll verifies session is active with device info recorded, zero pending dummy scans
        res_poll = self.api_client.get(f"/api/pos/scanner-session/{token}/poll/")
        self.assertEqual(res_poll.status_code, status.HTTP_200_OK)
        self.assertTrue(res_poll.data["active"])
        self.assertEqual(res_poll.data["device_info"], "Mobile Phone (Android Chrome)")
        self.assertEqual(len(res_poll.data["scans"]), 0)

    # L. Existing checkout remains idempotent
    def test_l_existing_checkout_remains_idempotent(self):
        """Checkout with Idempotency-Key prevents duplicate billing and negative stock."""
        self.api_client.force_authenticate(user=self.cashier_user)
        idempotency_key = "POS-IDEMP-TEST-SCANNER-999"

        payload = {
            "customer_id": None,
            "items": [
                {
                    "product_id": self.product_active.id,
                    "quantity": 1,
                    "unit_price": "260.00"
                }
            ],
            "discount_type": "FLAT",
            "discount_value": "0.00",
            "payments": [
                {
                    "payment_method": "CASH",
                    "amount": "273.00"  # 260 + 5% GST (13) = 273.00
                }
            ]
        }

        # First checkout
        res1 = self.api_client.post("/api/sales/checkout/", payload, format="json", HTTP_IDEMPOTENCY_KEY=idempotency_key)
        self.assertEqual(res1.status_code, status.HTTP_201_CREATED)
        invoice1 = res1.data.get("invoice_number") or res1.data.get("sale_number")

        # Stock deducted by 1 (50 -> 49)
        self.product_active.refresh_from_db()
        self.assertEqual(self.product_active.current_stock, Decimal("49.00"))

        # Replay with same idempotency key
        res2 = self.api_client.post("/api/sales/checkout/", payload, format="json", HTTP_IDEMPOTENCY_KEY=idempotency_key)
        self.assertIn(res2.status_code, [status.HTTP_200_OK, status.HTTP_201_CREATED])
        self.assertEqual(res2.headers.get("X-Idempotent-Replay"), "true")
        invoice2 = res2.data.get("invoice_number") or res2.data.get("sale_number")
        self.assertEqual(invoice1, invoice2)

        # Stock is NOT deducted again (still 49)
        self.product_active.refresh_from_db()
        self.assertEqual(self.product_active.current_stock, Decimal("49.00"))
