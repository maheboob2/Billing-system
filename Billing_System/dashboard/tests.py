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
