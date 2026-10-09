"""
Comprehensive Test Suite for POS Polish & Scanner/Telegram Fixes V3
Covers Tasks 1 through 9 requirements:
- POS barcode input collapsed by default in HTML template
- Product-name search absent from POS screen
- Catalog search available on authorized catalog pages (/product/, /api/products/search/)
- Exact barcode lookup API validation (exact match, inactive rejected, stock limits)
- Role label accuracy (Owner vs Manager vs Cashier vs Worker)
- Tenancy context processor and store name dynamic display with fallback
- Telegram bot security, unknown user rejection, tenant isolation
- Telegram Hinglish/NLP date parsing ("aaj", "kal", ISO dates, invalid dates)
- Telegram text report and chart generation rules (no chart on empty dataset)
- Phone scanner pairing session lifecycle, token expiration, LAN URL generation
"""

from decimal import Decimal
from datetime import timedelta, date
from django.test import TestCase, Client
from django.contrib.auth.models import User
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework import status

from accounts.models import companyRegistration
from accounts.context_processors import tenancy_context
from employee.models import user_registration
from inventory.models import Category, Supplier, Product
from sales.models import Sale, SaleItem, Payment, POSScannerSession, POSScannerScan, Customer
from dashboard.models import TelegramOwnerLink, TelegramOutboundMessage
from dashboard.telegram_service import (
    generate_pairing_code,
    verify_pairing_code,
    handle_telegram_command,
    NLPReportIntentParser,
    TelegramReportingEngine,
)
from sales.network_utils import get_pos_scanner_base_url, get_phone_scanner_full_url


class POSPolishV3TestSuite(TestCase):
    def setUp(self):
        self.api_client = APIClient()
        self.web_client = Client()

        # Company 1 (Owner: user_owner)
        self.owner_user = User.objects.create_user(username="test_owner", email="owner@test.com", password="pass")
        self.company = companyRegistration.objects.create(
            owner=self.owner_user,
            company_name="V3 Mart",
            address_line="MG Road",
            city="Bengaluru",
            state="Karnataka",
            country="India",
            pincode="560001",
            company_email="store@v3mart.com",
            phone="9988776655",
            gst_number="29ABCDE1234F1Z5",
        )

        # Manager
        self.manager_user = User.objects.create_user(username="test_manager", email="mgr@test.com", password="pass")
        self.manager_emp = user_registration.objects.create(
            user=self.manager_user,
            company=self.company,
            employee_id="MGR_V3",
            Role="Manager",
            Phone="9988776656",
            joining_date="2026-01-01",
            payment="Paid",
            status="active",
        )

        # Cashier
        self.cashier_user = User.objects.create_user(username="test_cashier", email="csh@test.com", password="pass")
        self.cashier_emp = user_registration.objects.create(
            user=self.cashier_user,
            company=self.company,
            employee_id="CSH_V3",
            Role="Cashier",
            Phone="9988776657",
            joining_date="2026-01-01",
            payment="Paid",
            status="active",
        )

        # Superuser (Not business owner)
        self.superuser = User.objects.create_superuser(username="admin_super", email="sup@test.com", password="pass")
        self.superuser_emp = user_registration.objects.create(
            user=self.superuser,
            company=self.company,
            employee_id="ADMIN_01",
            Role="Admin",
            Phone="9988776658",
            joining_date="2026-01-01",
            payment="Paid",
            status="active",
        )

        # Products
        self.category = Category.objects.create(company=self.company, name="Beverages")
        self.supplier = Supplier.objects.create(company=self.company, name="Test Supplier", phone="9876543210")
        self.prod_active = Product.objects.create(
            company=self.company,
            category=self.category,
            supplier=self.supplier,
            name="Mango Juice 1L",
            product_code="MJ-100",
            barcode="8901234567890",
            purchase_price=Decimal("40.00"),
            selling_price=Decimal("60.00"),
            current_stock=10,
            status=True,
        )
        self.prod_inactive = Product.objects.create(
            company=self.company,
            category=self.category,
            supplier=self.supplier,
            name="Discontinued Cola",
            product_code="DC-999",
            barcode="8909999999999",
            purchase_price=Decimal("20.00"),
            selling_price=Decimal("35.00"),
            current_stock=10,
            status=False,
        )

    # ── TASK 1: POS SCREEN & BARCODE INPUT ─────────────────────────────────
    def test_pos_template_has_collapsed_barcode_and_no_product_search(self):
        """POS screen must have collapsed barcode toggle and no duplicate product-name search control."""
        self.web_client.force_login(self.cashier_user)
        res = self.web_client.get("/cart/")
        self.assertEqual(res.status_code, 200)
        content = res.content.decode("utf-8")

        # Dedicated scanner control elements exist
        self.assertIn('id="toggle-barcode-input"', content)
        self.assertIn('id="barcode-input-panel"', content)
        self.assertIn('id="barcode-scanner-input"', content)
        self.assertIn('hidden', content)  # Barcode panel initially collapsed
        self.assertIn('aria-expanded="false"', content)

        # Product-name search card is removed from POS screen
        self.assertNotIn('id="pos-search-card"', content)
        self.assertNotIn('Search Product by Name / SKU', content)

    def test_product_search_available_on_catalog_page_for_authorized_users(self):
        """Catalog and inventory search remain available outside the POS screen."""
        self.web_client.force_login(self.manager_user)
        res = self.web_client.get("/product/")
        self.assertEqual(res.status_code, 200)

        # API search endpoint remains available for authorized inventory lookup
        self.api_client.force_authenticate(user=self.manager_user)
        res_api = self.api_client.get("/api/products/?search=Mango")
        self.assertEqual(res_api.status_code, status.HTTP_200_OK)

    def test_exact_barcode_lookup_success(self):
        """Exact barcode lookup API finds product with active status and available stock."""
        self.api_client.force_authenticate(user=self.cashier_user)
        res = self.api_client.get(f"/api/products/lookup/?code={self.prod_active.barcode}")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        data = res.json()
        self.assertTrue(data.get("found"))
        self.assertEqual(data["product"]["barcode"], self.prod_active.barcode)
        self.assertEqual(float(data["product"]["current_stock"]), 10.0)

    def test_exact_barcode_lookup_inactive_product(self):
        """Inactive barcode is rejected and does not allow selling."""
        self.api_client.force_authenticate(user=self.cashier_user)
        res = self.api_client.get(f"/api/products/lookup/?code={self.prod_inactive.barcode}")
        # Inactive product returns 400 or has inactive flag
        self.assertIn(res.status_code, [status.HTTP_400_BAD_REQUEST, status.HTTP_200_OK])
        data = res.json()
        self.assertTrue(data.get("inactive") or "inactive" in str(data).lower())

    def test_exact_barcode_lookup_unknown_code(self):
        """Unknown barcode returns 404 or not found without fuzzy fallback."""
        self.api_client.force_authenticate(user=self.cashier_user)
        res = self.api_client.get("/api/products/lookup/?code=UNKNOWN999888")
        self.assertIn(res.status_code, [status.HTTP_404_NOT_FOUND, status.HTTP_200_OK])
        data = res.json()
        self.assertFalse(data.get("found", False))

    # ── TASK 4 & 5: ROLE LABELS & DYNAMIC STORE NAME ──────────────────────
    def test_role_labels_and_tenancy_context(self):
        """Owner account displays 'Owner', manager displays 'Manager', superuser displays business role."""
        # 1. Actual business owner
        class MockRequestOwner:
            user = self.owner_user
        ctx_owner = tenancy_context(MockRequestOwner())
        self.assertEqual(ctx_owner["user_display_role"], "Owner")
        self.assertTrue(ctx_owner["is_owner"])

        # 2. Manager
        class MockRequestManager:
            user = self.manager_user
        ctx_mgr = tenancy_context(MockRequestManager())
        self.assertEqual(ctx_mgr["user_display_role"], "Manager")
        self.assertFalse(ctx_mgr["is_owner"])

        # 3. Cashier
        class MockRequestCashier:
            user = self.cashier_user
        ctx_csh = tenancy_context(MockRequestCashier())
        self.assertEqual(ctx_csh["user_display_role"], "Cashier")
        self.assertFalse(ctx_csh["is_owner"])

        # 4. Superuser (not owner of company)
        class MockRequestSuperuser:
            user = self.superuser
        ctx_sup = tenancy_context(MockRequestSuperuser())
        self.assertEqual(ctx_sup["user_display_role"], "Manager")
        self.assertFalse(ctx_sup["is_owner"])

    def test_dynamic_store_name_in_templates(self):
        """Store name is dynamically rendered from tenancy without hardcoded placeholders."""
        self.web_client.force_login(self.owner_user)
        res = self.web_client.get("/dashboard/")
        self.assertEqual(res.status_code, 200)
        content = res.content.decode("utf-8")
        self.assertIn("V3 Mart", content)
        self.assertNotIn("hello", content.lower().split(" "))

    # ── TASK 6: OVERVIEW VS REPORTS SEPARATION ────────────────────────────
    def test_overview_vs_reports_routes(self):
        """Overview and Reports provide distinct operational vs deep analytical views."""
        self.web_client.force_login(self.manager_user)

        res_ov = self.web_client.get("/dashboard/")
        self.assertEqual(res_ov.status_code, 200)
        content_ov = res_ov.content.decode("utf-8")
        self.assertIn("Today's Sales", content_ov)
        self.assertIn("Stock Status", content_ov)

        res_rep = self.web_client.get("/reports/")
        self.assertEqual(res_rep.status_code, 200)
        content_rep = res_rep.content.decode("utf-8")
        self.assertIn("filter-bar", content_rep)
        self.assertIn('input type="date"', content_rep)

    # ── TASK 7: TELEGRAM SETTINGS LOCATION ────────────────────────────────
    def test_telegram_settings_access(self):
        """Telegram reporting is accessible under Settings for owner, forbidden for cashier."""
        self.web_client.force_login(self.owner_user)
        res_owner = self.web_client.get("/settings/telegram/")
        self.assertEqual(res_owner.status_code, 200)
        content = res_owner.content.decode("utf-8")
        self.assertIn("Telegram Owner Reporting", content)
        self.assertIn("Cloud Sync Worker", content)

        # Cashier cannot access Telegram settings
        self.web_client.force_login(self.cashier_user)
        res_cashier = self.web_client.get("/settings/telegram/")
        self.assertIn(res_cashier.status_code, [302, 403])

    # ── TASK 8: TELEGRAM NLP, DATE PARSING, REPORTS & CHARTS ──────────────
    def test_telegram_nlp_date_parsing(self):
        """Parses 'aaj', 'kal', ISO dates, and catches invalid dates."""
        today = timezone.now().date()
        yesterday = today - timedelta(days=1)

        p_aaj = NLPReportIntentParser.parse("Aaj ki report do")
        self.assertEqual(p_aaj["start_date"], today)
        self.assertEqual(p_aaj["intent"], "REPORT")

        p_kal = NLPReportIntentParser.parse("Kal ki sales report do")
        self.assertEqual(p_kal["start_date"], yesterday)
        self.assertEqual(p_kal["intent"], "SALES")

        p_iso = NLPReportIntentParser.parse("2026-10-05 ki report do")
        self.assertEqual(p_iso["start_date"], date(2026, 10, 5))
        self.assertEqual(p_iso["intent"], "REPORT")

        p_pay = NLPReportIntentParser.parse("Aaj ki payment breakdown bhejo")
        self.assertEqual(p_pay["intent"], "PAYMENTS")

        p_stock = NLPReportIntentParser.parse("Low stock report do")
        self.assertEqual(p_stock["intent"], "INVENTORY")

        # Invalid date
        p_invalid = NLPReportIntentParser.parse("2026-99-99 ki report do")
        self.assertEqual(p_invalid["intent"], "INVALID_DATE")

    def test_telegram_unpaired_user_rejection(self):
        """Unpaired Telegram chat ID receives unauthorized notice."""
        reply = handle_telegram_command("UNKNOWN_CHAT_9999", "Aaj ki report do")
        self.assertIn("Unauthorized Access", reply)

    def test_telegram_reporting_engine_with_activity_and_empty_dataset(self):
        """Empty dataset does not generate a chart image; activity generates both text and chart image."""
        # Pair owner chat
        link = generate_pairing_code(self.company, self.owner_user)
        verify_pairing_code(link.pairing_code, "CHAT_12345", "owner_tg")

        # 1. Query for today (empty dataset)
        today_parsed = NLPReportIntentParser.parse("Aaj ki report do")
        result_empty = TelegramReportingEngine.execute(self.company, today_parsed)
        self.assertIn("Gross Sales", result_empty["text"])
        self.assertIn("Formulas:", result_empty["text"])
        self.assertFalse(result_empty.get("has_chart", False))  # No chart for 0 sales!

        # 2. Add a completed sale
        sale = Sale.objects.create(
            company=self.company,
            sale_number="SALE-V3-001",
            cashier=self.cashier_user,
            subtotal=Decimal("60.00"),
            discount_amount=Decimal("0.00"),
            tax_amount=Decimal("10.80"),
            grand_total=Decimal("70.80"),
            paid_amount=Decimal("70.80"),
            sale_status="COMPLETED",
        )
        Payment.objects.create(
            company=self.company,
            sale=sale,
            payment_method="UPI",
            amount=Decimal("70.80"),
            received_by=self.cashier_user,
        )

        # 3. Query again (activity exists)
        result_active = TelegramReportingEngine.execute(self.company, today_parsed)
        self.assertTrue(result_active.get("has_chart"))
        self.assertTrue(result_active.get("chart_path"))
        self.assertIn("₹70.80", result_active["text"])
        self.assertIn("UPI/QR: ₹70.80", result_active["text"])

    # ── TASK 9: PHONE SCANNER QR & PAIRING ────────────────────────────────
    def test_scanner_network_url_never_contains_loopback(self):
        """LAN URL resolution never contains loopback 127.0.0.1 or localhost."""
        url_info = get_pos_scanner_base_url()
        self.assertNotIn("127.0.0.1", url_info["base_url"])
        self.assertNotIn("localhost", url_info["base_url"])

        full_url = get_phone_scanner_full_url("temp_token_xyz")
        self.assertNotIn("127.0.0.1", full_url["phone_url"])
        self.assertIn("/pos/scanner/?session=temp_token_xyz", full_url["phone_url"])

    def test_settings_scanner_view_displays_terminal_pos001_and_qr(self):
        """Settings -> Scanner & POS Devices renders terminal ID POS-001, pairing code, and QR image."""
        self.web_client.force_login(self.owner_user)
        res = self.web_client.get("/settings/scanner/")
        self.assertEqual(res.status_code, 200)
        content = res.content.decode("utf-8")

        self.assertIn("TERMINAL POS-001", content)
        self.assertIn("One-Time Pairing Code", content)
        self.assertIn("create-qr-code", content)
        self.assertIn("mkcert", content)  # Local HTTPS workflow documented
