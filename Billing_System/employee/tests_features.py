from decimal import Decimal
from unittest.mock import patch, MagicMock
from django.test import TestCase, Client
from django.contrib.auth.models import User
from django.urls import reverse
from accounts.models import companyRegistration
from employee.models import user_registration
from employee.services import generate_next_employee_id, lookup_postal_pincode


class EmployeeIdAutoGenerationTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="owner_user", password="password123")
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

    def test_auto_generate_first_employee_id(self):
        """When no employees exist, generates a valid ID of length <= 10."""
        new_id = generate_next_employee_id(self.company, role="Cashier")
        self.assertTrue(len(new_id) <= 10)
        self.assertTrue(new_id.startswith("CSH-") or new_id.startswith("EMP-"))

    def test_auto_generate_increments_sequence(self):
        """Subsequent employees get unique, incremented IDs without collisions."""
        user1 = User.objects.create_user(username="emp_1", password="password123")
        id1 = generate_next_employee_id(self.company)
        user_registration.objects.create(
            user=user1,
            company=self.company,
            employee_id=id1,
            Role="Cashier",
            Phone="9876543211",
            address="123 Street",
            joining_date="2026-01-01",
            monthaly_salary=Decimal("20000.00"),
            payment="Paid",
            status="active"
        )

        id2 = generate_next_employee_id(self.company)
        self.assertNotEqual(id1, id2)
        self.assertFalse(user_registration.objects.filter(employee_id=id2).exists())

    def test_custom_id_accepted_when_unique(self):
        """User-supplied custom ID is respected when unique and <= 10 chars."""
        custom_id = generate_next_employee_id(self.company, requested_id="APX-007")
        self.assertEqual(custom_id, "APX-007")

    def test_custom_id_rejected_when_duplicate(self):
        """Duplicate custom ID raises ValueError with user-friendly message."""
        user1 = User.objects.create_user(username="emp_dup", password="password123")
        user_registration.objects.create(
            user=user1,
            company=self.company,
            employee_id="TAKEN-01",
            Role="Worker",
            Phone="9876543212",
            address="123 Street",
            joining_date="2026-01-01",
            monthaly_salary=Decimal("15000.00"),
            payment="Paid",
            status="active"
        )

        with self.assertRaises(ValueError) as ctx:
            generate_next_employee_id(self.company, requested_id="TAKEN-01")
        self.assertIn("already registered", str(ctx.exception))

    def test_custom_id_rejected_when_exceeds_10_chars(self):
        """Custom ID exceeding model max_length 10 is rejected."""
        with self.assertRaises(ValueError) as ctx:
            generate_next_employee_id(self.company, requested_id="TOOLONGEMPLOYEEID")
        self.assertIn("exceeds maximum allowed length of 10", str(ctx.exception))


class PostalPincodeLookupTests(TestCase):
    def test_valid_pincode_lookup(self):
        """Valid Indian PIN returns city/district and state."""
        res = lookup_postal_pincode("400001")
        self.assertTrue(res["success"])
        self.assertEqual(res["pincode"], "400001")
        self.assertIn("Mumbai", res["city"])
        self.assertEqual(res["state"], "Maharashtra")

    def test_invalid_pincode_format_rejected(self):
        """Non-6-digit PIN codes or codes starting with 0 are rejected."""
        res1 = lookup_postal_pincode("12345")  # 5 digits
        self.assertFalse(res1["success"])
        self.assertIn("Invalid PIN", res1["error"])

        res2 = lookup_postal_pincode("012345")  # starts with 0
        self.assertFalse(res2["success"])
        self.assertIn("Invalid PIN", res2["error"])

        res3 = lookup_postal_pincode("ABCDEF")  # letters
        self.assertFalse(res3["success"])

    def test_postal_lookup_api_endpoint(self):
        """GET /api/postal/lookup/?pincode=411001 returns 200 with JSON payload."""
        client = Client()
        resp = client.get("/api/postal/lookup/?pincode=411001")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertIn("Pune", data["city"])
        self.assertEqual(data["state"], "Maharashtra")

    def test_postal_lookup_invalid_returns_400(self):
        """GET /api/postal/lookup/?pincode=99 returns 400 Bad Request."""
        client = Client()
        resp = client.get("/api/postal/lookup/?pincode=99")
        self.assertEqual(resp.status_code, 400)
        data = resp.json()
        self.assertFalse(data["success"])
