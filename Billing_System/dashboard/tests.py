from django.test import TestCase, Client
from django.contrib.auth.models import User
from accounts.models import companyRegistration
from employee.models import user_registration
from dashboard.models import InternalNotification


class DashboardAndReportsRBACTests(TestCase):
    def setUp(self):
        # 1. Company and Owner
        self.owner = User.objects.create_user(username="owner_dash", password="password123")
        self.company = companyRegistration.objects.create(
            owner=self.owner,
            company_name="Dash Retailers",
            address_line="100 Dash Way",
            city="Mumbai",
            state="Maharashtra",
            country="India",
            pincode="400001",
            company_email="dash@example.com",
            phone="9800001111",
            alternate_phone="9800002222",
            gst_number="27DASH0000A1Z1",
            business_info="Retail"
        )

        # 2. Manager
        self.mgr_user = User.objects.create_user(username="mgr_dash", password="password123")
        self.mgr_emp = user_registration.objects.create(
            user=self.mgr_user,
            company=self.company,
            employee_id="MGR_D01",
            Role="Manager",
            Phone="9800003333",
            joining_date="2026-01-01",
            payment="Paid",
            status="active"
        )

        # 3. Cashier
        self.cashier_user = User.objects.create_user(username="cashier_dash", password="password123")
        self.cashier_emp = user_registration.objects.create(
            user=self.cashier_user,
            company=self.company,
            employee_id="CSH_D01",
            Role="Cashier",
            Phone="9800004444",
            joining_date="2026-01-01",
            payment="Paid",
            status="active"
        )

        # 4. Worker
        self.worker_user = User.objects.create_user(username="worker_dash", password="password123")
        self.worker_emp = user_registration.objects.create(
            user=self.worker_user,
            company=self.company,
            employee_id="WRK_D01",
            Role="Worker",
            Phone="9800005555",
            joining_date="2026-01-01",
            payment="Paid",
            status="active"
        )

    def test_owner_access_to_financial_dashboard(self):
        client = Client()
        client.force_login(self.owner)
        res = client.get("/dashboard/")
        self.assertEqual(res.status_code, 200)

    def test_manager_access_to_dashboard(self):
        client = Client()
        client.force_login(self.mgr_user)
        res = client.get("/dashboard/")
        self.assertEqual(res.status_code, 200)

    def test_cashier_denied_from_owner_dashboard(self):
        client = Client()
        client.force_login(self.cashier_user)
        res = client.get("/dashboard/")
        self.assertEqual(res.status_code, 403)

    def test_worker_denied_from_owner_dashboard(self):
        client = Client()
        client.force_login(self.worker_user)
        res = client.get("/dashboard/")
        self.assertEqual(res.status_code, 403)

    def test_anonymous_redirected_from_dashboard(self):
        client = Client()
        res = client.get("/dashboard/")
        self.assertEqual(res.status_code, 302)

    def test_cashier_portal_allowed_for_cashier(self):
        client = Client()
        client.force_login(self.cashier_user)
        res = client.get("/cashier_dashboard/")
        self.assertEqual(res.status_code, 200)
        # Verify owner sensitive revenue/profit metrics are not in context
        self.assertNotIn("total_revenue", res.context)
        self.assertNotIn("total_profit", res.context)

    def test_reports_security_anonymous_redirected(self):
        client = Client()
        res = client.get("/reports/")
        self.assertEqual(res.status_code, 302)

    def test_reports_security_cashier_forbidden(self):
        client = Client()
        client.force_login(self.cashier_user)
        res = client.get("/reports/")
        self.assertEqual(res.status_code, 403)

    def test_reports_security_worker_forbidden(self):
        client = Client()
        client.force_login(self.worker_user)
        res = client.get("/reports/")
        self.assertEqual(res.status_code, 403)

    def test_reports_security_owner_allowed(self):
        client = Client()
        client.force_login(self.owner)
        res = client.get("/reports/")
        self.assertEqual(res.status_code, 200)

    def test_reports_security_manager_allowed(self):
        client = Client()
        client.force_login(self.mgr_user)
        res = client.get("/reports/")
        self.assertEqual(res.status_code, 200)


from decimal import Decimal
from datetime import date, timedelta
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework import status
from inventory.models import Category, Supplier, Product
from sales.models import Customer, Sale, SaleItem, Payment, Invoice, ReturnRequest
from dashboard.ai_assistant import (
    parse_date_or_range,
    answer_business_question,
    generate_sales_insights,
    generate_inventory_intelligence,
)


class AIAssistantUnitTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="owner_ai", password="password123")
        self.company = companyRegistration.objects.create(
            owner=self.owner,
            company_name="Apex Retail Hub",
            address_line="200 Tech Park",
            city="Pune",
            state="Maharashtra",
            country="India",
            pincode="411001",
            company_email="apex@example.com",
            phone="9811112222",
            gst_number="27APEX0000A1Z1",
            business_info="Electronics"
        )
        self.category = Category.objects.create(company=self.company, name="Gadgets")
        self.supplier = Supplier.objects.create(company=self.company, name="Tech Distributor", phone="9800009999")

        self.prod1 = Product.objects.create(
            company=self.company,
            category=self.category,
            supplier=self.supplier,
            name="Wireless Mouse",
            product_code="MOU-001",
            purchase_price=Decimal("200.00"),
            selling_price=Decimal("500.00"),
            gst_percentage=Decimal("18.00"),
            current_stock=Decimal("50.00"),
            minimum_stock=Decimal("10.00"),
            unit="pcs"
        )
        self.prod2 = Product.objects.create(
            company=self.company,
            category=self.category,
            supplier=self.supplier,
            name="Mechanical Keyboard",
            product_code="KEY-001",
            purchase_price=Decimal("1000.00"),
            selling_price=Decimal("2500.00"),
            gst_percentage=Decimal("18.00"),
            current_stock=Decimal("2.00"),  # Low stock
            minimum_stock=Decimal("5.00"),
            unit="pcs"
        )
        self.prod3 = Product.objects.create(
            company=self.company,
            category=self.category,
            supplier=self.supplier,
            name="USB-C Hub",
            product_code="HUB-001",
            purchase_price=Decimal("400.00"),
            selling_price=Decimal("900.00"),
            gst_percentage=Decimal("18.00"),
            current_stock=Decimal("0.00"),  # Out of stock
            minimum_stock=Decimal("5.00"),
            unit="pcs"
        )

        self.cashier_user = User.objects.create_user(username="cashier_ai", password="password123")
        self.cashier_emp = user_registration.objects.create(
            user=self.cashier_user,
            company=self.company,
            employee_id="CSH_AI01",
            Role="Cashier",
            Phone="9811113333",
            joining_date="2026-01-01",
            payment="Paid",
            status="active"
        )

        self.mgr_user = User.objects.create_user(username="mgr_ai", password="password123")
        self.mgr_emp = user_registration.objects.create(
            user=self.mgr_user,
            company=self.company,
            employee_id="MGR_AI01",
            Role="Manager",
            Phone="9811114444",
            joining_date="2026-01-01",
            payment="Paid",
            status="active"
        )

        self.api_client = APIClient()

    def test_parse_date_or_range_natural_formats(self):
        today = timezone.now().date()
        
        # Today / Yesterday
        res_today = parse_date_or_range("sales today")
        self.assertIsNotNone(res_today)
        self.assertEqual(res_today[0], "single")
        self.assertEqual(res_today[1], today)

        res_yesterday = parse_date_or_range("sales yesterday")
        self.assertIsNotNone(res_yesterday)
        self.assertEqual(res_yesterday[0], "single")
        self.assertEqual(res_yesterday[1], today - timedelta(days=1))

        # Relative range: last 7 days
        res_7d = parse_date_or_range("show last 7 days sales")
        self.assertIsNotNone(res_7d)
        self.assertEqual(res_7d[0], "range")
        self.assertEqual(res_7d[1], today - timedelta(days=7))
        self.assertEqual(res_7d[2], today)

        # Named month: 20 September 2026
        res_named = parse_date_or_range("Sales for 20 September 2026")
        self.assertIsNotNone(res_named)
        self.assertEqual(res_named[0], "single")
        self.assertEqual(res_named[1], date(2026, 9, 20))

        # ISO format: 2026-09-20
        res_iso = parse_date_or_range("sales on 2026-09-20")
        self.assertIsNotNone(res_iso)
        self.assertEqual(res_iso[0], "single")
        self.assertEqual(res_iso[1], date(2026, 9, 20))

        # ISO Range: 2026-09-01 to 2026-09-20
        res_iso_range = parse_date_or_range("sales from 2026-09-01 to 2026-09-20")
        self.assertIsNotNone(res_iso_range)
        self.assertEqual(res_iso_range[0], "range")
        self.assertEqual(res_iso_range[1], date(2026, 9, 1))
        self.assertEqual(res_iso_range[2], date(2026, 9, 20))

    def test_answer_business_question_empty_and_greetings(self):
        # Empty
        res_empty = answer_business_question(self.company, "")
        self.assertEqual(res_empty["intent"], "empty_query")
        self.assertIn("Please ask a question", res_empty["answer"])

        # Greeting
        res_hi = answer_business_question(self.company, "hi")
        self.assertEqual(res_hi["intent"], "greeting")
        self.assertIn("Apex Retail Hub", res_hi["answer"])
        self.assertIn("Today's sales", res_hi["answer"])

    def test_answer_business_question_today_sales(self):
        today = timezone.now().date()
        # Create a sale today
        sale = Sale.objects.create(
            company=self.company,
            sale_number="SALE-AI-TODAY",
            cashier=self.owner,
            subtotal=Decimal("1000.00"),
            tax_amount=Decimal("180.00"),
            grand_total=Decimal("1180.00"),
            paid_amount=Decimal("1180.00"),
            balance_due=Decimal("0.00"),
            payment_status="PAID",
            sale_status="COMPLETED"
        )
        Payment.objects.create(
            company=self.company,
            sale=sale,
            payment_method="UPI",
            amount=Decimal("1180.00"),
            received_by=self.owner
        )
        SaleItem.objects.create(
            sale=sale,
            product=self.prod1,
            product_name=self.prod1.name,
            product_code=self.prod1.product_code,
            unit_price=Decimal("500.00"),
            quantity=Decimal("2.00"),
            subtotal=Decimal("1000.00"),
            tax_amount=Decimal("180.00"),
            total=Decimal("1180.00")
        )

        res = answer_business_question(self.company, "Today's sales")
        self.assertEqual(res["intent"], "sales_today")
        self.assertEqual(res["data"]["count"], 1)
        self.assertEqual(res["data"]["revenue"], 1180.00)
        self.assertEqual(res["data"]["upi"], 1180.00)
        self.assertEqual(res["data"]["cash"], 0.00)
        self.assertIn("₹1,180.00", res["answer"])

    def test_answer_business_question_specific_date(self):
        # Create a sale on 2026-09-20
        past_dt = timezone.now().replace(year=2026, month=9, day=20, hour=14, minute=30)
        sale = Sale.objects.create(
            company=self.company,
            sale_number="SALE-AI-PAST-1",
            cashier=self.owner,
            subtotal=Decimal("500.00"),
            tax_amount=Decimal("90.00"),
            grand_total=Decimal("590.00"),
            paid_amount=Decimal("590.00"),
            balance_due=Decimal("0.00"),
            payment_status="PAID",
            sale_status="COMPLETED"
        )
        Sale.objects.filter(id=sale.id).update(created_at=past_dt)
        p = Payment.objects.create(
            company=self.company,
            sale=sale,
            payment_method="CASH",
            amount=Decimal("590.00"),
            received_by=self.owner
        )
        Payment.objects.filter(id=p.id).update(payment_date=past_dt)

        res = answer_business_question(self.company, "Sales for 20 September 2026")
        self.assertEqual(res["intent"], "sales_date")
        self.assertEqual(res["data"]["count"], 1)
        self.assertEqual(res["data"]["revenue"], 590.00)
        self.assertEqual(res["data"]["cash"], 590.00)
        self.assertIn("20 September 2026", res["answer"])
        self.assertIn("₹590.00", res["answer"])

        # Date with zero sales
        res_zero = answer_business_question(self.company, "Sales for 15 August 2026")
        self.assertEqual(res_zero["intent"], "sales_date")
        self.assertEqual(res_zero["data"]["count"], 0)
        self.assertIn("No completed transactions were recorded", res_zero["answer"])

    def test_answer_business_question_payment_breakdowns(self):
        # Add a UPI payment and a Cash payment today
        sale1 = Sale.objects.create(
            company=self.company,
            sale_number="SALE-PAY-1",
            cashier=self.owner,
            subtotal=Decimal("100.00"),
            tax_amount=Decimal("18.00"),
            grand_total=Decimal("118.00"),
            paid_amount=Decimal("118.00"),
            sale_status="COMPLETED"
        )
        Payment.objects.create(
            company=self.company,
            sale=sale1,
            payment_method="UPI",
            amount=Decimal("118.00"),
            received_by=self.owner
        )

        sale2 = Sale.objects.create(
            company=self.company,
            sale_number="SALE-PAY-2",
            cashier=self.owner,
            subtotal=Decimal("200.00"),
            tax_amount=Decimal("36.00"),
            grand_total=Decimal("236.00"),
            paid_amount=Decimal("236.00"),
            sale_status="COMPLETED"
        )
        Payment.objects.create(
            company=self.company,
            sale=sale2,
            payment_method="CASH",
            amount=Decimal("236.00"),
            received_by=self.owner
        )

        # 1. UPI query
        res_upi = answer_business_question(self.company, "Show UPI collections")
        self.assertEqual(res_upi["intent"], "payment_upi")
        self.assertEqual(res_upi["data"]["upi_today"], 118.00)

        # 2. Cash query
        res_cash = answer_business_question(self.company, "Cash collections")
        self.assertEqual(res_cash["intent"], "payment_cash")
        self.assertEqual(res_cash["data"]["cash_today"], 236.00)

        # 3. General payment breakdown
        res_all = answer_business_question(self.company, "Payment breakdown")
        self.assertEqual(res_all["intent"], "payment_breakdown")
        self.assertEqual(res_all["data"]["today"]["upi"], 118.00)
        self.assertEqual(res_all["data"]["today"]["cash"], 236.00)

    def test_answer_business_question_low_stock(self):
        res = answer_business_question(self.company, "Low stock")
        self.assertEqual(res["intent"], "low_stock")
        self.assertTrue(len(res["data"]) >= 2)
        skus = [item["sku"] for item in res["data"]]
        self.assertIn("KEY-001", skus)
        self.assertIn("HUB-001", skus)
        self.assertIn("OUT OF STOCK", res["answer"])

    def test_answer_business_question_fallback(self):
        res = answer_business_question(self.company, "What is the capital of Australia?")
        self.assertEqual(res["intent"], "general_overview")
        self.assertIn("Store Overview for Apex Retail Hub", res["answer"])
        self.assertIn("Try asking:", res["answer"])

    def test_tenant_isolation_in_assistant(self):
        # Create Company B
        owner_b = User.objects.create_user(username="owner_b_ai", password="password123")
        company_b = companyRegistration.objects.create(
            owner=owner_b,
            company_name="Beta Superstore",
            address_line="500 Beta Road",
            city="Delhi",
            state="Delhi",
            country="India",
            pincode="110001",
            company_email="beta@example.com",
            phone="9822223333",
            gst_number="07BETA0000A1Z1",
            business_info="Grocery"
        )
        # Create sales and products for Company B only
        sale_b = Sale.objects.create(
            company=company_b,
            sale_number="SALE-B-001",
            cashier=owner_b,
            subtotal=Decimal("50000.00"),
            tax_amount=Decimal("9000.00"),
            grand_total=Decimal("59000.00"),
            paid_amount=Decimal("59000.00"),
            sale_status="COMPLETED"
        )
        Payment.objects.create(
            company=company_b,
            sale=sale_b,
            payment_method="UPI",
            amount=Decimal("59000.00"),
            received_by=owner_b
        )

        # Query assistant for Company A
        res_a = answer_business_question(self.company, "Today's sales")
        self.assertEqual(res_a["data"]["revenue"], 0.00)
        self.assertEqual(res_a["data"]["count"], 0)

        # Query assistant for Company B
        res_b = answer_business_question(company_b, "Today's sales")
        self.assertEqual(res_b["data"]["revenue"], 59000.00)
        self.assertEqual(res_b["data"]["count"], 1)

    def test_ai_assistant_ask_api_view_endpoints(self):
        # 1. Authenticated
        self.api_client.force_authenticate(user=self.owner)
        res = self.api_client.post(
            "/api/ai/ask/",
            data={"question": "Today's sales"},
            format="json"
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["status"], "success")
        self.assertEqual(res.data["intent"], "sales_today")
        self.assertIn("answer", res.data)

        # 2. Empty question -> 400
        res_empty = self.api_client.post(
            "/api/ai/ask/",
            data={"question": ""},
            format="json"
        )
        self.assertEqual(res_empty.status_code, status.HTTP_400_BAD_REQUEST)

        # 3. Unauthenticated -> 401/403
        self.api_client.logout()
        res_unauth = self.api_client.post(
            "/api/ai/ask/",
            data={"question": "Today's sales"},
            format="json"
        )
        self.assertIn(res_unauth.status_code, [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN])

    def test_conversational_intents(self):
        # 1. Identity & Name
        res_name = answer_business_question(self.company, "what is ur name")
        self.assertEqual(res_name["intent"], "identity")
        self.assertIn("Retail Business Assistant", res_name["answer"])
        self.assertIn(self.company.company_name, res_name["answer"])

        # 2. Thanks
        res_thanks = answer_business_question(self.company, "thank you so much")
        self.assertEqual(res_thanks["intent"], "thanks")
        self.assertIn("welcome", res_thanks["answer"].lower())

        # 3. Capabilities / Help
        res_help = answer_business_question(self.company, "what can you do")
        self.assertEqual(res_help["intent"], "capabilities")
        self.assertIn("Capabilities for", res_help["answer"])

        # 4. Today's Returns
        res_ret = answer_business_question(self.company, "Tell me today's returns")
        self.assertEqual(res_ret["intent"], "returns_today")
        self.assertIn("Today's Returns", res_ret["answer"])

        # 5. Best-selling products
        res_top = answer_business_question(self.company, "Who is our best-selling product?")
        self.assertEqual(res_top["intent"], "top_products")

    def test_staff_communication_and_rbac(self):
        # 1. Owner dispatches notification to Cashier
        res_tell = answer_business_question(self.company, "Tell cashier to speak to me in the office.", user=self.owner)
        self.assertEqual(res_tell["intent"], "staff_communication")
        self.assertIn("Delivered notification", res_tell["answer"])

        # Verify record in database
        notif = InternalNotification.objects.filter(company=self.company, sender=self.owner).first()
        self.assertIsNotNone(notif)
        self.assertEqual(notif.recipient_role, "Cashier")
        self.assertIn("speak to me in the office", notif.message)
        self.assertEqual(notif.status, "DELIVERED")
        self.assertFalse(notif.is_read)

        # 2. Cashier attempting broadcast is blocked
        res_cashier_block = answer_business_question(self.company, "Notify all managers that the store meeting is at 5 PM", user=self.cashier_user)
        self.assertEqual(res_cashier_block["intent"], "staff_communication")
        self.assertFalse(res_cashier_block.get("data", {}).get("permitted", True))
        self.assertIn("cannot send broadcast announcements", res_cashier_block["answer"])

    def test_notifications_api_and_tenant_isolation(self):
        # 1. Dispatch notification to Cashier
        notif = InternalNotification.objects.create(
            company=self.company,
            sender=self.owner,
            sender_role="Admin",
            recipient=self.cashier_user,
            recipient_role="Cashier",
            title="Important Directive",
            message="Please verify drawer cash at shift change.",
            status="DELIVERED"
        )

        # 2. Cashier checks notifications
        self.api_client.force_authenticate(user=self.cashier_user)
        res_list = self.api_client.get("/api/notifications/")
        self.assertEqual(res_list.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(res_list.data["unread_count"], 1)
        found = any(n["id"] == notif.id for n in res_list.data["notifications"])
        self.assertTrue(found)

        # 3. Cashier marks notification read
        res_read = self.api_client.post(f"/api/notifications/{notif.id}/read/")
        self.assertEqual(res_read.status_code, status.HTTP_200_OK)
        notif.refresh_from_db()
        self.assertTrue(notif.is_read)
        self.assertEqual(notif.status, "READ")
        self.assertIsNotNone(notif.read_at)

        # 4. Cross-Tenant Isolation: User from Company B cannot read Company A notification
        owner_b = User.objects.create_user(username="owner_b_sec", password="password123")
        company_b = companyRegistration.objects.create(
            owner=owner_b,
            company_name="Isolated Store B",
            address_line="200 West",
            city="Delhi",
            state="Delhi",
            country="India",
            pincode="110001",
            company_email="b@example.com",
            phone="9811112222",
            alternate_phone="9811113333",
            gst_number="07BETA0000B1Z2",
            business_info="Retail"
        )
        self.api_client.force_authenticate(user=owner_b)
        res_cross = self.api_client.post(f"/api/notifications/{notif.id}/read/")
        self.assertEqual(res_cross.status_code, status.HTTP_404_NOT_FOUND)

        # 5. Mark All Read
        notif2 = InternalNotification.objects.create(
            company=self.company,
            sender=self.owner,
            sender_role="Admin",
            recipient=self.cashier_user,
            recipient_role="Cashier",
            message="Second notice",
            status="DELIVERED"
        )
        self.api_client.force_authenticate(user=self.cashier_user)
        res_mark_all = self.api_client.post("/api/notifications/mark-all-read/")
        self.assertEqual(res_mark_all.status_code, status.HTTP_200_OK)
        notif2.refresh_from_db()
        self.assertTrue(notif2.is_read)


