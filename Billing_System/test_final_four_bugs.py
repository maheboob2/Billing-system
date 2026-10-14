"""
Comprehensive Automated Test Suite for the 4 Critical Bugs:
1. Product Form Phone Barcode Scan (Routing, Destination Isolation, and Auto-Fill)
2. Auto SKU Generation (Empty SKU handling, Uniqueness, Collision Prevention, Manual Preserved)
3. POS Cart Auto-Update (Authorized Cart Delivery, Unknown Barcode Honest Error, Idempotency, Session Expiry)
4. Indian Postal PIN Code Auto-Fetch (6-digit API/Fallback, Multi-Locality, 7-digit 4132477 Validation Rejection)
"""

import json
from decimal import Decimal
from django.test import TestCase, Client
from django.contrib.auth.models import User
from django.utils import timezone
from accounts.models import companyRegistration
from employee.models import user_registration
from inventory.models import Category, Supplier, Product
from inventory.forms import ProductForm, SupplierForm
from inventory.services import generate_unique_sku
from sales.models import POSScannerSession, POSScannerScan
from employee.services import lookup_postal_pincode


class FinalFourBugsIntegrationTests(TestCase):
    def setUp(self):
        self.client = Client()

        # Create test company & owner
        self.user = User.objects.create_user(
            username="owner_tester",
            email="owner@store.com",
            password="TestPassword123!"
        )
        self.company = companyRegistration.objects.create(
            company_name="Mega Mart Test Store",
            company_email="owner@store.com",
            phone="9876543210",
            alternate_phone="9876543211",
            address_line="123 Retail Lane",
            city="Pune",
            state="Maharashtra",
            pincode="411001",
            owner=self.user,
            business_info="Retail Store",
            gst_number="27AAAAA0000A1Z5"
        )
        self.user_reg = user_registration.objects.create(
            user=self.user,
            company=self.company,
            Role="Manager",
            employee_id="EMP-001",
            Phone="9876543210",
            Alternate_phone="9876543211",
            address="123 Retail Lane",
            Gender="Male",
            joining_date=timezone.now().date(),
            payment="Paid",
            status="active"
        )

        # Create Category & Supplier
        self.category = Category.objects.create(
            company=self.company,
            name="Groceries",
            description="Daily essentials"
        )
        self.supplier = Supplier.objects.create(
            company=self.company,
            name="Metro Wholesale Ltd",
            phone="9822001122",
            city="Mumbai",
            state="Maharashtra",
            pincode="400001",
            address="Depot 4A, APMC Market"
        )

        # Create an existing Product
        self.product = Product.objects.create(
            company=self.company,
            name="Tata Tea Gold 500g",
            product_code="TEA-GOLD-500",
            barcode="8901052001234",
            category=self.category,
            supplier=self.supplier,
            purchase_price=Decimal("200.00"),
            selling_price=Decimal("250.00"),
            gst_percentage=Decimal("5.00"),
            current_stock=Decimal("50.00"),
            minimum_stock=Decimal("10.00"),
            unit="pcs",
            status=True
        )

        # Log in the client
        self.client.force_login(self.user)

    def create_scanner_session(self, destination="POS", status="ACTIVE"):
        import secrets
        from datetime import timedelta
        return POSScannerSession.objects.create(
            company=self.company,
            user=self.user,
            session_token=secrets.token_urlsafe(24),
            pairing_code="123-456",
            status=status,
            destination=destination,
            expires_at=timezone.now() + timedelta(hours=2)
        )

    # =========================================================================
    # BUG 1 TESTS: PRODUCT FORM BARCODE SCANNING & DESTINATION ISOLATION
    # =========================================================================

    def test_bug1_active_scanner_session_switches_destination_to_product_form(self):
        """Active session endpoint switches destination to PRODUCT_FORM when requested."""
        resp = self.client.get("/api/pos/scanner-session/active/?destination=PRODUCT_FORM")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data.get("active"))
        self.assertEqual(data.get("destination"), "PRODUCT_FORM")
        self.assertIn("dest=PRODUCT_FORM", data.get("lan_connect_url", ""))

    def test_bug1_product_form_scan_accepts_uncataloged_barcode(self):
        """
        When scanning for a new product, the barcode does NOT exist in the catalog yet.
        PRODUCT_FORM mode must accept it with success=True, found=False without rejecting.
        """
        session = self.create_scanner_session(destination="PRODUCT_FORM")

        new_barcode = "8909876543210"
        scan_url = f"/api/pos/scanner-session/{session.session_token}/scan/"
        resp = self.client.post(
            scan_url,
            data=json.dumps({
                "barcode": new_barcode,
                "idempotency_key": "idemp-prod-form-1"
            }),
            content_type="application/json"
        )
        self.assertIn(resp.status_code, [200, 201])
        data = resp.json()
        self.assertTrue(data.get("success"))
        self.assertEqual(data.get("destination"), "PRODUCT_FORM")
        self.assertEqual(data.get("barcode"), new_barcode)

        # Polling for PRODUCT_FORM returns this scan
        poll_url = f"/api/pos/scanner-session/{session.session_token}/poll/?destination=PRODUCT_FORM"
        poll_resp = self.client.get(poll_url)
        self.assertEqual(poll_resp.status_code, 200)
        poll_data = poll_resp.json()
        self.assertIn(new_barcode, poll_data.get("scans", []))

    def test_bug1_product_form_scan_is_isolated_from_pos_cart(self):
        """A barcode scanned while in PRODUCT_FORM mode must NEVER be dispatched to the POS cart."""
        session = self.create_scanner_session(destination="PRODUCT_FORM")

        # Scan the existing Tata Tea barcode while in PRODUCT_FORM mode
        self.client.post(
            f"/api/pos/scanner-session/{session.session_token}/scan/",
            data=json.dumps({"barcode": self.product.barcode, "idempotency_key": "idemp-iso-1"}),
            content_type="application/json"
        )

        # A POS cart poll should receive NO scans because destination was PRODUCT_FORM
        pos_poll_resp = self.client.get(
            f"/api/pos/scanner-session/{session.session_token}/poll/?destination=POS"
        )
        self.assertEqual(pos_poll_resp.status_code, 200)
        pos_poll_data = pos_poll_resp.json()
        self.assertEqual(len(pos_poll_data.get("scans", [])), 0)

    # =========================================================================
    # BUG 2 TESTS: AUTO SKU GENERATION
    # =========================================================================

    def test_bug2_auto_sku_generated_when_product_code_is_blank(self):
        """When creating a product and leaving product_code blank, auto-generates unique SKU."""
        form_data = {
            "name": "Basmati Rice 5kg",
            "product_code": "",  # Blank SKU
            "barcode": "8901234999999",
            "category": self.category.id,
            "supplier": self.supplier.id,
            "purchase_price": "350.00",
            "selling_price": "420.00",
            "gst_percentage": "5.00",
            "current_stock": "25.00",
            "minimum_stock": "5.00",
            "unit": "pcs",
            "status": True,
        }
        form = ProductForm(data=form_data, company=self.company)
        self.assertTrue(form.is_valid(), f"Form errors: {form.errors}")
        product = form.save(commit=False)
        product.company = self.company
        product.save()

        self.assertTrue(bool(product.product_code))
        self.assertTrue(len(product.product_code) > 0)
        self.assertTrue(product.product_code.startswith("SKU-"))

    def test_bug2_manual_sku_is_preserved_when_entered(self):
        """When the user enters a custom SKU, it must be preserved without alteration."""
        custom_sku = "MANUAL-RICE-SPEC-01"
        form_data = {
            "name": "Custom Rice Spec",
            "product_code": custom_sku,
            "barcode": "8901234888888",
            "category": self.category.id,
            "supplier": self.supplier.id,
            "purchase_price": "300.00",
            "selling_price": "380.00",
            "gst_percentage": "5.00",
            "current_stock": "20.00",
            "minimum_stock": "5.00",
            "unit": "pcs",
            "status": True,
        }
        form = ProductForm(data=form_data, company=self.company)
        self.assertTrue(form.is_valid(), f"Form errors: {form.errors}")
        product = form.save(commit=False)
        product.company = self.company
        product.save()

        self.assertEqual(product.product_code, custom_sku)

    def test_bug2_duplicate_manual_sku_rejected_with_validation_error(self):
        """Entering an existing SKU triggers validation error and does not overwrite existing item."""
        form_data = {
            "name": "Another Tea Product",
            "product_code": self.product.product_code,  # Duplicate!
            "barcode": "8901234777777",
            "category": self.category.id,
            "supplier": self.supplier.id,
            "purchase_price": "100.00",
            "selling_price": "150.00",
            "gst_percentage": "5.00",
            "current_stock": "10.00",
            "minimum_stock": "2.00",
            "unit": "pcs",
            "status": True,
        }
        form = ProductForm(data=form_data, company=self.company)
        self.assertFalse(form.is_valid())
        self.assertIn("product_code", form.errors)

    def test_bug2_auto_sku_generator_collision_avoidance(self):
        """Calling generate_unique_sku multiple times produces unique SKUs without collision."""
        sku1 = generate_unique_sku(self.company, name="Aashirvaad Atta", barcode="8901234111111", category=self.category)
        Product.objects.create(
            company=self.company,
            name="Aashirvaad Atta 1",
            product_code=sku1,
            category=self.category,
            supplier=self.supplier,
            purchase_price=Decimal("150"),
            selling_price=Decimal("180"),
            current_stock=Decimal("10"),
            unit="pcs"
        )

        sku2 = generate_unique_sku(self.company, name="Aashirvaad Atta", barcode="8901234222222", category=self.category)
        self.assertNotEqual(sku1, sku2)

    # =========================================================================
    # BUG 3 TESTS: POS CART SCANNER AUTO-UPDATE & ROUTING
    # =========================================================================

    def test_bug3_pos_scan_unknown_barcode_returns_honest_error(self):
        """Unknown barcode in POS mode must return success=False, found=False, and not enter queue."""
        session = self.create_scanner_session(destination="POS")

        unknown_barcode = "9999999999999"
        resp = self.client.post(
            f"/api/pos/scanner-session/{session.session_token}/scan/",
            data=json.dumps({"barcode": unknown_barcode, "idempotency_key": "idemp-unk-1"}),
            content_type="application/json"
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertFalse(data.get("success"))
        self.assertFalse(data.get("found"))
        self.assertIn("not found", data.get("error", "").lower())

        # Polling POS must return 0 items
        poll_resp = self.client.get(
            f"/api/pos/scanner-session/{session.session_token}/poll/?destination=POS"
        )
        poll_data = poll_resp.json()
        self.assertEqual(len(poll_data.get("scans", [])), 0)

    def test_bug3_pos_scan_known_product_returns_authoritative_details_and_polls(self):
        """Known product in POS mode delivers barcode to polling queue with correct pricing."""
        session = self.create_scanner_session(destination="POS")

        resp = self.client.post(
            f"/api/pos/scanner-session/{session.session_token}/scan/",
            data=json.dumps({"barcode": self.product.barcode, "idempotency_key": "idemp-known-1"}),
            content_type="application/json"
        )
        self.assertIn(resp.status_code, [200, 201])
        data = resp.json()
        self.assertTrue(data.get("success"))
        self.assertTrue(data.get("found"))
        self.assertEqual(data.get("product_name"), self.product.name)
        self.assertEqual(data.get("selling_price"), float(self.product.selling_price))

        # Polling returns barcode for live cart addition
        poll_resp = self.client.get(
            f"/api/pos/scanner-session/{session.session_token}/poll/?destination=POS"
        )
        poll_data = poll_resp.json()
        self.assertIn(self.product.barcode, poll_data.get("scans", []))

    def test_bug3_idempotency_prevents_duplicate_camera_frames(self):
        """Retrying the same scan with identical idempotency_key does not add duplicate queue entries."""
        session = self.create_scanner_session(destination="POS")

        key = "idemp-frame-burst-123"
        # First scan
        r1 = self.client.post(
            f"/api/pos/scanner-session/{session.session_token}/scan/",
            data=json.dumps({"barcode": self.product.barcode, "idempotency_key": key}),
            content_type="application/json"
        )
        self.assertTrue(r1.json().get("success"))

        # Immediate duplicate camera frame retry with same key
        r2 = self.client.post(
            f"/api/pos/scanner-session/{session.session_token}/scan/",
            data=json.dumps({"barcode": self.product.barcode, "idempotency_key": key}),
            content_type="application/json"
        )
        self.assertTrue(r2.json().get("success"))

        # Verify only 1 scan record created for this key
        count = POSScannerScan.objects.filter(session=session, idempotency_key=key).count()
        self.assertEqual(count, 1)

    def test_bug3_expired_session_cannot_submit_scans(self):
        """A stale or deactivated scanner session returns HTTP 404."""
        session = self.create_scanner_session(status="EXPIRED")

        resp = self.client.post(
            f"/api/pos/scanner-session/{session.session_token}/scan/",
            data=json.dumps({"barcode": self.product.barcode}),
            content_type="application/json"
        )
        self.assertEqual(resp.status_code, 404)

    # =========================================================================
    # BUG 4 TESTS: PINCODE AUTO-FETCH & VALIDATION
    # =========================================================================

    def test_bug4_valid_pincode_returns_city_state_and_localities(self):
        """Valid 6-digit PIN code returns city, district, state, and list of matching localities."""
        res = lookup_postal_pincode("400001")
        self.assertTrue(res.get("success"))
        self.assertEqual(res.get("pincode"), "400001")
        self.assertIn("Maharashtra", res.get("state", ""))
        self.assertTrue(len(res.get("localities", [])) >= 1)

    def test_bug4_seven_digit_pincode_rejected_without_inventing_details(self):
        """A 7-digit PIN like 4132477 must be rejected with success=False, HTTP 400, never invented."""
        res = lookup_postal_pincode("4132477")
        self.assertFalse(res.get("success"))
        self.assertIn("Invalid PIN code", res.get("error", ""))

        # HTTP API check
        resp = self.client.get("/api/postal/lookup/?pincode=4132477")
        self.assertEqual(resp.status_code, 400)
        data = resp.json()
        self.assertFalse(data.get("success"))
        self.assertIn("Invalid PIN", data.get("error", ""))

    def test_bug4_invalid_format_pincodes_rejected(self):
        """Pincodes starting with 0, containing letters, or less than 6 digits are rejected."""
        self.assertFalse(lookup_postal_pincode("012345").get("success"))
        self.assertFalse(lookup_postal_pincode("12345").get("success"))
        self.assertFalse(lookup_postal_pincode("ABCDEF").get("success"))

    def test_bug4_offline_fallback_for_major_retail_zones(self):
        """Offline fallback provides authoritative city and state for regional prefix even without external network."""
        # Test regional prefix 110 (New Delhi)
        res = lookup_postal_pincode("110001")
        self.assertTrue(res.get("success"))
        self.assertIn("Delhi", res.get("state"))

    def test_bug4_supplier_form_rejects_seven_digit_pin_and_accepts_valid(self):
        """SupplierForm backend validation rejects 7-digit PIN and accepts valid 6-digit PIN."""
        # 7-digit invalid
        form_invalid = SupplierForm(data={
            "name": "Invalid Vendor",
            "phone": "9811223344",
            "pincode": "4132477",
            "city": "Unknown",
            "state": "Maharashtra",
            "address": "Street 1",
            "status": True,
        })
        self.assertFalse(form_invalid.is_valid())
        self.assertIn("pincode", form_invalid.errors)

        # 6-digit valid
        form_valid = SupplierForm(data={
            "name": "Valid Vendor Ltd",
            "phone": "9811223344",
            "pincode": "400001",
            "city": "Mumbai",
            "state": "Maharashtra",
            "address": "Street 1",
            "status": True,
        })
        self.assertTrue(form_valid.is_valid(), f"Errors: {form_valid.errors}")
        supplier = form_valid.save(commit=False)
        supplier.company = self.company
        supplier.save()
        self.assertEqual(supplier.pincode, "400001")
