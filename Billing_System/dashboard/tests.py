from django.test import TestCase, Client
from django.contrib.auth.models import User
from accounts.models import companyRegistration
from employee.models import user_registration


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
from datetime import timedelta, date
from django.utils import timezone
from django.core.management import call_command
from django.conf import settings
from employee.models import SalaryPayment
from sales.models import POSScannerSession, POSScannerScan, Sale, SaleItem, Payment
from inventory.models import Product, Category
from dashboard.models import TelegramOwnerLink, TelegramOutboundMessage
from dashboard.telegram_service import (
    is_bot_token_configured,
    get_telegram_gateway_status,
    test_telegram_connection,
    dispatch_outbound_messages,
    _telegram_verification_state,
)


class Phase11ComprehensiveTests(TestCase):
    def setUp(self):
        self.owner_user = User.objects.create_user(
            username="phase11_owner",
            password="OwnerPassword123!",
            email="owner@phase11.test"
        )
        self.company = companyRegistration.objects.create(
            owner=self.owner_user,
            company_name="Phase11 Test Retail",
            address_line="123 High Street",
            city="Bengaluru",
            state="Karnataka",
            country="India",
            pincode="560001",
            company_email="owner@phase11.test",
            phone="9876543210",
            alternate_phone="9876543211",
            gst_number="29AAAAA0000A1Z5",
            business_info="Retail Store"
        )
        self.client = Client()

    def test_01_telegram_unconfigured_honest_status(self):
        """Requirement 1: Telegram must never show fake Connected status when token is absent/blank."""
        old_token = getattr(settings, "TELEGRAM_BOT_TOKEN", "")
        try:
            settings.TELEGRAM_BOT_TOKEN = ""
            _telegram_verification_state["status"] = None
            _telegram_verification_state["bot_username"] = None

            self.assertFalse(is_bot_token_configured())
            status_info = get_telegram_gateway_status(company=self.company)
            self.assertEqual(status_info["status"], "Not configured")
            self.assertEqual(status_info["badge_class"], "neutral")
            self.assertFalse(status_info["is_verified"])
            self.assertFalse(status_info["is_configured"])

            # Test connection action when token is blank
            result = test_telegram_connection()
            self.assertFalse(result["success"])
            self.assertEqual(result["status"], "Not configured")
            self.assertIn("not configured", result["message"].lower())

            # Verify outbound dispatcher does NOT mark messages SENT when token is unconfigured
            msg = TelegramOutboundMessage.objects.create(
                company=self.company,
                chat_id="12345678",
                message_text="Test Message",
                status="QUEUED"
            )
            dispatched = dispatch_outbound_messages()
            self.assertEqual(dispatched, 0)
            msg.refresh_from_db()
            self.assertEqual(msg.status, "QUEUED")
        finally:
            settings.TELEGRAM_BOT_TOKEN = old_token

    def test_02_telegram_six_states_distinction(self):
        """Requirement 1: Accurately distinguish the 6 distinct states."""
        old_token = getattr(settings, "TELEGRAM_BOT_TOKEN", "")
        try:
            # 1. Not configured
            settings.TELEGRAM_BOT_TOKEN = ""
            _telegram_verification_state["status"] = None
            s1 = get_telegram_gateway_status(self.company)
            self.assertEqual(s1["status"], "Not configured")

            # 2. Configured but not verified
            settings.TELEGRAM_BOT_TOKEN = "123456789:ABCdefGHIjklMNOpqrsTUVwxyz12345"
            _telegram_verification_state["status"] = None
            s2 = get_telegram_gateway_status(self.company)
            self.assertEqual(s2["status"], "Configured but not verified")
            self.assertEqual(s2["badge_class"], "warning")

            # 3. Connection verified
            _telegram_verification_state["status"] = "Connection verified"
            _telegram_verification_state["bot_username"] = "@TestBillingBot"
            s3 = get_telegram_gateway_status(self.company)
            self.assertEqual(s3["status"], "Connection verified")
            self.assertEqual(s3["badge_class"], "success")

            # 4. Connection failed
            _telegram_verification_state["status"] = "Connection failed"
            _telegram_verification_state["last_error"] = "HTTP 401: Unauthorized"
            s4 = get_telegram_gateway_status(self.company)
            self.assertEqual(s4["status"], "Connection failed")
            self.assertEqual(s4["badge_class"], "danger")

            # 5. Sync pending
            TelegramOutboundMessage.objects.create(
                company=self.company,
                chat_id="999",
                message_text="Pending message",
                status="QUEUED"
            )
            s5 = get_telegram_gateway_status(self.company)
            self.assertEqual(s5["status"], "Sync pending")
            self.assertEqual(s5["badge_class"], "warning")

            # 6. Sync failed
            TelegramOutboundMessage.objects.create(
                company=self.company,
                chat_id="999",
                message_text="Failed message",
                status="FAILED"
            )
            s6 = get_telegram_gateway_status(self.company)
            self.assertEqual(s6["status"], "Sync failed")
            self.assertEqual(s6["badge_class"], "danger")
        finally:
            settings.TELEGRAM_BOT_TOKEN = old_token
            _telegram_verification_state["status"] = None

    def test_03_scanner_session_expiration_and_evidence_badges(self):
        """Requirement 2: Scanner sessions expire safely, status badges reflect real evidence."""
        expired_sess = POSScannerSession.objects.create(
            company=self.company,
            user=self.owner_user,
            session_token="expired-token-session-qa",
            pairing_code="123456",
            status="ACTIVE",
            expires_at=timezone.now() - timedelta(minutes=5)
        )
        self.client.login(username="phase11_owner", password="OwnerPassword123!")
        resp = self.client.get("/settings/scanner/")
        self.assertEqual(resp.status_code, 200)

        expired_sess.refresh_from_db()
        self.assertEqual(expired_sess.status, "EXPIRED")

        content = resp.content.decode("utf-8")
        self.assertIn("HID SCANNER SUPPORTED (Driverless)", content)
        self.assertIn("Pairing Available (Awaiting Phone)", content)
        self.assertNotIn("HID Ready", content)

    def test_04_seed_demo_data_dry_run_and_password_preservation(self):
        """Requirement 3: seed_demo_data --dry-run writes 0 records, never overwrites passwords."""
        u_count_before = User.objects.count()
        p_count_before = Product.objects.count()

        call_command("seed_demo_data", dry_run=True)

        self.assertEqual(User.objects.count(), u_count_before)
        self.assertEqual(Product.objects.count(), p_count_before)

        existing_pass = "ExistingSecretPassword!"
        safe_user = User.objects.create_user(
            username="retail_owner_safety",
            password=existing_pass,
            email="retail_owner_safety@example.com"
        )
        call_command("seed_demo_data")
        safe_user.refresh_from_db()
        self.assertTrue(safe_user.check_password(existing_pass), "Existing user password must NEVER be modified by seed_demo_data!")

        demo_payments = SalaryPayment.objects.filter(payment_reference__startswith="DEMO-SEED-")
        self.assertTrue(demo_payments.exists(), "Seeded salary payments must be clearly identified with DEMO-SEED- reference")
        for dp in demo_payments:
            self.assertIn("[DEMO SEED]", dp.notes)

    def test_05_payroll_reconciliation_and_date_range(self):
        """Requirement 4: April 2025 - April 2026 payroll filters and totals reconciliation."""
        emp_user = User.objects.create_user(
            username="ramesh_cashier",
            password="CashierPass123!",
            email="ramesh@retail.com"
        )
        emp = user_registration.objects.create(
            user=emp_user,
            company=self.company,
            employee_id="EMP-991",
            Role="Cashier",
            Phone="9876543210",
            Alternate_phone="9876543211",
            address="12 Cashier Lane",
            joining_date=date(2025, 1, 1),
            monthaly_salary=Decimal("25000.00"),
            payment="Paid",
            status="active"
        )

        SalaryPayment.objects.create(
            company=self.company,
            employee=emp,
            year=2025,
            month=5,
            basic_salary=Decimal("25000.00"),
            gross_salary=Decimal("25000.00"),
            net_payable=Decimal("25000.00"),
            paid_amount=Decimal("25000.00"),
            payment_status="Paid",
            payment_method="Bank Transfer",
            payment_reference="TEST-SAL-MAY25",
            payment_date=date(2025, 5, 31),
            notes="May 2025 salary"
        )
        SalaryPayment.objects.create(
            company=self.company,
            employee=emp,
            year=2025,
            month=6,
            basic_salary=Decimal("25000.00"),
            gross_salary=Decimal("25000.00"),
            net_payable=Decimal("25000.00"),
            paid_amount=Decimal("15000.00"),
            payment_status="Partial",
            payment_method="Cash",
            payment_reference="TEST-SAL-JUN25",
            payment_date=date(2025, 6, 30),
            notes="June 2025 partial"
        )
        SalaryPayment.objects.create(
            company=self.company,
            employee=emp,
            year=2026,
            month=1,
            basic_salary=Decimal("25000.00"),
            gross_salary=Decimal("25000.00"),
            net_payable=Decimal("25000.00"),
            paid_amount=Decimal("25000.00"),
            payment_status="Paid",
            payment_method="UPI",
            payment_reference="TEST-SAL-JAN26",
            payment_date=date(2026, 1, 31),
            notes="Jan 2026 salary"
        )

        self.client.login(username="phase11_owner", password="OwnerPassword123!")

        resp_may = self.client.get("/payroll/?year=2025&month=5")
        self.assertEqual(resp_may.status_code, 200)
        self.assertEqual(resp_may.context["selected_year"], 2025)
        self.assertEqual(resp_may.context["selected_month"], 5)
        self.assertEqual(resp_may.context["total_payable"], Decimal("25000.00"))
        self.assertEqual(resp_may.context["total_paid"], Decimal("25000.00"))
        self.assertEqual(resp_may.context["total_pending"], Decimal("0.00"))

        resp_jun = self.client.get("/payroll/?year=2025&month=6")
        self.assertEqual(resp_jun.status_code, 200)
        self.assertEqual(resp_jun.context["total_payable"], Decimal("25000.00"))
        self.assertEqual(resp_jun.context["total_paid"], Decimal("15000.00"))
        self.assertEqual(resp_jun.context["total_pending"], Decimal("10000.00"))

        resp_jan = self.client.get("/payroll/?year=2026&month=1")
        self.assertEqual(resp_jan.status_code, 200)
        self.assertEqual(resp_jan.context["total_payable"], Decimal("25000.00"))
        self.assertEqual(resp_jan.context["total_paid"], Decimal("25000.00"))
        self.assertEqual(resp_jan.context["total_pending"], Decimal("0.00"))

        payslip_url = f"/payslip/{emp.employee_id}/?year=2025&month=5"
        resp_payslip = self.client.get(payslip_url)
        self.assertEqual(resp_payslip.status_code, 200)

        other_user = User.objects.create_user(username="rival_owner", password="RivalPassword123!")
        other_company = companyRegistration.objects.create(
            owner=other_user,
            company_name="Rival Store",
            address_line="456 Rival Rd",
            city="Bengaluru",
            state="Karnataka",
            country="India",
            pincode="560002",
            company_email="rival@test.com",
            phone="9800000001",
            alternate_phone="9800000002",
            gst_number="29BBBBB0000B1Z2",
            business_info="Competitor"
        )
        self.client.login(username="rival_owner", password="RivalPassword123!")
        resp_rival_payslip = self.client.get(payslip_url)
        self.assertEqual(resp_rival_payslip.status_code, 404)

    def test_06_registration_privilege_boundary(self):
        """Requirement 4: Public registration cannot grant unauthorized Owner/Admin to other companies or elevate to staff/superuser."""
        resp = self.client.post("/registration/", {
            "admin_first_name": "New",
            "admin_last_name": "Merchant",
            "username": "new_public_registrant",
            "password": "SecurePassword987!",
            "confirm_pass": "SecurePassword987!",
            "email": "merchant@newbiz.com",
            "company_name": "New Fresh Grocers",
            "address_line": "12 Market St",
            "city": "Bengaluru",
            "state": "Karnataka",
            "country": "India",
            "pincode": "560001",
            "company_email": "info@newgrocers.com",
            "phone": "9812345678",
            "alternate_phone": "9812345679",
            "business_info": "Grocery Store"
        })
        self.assertEqual(resp.status_code, 302)
        new_u = User.objects.get(username="new_public_registrant")
        self.assertFalse(new_u.is_staff)
        self.assertFalse(new_u.is_superuser)

        owned = companyRegistration.objects.filter(owner=new_u)
        self.assertEqual(owned.count(), 1)
        self.assertEqual(owned.first().company_name, "New Fresh Grocers")
        self.assertNotEqual(owned.first().id, self.company.id)
