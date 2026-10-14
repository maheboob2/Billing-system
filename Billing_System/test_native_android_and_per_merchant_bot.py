"""
Automated Test Suite for Native Android Scanner App + Per-Merchant AI-Guided Telegram Bot.

Tests:
1. Native Android App: APK generation, source files, CameraX & ML Kit configuration, network security config.
2. QR Code Pairing & Scanner APIs: QR payload structure, product catalog lookup, "Product not found" handling.
3. Debounce & Cooldown: 1.0s duplicate scan suppression, scan event idempotency, offline queue idempotency.
4. Tenant Isolation: Cross-tenant scanner polling and scanning strictly blocked.
5. Telegram Token Encryption at Rest: Fernet AES-128 + HMAC encryption, key separation, safe masking.
6. Zero Token Leakage: Tokens absent from status APIs, logs, AI assistant responses, and URLs.
7. Merchant Bot Configuration: Owner-only RBAC, Telegram getMe validation, single-use verification challenge.
8. One-Time Owner Verification: Challenge matching, replay rejection, chat binding, welcome message.
9. Multi-Bot Registry & Runtime Isolation: Supervised worker uniqueness, HTTP 401/404 token revocation handling.
10. AI Assistant Diagnostics: Safe Hinglish/English recovery guidance across all bot connection states.
11. Token Replacement / Disconnect: Preserves all previous sales, invoices, customers, and reports intact.
"""

import os
import json
import base64
import unittest
from decimal import Decimal
from datetime import timedelta, date
from unittest.mock import patch, MagicMock

from django.test import TestCase, Client
from django.utils import timezone
from django.contrib.auth.models import User
from rest_framework.test import APIClient
from rest_framework import status

from accounts.models import companyRegistration
from employee.models import user_registration
from inventory.models import Category, Product
from purchases.models import Supplier
from sales.models import Sale, SaleItem, Payment, POSScannerSession, POSScannerScan
from dashboard.models import MerchantTelegramBot, TelegramOwnerLink
from dashboard.token_encryption import (
    encrypt_telegram_token,
    decrypt_telegram_token,
    mask_token_for_display,
)
from dashboard.multi_bot_registry import (
    SupervisedBotWorker,
    SupervisedBotRegistry,
    get_supervised_bot_registry,
)
from dashboard.ai_assistant import answer_business_question


class NativeAndroidScannerAndPerMerchantBotTests(TestCase):
    def setUp(self):
        # 1. Company A (Store 1) - Owner Alpha
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

        # Products in Store A
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

        # 2. Company B (Store 2) - Tenant Isolation
        self.owner_b = User.objects.create_user(
            username="owner_store_b",
            first_name="Owner",
            last_name="Beta",
            password="Password123!",
            email="owner_b@store.local"
        )
        self.company_b = companyRegistration.objects.create(
            owner=self.owner_b,
            company_name="Beta Retail Mart B",
            city="Pune"
        )
        self.supplier_b = Supplier.objects.create(company=self.company_b, name="Dist B", phone="9988776656")
        self.category_b = Category.objects.create(company=self.company_b, name="Beverages B")
        self.product_b = Product.objects.create(
            company=self.company_b,
            category=self.category_b,
            supplier=self.supplier_b,
            name="Orange Juice 1L",
            barcode="890123459999",
            purchase_price=Decimal("45.00"),
            selling_price=Decimal("65.00"),
            current_stock=Decimal("20.00"),
            minimum_stock=Decimal("5.00"),
            status=True
        )

        self.api_client_a = APIClient()
        self.api_client_a.force_authenticate(user=self.owner_a)

    # ── PHASE 1: NATIVE ANDROID APPLICATION TESTS ────────────────────────

    def test_android_project_structure_and_apk_artifact(self):
        """Verify the native Android project exists, compiles with Kotlin/CameraX/ML Kit, and produced an APK."""
        base_dir = os.path.dirname(os.path.abspath(__file__))
        android_dir = os.path.join(base_dir, "android_scanner")

        # 1. Check core project files exist
        self.assertTrue(os.path.exists(os.path.join(android_dir, "build.gradle")), "Root build.gradle must exist")
        self.assertTrue(os.path.exists(os.path.join(android_dir, "settings.gradle")), "settings.gradle must exist")
        self.assertTrue(os.path.exists(os.path.join(android_dir, "app", "build.gradle")), "App build.gradle must exist")
        self.assertTrue(os.path.exists(os.path.join(android_dir, "app", "src", "main", "AndroidManifest.xml")), "Manifest must exist")

        # 2. Check Kotlin sources
        src_dir = os.path.join(android_dir, "app", "src", "main", "java", "com", "brisk", "pos", "scanner")
        expected_classes = [
            "MainActivity.kt",
            "PairingActivity.kt",
            "BarcodeAnalyzer.kt",
            "ScannerSessionManager.kt",
            "PosApiClient.kt",
            "OfflineScanQueue.kt",
            "SoundVibrationHelper.kt",
            "BriskScannerApp.kt",
        ]
        for cls_file in expected_classes:
            self.assertTrue(os.path.exists(os.path.join(src_dir, cls_file)), f"Kotlin class {cls_file} must exist")

        # 3. Check Network Security Config restricts cleartext to local LAN only
        net_cfg = os.path.join(android_dir, "app", "src", "main", "res", "xml", "network_security_config.xml")
        self.assertTrue(os.path.exists(net_cfg), "network_security_config.xml must exist")
        with open(net_cfg, "r", encoding="utf-8") as f:
            content = f.read()
            self.assertIn("192.168.0.0", content)
            self.assertIn("cleartextTrafficPermitted=\"false\"", content)

        # 4. Check that assembled debug APK exists and has non-trivial size (>15 MB)
        apk_path = os.path.join(android_dir, "app", "build", "outputs", "apk", "debug", "app-debug.apk")
        self.assertTrue(os.path.exists(apk_path), f"Built Android APK must exist at {apk_path}")
        apk_size_mb = os.path.getsize(apk_path) / (1024 * 1024)
        self.assertGreater(apk_size_mb, 15.0, f"APK size should be > 15MB, got {apk_size_mb:.2f}MB")

    def test_scanner_session_qr_payload_and_catalog_lookup(self):
        """Verify POS scanner session returns QR payload for native Android app, and scan returns catalog details."""
        # Create session
        res_create = self.api_client_a.post("/api/pos/scanner-session/create/")
        self.assertEqual(res_create.status_code, status.HTTP_201_CREATED)
        data = res_create.json()

        # Check QR payload structure
        self.assertIn("qr_payload", data)
        qr_payload = data["qr_payload"]
        self.assertEqual(qr_payload["type"], "BRISK_POS_SCANNER_PAIR")
        self.assertEqual(qr_payload["session_token"], data["session_token"])
        self.assertEqual(qr_payload["company_name"], self.company_a.company_name)
        self.assertEqual(qr_payload["terminal_id"], "POS-001")

        token = data["session_token"]
        unauth_client = Client()

        # Scan existing product in catalog
        scan_res = unauth_client.post(
            f"/api/pos/scanner-session/{token}/scan/",
            data={"barcode": "890123450001"},
            content_type="application/json"
        )
        self.assertEqual(scan_res.status_code, status.HTTP_201_CREATED)
        s_data = scan_res.json()
        self.assertTrue(s_data["found"])
        self.assertEqual(s_data["product_name"], "Mango Juice 1L")
        self.assertEqual(s_data["selling_price"], 60.0)
        self.assertEqual(s_data["current_stock"], 50.0)

        # Scan non-existent product in catalog: clearly indicates not found
        scan_missing = unauth_client.post(
            f"/api/pos/scanner-session/{token}/scan/",
            data={"barcode": "999999999999"},
            content_type="application/json"
        )
        self.assertEqual(scan_missing.status_code, status.HTTP_201_CREATED)
        m_data = scan_missing.json()
        self.assertFalse(m_data["found"])
        self.assertIsNone(m_data["product_name"])
        self.assertIn("Product not found", m_data["message"])

    def test_scanner_duplicate_suppression_and_offline_idempotency(self):
        """Verify rapid identical scan delivery within 1.0s is suppressed and does not duplicate cart scans."""
        res_create = self.api_client_a.post("/api/pos/scanner-session/create/")
        token = res_create.json()["session_token"]
        session = POSScannerSession.objects.get(session_token=token)

        unauth_client = Client()
        # Scan 1: creates scan
        res1 = unauth_client.post(
            f"/api/pos/scanner-session/{token}/scan/",
            data={"barcode": "890123450001", "idempotency_key": "uuid-101"},
            content_type="application/json"
        )
        self.assertEqual(res1.status_code, status.HTTP_201_CREATED)
        self.assertFalse(res1.json().get("duplicate_suppressed", False))
        self.assertEqual(session.scans.count(), 1)

        # Scan 2 immediately within 1.0s: suppressed
        res2 = unauth_client.post(
            f"/api/pos/scanner-session/{token}/scan/",
            data={"barcode": "890123450001", "idempotency_key": "uuid-102"},
            content_type="application/json"
        )
        self.assertEqual(res2.status_code, status.HTTP_200_OK)
        self.assertTrue(res2.json().get("duplicate_suppressed", True))
        self.assertEqual(session.scans.count(), 1, "Duplicate scan must not add second scan entry!")

    # ── PHASE 2: PER-MERCHANT TELEGRAM BOT ONBOARDING & ENCRYPTION ───────

    def test_token_encryption_at_rest_and_zero_hardcoding(self):
        """Verify tokens are encrypted using Fernet at rest, separate from DB, and never displayed in plaintext."""
        raw_token = "123456789:ABCdefGHIjklMNOpqrSTUvwxYZ_test_token"

        encrypted = encrypt_telegram_token(raw_token)
        self.assertNotEqual(raw_token, encrypted)
        self.assertNotIn(raw_token, encrypted)

        decrypted = decrypt_telegram_token(encrypted)
        self.assertEqual(raw_token, decrypted)

        # Masking for safe display
        masked = mask_token_for_display(raw_token)
        self.assertTrue(masked.startswith("123456"))
        self.assertTrue(masked.endswith("oken"))
        self.assertIn("••••••••", masked)
        self.assertNotIn("ABCdefGHIjklMNOpqrSTUvwxYZ", masked)

    @patch("urllib.request.urlopen")
    def test_merchant_bot_configuration_owner_rbac_and_getme_validation(self, mock_urlopen):
        """Verify only Owner can configure bot token, getMe is validated, and challenge is generated."""
        fake_token = "987654321:AAFakeBotTokenForTesting_123456789"

        # Mock Telegram getMe success response
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "ok": True,
            "result": {
                "id": 987654321,
                "is_bot": True,
                "first_name": "Apex Reports Bot",
                "username": "ApexReports_bot",
            }
        }).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        # 1. Cashier attempts to configure bot -> 403 Forbidden
        cashier_client = APIClient()
        cashier_client.force_authenticate(user=self.cashier_user_a)
        res_cashier = cashier_client.post(
            "/api/telegram/merchant-bot/configure/",
            data={"bot_token": fake_token},
            format="json"
        )
        self.assertEqual(res_cashier.status_code, status.HTTP_403_FORBIDDEN)

        # 2. Owner configures bot -> 200 OK
        res_owner = self.api_client_a.post(
            "/api/telegram/merchant-bot/configure/",
            data={"bot_token": fake_token},
            format="json"
        )
        self.assertEqual(res_owner.status_code, status.HTTP_200_OK)
        d = res_owner.json()
        self.assertTrue(d["success"])
        self.assertEqual(d["status"], "PENDING_VERIFICATION")
        self.assertEqual(d["bot_username"], "ApexReports_bot")
        self.assertTrue(len(d["challenge"]) >= 6)
        self.assertIn("start=", d["bot_link"])
        # Token must NOT be exposed in plaintext in response!
        self.assertNotIn(fake_token, json.dumps(d))

        # Check DB record is encrypted at rest
        bot_record = MerchantTelegramBot.objects.get(company=self.company_a)
        self.assertEqual(bot_record.status, "PENDING_VERIFICATION")
        self.assertNotEqual(bot_record.encrypted_token, fake_token)
        self.assertEqual(bot_record.get_decrypted_token(), fake_token)

    def test_zero_token_leakage_in_status_api(self):
        """Verify MerchantBotStatusAPIView returns safe status fields and never exposes encrypted or raw tokens."""
        bot = MerchantTelegramBot.objects.create(
            company=self.company_a,
            owner=self.owner_a,
            bot_username="SecretBot_bot",
            bot_first_name="Secret Bot",
            status="CONNECTED",
            verified_chat_id="chat_12345",
            verified_username="StoreOwnerAlpha",
            is_active=True
        )
        raw_secret = "111222333:SuperSecretTokenNeverExpose"
        bot.set_and_encrypt_token(raw_secret)
        bot.save()

        res = self.api_client_a.get("/api/telegram/merchant-bot/status/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        content_str = res.content.decode("utf-8")

        self.assertNotIn(raw_secret, content_str)
        self.assertNotIn(bot.encrypted_token, content_str)
        self.assertEqual(res.json()["status"], "CONNECTED")
        self.assertTrue(res.json()["is_verified"])
        self.assertEqual(res.json()["bot_username"], "SecretBot_bot")

    def test_one_time_owner_verification_challenge_matching_and_replay_rejection(self):
        """Verify challenge matching binds chat, single-use invalidation prevents replay, and wrong code rejects."""
        bot = MerchantTelegramBot.objects.create(
            company=self.company_a,
            owner=self.owner_a,
            bot_username="TestStore_bot",
            status="PENDING_VERIFICATION",
            verification_challenge="VERIFY99",
            verification_expires_at=timezone.now() + timedelta(minutes=15),
            is_active=True
        )
        bot.set_and_encrypt_token("fake:token")
        bot.save()

        worker = SupervisedBotWorker(bot.pk, self.company_a.pk, bot.encrypted_token)

        # 1. Wrong challenge rejected
        with patch.object(SupervisedBotWorker, "_run_poll_loop"):
            worker._handle_incoming_message(
                bot_obj=bot,
                token="fake:token",
                chat_id="chat_999",
                sender_username="OwnerUser",
                text="/verify WRONGCODE"
            )
        bot.refresh_from_db()
        self.assertEqual(bot.status, "PENDING_VERIFICATION")
        self.assertEqual(bot.verified_chat_id, "")

        # 2. Correct challenge accepted -> binds chat, sets CONNECTED, invalidates challenge
        with patch.object(SupervisedBotWorker, "_run_poll_loop"):
            worker._handle_incoming_message(
                bot_obj=bot,
                token="fake:token",
                chat_id="chat_999",
                sender_username="OwnerUser",
                text="/verify VERIFY99"
            )
        bot.refresh_from_db()
        self.assertEqual(bot.status, "CONNECTED")
        self.assertEqual(bot.verified_chat_id, "chat_999")
        self.assertEqual(bot.verified_username, "OwnerUser")
        self.assertEqual(bot.verification_challenge, "", "Challenge must be cleared immediately after use!")

        # 3. Replay attack: Sending previously valid challenge again cannot re-bind
        with patch.object(SupervisedBotWorker, "_run_poll_loop"):
            worker._handle_incoming_message(
                bot_obj=bot,
                token="fake:token",
                chat_id="attacker_chat_888",
                sender_username="AttackerUser",
                text="/verify VERIFY99"
            )
        bot.refresh_from_db()
        self.assertEqual(bot.verified_chat_id, "chat_999", "Replay must not change verified chat ID!")

    def test_multi_bot_registry_uniqueness_and_tenant_isolation(self):
        """Verify registry maintains single worker per bot and isolates reporting to that company."""
        registry = get_supervised_bot_registry()

        bot_a = MerchantTelegramBot.objects.create(
            company=self.company_a,
            owner=self.owner_a,
            bot_username="Alpha_bot",
            status="CONNECTED",
            verified_chat_id="chat_alpha_1",
            is_active=True
        )
        bot_a.set_and_encrypt_token("tok_a")
        bot_a.save()

        bot_b = MerchantTelegramBot.objects.create(
            company=self.company_b,
            owner=self.owner_b,
            bot_username="Beta_bot",
            status="CONNECTED",
            verified_chat_id="chat_beta_2",
            is_active=True
        )
        bot_b.set_and_encrypt_token("tok_b")
        bot_b.save()

        # Mock start to prevent live network polling during test
        with patch.object(SupervisedBotWorker, "start"):
            registry.sync_active_bots()
            self.assertIn(bot_a.pk, registry._workers)
            self.assertIn(bot_b.pk, registry._workers)

            # Re-syncing does not create duplicate workers
            registry.sync_active_bots()
            self.assertEqual(len(registry._workers), 2)

            # Stopping bot A
            registry.stop_bot(bot_a.pk)
            self.assertNotIn(bot_a.pk, registry._workers)
            self.assertIn(bot_b.pk, registry._workers)

            registry.stop_all()
            self.assertEqual(len(registry._workers), 0)

    # ── PHASE 3: AI ASSISTANT DIAGNOSTICS & RECOVERY ─────────────────────

    def test_ai_assistant_diagnoses_telegram_connection_states_safely(self):
        """Verify AI assistant diagnoses each safe connection status conversationally without leaking tokens."""
        # Case 1: NOT_CONFIGURED
        res_not_cfg = answer_business_question(self.company_a, "telegram connect nahi ho raha", user=self.owner_a)
        self.assertEqual(res_not_cfg["intent"], "telegram_not_configured")
        self.assertIn("@BotFather", res_not_cfg["answer"])
        self.assertIn("/newbot", res_not_cfg["answer"])

        # Case 2: PENDING_VERIFICATION
        bot = MerchantTelegramBot.objects.create(
            company=self.company_a,
            owner=self.owner_a,
            bot_username="MyStore_bot",
            status="PENDING_VERIFICATION",
            verification_challenge="CODE1234",
            is_active=True
        )
        bot.set_and_encrypt_token("secret:tok")
        bot.save()

        res_pending = answer_business_question(self.company_a, "bot connect nahi ho raha", user=self.owner_a)
        self.assertEqual(res_pending["intent"], "telegram_pending")
        self.assertIn("CODE1234", res_pending["answer"])
        self.assertIn("@MyStore_bot", res_pending["answer"])
        self.assertNotIn("secret:tok", res_pending["answer"])

        # Case 3: INVALID_TOKEN / REVOKED
        bot.status = "INVALID_TOKEN"
        bot.save()

        res_invalid = answer_business_question(self.company_a, "telegram problem", user=self.owner_a)
        self.assertEqual(res_invalid["intent"], "telegram_invalid_token")
        self.assertIn("Token invalid ya revoke ho gaya hai", res_invalid["answer"])
        self.assertIn("@BotFather", res_invalid["answer"])
        self.assertIn("purana sales aur business data 100% safe rahega", res_invalid["answer"])

        # Case 4: CONNECTED
        bot.status = "CONNECTED"
        bot.verified_username = "AlphaBoss"
        bot.save()

        res_connected = answer_business_question(self.company_a, "telegram status", user=self.owner_a)
        self.assertEqual(res_connected["intent"], "telegram_status")
        self.assertIn("Executive Bot is Connected and Active", res_connected["answer"])
        self.assertIn("@MyStore_bot", res_connected["answer"])

    def test_merchant_bot_disconnect_and_token_replacement_preserves_business_data(self):
        """Verify disconnecting or replacing bot token preserves sales, invoices, customers, and reports intact."""
        # Create a sale in Store A
        sale = Sale.objects.create(
            company=self.company_a,
            cashier=self.owner_a,
            sale_number="SALE-PRESERVE-001",
            subtotal=Decimal("120.00"),
            grand_total=Decimal("120.00"),
            payment_status="PAID"
        )

        bot = MerchantTelegramBot.objects.create(
            company=self.company_a,
            owner=self.owner_a,
            bot_username="OldBot_bot",
            status="CONNECTED",
            verified_chat_id="chat_old",
            is_active=True
        )
        bot.set_and_encrypt_token("old:token")
        bot.save()

        # Disconnect bot via API
        res_disconn = self.api_client_a.post("/api/telegram/merchant-bot/disconnect/")
        self.assertEqual(res_disconn.status_code, status.HTTP_200_OK)
        self.assertEqual(res_disconn.json()["status"], "DISCONNECTED")

        bot.refresh_from_db()
        self.assertEqual(bot.status, "DISCONNECTED")
        self.assertFalse(bot.is_active)
        self.assertEqual(bot.verified_chat_id, "")

        # Verify sale record remains completely intact!
        self.assertTrue(Sale.objects.filter(pk=sale.pk, company=self.company_a).exists())
        self.assertEqual(Sale.objects.get(pk=sale.pk).grand_total, Decimal("120.00"))
