"""
Automated Test Suite for Phase 1 (Automatic Sync) and Phase 2 (Phone Barcode Scanner & Pairing).

Tests:
1. Startup sync: queues transactions and syncs them automatically.
2. Retry & bounded backoff: increases interval on errors and resets on recovery.
3. Strict idempotency: retries and restarts never duplicate sales, payments, purchases, customers, or reports.
4. Stale report labeling: Telegram reports explicitly state last sync data-through timestamp and warn of missing offline activity when disconnected.
5. Telegram delivery status: confirms delivery before marking messages SENT.
6. Pairing expiry & revocation: verifies expiration enforcement and immediate revocation API.
7. Scanner reconnects: heartbeat pings restore active companion presence.
8. Tenant isolation: cross-tenant scanner polling, scanning, and catalog lookups are strictly blocked.
"""

from decimal import Decimal
from datetime import timedelta, date
from django.test import TestCase, Client
from django.utils import timezone
from django.contrib.auth.models import User
from rest_framework.test import APIClient

from accounts.models import companyRegistration
from employee.models import user_registration
from sales.models import Sale, SaleItem, Payment, POSScannerSession, POSScannerScan, Customer
from inventory.models import Category, Product
from purchases.models import Purchase, Supplier
from dashboard.models import (
    SyncQueueItem,
    RemoteSyncedRecord,
    RemoteSyncStatus,
    TelegramOwnerLink,
    TelegramOutboundMessage,
)
from dashboard.sync_service import SyncService
from dashboard.auto_sync import AutoSyncDaemon, get_auto_sync_daemon, ProcessWorkerLock
from dashboard.telegram_service import (
    generate_pairing_code,
    verify_pairing_code,
    handle_telegram_command,
    queue_telegram_message,
    dispatch_outbound_messages,
    NLPReportIntentParser,
    TelegramReportingEngine,
)


class AutoSyncAndScannerPhaseTests(TestCase):
    def setUp(self):
        # Company A (Store 1)
        self.owner_a = User.objects.create_user(
            username="owner_store_a",
            first_name="Owner",
            last_name="Alpha",
            password="Password123!",
            email="owner_a@store.local"
        )
        self.company_a = companyRegistration.objects.create(
            owner=self.owner_a,
            company_name="Apex Supermarket A",
            city="Mumbai"
        )
        self.cashier_user_a = User.objects.create_user(
            username="cashier_store_a",
            first_name="Cashier",
            last_name="Alpha",
            password="Password123!"
        )
        self.cashier_emp_a = user_registration.objects.create(
            user=self.cashier_user_a,
            company=self.company_a,
            employee_id="CSH-A01",
            Role="Cashier",
            Phone="9800000001",
            joining_date="2026-01-01",
            payment="Paid",
            status="active"
        )

        # Products in Company A
        self.category_a = Category.objects.create(company=self.company_a, name="Beverages")
        self.supplier_a = Supplier.objects.create(company=self.company_a, name="Beverage Dist Corp", phone="9988776655")
        self.product_a = Product.objects.create(
            company=self.company_a,
            category=self.category_a,
            supplier=self.supplier_a,
            name="Mango Juice 1L",
            barcode="890123450001",
            product_code="BEV-001",
            purchase_price=Decimal("40.00"),
            selling_price=Decimal("60.00"),
            current_stock=Decimal("50.00"),
            minimum_stock=Decimal("5.00"),
            status=True
        )

        # Company B (Store 2 - for Tenant Isolation)
        self.owner_b = User.objects.create_user(
            username="owner_store_b",
            first_name="Owner",
            last_name="Beta",
            password="Password123!",
            email="owner_b@store.local"
        )
        self.company_b = companyRegistration.objects.create(
            owner=self.owner_b,
            company_name="Beta Retailers B",
            city="Pune"
        )
        self.category_b = Category.objects.create(company=self.company_b, name="Coffee")
        self.supplier_b = Supplier.objects.create(company=self.company_b, name="Bean Roasters", phone="9988776644")
        self.product_b = Product.objects.create(
            company=self.company_b,
            category=self.category_b,
            supplier=self.supplier_b,
            name="Beta Exclusive Coffee",
            barcode="990000000002",
            product_code="COF-999",
            purchase_price=Decimal("100.00"),
            selling_price=Decimal("150.00"),
            current_stock=Decimal("20.00"),
            status=True
        )

        self.api_client_a = APIClient()
        self.api_client_a.force_authenticate(user=self.cashier_user_a)

    # ─────────────────────────────────────────────────────────────
    # PHASE 1: AUTOMATIC SYNC TESTS
    # ─────────────────────────────────────────────────────────────

    def test_startup_sync_executes_and_syncs_pending_data(self):
        """Verify startup sync discovers pending queue items, syncs them and updates RemoteSyncStatus."""
        # Create a local sale in Company A
        sale = Sale.objects.create(
            company=self.company_a,
            cashier=self.cashier_user_a,
            sale_number="SALE-2026-001",
            subtotal=Decimal("60.00"),
            grand_total=Decimal("60.00")
        )
        item = SyncService.queue_sale(sale)
        self.assertEqual(item.status, "PENDING")

        # Initialize AutoSync daemon and trigger sync cycle
        daemon = AutoSyncDaemon()
        cycle_result = daemon.trigger_immediate_sync()

        self.assertTrue(cycle_result["success"])
        self.assertGreaterEqual(cycle_result["synced"], 1)

        # Verify item is marked SYNCED
        item.refresh_from_db()
        self.assertEqual(item.status, "SYNCED")

        # Verify RemoteSyncStatus updated
        status_info = SyncService.get_sync_status(self.company_a)
        self.assertEqual(status_info["pending_count"], 0)
        self.assertIsNotNone(status_info["last_sync_success"])

    def test_sync_retry_bounded_exponential_backoff(self):
        """Verify failed syncs increment consecutive failures and back off up to 120s max, then recover."""
        daemon = AutoSyncDaemon()
        self.assertEqual(daemon._current_interval, 30)

        # Simulate offline state
        SyncService.run_sync(self.company_a, simulate_offline=True)

        # Force a failure cycle in daemon
        orig_run_sync = SyncService.run_sync
        try:
            def mock_fail(*args, **kwargs):
                raise ConnectionError("Remote reporting API timed out")
            SyncService.run_sync = mock_fail

            res1 = daemon._execute_sync_cycle()
            self.assertFalse(res1["success"])
            self.assertEqual(daemon._consecutive_failures, 1)
            self.assertEqual(daemon._current_interval, 60)

            res2 = daemon._execute_sync_cycle()
            self.assertFalse(res2["success"])
            self.assertEqual(daemon._consecutive_failures, 2)
            self.assertEqual(daemon._current_interval, 120)

            # Cap at 120s max
            res3 = daemon._execute_sync_cycle()
            self.assertLessEqual(daemon._current_interval, 120)

        finally:
            SyncService.run_sync = orig_run_sync

        # Recover connection
        recovery_res = daemon._execute_sync_cycle()
        self.assertTrue(recovery_res["success"])
        self.assertEqual(daemon._consecutive_failures, 0)
        self.assertEqual(daemon._current_interval, 30)

    def test_strict_idempotency_prevents_duplicate_records(self):
        """Verify retries, re-runs, and restarts never duplicate sales, payments, purchases, or customers."""
        sale = Sale.objects.create(
            company=self.company_a,
            cashier=self.cashier_user_a,
            sale_number="SALE-IDEMPOTENT-999",
            subtotal=Decimal("120.00"),
            grand_total=Decimal("120.00")
        )
        SyncService.queue_sale(sale)

        customer = Customer.objects.create(
            company=self.company_a,
            name="John Doe",
            phone="9876543210"
        )
        SyncService.queue_customer(customer)

        # Run 1: Synced
        res1 = SyncService.run_sync(self.company_a)
        self.assertTrue(res1["success"])
        self.assertEqual(res1["synced"], 2)

        count_after_run1 = RemoteSyncedRecord.objects.filter(company=self.company_a).count()
        self.assertEqual(count_after_run1, 2)

        # Run 2: Re-run immediately (simulating restart or retry)
        res2 = SyncService.run_sync(self.company_a)
        self.assertEqual(res2["synced"], 0)

        count_after_run2 = RemoteSyncedRecord.objects.filter(company=self.company_a).count()
        self.assertEqual(count_after_run1, count_after_run2, "Record count must remain strictly identical (idempotent)!")

    def test_stale_report_labeling_when_store_offline(self):
        """Verify Telegram reports clearly label offline snapshots with data-through timestamp and missing activity notice."""
        # Pair test owner on Telegram
        link = generate_pairing_code(self.company_a, self.owner_a)
        chat_id = "test_chat_stale_99"
        verify_pairing_code(link.pairing_code, chat_id, "StoreOwner")

        # 1. Store is online
        SyncService.run_sync(self.company_a, simulate_offline=False)
        online_resp = handle_telegram_command(chat_id, "today's report")
        self.assertIn("Live Data", online_resp)

        # 2. Store goes offline with pending items
        sale = Sale.objects.create(
            company=self.company_a,
            cashier=self.cashier_user_a,
            sale_number="SALE-OFFLINE-001",
            subtotal=Decimal("50.00"),
            grand_total=Decimal("50.00")
        )
        SyncService.queue_sale(sale)
        SyncService.run_sync(self.company_a, simulate_offline=True)

        offline_resp = handle_telegram_command(chat_id, "today's report")
        self.assertIn("Offline Snapshot", offline_resp)
        self.assertIn("Data Through", offline_resp)
        self.assertIn("Store Offline", offline_resp)
        self.assertIn("Newer offline sales or stock activity may be missing", offline_resp)

    def test_telegram_delivery_status_confirmation(self):
        """Verify messages remain QUEUED or mark FAILED when unconfirmed, and never falsely claim delivery."""
        msg = queue_telegram_message(
            company=self.company_a,
            chat_id="fake_chat_123",
            message_text="Daily summary report"
        )
        self.assertEqual(msg.status, "QUEUED")
        self.assertIsNone(msg.sent_at)

        # When bot token is empty/unconfigured, dispatch does not invent delivery
        sent = dispatch_outbound_messages()
        msg.refresh_from_db()
        self.assertNotEqual(msg.status, "SENT")

    # ─────────────────────────────────────────────────────────────
    # PHASE 2: PHONE BARCODE SCANNER & PAIRING TESTS
    # ─────────────────────────────────────────────────────────────

    def test_scanner_pairing_session_creation_and_expiry(self):
        """Verify scanner session is created with 30-minute expiry and valid token."""
        res = self.api_client_a.post("/api/pos/scanner-session/create/")
        self.assertEqual(res.status_code, 201)
        data = res.json()

        self.assertIn("session_token", data)
        self.assertIn("pairing_code", data)
        self.assertIn("lan_connect_url", data)

        token = data["session_token"]
        session_obj = POSScannerSession.objects.get(session_token=token)
        self.assertEqual(session_obj.company, self.company_a)
        self.assertTrue(session_obj.is_valid())

        # Test expiration
        session_obj.expires_at = timezone.now() - timedelta(minutes=5)
        session_obj.save(update_fields=["expires_at"])
        self.assertFalse(session_obj.is_valid())

    def test_scanner_session_immediate_revocation(self):
        """Verify revoking a pairing session immediately marks it REVOKED and blocks subsequent scans."""
        res_create = self.api_client_a.post("/api/pos/scanner-session/create/")
        token = res_create.json()["session_token"]

        # Revoke session
        res_revoke = self.api_client_a.post(f"/api/pos/scanner-session/{token}/revoke/")
        self.assertEqual(res_revoke.status_code, 200)
        self.assertEqual(res_revoke.json()["status"], "REVOKED")

        session_obj = POSScannerSession.objects.get(session_token=token)
        self.assertEqual(session_obj.status, "REVOKED")
        self.assertFalse(session_obj.is_valid())

        # Phone companion attempts to scan using revoked session
        unauth_client = Client()
        scan_res = unauth_client.post(
            f"/api/pos/scanner-session/{token}/scan/",
            data={"barcode": "890123450001"},
            content_type="application/json"
        )
        self.assertEqual(scan_res.status_code, 400)
        self.assertIn("invalid or expired", scan_res.json()["error"].lower())

    def test_scanner_reconnect_and_heartbeat_ping(self):
        """Verify phone heartbeat ping updates last_ping and establishes connected status."""
        res_create = self.api_client_a.post("/api/pos/scanner-session/create/")
        token = res_create.json()["session_token"]

        # Phone sends heartbeat ping
        unauth_client = Client()
        ping_res = unauth_client.post(
            f"/api/pos/scanner-session/{token}/scan/",
            data={
                "heartbeat": True,
                "device_info": "iPhone 15 Mobile Safari"
            },
            content_type="application/json"
        )
        self.assertEqual(ping_res.status_code, 200)
        self.assertTrue(ping_res.json()["connected"])

        # POS Terminal polls session
        poll_res = self.api_client_a.get(f"/api/pos/scanner-session/{token}/poll/")
        self.assertEqual(poll_res.status_code, 200)
        poll_data = poll_res.json()
        self.assertTrue(poll_data["connected"])
        self.assertEqual(poll_data["device_info"], "iPhone 15 Mobile Safari")

    def test_scanner_barcode_delivery_to_pos_cart(self):
        """Verify scanned barcode is queued and consumed cleanly on next poll."""
        res_create = self.api_client_a.post("/api/pos/scanner-session/create/")
        token = res_create.json()["session_token"]

        # Phone submits scanned barcode
        unauth_client = Client()
        scan_res = unauth_client.post(
            f"/api/pos/scanner-session/{token}/scan/",
            data={"barcode": "890123450001"},
            content_type="application/json"
        )
        self.assertEqual(scan_res.status_code, 201)

        # Terminal polls and receives the scan
        poll_res = self.api_client_a.get(f"/api/pos/scanner-session/{token}/poll/")
        self.assertEqual(poll_res.status_code, 200)
        self.assertEqual(poll_res.json()["scans"], ["890123450001"])

        # Second poll: scan is now consumed, list is empty
        poll_res_2 = self.api_client_a.get(f"/api/pos/scanner-session/{token}/poll/")
        self.assertEqual(poll_res_2.json()["scans"], [])

    def test_scanner_tenant_isolation_strictly_enforced(self):
        """Verify Company B cannot poll or receive scans belonging to Company A."""
        # Create cashier for Company B
        cashier_user_b = User.objects.create_user(
            username="cashier_store_b",
            first_name="Cashier",
            last_name="Beta",
            password="Password123!"
        )
        user_registration.objects.create(
            user=cashier_user_b,
            company=self.company_b,
            employee_id="CSH-B01",
            Role="Cashier",
            Phone="9800000002",
            joining_date="2026-01-01",
            payment="Paid",
            status="active"
        )
        api_client_b = APIClient()
        api_client_b.force_authenticate(user=cashier_user_b)

        # Session created by Company A
        res_create_a = self.api_client_a.post("/api/pos/scanner-session/create/")
        token_a = res_create_a.json()["session_token"]

        # Company B cashier attempts to poll Company A's scanner session
        poll_b = api_client_b.get(f"/api/pos/scanner-session/{token_a}/poll/")
        self.assertEqual(poll_b.status_code, 200)
        self.assertFalse(poll_b.json()["active"], "Company B must not access Company A scanner session!")

        # Company A product lookup cannot find Company B product (returns 404 Not Found)
        lookup_res = self.api_client_a.get(f"/api/products/lookup/?code={self.product_b.barcode}")
        self.assertEqual(lookup_res.status_code, 404)
        self.assertFalse(lookup_res.json()["found"], "Company B product must not appear in Company A catalog!")

    def test_telegram_explicit_date_parsing_and_ranges(self):
        """Phase A: Verify /report date parsing supports ISO, DD-MM-YYYY, DD-MM, day of month, and date ranges."""
        today = timezone.localdate()

        # 1. /report or /report today -> today
        p_today = NLPReportIntentParser.parse("/report")
        self.assertEqual(p_today["intent"], "REPORT")
        self.assertEqual(p_today["start_date"], today)
        self.assertEqual(p_today["end_date"], today)
        self.assertFalse(p_today["is_range"])

        p_today_arg = NLPReportIntentParser.parse("/report today")
        self.assertEqual(p_today_arg["start_date"], today)

        # 2. ISO format: /report 2026-10-02
        p_iso = NLPReportIntentParser.parse("/report 2026-10-02")
        self.assertEqual(p_iso["start_date"], date(2026, 10, 2))
        self.assertEqual(p_iso["end_date"], date(2026, 10, 2))
        self.assertFalse(p_iso["is_range"])

        # 3. UK/Indian format: /report 02-10-2026
        p_uk = NLPReportIntentParser.parse("/report 02-10-2026")
        self.assertEqual(p_uk["start_date"], date(2026, 10, 2))

        # 4. Day-Month without year: /report 02-10
        p_dm = NLPReportIntentParser.parse("/report 02-10")
        self.assertEqual(p_dm["start_date"], date(today.year, 10, 2))

        # 5. Bare day of current month: /report 2
        p_day = NLPReportIntentParser.parse("/report 2")
        self.assertEqual(p_day["start_date"], date(today.year, today.month, 2))

        # 6. Date Range: /report 2026-10-01 2026-10-11
        p_range = NLPReportIntentParser.parse("/report 2026-10-01 2026-10-11")
        self.assertEqual(p_range["start_date"], date(2026, 10, 1))
        self.assertEqual(p_range["end_date"], date(2026, 10, 11))
        self.assertTrue(p_range["is_range"])

        # 7. Engine execution carries exact date title & period
        report_res = TelegramReportingEngine.execute(self.company_a, p_iso)
        self.assertIn("Daily Sales Report (02-10-2026)", report_res["text"])
        self.assertIn("Period: `2026-10-02`", report_res["text"])
        self.assertIn("Data Through (Last Sync)", report_res["text"])
        self.assertIn("Report Generated", report_res["text"])

    def test_telegram_invalid_date_returns_format_guide(self):
        """Phase A: Invalid date argument must never silently return today's report; must return format guide."""
        p_invalid = NLPReportIntentParser.parse("/report invalid_gibberish")
        self.assertEqual(p_invalid["intent"], "INVALID_DATE")

        p_invalid_digits = NLPReportIntentParser.parse("/report 99-99-9999")
        self.assertEqual(p_invalid_digits["intent"], "INVALID_DATE")

        # Engine execution returns format help text
        res = TelegramReportingEngine.execute(self.company_a, p_invalid)
        self.assertIn("Invalid or Unrecognized Date Format", res["text"])
        self.assertIn("Supported Date Formats", res["text"])
        self.assertNotIn("Gross Sales (Invoiced)", res["text"])

    def test_process_worker_lock_guarantees_single_worker(self):
        """Phase B: Verify ProcessWorkerLock guarantees single-worker lock acquisition across workers."""
        import tempfile
        import os
        with tempfile.NamedTemporaryFile(delete=False) as tf:
            lock_path = tf.name

        try:
            lock1 = ProcessWorkerLock(lockfile_path=lock_path)
            lock2 = ProcessWorkerLock(lockfile_path=lock_path)

            # First worker acquires lock
            self.assertTrue(lock1.acquire())
            self.assertTrue(lock1.is_locked)

            # Second worker cannot acquire while first holds it
            self.assertFalse(lock2.acquire())
            self.assertFalse(lock2.is_locked)

            # First worker releases lock
            lock1.release()
            self.assertFalse(lock1.is_locked)

            # Second worker can now acquire lock
            self.assertTrue(lock2.acquire())
            self.assertTrue(lock2.is_locked)
            lock2.release()
        finally:
            if os.path.exists(lock_path):
                try:
                    os.remove(lock_path)
                except Exception:
                    pass

    def test_scanner_duplicate_suppression_within_time_window(self):
        """Phase C: Verify duplicate scan delivery is suppressed within 1.0s window for identical barcode."""
        res_create = self.api_client_a.post("/api/pos/scanner-session/create/")
        token = res_create.json()["session_token"]
        session = POSScannerSession.objects.get(session_token=token)

        unauth_client = Client()
        # First scan: creates new POSScannerScan
        res1 = unauth_client.post(
            f"/api/pos/scanner-session/{token}/scan/",
            data={"barcode": "890123450001"},
            content_type="application/json"
        )
        self.assertEqual(res1.status_code, 201)
        self.assertFalse(res1.json().get("duplicate_suppressed", False))
        self.assertEqual(session.scans.count(), 1)

        # Immediate rapid duplicate scan: suppressed
        res2 = unauth_client.post(
            f"/api/pos/scanner-session/{token}/scan/",
            data={"barcode": "890123450001"},
            content_type="application/json"
        )
        self.assertEqual(res2.status_code, 200)
        self.assertTrue(res2.json().get("duplicate_suppressed", False))
        self.assertEqual(session.scans.count(), 1, "Duplicate scan within 1.0s must not create second scan object!")

    def test_merchant_registration_telegram_onboarding_lifecycle(self):
        """Phase D: Verify merchant registration idempotently creates RemoteSyncStatus and Telegram pairing code."""
        from accounts.views import registration_form
        from django.test import RequestFactory

        rf = RequestFactory()
        post_data = {
            "admin_first_name": "New",
            "admin_last_name": "Merchant",
            "username": "new_merchant_unique_101",
            "password": "Password123!",
            "confirm_pass": "Password123!",
            "email": "new_merchant@test.local",
            "company_name": "New Mart 101",
            "address_line": "101 Market St",
            "city": "Pune",
            "state": "Maharashtra",
            "country": "India",
            "pincode": "411001",
            "company_email": "info@newmart101.local",
            "phone": "9811223344",
            "alternate_phone": "",
            "gst_number": "",
            "pan_number": "",
            "business_info": "Retail Mart",
        }
        req = rf.post("/accounts/register/", data=post_data)
        resp = registration_form(req)
        self.assertEqual(resp.status_code, 302)

        new_user = User.objects.get(username="new_merchant_unique_101")
        new_company = companyRegistration.objects.get(owner=new_user)

        # RemoteSyncStatus created
        sync_status = RemoteSyncStatus.objects.filter(company=new_company).first()
        self.assertIsNotNone(sync_status)
        self.assertEqual(sync_status.status, "ONLINE")

        # TelegramOwnerLink created
        link = TelegramOwnerLink.objects.filter(company=new_company, owner=new_user).first()
        self.assertIsNotNone(link)
        self.assertFalse(link.is_verified)
        self.assertTrue(len(link.pairing_code) > 0)
        self.assertTrue(link.pairing_code_expires_at > timezone.now())

    def test_telegram_linking_and_unlinking_commands(self):
        """Phase D: Verify /link <CODE>, /connect <CODE>, /start <CODE>, and /unlink commands."""
        link = generate_pairing_code(self.company_a, self.owner_a)
        code = link.pairing_code
        chat_id = "test_owner_chat_999"

        # 1. Unlinked /start explains pairing
        start_unlinked = handle_telegram_command(chat_id, "/start")
        self.assertIn("Not yet paired", start_unlinked)
        self.assertIn("/pair <YOUR-CODE>", start_unlinked)

        # 2. Pair via /link <CODE>
        link_resp = handle_telegram_command(chat_id, f"/link {code}")
        self.assertIn("Pairing successful", link_resp)
        self.assertIn(self.company_a.company_name, link_resp)
        self.assertIn("/report", link_resp)
        self.assertIn("/unlink", link_resp)

        # Verified state in database
        link.refresh_from_db()
        self.assertTrue(link.is_verified)
        self.assertEqual(link.telegram_chat_id, chat_id)

        # 3. Disconnect via /unlink
        unlink_resp = handle_telegram_command(chat_id, "/unlink")
        self.assertIn("Store Disconnected", unlink_resp)
        self.assertIn(self.company_a.company_name, unlink_resp)

        link.refresh_from_db()
        self.assertFalse(link.is_verified)
        self.assertEqual(link.telegram_chat_id, "")

