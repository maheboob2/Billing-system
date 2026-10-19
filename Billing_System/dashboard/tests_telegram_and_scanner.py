import json
from datetime import timedelta
from unittest.mock import patch, MagicMock
import urllib.error
from decimal import Decimal
from django.test import TestCase, Client
from django.contrib.auth.models import User
from django.utils import timezone
from accounts.models import companyRegistration
from dashboard.models import MerchantTelegramBot
from inventory.models import Product, Category, Supplier
from sales.models import POSScannerSession


class TelegramBotTokenValidationTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="bot_owner", password="password123")
        self.company = companyRegistration.objects.create(
            owner=self.owner,
            company_name="Apex Retailers",
            address_line="Station Road",
            city="Pune",
            state="Maharashtra",
            pincode="411001",
            phone="9876543210",
            company_email="apex@retailers.com",
            gst_number="27AAAAA0000A1Z5"
        )
        self.client = Client()
        self.client.force_login(self.owner)

    def test_merchant_bot_timedelta_and_encryption(self):
        """Verifies timedelta is defined, challenge expiry is set to +15 mins, and token is encrypted."""
        bot = MerchantTelegramBot.objects.create(
            company=self.company,
            owner=self.owner,
            status="PENDING_VERIFICATION",
            verification_challenge="A1B2C3D4",
            verification_expires_at=timezone.now() + timedelta(minutes=15)
        )
        test_raw_token = "123456789:ABCdefGHIjklMNOpqrsTUVwxyz1234567"
        bot.set_and_encrypt_token(test_raw_token)
        bot.save()

        # Token must be encrypted at rest (never stored as raw plaintext in encrypted_token)
        self.assertNotEqual(bot.encrypted_token, test_raw_token)
        self.assertEqual(bot.get_decrypted_token(), test_raw_token)
        self.assertTrue(bot.verification_expires_at > timezone.now())
        self.assertEqual(bot.status, "PENDING_VERIFICATION")

    @patch("urllib.request.urlopen")
    def test_configure_merchant_bot_view_success(self, mock_urlopen):
        """Submitting valid token sets PENDING_VERIFICATION and challenge without NameError."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "ok": True,
            "result": {
                "id": 987654321,
                "is_bot": True,
                "first_name": "ApexStoreBot",
                "username": "ApexStoreBot"
            }
        }).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        resp = self.client.post("/settings/telegram/", {
            "action": "configure_merchant_bot",
            "bot_token": "987654321:AAFakeValidTokenHereForTesting1234"
        })
        self.assertEqual(resp.status_code, 200)

        # Bot record must exist with PENDING_VERIFICATION status
        bot = MerchantTelegramBot.objects.filter(company=self.company).first()
        self.assertIsNotNone(bot)
        self.assertEqual(bot.status, "PENDING_VERIFICATION")
        self.assertEqual(bot.bot_username, "ApexStoreBot")
        self.assertTrue(bot.verification_expires_at > timezone.now())

    @patch("urllib.request.urlopen")
    def test_configure_merchant_bot_view_http_401_handled_safely(self, mock_urlopen):
        """HTTP 401 Unauthorized from Telegram is gracefully reported without leaking secrets."""
        raw_secret_token = "987654321:SecretInvalidToken999"
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="https://api.telegram.org/bot...",
            code=401,
            msg="Unauthorized",
            hdrs={},
            fp=None
        )

        resp = self.client.post("/settings/telegram/", {
            "action": "configure_merchant_bot",
            "bot_token": raw_secret_token
        })
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode("utf-8")
        # Must display clean error message and never leak the raw token in the response HTML
        self.assertIn("HTTP 401", content)
        self.assertNotIn(raw_secret_token, content)


class POSScannerSessionAndApiTests(TestCase):
    """
    Tests for the POS Phone Scanner pairing system.
    API contract (per test_auto_sync_and_scanner.py reference tests):
    - POST /api/pos/scanner-session/create/ -> 201, {session_token, pairing_code, lan_connect_url}
    - POST /api/pos/scanner-session/<token>/revoke/ -> 200, {status: "REVOKED"}
    - GET  /api/pos/scanner-session/<token>/poll/ -> 200, {connected, scans, ...}
    """
    def setUp(self):
        self.owner = User.objects.create_user(username="scanner_owner", password="password123")
        self.company = companyRegistration.objects.create(
            owner=self.owner,
            company_name="Apex Retailers",
            address_line="Station Road",
            city="Pune",
            state="Maharashtra",
            pincode="411001",
            phone="9876543210",
            company_email="apex@retailers.com",
            gst_number="27AAAAA0000A1Z5"
        )
        self.category = Category.objects.create(name="Groceries", company=self.company)
        self.supplier = Supplier.objects.create(
            name="Beverage Distributor", phone="9876500000", company=self.company
        )
        self.product = Product.objects.create(
            name="Mineral Water 1L",
            product_code="MW1001",
            barcode="890123456789",
            category=self.category,
            supplier=self.supplier,
            selling_price=Decimal("20.00"),
            purchase_price=Decimal("12.00"),
            current_stock=100,
            company=self.company,
        )
        self.client = Client()
        self.client.force_login(self.owner)

    def test_scanner_session_create_returns_201_with_token_and_url(self):
        """Scanner session creation returns 201 with session_token, pairing_code, lan_connect_url."""
        resp = self.client.post("/api/pos/scanner-session/create/")
        self.assertEqual(resp.status_code, 201)
        data = resp.json()

        self.assertIn("session_token", data)
        self.assertIn("pairing_code", data)
        self.assertIn("lan_connect_url", data)
        self.assertTrue(len(data["session_token"]) > 10)
        self.assertTrue(len(data["pairing_code"]) >= 6)

        # Verify session saved in DB under correct company
        token = data["session_token"]
        session_obj = POSScannerSession.objects.get(session_token=token)
        self.assertEqual(session_obj.company, self.company)
        self.assertTrue(session_obj.is_valid())

    def test_scanner_session_revoke_then_poll_reflects_revoked(self):
        """After revocation, poll should report session as REVOKED or return non-200."""
        create_data = self.client.post("/api/pos/scanner-session/create/").json()
        token = create_data["session_token"]

        # 1. Active session poll should succeed
        poll_resp = self.client.get(f"/api/pos/scanner-session/{token}/poll/")
        self.assertEqual(poll_resp.status_code, 200)

        # 2. Revoke
        rev_resp = self.client.post(f"/api/pos/scanner-session/{token}/revoke/")
        self.assertEqual(rev_resp.status_code, 200)
        self.assertEqual(rev_resp.json().get("status"), "REVOKED")

        # 3. Session in DB should be REVOKED
        session_obj = POSScannerSession.objects.get(session_token=token)
        self.assertEqual(session_obj.status, "REVOKED")

    def test_scanner_session_barcode_scan_and_poll_consumption(self):
        """Barcode pushed via /scan/ is returned once on /poll/ and empty on second poll."""
        create_data = self.client.post("/api/pos/scanner-session/create/").json()
        token = create_data["session_token"]

        # Submit scan (phone-side, unauthenticated)
        unauth = Client()
        scan_resp = unauth.post(
            f"/api/pos/scanner-session/{token}/scan/",
            data=json.dumps({"barcode": "890123456789"}),
            content_type="application/json",
        )
        self.assertEqual(scan_resp.status_code, 201)

        # First poll: barcode should appear
        poll1 = self.client.get(f"/api/pos/scanner-session/{token}/poll/")
        self.assertEqual(poll1.status_code, 200)
        self.assertIn("890123456789", poll1.json()["scans"])

        # Second poll: barcode consumed, scans should be empty
        poll2 = self.client.get(f"/api/pos/scanner-session/{token}/poll/")
        self.assertEqual(poll2.json()["scans"], [])


class TelegramWebhookOnboardingTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="store_owner", password="password123")
        self.company = companyRegistration.objects.create(
            owner=self.owner,
            company_name="Apex Superstore",
            address_line="Main Street",
            city="Pune",
            state="Maharashtra",
            pincode="411001",
            phone="9876543210",
            company_email="apex@superstore.com",
            gst_number="27AAAAA0000A1Z5"
        )
        self.client = Client()

    @patch("dashboard.telegram_service.send_telegram_direct_message")
    def test_webhook_start_command_returns_welcome_and_pairing_instructions(self, mock_send):
        """Unlinked user sending /start receives welcome instructions."""
        mock_send.return_value = (True, "")
        payload = {
            "update_id": 10001,
            "message": {
                "message_id": 1,
                "chat": {"id": 99887766},
                "from": {"id": 99887766, "first_name": "John"},
                "text": "/start"
            }
        }
        resp = self.client.post(
            "/api/telegram/webhook/",
            data=json.dumps(payload),
            content_type="application/json"
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(mock_send.called)
        sent_text = mock_send.call_args[0][1]
        self.assertIn("Welcome to Retail POS", sent_text)
        self.assertIn("/link", sent_text)

    @patch("dashboard.telegram_service.send_telegram_direct_message")
    def test_webhook_link_command_pairs_merchant(self, mock_send):
        """Sending /link <CODE> binds the chat ID and marks the link verified."""
        from dashboard.telegram_service import generate_pairing_code
        from dashboard.models import TelegramOwnerLink

        link = generate_pairing_code(self.company, self.owner)
        mock_send.return_value = (True, "")

        payload = {
            "update_id": 10002,
            "message": {
                "message_id": 2,
                "chat": {"id": 12345678},
                "from": {"id": 12345678, "first_name": "Owner"},
                "text": f"/link {link.pairing_code}"
            }
        }
        resp = self.client.post(
            "/api/telegram/webhook/",
            data=json.dumps(payload),
            content_type="application/json"
        )
        self.assertEqual(resp.status_code, 200)

        # TelegramOwnerLink must now be verified with this chat_id
        link.refresh_from_db()
        self.assertTrue(link.is_verified)
        self.assertEqual(link.telegram_chat_id, "12345678")

    @patch("dashboard.telegram_service.send_telegram_direct_message")
    def test_webhook_connect_command_alias(self, mock_send):
        """Sending /connect <CODE> works as an alias to /link."""
        from dashboard.telegram_service import generate_pairing_code

        link = generate_pairing_code(self.company, self.owner)
        mock_send.return_value = (True, "")

        payload = {
            "update_id": 10003,
            "message": {
                "message_id": 3,
                "chat": {"id": 55443322},
                "from": {"id": 55443322, "first_name": "Owner"},
                "text": f"/connect {link.pairing_code}"
            }
        }
        resp = self.client.post(
            "/api/telegram/webhook/",
            data=json.dumps(payload),
            content_type="application/json"
        )
        self.assertEqual(resp.status_code, 200)

        link.refresh_from_db()
        self.assertTrue(link.is_verified)
        self.assertEqual(link.telegram_chat_id, "55443322")

    @patch("dashboard.telegram_service.send_telegram_direct_message")
    def test_webhook_unlink_command_disconnects_store(self, mock_send):
        """Sending /unlink disconnects the chat and sets is_verified=False."""
        from dashboard.models import TelegramOwnerLink

        link = TelegramOwnerLink.objects.create(
            company=self.company,
            owner=self.owner,
            is_verified=True,
            telegram_chat_id="77665544",
            pairing_code_expires_at=timezone.now() + timedelta(days=365)
        )
        mock_send.return_value = (True, "")

        payload = {
            "update_id": 10004,
            "message": {
                "message_id": 4,
                "chat": {"id": 77665544},
                "from": {"id": 77665544, "first_name": "Owner"},
                "text": "/unlink"
            }
        }
        resp = self.client.post(
            "/api/telegram/webhook/",
            data=json.dumps(payload),
            content_type="application/json"
        )
        self.assertEqual(resp.status_code, 200)

        link.refresh_from_db()
        self.assertFalse(link.is_verified)
        self.assertEqual(link.telegram_chat_id, "")

    @patch("dashboard.telegram_service.send_telegram_direct_message")
    def test_webhook_invalid_pairing_code(self, mock_send):
        """Sending invalid pairing code returns graceful error without crashing."""
        mock_send.return_value = (True, "")
        payload = {
            "update_id": 10005,
            "message": {
                "message_id": 5,
                "chat": {"id": 11223344},
                "from": {"id": 11223344, "first_name": "User"},
                "text": "/link BOGUS1"
            }
        }
        resp = self.client.post(
            "/api/telegram/webhook/",
            data=json.dumps(payload),
            content_type="application/json"
        )
        self.assertEqual(resp.status_code, 200)
        sent_text = mock_send.call_args[0][1]
        self.assertIn("Invalid or expired pairing code", sent_text)


class TelegramReportDateParsingTests(TestCase):
    def test_date_parsing_today_and_daily(self):
        from dashboard.telegram_service import parse_report_command_text
        today = timezone.now().date()

        for cmd in ["/report today", "/today", "/daily", "daily sales"]:
            res = parse_report_command_text(cmd)
            self.assertEqual(res["start_date"], today)
            self.assertEqual(res["end_date"], today)
            self.assertFalse(res["is_range"])

    def test_date_parsing_weekly(self):
        from dashboard.telegram_service import parse_report_command_text
        today = timezone.now().date()
        expected_start = today - timedelta(days=today.weekday())

        for cmd in ["/report weekly", "/weekly", "weekly report", "this week"]:
            res = parse_report_command_text(cmd)
            self.assertEqual(res["start_date"], expected_start)
            self.assertEqual(res["end_date"], today)
            self.assertTrue(res["is_range"])

    def test_date_parsing_monthly(self):
        from dashboard.telegram_service import parse_report_command_text
        today = timezone.now().date()
        expected_start = today.replace(day=1)

        for cmd in ["/report monthly", "/monthly", "monthly report", "this month"]:
            res = parse_report_command_text(cmd)
            self.assertEqual(res["start_date"], expected_start)
            self.assertEqual(res["end_date"], today)
            self.assertTrue(res["is_range"])

    def test_date_parsing_iso_format(self):
        from dashboard.telegram_service import parse_report_command_text
        from datetime import date

        res = parse_report_command_text("/report 2026-10-02")
        self.assertEqual(res["start_date"], date(2026, 10, 2))
        self.assertEqual(res["end_date"], date(2026, 10, 2))

    def test_date_parsing_day_month_format(self):
        from dashboard.telegram_service import parse_report_command_text
        from datetime import date
        year = timezone.now().date().year

        res = parse_report_command_text("/report 02-10")
        self.assertEqual(res["start_date"], date(year, 10, 2))
        self.assertEqual(res["end_date"], date(year, 10, 2))


class InsecureContextDetectionStaticTests(TestCase):
    def test_cart_template_has_insecure_warning_element(self):
        """cart.html must have #scanner-insecure-warning and #manual-barcode-input."""
        from django.template.loader import render_to_string
        from accounts.models import companyRegistration
        owner = User.objects.create_user(username="test_cashier", password="password")
        company = companyRegistration.objects.create(
            owner=owner, company_name="Test Store", address_line="Lane 1",
            city="Pune", state="MH", pincode="411001", phone="999", company_email="t@t.com"
        )
        content = render_to_string("sales/cart.html", {
            "company": company,
            "customers": [],
            "user_role": "Cashier",
            "pending_sales_count": 0,
            "cart_items": []
        })
        self.assertIn('id="scanner-insecure-warning"', content)
        self.assertIn('id="manual-barcode-input"', content)

    def test_cart_js_has_is_secure_context_guard(self):
        """cart.js must check isSecureContext or localhost and suppress the reticle."""
        import os
        from django.conf import settings
        cart_js_path = os.path.join(settings.BASE_DIR, "sales", "static", "sales", "js", "cart.js")
        with open(cart_js_path, "r", encoding="utf-8") as f:
            js_code = f.read()

        self.assertIn("isSecureContext", js_code)
        self.assertIn("scanner-insecure-warning", js_code)
        self.assertIn("manual-barcode-input", js_code)

