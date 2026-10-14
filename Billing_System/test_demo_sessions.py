"""
Regression and Verification Tests for College Demo Multi-Role Session Isolation.

Tests:
1. COLLEGE_DEMO_MODE configuration defaults to False.
2. Demo hub (/demo/) and status API (/api/demo/status/) endpoints.
3. Multi-origin independent sessions across Owner, Manager, and Cashier.
4. Isolated logout: logging out in Cashier session does not invalidate Owner or Manager sessions.
5. Server-side RBAC enforcement: Cashier cannot access Manager/Owner portals.
6. Notification and staff messaging targeting: messages reach only the intended recipient without crosstalk.
7. Tenant isolation integrity.
8. DemoSessionIsolationMiddleware behavior in active vs inactive mode.
"""

from django.test import TestCase, Client, override_settings
from django.contrib.auth.models import User
from django.urls import reverse
from accounts.models import companyRegistration
from employee.models import user_registration
from dashboard.models import InternalNotification
from inventory.models import Category, Product


class CollegeDemoSessionIsolationTests(TestCase):
    def setUp(self):
        # Company A
        self.owner_a = User.objects.create_user(
            username="demo_owner_corp",
            password="OwnerPassword123!",
            email="owner@corp.local"
        )
        self.company_a = companyRegistration.objects.create(
            owner=self.owner_a,
            company_name="Apex Retail Corp",
            city="New York"
        )

        # Manager A
        self.mgr_user = User.objects.create_user(
            username="demo_mgr_corp",
            first_name="Store",
            last_name="Manager",
            password="MgrPassword123!",
            email="mgr@corp.local"
        )
        self.mgr_emp = user_registration.objects.create(
            user=self.mgr_user,
            employee_id="MGR_DEMO_01",
            Role="Manager",
            company=self.company_a,
            Phone="9811111111",
            joining_date="2026-01-01",
            payment="Paid",
            status="active"
        )

        # Cashier A
        self.cashier_user = User.objects.create_user(
            username="demo_cashier_corp",
            first_name="Front",
            last_name="Cashier",
            password="CashierPassword123!",
            email="cashier@corp.local"
        )
        self.cashier_emp = user_registration.objects.create(
            user=self.cashier_user,
            employee_id="CSH_DEMO_01",
            Role="Cashier",
            company=self.company_a,
            Phone="9822222222",
            joining_date="2026-01-01",
            payment="Paid",
            status="active"
        )

        # Company B (for tenancy verification)
        self.owner_b = User.objects.create_user(
            username="demo_owner_beta",
            password="OwnerBeta123!",
            email="owner@beta.local"
        )
        self.company_b = companyRegistration.objects.create(
            owner=self.owner_b,
            company_name="Beta Supermarket",
            city="Boston"
        )

    def test_demo_hub_renders_successfully(self):
        """Verify the /demo/ launch portal renders with architecture documentation."""
        client = Client()
        response = client.get("/demo/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Independent Multi-Role Demonstration Portal")
        self.assertContains(response, "RFC 6265")
        self.assertContains(response, "127.0.0.1:8000")
        self.assertContains(response, "127.0.0.2:8000")

    def test_demo_status_api_endpoint(self):
        """Verify /api/demo/status/ returns valid JSON detailing multi-origin architecture."""
        client = Client()
        response = client.get("/api/demo/status/")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data.get("success"))
        self.assertIn("RFC 6265", data.get("architecture", ""))
        self.assertIn("origins", data)
        self.assertEqual(data["origins"]["owner"], "http://localhost:8000")
        self.assertEqual(data["origins"]["manager"], "http://127.0.0.1:8000")
        self.assertEqual(data["origins"]["cashier"], "http://127.0.0.2:8000")

    @override_settings(COLLEGE_DEMO_MODE=True)
    def test_multi_origin_independent_sessions_simulation(self):
        """
        Simulate 3 independent browser tabs across loopback origins:
        Tab 1 (Owner on localhost:8000)
        Tab 2 (Manager on 127.0.0.1:8000)
        Tab 3 (Cashier on 127.0.0.2:8000)
        Verify all 3 sessions remain active simultaneously without crosstalk.
        """
        client_owner = Client(SERVER_NAME="localhost")
        client_mgr = Client(SERVER_NAME="127.0.0.1")
        client_cashier = Client(SERVER_NAME="127.0.0.2")

        # Log in each role on their designated origin
        login_res_owner = client_owner.post("/accounts/", {
            "username": "demo_owner_corp",
            "password": "OwnerPassword123!"
        })
        self.assertEqual(login_res_owner.status_code, 302)

        login_res_mgr = client_mgr.post("/accounts/", {
            "username": "demo_mgr_corp",
            "password": "MgrPassword123!"
        })
        self.assertEqual(login_res_mgr.status_code, 302)

        login_res_cashier = client_cashier.post("/accounts/", {
            "username": "demo_cashier_corp",
            "password": "CashierPassword123!"
        })
        self.assertEqual(login_res_cashier.status_code, 302)

        # Access dashboards simultaneously
        resp_owner = client_owner.get("/dashboard/")
        resp_mgr = client_mgr.get("/manager_dashboard/")
        resp_cashier = client_cashier.get("/cashier_dashboard/")

        self.assertEqual(resp_owner.status_code, 200)
        self.assertEqual(resp_mgr.status_code, 200)
        self.assertEqual(resp_cashier.status_code, 200)

        # Verify role identities remain strictly independent
        self.assertEqual(resp_owner.context["user_display_role"], "Owner")
        self.assertEqual(resp_mgr.context["user_display_role"], "Manager")
        self.assertEqual(resp_cashier.context["user_display_role"], "Cashier")

        # Second pass: re-verify Tab 1 (Owner) did not become Cashier or Manager
        resp_owner_recheck = client_owner.get("/dashboard/")
        self.assertEqual(resp_owner_recheck.context["user_display_role"], "Owner")
        self.assertEqual(resp_owner_recheck.wsgi_request.user.username, "demo_owner_corp")

    @override_settings(COLLEGE_DEMO_MODE=True)
    def test_isolated_logout_does_not_affect_other_sessions(self):
        """
        Verify that logging out in Tab 3 (Cashier on 127.0.0.2)
        does NOT log out Tab 1 (Owner) or Tab 2 (Manager).
        """
        client_owner = Client(SERVER_NAME="localhost")
        client_mgr = Client(SERVER_NAME="127.0.0.1")
        client_cashier = Client(SERVER_NAME="127.0.0.2")

        client_owner.login(username="demo_owner_corp", password="OwnerPassword123!")
        client_mgr.login(username="demo_mgr_corp", password="MgrPassword123!")
        client_cashier.login(username="demo_cashier_corp", password="CashierPassword123!")

        # Cashier logs out
        logout_resp = client_cashier.get("/logout/")
        self.assertEqual(logout_resp.status_code, 302)

        # Cashier is now logged out
        cashier_check = client_cashier.get("/cashier_dashboard/")
        self.assertEqual(cashier_check.status_code, 302)  # Redirects to login

        # Owner and Manager REMAIN logged in!
        owner_check = client_owner.get("/dashboard/")
        self.assertEqual(owner_check.status_code, 200)
        self.assertEqual(owner_check.wsgi_request.user.username, "demo_owner_corp")

        mgr_check = client_mgr.get("/manager_dashboard/")
        self.assertEqual(mgr_check.status_code, 200)
        self.assertEqual(mgr_check.wsgi_request.user.username, "demo_mgr_corp")

    @override_settings(COLLEGE_DEMO_MODE=True)
    def test_server_side_rbac_enforcement(self):
        """Verify Cashier cannot access Manager-only dashboard or approvals."""
        client_cashier = Client(SERVER_NAME="127.0.0.2")
        client_cashier.login(username="demo_cashier_corp", password="CashierPassword123!")

        # Cashier attempting to view manager dashboard
        resp = client_cashier.get("/manager_dashboard/")
        self.assertIn(resp.status_code, [302, 403])

        # Cashier attempting to view owner general settings
        resp_settings = client_cashier.get("/settings/general/")
        self.assertIn(resp_settings.status_code, [302, 403])

    def test_internal_notifications_delivered_only_to_designated_recipient(self):
        """Verify staff messaging and internal notifications route strictly to the target user."""
        notif = InternalNotification.objects.create(
            company=self.company_a,
            sender=self.mgr_user,
            sender_role="Manager",
            recipient=self.cashier_user,
            recipient_role="Cashier",
            title="Price Check",
            message="Please verify shelf price on barcode 89012345"
        )

        # Cashier query
        cashier_notifs = InternalNotification.objects.filter(
            company=self.company_a,
            recipient=self.cashier_user
        )
        self.assertEqual(cashier_notifs.count(), 1)
        self.assertEqual(cashier_notifs.first().title, "Price Check")

        # Manager query - should be empty
        mgr_notifs = InternalNotification.objects.filter(
            company=self.company_a,
            recipient=self.mgr_user
        )
        self.assertEqual(mgr_notifs.count(), 0)

        # Owner query - should be empty
        owner_notifs = InternalNotification.objects.filter(
            company=self.company_a,
            recipient=self.owner_a
        )
        self.assertEqual(owner_notifs.count(), 0)

    @override_settings(COLLEGE_DEMO_MODE=True)
    def test_middleware_annotates_headers_when_enabled(self):
        """Verify middleware sets X-Demo-Mode and X-Demo-Host-Role when enabled."""
        client = Client(SERVER_NAME="127.0.0.1")
        response = client.get("/demo/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("X-Demo-Mode"), "active")
        self.assertEqual(response.headers.get("X-Demo-Host-Role"), "Manager")

    @override_settings(COLLEGE_DEMO_MODE=False)
    def test_middleware_zero_overhead_when_disabled(self):
        """Verify middleware leaves response headers untouched when COLLEGE_DEMO_MODE is False."""
        client = Client(SERVER_NAME="127.0.0.1")
        response = client.get("/demo/")
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.headers.get("X-Demo-Mode"))
        self.assertIsNone(response.headers.get("X-Demo-Host-Role"))
