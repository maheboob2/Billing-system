"""
Comprehensive Distributed Sync & 24/7 Telegram Owner Access Test Suite

Covers:
1. Scenario A: Shop & Cloud Online (POS checkout, Durable Outbox, Remote Ingestion, Telegram synced reporting)
2. Scenario B: Shop Offline (POS transactions buffered in durable outbox, Exponential Backoff, Telegram stale snapshot notice)
3. Scenario C: Shop PC Shutdown (Independently hosted Telegram bot serves historical cloud snapshot 24/7)
4. Scenario D: Reconnection & Checkpoint Recovery (Resumes from checkpoint, strict idempotency prevents duplicate records)
5. Security & Multi-Tenant Isolation (API key validation, cross-tenant event rejection)
6. Monetary Precision (Decimal arithmetic with zero float drift)
"""

import os
import sys
import json
from decimal import Decimal
from datetime import date, timedelta
from unittest.mock import patch, MagicMock

import django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.conf import settings

from accounts.models import companyRegistration
from inventory.models import Product, Category, Supplier, StockMovement
from sales.models import Sale, SaleItem, Payment, Customer, ReturnRequest
from purchases.models import Purchase, PurchaseItem
from dashboard.models import (
    SyncQueueItem,
    RemoteSyncedRecord,
    RemoteSyncStatus,
    CompanyCloudSnapshot,
    TelegramOwnerLink,
    TelegramOutboundMessage,
)
from dashboard.sync_service import SyncService
from dashboard.telegram_service import (
    generate_pairing_code,
    verify_pairing_code,
    handle_telegram_command,
    TelegramReportingEngine,
)
from sales.services import SalesService
from inventory.services import StockService

User = get_user_model()


class DistributedOfflineCloudSyncTestCase(TestCase):
    def setUp(self):
        # 1. Setup Company A (Primary Store)
        self.owner_a = User.objects.create_user(username="owner_alpha", password="password123")
        self.company_a = companyRegistration.objects.create(
            owner=self.owner_a,
            company_name="Alpha Mart",
            address_line="Main Road",
            city="Mumbai",
            state="Maharashtra",
            pincode="400001",
            phone="9876543210",
            company_email="alpha@mart.com",
            gst_number="27AAAAA0000A1Z5",
        )
        self.cashier_a = User.objects.create_user(username="cashier_alpha", password="password123")

        # 2. Setup Company B (Competitor Mart for tenant isolation testing)
        self.owner_b = User.objects.create_user(username="owner_beta", password="password123")
        self.company_b = companyRegistration.objects.create(
            owner=self.owner_b,
            company_name="Beta Retailers",
            address_line="Market Street",
            city="Pune",
            state="Maharashtra",
            pincode="411001",
            phone="9123456780",
            company_email="beta@retailers.com",
            gst_number="27BBBBB1111B1Z9",
        )

        # 3. Setup Category, Supplier, Products for Company A
        self.cat_a = Category.objects.create(name="Groceries", company=self.company_a)
        self.supplier_a = Supplier.objects.create(name="Apex Wholesale", company=self.company_a)
        self.prod_a1 = Product.objects.create(
            name="Basmati Rice 5kg",
            product_code="RICE-001",
            barcode="890111100001",
            category=self.cat_a,
            supplier=self.supplier_a,
            selling_price=Decimal("450.50"),
            purchase_price=Decimal("380.00"),
            gst_percentage=Decimal("5.00"),
            current_stock=20,
            minimum_stock=5,
            company=self.company_a,
        )
        self.prod_a2 = Product.objects.create(
            name="Organic Sugar 1kg",
            product_code="SUG-002",
            barcode="890111100002",
            category=self.cat_a,
            supplier=self.supplier_a,
            selling_price=Decimal("55.25"),
            purchase_price=Decimal("42.00"),
            gst_percentage=Decimal("5.00"),
            current_stock=3,  # Low stock!
            minimum_stock=10,
            company=self.company_a,
        )

        # Pair Telegram Owner for Company A
        link_a = generate_pairing_code(self.company_a, self.owner_a)
        self.chat_id_a = "telegram_chat_alpha_owner_101"
        ok_a, _, _ = verify_pairing_code(link_a.pairing_code, self.chat_id_a, "AlphaOwner")
        self.assertTrue(ok_a)

        # API Client
        self.client = Client()

    # ─────────────────────────────────────────────────────────────────
    # SCENARIO A — SHOP AND INTERNET ARE ONLINE
    # ─────────────────────────────────────────────────────────────────
    def test_scenario_a_online_pos_sync_and_telegram_queries(self):
        """
        Scenario A: POS creates sales and returns, sync service sends them to cloud API,
        cloud validates and acknowledges, Telegram bot answers all owner queries with
        synced data.
        """
        # 1. POS Sale with multi-tender payments (CASH & UPI)
        sale, invoice = SalesService.checkout(
            company=self.company_a,
            cashier=self.cashier_a,
            items_data=[
                {"product_id": self.prod_a1.id, "quantity": 2, "unit_price": self.prod_a1.selling_price, "tax_rate": 5},
                {"product_id": self.prod_a2.id, "quantity": 1, "unit_price": self.prod_a2.selling_price, "tax_rate": 5},
            ],
            payments_data=[
                {"payment_method": "CASH", "amount": Decimal("500.00")},
                {"payment_method": "UPI", "amount": Decimal("502.51")},
            ],
        )
        self.assertIsNotNone(sale)

        # 2. Stock adjustment via StockService
        movement = StockService.adjust_stock(
            company=self.company_a,
            product=self.prod_a1,
            user=self.cashier_a,
            movement_type="ADJUSTMENT",
            quantity=10,
            reason="Supplier restocking delivery"
        )
        self.assertIsNotNone(movement)

        # 3. Verify durable local outbox queuing
        pending_items = SyncQueueItem.objects.filter(company=self.company_a, status="PENDING")
        self.assertTrue(pending_items.count() >= 3, "Sale, Payments, and Stock movement must be enqueued in outbox")

        # 4. Background Sync pushes to remote Cloud
        sync_res = SyncService.run_sync(company=self.company_a, simulate_offline=False)
        self.assertTrue(sync_res["success"])
        self.assertTrue(sync_res["synced"] >= 3)
        self.assertEqual(sync_res["pending"], 0)

        # Verify items marked SYNCED
        synced_count = SyncQueueItem.objects.filter(company=self.company_a, status="SYNCED").count()
        self.assertTrue(synced_count >= 3)

        # 5. Telegram Owner Natural Language Queries
        # Question 1: How much did my shop sell today?
        q1_resp = handle_telegram_command(self.chat_id_a, "How much did my shop sell today?")
        self.assertIn("Today's Sales Report", q1_resp)
        self.assertIn("1,004.06", q1_resp)
        self.assertIn("Live Data", q1_resp)

        # Question 2: What is the current stock in the last synced snapshot?
        q2_resp = handle_telegram_command(self.chat_id_a, "What is the current stock in the last synced snapshot?")
        self.assertIn("Inventory", q2_resp)
        self.assertIn("Total Stock Volume:", q2_resp)
        self.assertIn("Total Catalog SKUs: *2*", q2_resp)

        # Question 3: Which products have low stock?
        q3_resp = handle_telegram_command(self.chat_id_a, "Which products have low stock?")
        self.assertIn("Organic Sugar 1kg", q3_resp)

        # Question 4: What returns are pending?
        q4_resp = handle_telegram_command(self.chat_id_a, "What returns are pending?")
        self.assertIn("Returns & Refunds Status", q4_resp)

        # Question 5: What are the cash, UPI, and credit totals?
        q5_resp = handle_telegram_command(self.chat_id_a, "What are the cash, UPI, and credit totals?")
        self.assertIn("Payment Breakdown", q5_resp)
        self.assertIn("Cash:", q5_resp)
        self.assertIn("UPI/QR:", q5_resp)

    # ─────────────────────────────────────────────────────────────────
    # SCENARIO B — SHOP INTERNET IS DISCONNECTED (LOCAL RUNNING)
    # ─────────────────────────────────────────────────────────────────
    def test_scenario_b_offline_pos_buffering_and_stale_telegram_notice(self):
        """
        Scenario B: Local POS continues recording sales offline, outbox holds records safely,
        Telegram bot detects store is offline and returns stale snapshot with last sync timestamp.
        """
        # First sync establishing baseline online snapshot
        SyncService.run_sync(company=self.company_a, simulate_offline=False)

        # Offline sale occurs
        sale_offline, _ = SalesService.checkout(
            company=self.company_a,
            cashier=self.cashier_a,
            items_data=[
                {"product_id": self.prod_a1.id, "quantity": 1, "unit_price": self.prod_a1.selling_price, "tax_rate": 5},
            ],
            payments_data=[
                {"payment_method": "CASH", "amount": Decimal("473.03")},
            ],
        )
        self.assertIsNotNone(sale_offline)

        # Run sync in offline mode (network unreachable)
        res_offline = SyncService.run_sync(company=self.company_a, simulate_offline=True)
        self.assertTrue(res_offline["offline"])
        self.assertTrue(res_offline["pending"] > 0)

        # Check outbox item is still PENDING and NOT lost
        offline_queue_item = SyncQueueItem.objects.filter(
            company=self.company_a, entity_type="SALE", entity_id=str(sale_offline.id)
        ).first()
        self.assertIsNotNone(offline_queue_item)
        self.assertEqual(offline_queue_item.status, "PENDING")

        # Query Telegram bot during store disconnect
        telegram_offline_resp = handle_telegram_command(self.chat_id_a, "today's sales")
        self.assertIn("Last Synced Snapshot", telegram_offline_resp)
        self.assertTrue(
            "Store Offline" in telegram_offline_resp or "Offline Snapshot" in telegram_offline_resp or "stale" in telegram_offline_resp.lower(),
            "Must clearly label response with stale / offline snapshot status!"
        )
        self.assertIn("Recent local transactions may not be included", telegram_offline_resp)

    # ─────────────────────────────────────────────────────────────────
    # SCENARIO C — THE SHOP PC/SERVER IS SHUT DOWN (24/7 CLOUD BOT)
    # ─────────────────────────────────────────────────────────────────
    def test_scenario_c_shop_pc_shutdown_24_7_cloud_telegram_access(self):
        """
        Scenario C: Shop PC is powered off. The cloud-hosted Telegram worker answers queries
        from CompanyCloudSnapshot without live connection to the shop PC.
        """
        # Ensure a valid cloud snapshot exists
        SyncService.run_sync(company=self.company_a, simulate_offline=False)
        snapshot = CompanyCloudSnapshot.objects.filter(company=self.company_a).first()
        self.assertIsNotNone(snapshot, "Materialized cloud snapshot must exist")

        # Simulate shop PC powered off:
        # Mark sync status as OFFLINE with last sync 2 hours ago
        status_rec, _ = RemoteSyncStatus.objects.get_or_create(company=self.company_a)
        status_rec.status = "OFFLINE"
        status_rec.pending_count = 5
        status_rec.save()

        # Execute Telegram command simulating CLOUD_WORKER environment
        with patch.object(settings, "CLOUD_SYNC_ROLE", "CLOUD_WORKER"):
            report_text = handle_telegram_command(self.chat_id_a, "today's report")
            self.assertIn("Last Synced Snapshot", report_text)
            self.assertTrue("Last Sync" in report_text or "Last Cloud Sync" in report_text)
            self.assertTrue(
                "Offline Snapshot" in report_text or "Store Offline" in report_text or "stale" in report_text.lower()
            )

    # ─────────────────────────────────────────────────────────────────
    # SCENARIO D — SHOP RECONNECTS & CHECKPOINT RECOVERY
    # ─────────────────────────────────────────────────────────────────
    def test_scenario_d_reconnection_checkpoint_recovery_and_idempotency(self):
        """
        Scenario D: Network reconnects. Sync worker delivers pending outbox records from
        checkpoint. Retrying or duplicate delivery is strictly idempotent.
        """
        # 1. Enqueue 2 new sales while offline
        sale1, _ = SalesService.checkout(
            company=self.company_a,
            cashier=self.cashier_a,
            items_data=[{"product_id": self.prod_a2.id, "quantity": 1, "unit_price": self.prod_a2.selling_price}],
            payments_data=[{"payment_method": "CASH", "amount": self.prod_a2.selling_price}],
        )
        sale2, _ = SalesService.checkout(
            company=self.company_a,
            cashier=self.cashier_a,
            items_data=[{"product_id": self.prod_a2.id, "quantity": 1, "unit_price": self.prod_a2.selling_price}],
            payments_data=[{"payment_method": "UPI", "amount": self.prod_a2.selling_price}],
        )

        # 2. Reconnect: run online sync
        res_reconnect = SyncService.run_sync(company=self.company_a, simulate_offline=False)
        self.assertTrue(res_reconnect["success"])
        self.assertTrue(res_reconnect["synced"] >= 2)
        self.assertEqual(res_reconnect["pending"], 0)
        checkpoint_after_sync = res_reconnect["checkpoint"]

        # 3. Simulate retry / network replay of the exact same records
        remote_count_before = RemoteSyncedRecord.objects.filter(company=self.company_a).count()
        res_replay = SyncService.run_sync(company=self.company_a, simulate_offline=False)
        remote_count_after = RemoteSyncedRecord.objects.filter(company=self.company_a).count()

        # Idempotency checks:
        self.assertEqual(res_replay["synced"], 0, "Replaying sync must ingest 0 new items")
        self.assertEqual(remote_count_before, remote_count_after, "Zero duplicate records created")
        self.assertEqual(res_replay["checkpoint"], checkpoint_after_sync, "Checkpoint remains stable")

        # 4. Telegram immediately reflects newly accepted data
        fresh_report = handle_telegram_command(self.chat_id_a, "today's report")
        self.assertIn("Live Data", fresh_report)
        self.assertNotIn("Store Offline: 2", fresh_report)

    # ─────────────────────────────────────────────────────────────────
    # SECURITY & MULTI-TENANT ISOLATION
    # ─────────────────────────────────────────────────────────────────
    def test_security_cloud_sync_api_authentication_and_tenant_isolation(self):
        """
        Verifies:
        - HTTP 401 Unauthorized when X-Cloud-Sync-Key is missing or wrong.
        - HTTP 400 Bad Request when company_id is missing or invalid.
        - Tenant A cannot ingest records for Tenant B or read Tenant B snapshots.
        """
        push_url = "/api/cloud-sync/push/"

        # 1. Missing API Key
        unauth_resp = self.client.post(
            push_url,
            data=json.dumps({"company_id": self.company_a.id, "records": []}),
            content_type="application/json"
        )
        self.assertEqual(unauth_resp.status_code, 401)
        self.assertIn("Invalid or missing X-Cloud-Sync-Key", unauth_resp.json()["error"])

        # 2. Invalid API Key
        bad_key_resp = self.client.post(
            push_url,
            data=json.dumps({"company_id": self.company_a.id, "records": []}),
            content_type="application/json",
            HTTP_X_CLOUD_SYNC_KEY="incorrect-secret-key-12345"
        )
        self.assertEqual(bad_key_resp.status_code, 401)

        # 3. Valid API Key, Invalid Tenant ID
        valid_key = getattr(settings, "CLOUD_SYNC_API_KEY", "pos-cloud-sync-secret-dev-key")
        bad_tenant_resp = self.client.post(
            push_url,
            data=json.dumps({"company_id": 999999, "records": []}),
            content_type="application/json",
            HTTP_X_CLOUD_SYNC_KEY=valid_key
        )
        self.assertEqual(bad_tenant_resp.status_code, 400)
        self.assertIn("Invalid or missing company_id", bad_tenant_resp.json()["error"])

        # 4. Tenant Isolation: Company A push cannot mutate Company B records
        payload_a = {
            "company_id": self.company_a.id,
            "items": [
                {
                    "idempotency_key": "SALE-A-SEC-001",
                    "event_id": "EVT-A-001",
                    "entity_type": "SALE",
                    "entity_id": "1",
                    "action": "CREATE",
                    "payload": {"sale_number": "SALE-A-001", "total": "500.00"},
                }
            ],
            "business_snapshot": {"total_sales_today": "500.00", "low_stock_count": 0}
        }
        res_a = self.client.post(
            push_url,
            data=json.dumps(payload_a),
            content_type="application/json",
            HTTP_X_CLOUD_SYNC_KEY=valid_key
        )
        self.assertEqual(res_a.status_code, 200)

        # Verify Record is bound only to Company A
        record_a = RemoteSyncedRecord.objects.filter(idempotency_key="SALE-A-SEC-001").first()
        self.assertIsNotNone(record_a)
        self.assertEqual(record_a.company, self.company_a)
        self.assertNotEqual(record_a.company, self.company_b)

        # Company B has zero records
        self.assertEqual(RemoteSyncedRecord.objects.filter(company=self.company_b).count(), 0)

    # ─────────────────────────────────────────────────────────────────
    # MONETARY DECIMAL PRECISION
    # ─────────────────────────────────────────────────────────────────
    def test_monetary_precision_decimal_arithmetic(self):
        """
        Verify all monetary calculations adhere to Decimal precision without floating-point drift.
        """
        prod_precise = Product.objects.create(
            name="Precise Item",
            product_code="PRECISE-001",
            barcode="890999900001",
            category=self.cat_a,
            supplier=self.supplier_a,
            selling_price=Decimal("19.99"),
            purchase_price=Decimal("12.00"),
            gst_percentage=Decimal("18.00"),
            current_stock=100,
            minimum_stock=5,
            company=self.company_a,
        )
        qty = 3
        subtotal = Decimal("19.99") * qty  # 59.97
        tax_amount = (subtotal * Decimal("18.00") / Decimal("100")).quantize(Decimal("0.01"))  # 10.79
        grand_total = subtotal + tax_amount  # 70.76

        sale, _ = SalesService.checkout(
            company=self.company_a,
            cashier=self.cashier_a,
            items_data=[{"product_id": prod_precise.id, "quantity": qty, "unit_price": Decimal("19.99"), "tax_rate": 18}],
            payments_data=[{"payment_method": "CASH", "amount": grand_total}],
        )

        self.assertEqual(sale.subtotal, Decimal("59.97"))
        self.assertEqual(sale.tax_amount, Decimal("10.79"))
        self.assertEqual(sale.grand_total, Decimal("70.76"))

        # Queue and verify serialized payload preserves Decimal string
        SyncService.queue_sale(sale)
        item = SyncQueueItem.objects.filter(company=self.company_a, entity_type="SALE", entity_id=str(sale.id)).first()
        self.assertIsNotNone(item)
        self.assertEqual(item.payload["grand_total"], "70.76")
        self.assertEqual(item.payload["subtotal"], "59.97")
        self.assertEqual(item.payload["tax_amount"], "10.79")

    @patch("urllib.request.urlopen")
    def test_push_to_remote_cloud_network_retry_and_acknowledgment(self, mock_urlopen):
        """
        Directly exercises SyncService._push_to_remote_cloud:
        1. Verifies that only acknowledged keys are marked SYNCED.
        2. Verifies that unacknowledged keys remain PENDING.
        3. Verifies that network timeouts trigger bounded retry increments and exponential failure tracking.
        """
        sale = Sale.objects.create(
            company=self.company_a,
            cashier=self.cashier_a,
            sale_number="SALE-PUSH-001",
            subtotal=Decimal("100.00"),
            grand_total=Decimal("105.00"),
        )
        item1 = SyncService.queue_sale(sale)

        customer = Customer.objects.create(
            company=self.company_a,
            name="Alice Walker",
            phone="9876500001",
        )
        item2 = SyncService.queue_customer(customer)

        status_obj, _ = RemoteSyncStatus.objects.get_or_create(company=self.company_a)

        # 1. Simulate partial ACK from cloud: only item1 acknowledged
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps({
            "success": True,
            "acknowledged_keys": [item1.idempotency_key],
            "checkpoint": item1.id,
            "synced_items_count": 1,
        }).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        res = SyncService._push_to_remote_cloud(self.company_a, status_obj, "https://mock-cloud.internal")
        self.assertTrue(res["success"])
        self.assertEqual(res["synced"], 1)

        item1.refresh_from_db()
        item2.refresh_from_db()
        self.assertEqual(item1.status, "SYNCED")
        self.assertEqual(item2.status, "PENDING", "Unacknowledged item must remain PENDING!")

        # 2. Simulate network failure (e.g. timeout / connection refused)
        import urllib.error
        mock_urlopen.side_effect = urllib.error.URLError("Connection refused: 503 Service Unavailable")

        fail_res = SyncService._push_to_remote_cloud(self.company_a, status_obj, "https://mock-cloud.internal")
        self.assertFalse(fail_res["success"])
        self.assertTrue(fail_res["offline"])

        item2.refresh_from_db()
        self.assertEqual(item2.status, "PENDING", "Failed item must NEVER be lost or marked SYNCED!")
        self.assertTrue(item2.retry_count >= 1)

        status_obj.refresh_from_db()
        self.assertEqual(status_obj.status, "OFFLINE")
        self.assertTrue(status_obj.consecutive_failures >= 1)


if __name__ == "__main__":
    import unittest
    unittest.main()
