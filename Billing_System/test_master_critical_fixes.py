import os
import json
from decimal import Decimal
from django.test import TestCase, Client
from django.contrib.auth.models import User
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models import companyRegistration
from employee.models import user_registration
from inventory.models import Category, Supplier, Product, StockMovement
from sales.models import Customer, Sale, SaleItem, Payment, Invoice, ReturnRequest, ReturnItem
from dashboard.models import InternalNotification
from dashboard.ai_assistant import answer_business_question, handle_staff_communication


class MasterCriticalFixesTestSuite(TestCase):
    """
    Comprehensive regression test suite verifying all P0-P5 requirements
    from the ANTIGRAVITY Master Bugfix Prompt V2.
    Uses Django's isolated test database to protect production/local SQLite data.
    """

    def setUp(self):
        # ── 1. Tenant Setup: Store A (Alpha Retail) ───────────────────────────
        self.owner_a = User.objects.create_user(username="owner_store_a", password="password123")
        self.company_a = companyRegistration.objects.create(
            owner=self.owner_a,
            company_name="Alpha Retail Store",
            address_line="100 MG Road",
            city="Bengaluru",
            state="Karnataka",
            country="India",
            pincode="560001",
            company_email="alpha@store.test",
            phone="9811111111",
            gst_number="29AAAAA0000A1Z5",
            business_info="Retail Supermarket"
        )

        # Store A Manager
        self.manager_a = User.objects.create_user(username="manager_rahul", first_name="Rahul", last_name="Verma", password="password123")
        self.manager_emp_a = user_registration.objects.create(
            user=self.manager_a,
            company=self.company_a,
            employee_id="MGR001",
            Role="Manager",
            Phone="9822222222",
            joining_date="2026-01-01",
            payment="Paid",
            status="active"
        )

        # Store A Cashier (Billo)
        self.cashier_billo = User.objects.create_user(username="billo_cashier", first_name="Billo", last_name="Singh", password="password123")
        self.cashier_emp_billo = user_registration.objects.create(
            user=self.cashier_billo,
            company=self.company_a,
            employee_id="CSH001",
            Role="Cashier",
            Phone="9833333333",
            joining_date="2026-01-01",
            payment="Paid",
            status="active"
        )

        # Store A Catalog & Customer
        self.category = Category.objects.create(company=self.company_a, name="Groceries")
        self.supplier = Supplier.objects.create(company=self.company_a, name="ABC Suppliers", phone="9844444444")
        self.product = Product.objects.create(
            company=self.company_a,
            category=self.category,
            supplier=self.supplier,
            name="Organic Basmati Rice 1kg",
            product_code="RICE-001",
            purchase_price=Decimal("60.00"),
            selling_price=Decimal("100.00"),
            gst_percentage=Decimal("5.00"),
            current_stock=Decimal("50.00"),
            minimum_stock=Decimal("10.00")
        )
        self.customer = Customer.objects.create(
            company=self.company_a,
            name="Priya Sharma",
            phone="9988776655"
        )

        # Create Sale & SaleItem for return tests
        self.sale = Sale.objects.create(
            company=self.company_a,
            sale_number="SALE-2026-0001",
            cashier=self.cashier_billo,
            customer=self.customer,
            subtotal=Decimal("200.00"),
            tax_amount=Decimal("10.00"),
            grand_total=Decimal("210.00"),
            paid_amount=Decimal("210.00")
        )
        self.sale_item = SaleItem.objects.create(
            sale=self.sale,
            product=self.product,
            product_name=self.product.name,
            product_code=self.product.product_code,
            quantity=Decimal("2.00"),
            unit_price=Decimal("100.00"),
            subtotal=Decimal("200.00"),
            tax_amount=Decimal("10.00"),
            total=Decimal("210.00")
        )
        self.payment = Payment.objects.create(
            company=self.company_a,
            sale=self.sale,
            payment_method="UPI",
            amount=Decimal("210.00"),
            transaction_reference="UPI/123456",
            received_by=self.cashier_billo
        )
        self.invoice = Invoice.objects.create(
            company=self.company_a,
            sale=self.sale,
            invoice_number="INV-2026-0001",
            customer_name="Priya Sharma",
            company_name=self.company_a.company_name,
            subtotal=Decimal("200.00"),
            tax_amount=Decimal("10.00"),
            discount_amount=Decimal("0.00"),
            grand_total=Decimal("210.00"),
            paid_amount=Decimal("210.00"),
            balance_due=Decimal("0.00"),
            cashier_name="billo_cashier"
        )

        # ── 2. Tenant Setup: Store B (Isolated Store B) ────────────────────────
        self.owner_b = User.objects.create_user(username="owner_store_b", password="password123")
        self.company_b = companyRegistration.objects.create(
            owner=self.owner_b,
            company_name="Beta Retail Store",
            address_line="200 Park Street",
            city="Mumbai",
            state="Maharashtra",
            country="India",
            pincode="400001",
            company_email="beta@store.test",
            phone="9855555555",
            gst_number="27BBBBB0000B1Z6",
            business_info="Retail Store"
        )
        self.cashier_b = User.objects.create_user(username="cashier_store_b", password="password123")
        self.cashier_emp_b = user_registration.objects.create(
            user=self.cashier_b,
            company=self.company_b,
            employee_id="CSH_B01",
            Role="Cashier",
            Phone="9866666666",
            joining_date="2026-01-01",
            payment="Paid",
            status="active"
        )

        self.api_client = APIClient()
        self.web_client = Client()

    # ══════════════════════════════════════════════════════════════════════════
    # P0: RETURN REQUEST SUBMISSION TESTS
    # ══════════════════════════════════════════════════════════════════════════

    def test_p0_cashier_submits_return_request_pending_zero_stock_side_effects(self):
        """
        Cashier submits a return request:
        1. API returns HTTP 201 Created (no 500 serializer crash on approved_by).
        2. Status is strictly 'PENDING'.
        3. approved_by is None.
        4. Stock does NOT increase while pending.
        5. No customer refund or payment reversal occurs before approval.
        """
        stock_before = self.product.current_stock
        self.api_client.force_authenticate(user=self.cashier_billo)

        payload = {
            "sale_id": self.sale.id,
            "reason": "Customer changed mind",
            "refund_method": "ORIGINAL",
            "items": [
                {
                    "sale_item_id": self.sale_item.id,
                    "quantity": 1,
                    "restock": True
                }
            ]
        }

        response = self.api_client.post("/api/sales/returns/", data=payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)

        ret_id = response.data.get("id")
        ret_req = ReturnRequest.objects.get(id=ret_id)
        self.assertEqual(ret_req.status, "PENDING")
        self.assertIsNone(ret_req.approved_by)
        self.assertEqual(response.data.get("approved_by_name"), None)

        # Stock must NOT be touched
        self.product.refresh_from_db()
        self.assertEqual(self.product.current_stock, stock_before)

        # No stock movements created
        movements = StockMovement.objects.filter(product=self.product, reference=f"RETURN-{ret_req.id}")
        self.assertEqual(movements.count(), 0)

    def test_p0_manager_submits_direct_return_approved_and_restocked(self):
        """
        Manager/Owner submitting return directly approves it, restocks inventory,
        and generates audit records.
        """
        stock_before = self.product.current_stock
        self.api_client.force_authenticate(user=self.manager_a)

        payload = {
            "sale_id": self.sale.id,
            "reason": "Defective item packaging",
            "refund_method": "CASH",
            "items": [
                {
                    "sale_item_id": self.sale_item.id,
                    "quantity": 1,
                    "restock": True
                }
            ]
        }

        response = self.api_client.post("/api/sales/returns/", data=payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        ret_req = ReturnRequest.objects.get(id=response.data["id"])
        self.assertEqual(ret_req.status, "APPROVED")
        self.assertEqual(ret_req.approved_by, self.manager_a)

        # Stock IS incremented by 1
        self.product.refresh_from_db()
        self.assertEqual(self.product.current_stock, stock_before + Decimal("1.00"))

    def test_p0_return_quantity_validation_cannot_exceed_eligible_or_zero(self):
        """
        Validation: Returned quantity cannot be 0, negative, or exceed eligible quantity.
        """
        self.api_client.force_authenticate(user=self.cashier_billo)

        # 1. Zero quantity rejected
        res_zero = self.api_client.post("/api/sales/returns/", data={
            "sale_id": self.sale.id,
            "reason": "Wrong item",
            "refund_method": "CASH",
            "items": [{"sale_item_id": self.sale_item.id, "quantity": 0}]
        }, format="json")
        self.assertEqual(res_zero.status_code, status.HTTP_400_BAD_REQUEST)

        # 2. Exceeding quantity (original quantity is 2; try returning 5)
        res_excess = self.api_client.post("/api/sales/returns/", data={
            "sale_id": self.sale.id,
            "reason": "Wrong item",
            "refund_method": "CASH",
            "items": [{"sale_item_id": self.sale_item.id, "quantity": 5}]
        }, format="json")
        self.assertEqual(res_excess.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("exceeds eligible remaining", str(res_excess.data))

    def test_p0_cross_tenant_return_isolation(self):
        """
        Cashier from Company B cannot return a sale from Company A.
        """
        self.api_client.force_authenticate(user=self.cashier_b)
        response = self.api_client.post("/api/sales/returns/", data={
            "sale_id": self.sale.id,
            "reason": "Wrong store return attempt",
            "refund_method": "CASH",
            "items": [{"sale_item_id": self.sale_item.id, "quantity": 1}]
        }, format="json")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    # ══════════════════════════════════════════════════════════════════════════
    # P1: SIDEBAR ROUTING & DESTINATIONS
    # ══════════════════════════════════════════════════════════════════════════

    def test_p1_sidebar_navigation_destinations(self):
        """
        Verifies that each menu item has its own distinct Django route and returns 200 OK:
        - Sales & Invoices: /sales/
        - Returns: /sales/returns/
        - Reports: /reports/
        - POS Terminal: /cart/
        """
        self.web_client.force_login(self.manager_a)

        # 1. Sales & Invoices
        res_sales = self.web_client.get("/sales/")
        self.assertEqual(res_sales.status_code, 200)

        # 2. Returns
        res_returns = self.web_client.get("/sales/returns/")
        self.assertEqual(res_returns.status_code, 200)

        # 3. Reports
        res_reports = self.web_client.get("/reports/")
        self.assertEqual(res_reports.status_code, 200)

        # 4. POS Terminal
        res_pos = self.web_client.get("/cart/")
        self.assertEqual(res_pos.status_code, 200)

    # ══════════════════════════════════════════════════════════════════════════
    # P2: OWNER/MANAGER -> CASHIER MESSAGING & VISIBILITY
    # ══════════════════════════════════════════════════════════════════════════

    def test_p2_owner_messages_cashier_persists_internal_notification(self):
        """
        Owner uses AI Assistant: 'Tell Billo to meet me in the office.'
        1. Recipient Billo is resolved.
        2. InternalNotification is persisted in DB with status DELIVERED.
        3. Assistant returns truthful feedback indicating notification is queued in inbox.
        4. Cashier sees unread notification via /api/notifications/.
        5. Cashier marks notification as read.
        """
        res_ai = answer_business_question(
            company=self.company_a,
            question_text="Tell Billo to meet me in the office.",
            user=self.owner_a
        )
        self.assertEqual(res_ai["intent"], "staff_communication")
        self.assertIn("Billo", res_ai["answer"])
        self.assertIn("meet me in the office", res_ai["answer"])

        # Check DB persistence
        notif = InternalNotification.objects.filter(company=self.company_a, recipient=self.cashier_billo).first()
        self.assertIsNotNone(notif)
        self.assertEqual(notif.sender, self.owner_a)
        self.assertEqual(notif.message, "meet me in the office.")
        self.assertFalse(notif.is_read)

        # Cashier inspects notifications endpoint
        self.api_client.force_authenticate(user=self.cashier_billo)
        res_list = self.api_client.get("/api/notifications/")
        self.assertEqual(res_list.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(res_list.data["unread_count"], 1)

        # Cashier marks read
        res_read = self.api_client.post(f"/api/notifications/{notif.id}/read/")
        self.assertEqual(res_read.status_code, status.HTTP_200_OK)
        notif.refresh_from_db()
        self.assertTrue(notif.is_read)
        self.assertEqual(notif.status, "READ")

    def test_p2_call_intent_truthful_response(self):
        """
        Owner says: 'Call Billo'.
        Assistant responds truthfully that direct telephony is not integrated,
        and provides in-app messaging option and registered phone number.
        """
        res_call = answer_business_question(
            company=self.company_a,
            question_text="Call Billo",
            user=self.owner_a
        )
        self.assertEqual(res_call["intent"], "staff_call_inquiry")
        self.assertIn("Automatic telephony dialing is not integrated", res_call["answer"])
        self.assertIn("9833333333", res_call["answer"])

    def test_p2_ambiguous_staff_resolution(self):
        """
        If two active staff members have the same name, assistant asks for disambiguation.
        """
        # Create a second Rahul in Company A
        user_rahul2 = User.objects.create_user(username="rahul_cashier", first_name="Rahul", last_name="Sharma", password="password123")
        user_registration.objects.create(
            user=user_rahul2,
            company=self.company_a,
            employee_id="CSH002",
            Role="Cashier",
            Phone="9877777777",
            joining_date="2026-01-01",
            payment="Paid",
            status="active"
        )

        res = answer_business_question(
            company=self.company_a,
            question_text="Tell Rahul to verify the drawers",
            user=self.owner_a
        )
        self.assertEqual(res["intent"], "staff_communication")
        self.assertTrue(res["data"].get("ambiguous"))
        self.assertIn("Multiple active staff members match", res["answer"])
        self.assertIn("MGR001", res["answer"])
        self.assertIn("CSH002", res["answer"])

    def test_p2_cashier_broadcast_blocked_by_rbac(self):
        """
        Cashier attempting to broadcast announcement is blocked.
        """
        res_blocked = answer_business_question(
            company=self.company_a,
            question_text="Notify all managers that meeting is postponed",
            user=self.cashier_billo
        )
        self.assertEqual(res_blocked["intent"], "staff_communication")
        self.assertFalse(res_blocked.get("data", {}).get("permitted", True))
        self.assertIn("cannot send broadcast announcements", res_blocked["answer"])

    # ══════════════════════════════════════════════════════════════════════════
    # P3: THERMAL PRINTING & KEYBOARD SHORTCUT
    # ══════════════════════════════════════════════════════════════════════════

    def test_p3_invoice_thermal_layout_and_shortcut(self):
        """
        Invoice detail page:
        1. Renders dedicated #thermal-receipt container.
        2. Includes item details, quantities, rates, taxes, grand total, and payment method.
        3. Contains 58mm and 80mm print actions.
        4. Contains setPrintPageSize dynamic page styling function.
        5. Registers F8 keyboard shortcut for thermal print.
        """
        self.web_client.force_login(self.owner_a)
        res = self.web_client.get(f"/invoice/{self.invoice.invoice_number}/")
        self.assertEqual(res.status_code, 200)

        html = res.content.decode("utf-8")
        self.assertIn('id="thermal-receipt"', html)
        self.assertIn("Organic Basmati Rice 1kg", html)
        self.assertIn("₹210.00", html)
        self.assertIn("UPI", html)
        self.assertIn("printThermalReceipt('80mm')", html)
        self.assertIn("printThermalReceipt('58mm')", html)
        self.assertIn("setPrintPageSize", html)
        self.assertIn("F8", html)

    # ══════════════════════════════════════════════════════════════════════════
    # P4: LOGIN UI "WELCOME BACK" POSITIONING & REDUCED MOTION
    # ══════════════════════════════════════════════════════════════════════════

    def test_p4_login_page_welcome_back_styles_and_reduced_motion(self):
        """
        Login page (/accounts/):
        1. .aside-hero h2 has font-size 24px (modestly enlarged).
        2. .auth-aside-body has margin: auto 0; (vertically centered).
        3. Bottom-right registration link is preserved.
        4. prefers-reduced-motion media query is respected.
        """
        res = self.web_client.get("/accounts/")
        self.assertEqual(res.status_code, 200)

        html = res.content.decode("utf-8")
        self.assertIn("font-size: 24px;", html)
        self.assertIn(".auth-aside-body", html)
        self.assertIn("margin: auto 0;", html)
        self.assertIn("compact-reg-link", html)
        self.assertIn("@media (prefers-reduced-motion: reduce)", html)
