from django.test import TestCase
from django.contrib.auth.models import User
from accounts.models import companyRegistration
from employee.models import user_registration
from accounts.tenancy import get_user_company, get_user_role


class TenancyAndRBACTests(TestCase):
    def setUp(self):
        # Create Owner & Company
        self.owner = User.objects.create_user(username="owner1", password="password123")
        self.company = companyRegistration.objects.create(
            owner=self.owner,
            company_name="Apex Retailers",
            address_line="123 Main St",
            city="Mumbai",
            state="Maharashtra",
            country="India",
            pincode="400001",
            company_email="apex@example.com",
            phone="9876543210",
            alternate_phone="9876543211",
            gst_number="27ABCDE1234F1Z5",
            business_info="Retail Store"
        )

        # Create Manager Employee
        self.manager_user = User.objects.create_user(username="manager1", password="password123")
        self.manager_emp = user_registration.objects.create(
            user=self.manager_user,
            company=self.company,
            employee_id="EMP001",
            Role="Manager",
            Phone="9876543212",
            Alternate_phone="9876543213",
            address="456 Elm St",
            joining_date="2026-01-01",
            monthaly_salary=50000.00,
            payment="Paid",
            status="active"
        )

        # Create Cashier Employee
        self.cashier_user = User.objects.create_user(username="cashier1", password="password123")
        self.cashier_emp = user_registration.objects.create(
            user=self.cashier_user,
            company=self.company,
            employee_id="EMP002",
            Role="Cashier",
            Phone="9876543214",
            Alternate_phone="9876543215",
            address="789 Oak St",
            joining_date="2026-02-01",
            monthaly_salary=25000.00,
            payment="Paid",
            status="active"
        )

        # Unaffiliated User
        self.unaffiliated = User.objects.create_user(username="stranger", password="password123")

    def test_owner_tenant_and_role_resolution(self):
        comp = get_user_company(self.owner)
        self.assertEqual(comp, self.company)
        role = get_user_role(self.owner)
        self.assertEqual(role, "Admin")

    def test_manager_tenant_and_role_resolution(self):
        comp = get_user_company(self.manager_user)
        self.assertEqual(comp, self.company)
        role = get_user_role(self.manager_user)
        self.assertEqual(role, "Manager")

    def test_cashier_tenant_and_role_resolution(self):
        comp = get_user_company(self.cashier_user)
        self.assertEqual(comp, self.company)
        role = get_user_role(self.cashier_user)
        self.assertEqual(role, "Cashier")

    def test_unaffiliated_user_has_no_company(self):
        comp = get_user_company(self.unaffiliated)
        self.assertIsNone(comp)
        role = get_user_role(self.unaffiliated)
        self.assertIsNone(role)
