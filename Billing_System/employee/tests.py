from decimal import Decimal
from django.test import TestCase, Client
from django.contrib.auth.models import User
from django.urls import reverse
from accounts.models import companyRegistration
from employee.models import user_registration, SalaryPayment
from inventory.models import Category, Supplier, Product


class TeamAndRoleBBACTests(TestCase):
    def setUp(self):
        self.client = Client()

        # 1. Company and Owner
        self.owner_user = User.objects.create_user(
            username="test_owner",
            password="password123",
            first_name="Alice",
            last_name="Owner"
        )
        self.company = companyRegistration.objects.create(
            owner=self.owner_user,
            company_name="SuperMart Retail",
            address_line="123 Main St",
            city="Metropolis",
            state="Central",
            pincode="123456",
            phone="9876543210",
            company_email="owner@supermart.com",
            gst_number="27ABCDE1234F1Z5"
        )

        # 2. Manager Employee
        self.mgr_user = User.objects.create_user(
            username="test_manager",
            password="password123",
            first_name="Bob",
            last_name="Manager"
        )
        self.mgr_emp = user_registration.objects.create(
            user=self.mgr_user,
            company=self.company,
            Phone="9876543211",
            employee_id="MGR-001",
            Role="Manager",
            joining_date="2025-01-01",
            monthaly_salary=Decimal("45000.00"),
            payment="Paid",
            status="active"
        )

        # 3. Cashier Employee
        self.cashier_user = User.objects.create_user(
            username="test_cashier",
            password="password123",
            first_name="Charlie",
            last_name="Cashier"
        )
        self.cashier_emp = user_registration.objects.create(
            user=self.cashier_user,
            company=self.company,
            Phone="9876543212",
            employee_id="CSH-001",
            Role="Cashier",
            joining_date="2025-02-01",
            monthaly_salary=Decimal("22000.00"),
            payment="Paid",
            status="active"
        )

        # 4. Stock Manager Employee
        self.stock_user = User.objects.create_user(
            username="test_stock",
            password="password123",
            first_name="David",
            last_name="Stock"
        )
        self.stock_emp = user_registration.objects.create(
            user=self.stock_user,
            company=self.company,
            Phone="9876543213",
            employee_id="STK-001",
            Role="Stock Manager",
            joining_date="2025-03-01",
            monthaly_salary=Decimal("28000.00"),
            payment="Due",
            status="active"
        )

    def test_owner_can_view_team_with_salaries(self):
        """Owner can view team management and receives salary and payroll context."""
        self.client.force_login(self.owner_user)
        response = self.client.get(reverse("view_employee"))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["is_owner"])
        self.assertIsNotNone(response.context["Total_monthly_salary"])
        self.assertGreater(response.context["Total_monthly_salary"], Decimal("0.00"))
        # Check rendered salary amount in response content
        self.assertContains(response, "45000.00")

    def test_manager_can_view_team_without_salaries(self):
        """Manager can view team members but must NOT receive or see salary values."""
        self.client.force_login(self.mgr_user)
        response = self.client.get(reverse("view_employee"))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["is_owner"])
        self.assertIsNone(response.context["Total_monthly_salary"])
        # Content must NOT contain the salary values
        self.assertNotContains(response, "45000.00")
        self.assertNotContains(response, "22000.00")
        self.assertNotContains(response, "28000.00")
        self.assertNotContains(response, "Total Payroll (Mo)")

    def test_cashier_is_forbidden_from_team_management(self):
        """Cashier must receive 403 Forbidden on all team management endpoints."""
        self.client.force_login(self.cashier_user)
        res_view = self.client.get(reverse("view_employee"))
        self.assertEqual(res_view.status_code, 403)

        res_add = self.client.get(reverse("employee"))
        self.assertEqual(res_add.status_code, 403)

        res_edit = self.client.get(reverse("edit_employee", kwargs={"employee_id": "CSH-001"}))
        self.assertEqual(res_edit.status_code, 403)

        res_del = self.client.get(reverse("delete_employee", kwargs={"employee_id": "CSH-001"}))
        self.assertEqual(res_del.status_code, 403)

    def test_manager_can_add_team_member_operational_only(self):
        """Manager can register a new team member, but salary is forced to 0 / Unpaid."""
        self.client.force_login(self.mgr_user)
        post_data = {
            "first_name": "Eve",
            "last_name": "Worker",
            "username": "eve_worker",
            "email": "eve@supermart.com",
            "password": "workerpass123",
            "confirm_pass": "workerpass123",
            "employee_id": "WRK-001",
            "Role": "Worker",
            "joining_date": "2025-04-01",
            "status": "active",
            "Phone": "9876543299",
            "Alternate_phone": "",
            "address": "456 Market Road",
            "Gender": "Female",
            "monthaly_salary": "35000.00",  # Manager attempts to set salary
            "payment": "Paid",
        }
        response = self.client.post(reverse("employee"), post_data)
        self.assertEqual(response.status_code, 302)

        # Check created employee
        new_emp = user_registration.objects.get(employee_id="WRK-001")
        self.assertEqual(new_emp.user.username, "eve_worker")
        self.assertEqual(new_emp.Role, "Worker")
        # Salary must be forced to 0 for Manager creation
        self.assertEqual(new_emp.monthaly_salary, Decimal("0.00"))
        self.assertEqual(new_emp.payment, "Unpaid")

    def test_manager_cannot_delete_team_member(self):
        """Manager is forbidden from deleting a team member."""
        self.client.force_login(self.mgr_user)
        res_del = self.client.post(reverse("delete_employee", kwargs={"employee_id": "CSH-001"}))
        self.assertEqual(res_del.status_code, 403)
        self.assertTrue(user_registration.objects.filter(employee_id="CSH-001").exists())

    def test_owner_can_delete_team_member(self):
        """Owner can successfully delete a team member."""
        self.client.force_login(self.owner_user)
        res_del = self.client.post(reverse("delete_employee", kwargs={"employee_id": "CSH-001"}))
        self.assertEqual(res_del.status_code, 302)
        self.assertFalse(user_registration.objects.filter(employee_id="CSH-001").exists())

    def test_cashier_is_forbidden_from_inventory_management(self):
        """Cashier must be blocked from catalog, category and supplier management pages."""
        self.client.force_login(self.cashier_user)
        res_prod = self.client.get(reverse("product"))
        self.assertEqual(res_prod.status_code, 403)

        res_cat = self.client.get(reverse("category"))
        self.assertEqual(res_cat.status_code, 403)

        res_sup = self.client.get(reverse("supplier"))
        self.assertEqual(res_sup.status_code, 403)

    def test_manager_can_access_inventory_management(self):
        """Manager can access product catalog, categories and suppliers."""
        self.client.force_login(self.mgr_user)
        self.assertEqual(self.client.get(reverse("product")).status_code, 200)
        self.assertEqual(self.client.get(reverse("category")).status_code, 200)
        self.assertEqual(self.client.get(reverse("supplier")).status_code, 200)

    def test_settings_rbac(self):
        """Owner has full settings access, Manager has appearance/general, Cashier is forbidden."""
        # Cashier
        self.client.force_login(self.cashier_user)
        self.assertEqual(self.client.get(reverse("settings_general")).status_code, 403)
        self.assertEqual(self.client.get(reverse("settings_appearance")).status_code, 403)
        self.assertEqual(self.client.get(reverse("settings_company")).status_code, 403)

        # Manager
        self.client.force_login(self.mgr_user)
        self.assertEqual(self.client.get(reverse("settings_general")).status_code, 200)
        self.assertEqual(self.client.get(reverse("settings_appearance")).status_code, 200)
        self.assertEqual(self.client.get(reverse("settings_company")).status_code, 403)  # Forbidden for Manager!

        # Owner
        self.client.force_login(self.owner_user)
        self.assertEqual(self.client.get(reverse("settings_general")).status_code, 200)
        self.assertEqual(self.client.get(reverse("settings_appearance")).status_code, 200)
        self.assertEqual(self.client.get(reverse("settings_company")).status_code, 200)

    def test_dashboard_team_bar_and_payroll_visibility(self):
        """Dashboard shows team bar for both, but only Owner sees payroll amounts."""
        # Manager
        self.client.force_login(self.mgr_user)
        res_mgr = self.client.get(reverse("manager_dashboard"))
        self.assertEqual(res_mgr.status_code, 200)
        self.assertEqual(res_mgr.context["total_team"], 3)
        self.assertIsNone(res_mgr.context["payroll_data"])
        self.assertNotContains(res_mgr, "PAYROLL THIS MONTH")

        # Owner
        self.client.force_login(self.owner_user)
        res_owner = self.client.get(reverse("dashboard"))
        self.assertEqual(res_owner.status_code, 200)
        self.assertEqual(res_owner.context["total_team"], 3)
        self.assertIsNotNone(res_owner.context["payroll_data"])
        self.assertContains(res_owner, "PAYROLL THIS MONTH")


class PayrollHistoryAndPayslipTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.owner = User.objects.create_user(
            username="owner_pay", password="password123", first_name="Owen", last_name="Store"
        )
        self.company = companyRegistration.objects.create(
            owner=self.owner,
            company_name="Apex Retail Corp",
            address_line="10 Market St",
            city="Mumbai",
            state="Maharashtra",
            pincode="400001",
            phone="9876543210",
            company_email="owen@apex.com",
            gst_number="27ABCDE1234F1Z5"
        )

        # Other company for tenancy isolation tests
        self.other_owner = User.objects.create_user(
            username="other_owner", password="password123"
        )
        self.other_company = companyRegistration.objects.create(
            owner=self.other_owner,
            company_name="Other Retail Ltd",
            phone="9876543299",
            gst_number="27XYZDE1234F1Z5"
        )

        # Employees
        self.emp1_user = User.objects.create_user(username="emp_john", password="password123")
        self.emp1 = user_registration.objects.create(
            user=self.emp1_user,
            company=self.company,
            employee_id="EMP-001",
            Role="Cashier",
            joining_date="2025-01-15",
            monthaly_salary=Decimal("20000.00"),
            payment="Paid",
            status="active"
        )

        self.emp2_user = User.objects.create_user(username="emp_sara", password="password123")
        self.emp2 = user_registration.objects.create(
            user=self.emp2_user,
            company=self.company,
            employee_id="EMP-002",
            Role="Manager",
            joining_date="2025-02-01",
            monthaly_salary=Decimal("50000.00"),
            payment="Paid",
            status="active"
        )

        self.emp3_user = User.objects.create_user(username="emp_mike", password="password123")
        self.emp3 = user_registration.objects.create(
            user=self.emp3_user,
            company=self.company,
            employee_id="EMP-003",
            Role="Worker",
            joining_date="2025-03-01",
            monthaly_salary=Decimal("15000.00"),
            payment="Due",
            status="inactive"  # Inactive employee
        )

    def test_payroll_history_metrics_and_period_filtering(self):
        """Owner can view payroll history with exact calculations for April 2025 to April 2026."""
        # Create salary payment records for April 2025
        SalaryPayment.objects.create(
            company=self.company,
            employee=self.emp1,
            year=2025,
            month=4,
            basic_salary=Decimal("20000.00"),
            gross_salary=Decimal("20000.00"),
            net_payable=Decimal("20000.00"),
            paid_amount=Decimal("20000.00"),
            payment_status="Paid",
            payment_method="Bank Transfer",
            payment_reference="TXN-202504-001"
        )
        # Emp2 has partial payment for April 2025
        SalaryPayment.objects.create(
            company=self.company,
            employee=self.emp2,
            year=2025,
            month=4,
            basic_salary=Decimal("50000.00"),
            gross_salary=Decimal("50000.00"),
            net_payable=Decimal("50000.00"),
            paid_amount=Decimal("25000.00"),
            payment_status="Partial",
            payment_method="Cash",
            payment_reference="TXN-202504-002"
        )

        self.client.force_login(self.owner)
        response = self.client.get(reverse("payroll_history"), {"year": 2025, "month": 4})
        self.assertEqual(response.status_code, 200)

        ctx = response.context
        self.assertEqual(ctx["selected_year"], 2025)
        self.assertEqual(ctx["selected_month"], 4)
        self.assertEqual(ctx["eligible_count"], 2)  # emp1 & emp2 active
        self.assertEqual(ctx["paid_count"], 1)      # emp1 fully paid
        self.assertEqual(ctx["partial_count"], 1)   # emp2 partial
        self.assertEqual(ctx["total_payable"], Decimal("70000.00"))
        self.assertEqual(ctx["total_paid"], Decimal("45000.00"))
        self.assertEqual(ctx["total_pending"], Decimal("25000.00"))
        self.assertAlmostEqual(float(ctx["completion_pct"]), 64.3, places=1)

    def test_payroll_history_rbac(self):
        """Manager and Cashier are forbidden from accessing payroll history."""
        self.client.force_login(self.emp2_user)  # Manager
        self.assertEqual(self.client.get(reverse("payroll_history")).status_code, 403)

        self.client.force_login(self.emp1_user)  # Cashier
        self.assertEqual(self.client.get(reverse("payroll_history")).status_code, 403)

    def test_payslip_view_and_permissions(self):
        """Owner can view payslip; non-owners are strictly blocked by RBAC."""
        SalaryPayment.objects.create(
            company=self.company,
            employee=self.emp1,
            year=2025,
            month=5,
            basic_salary=Decimal("20000.00"),
            gross_salary=Decimal("20000.00"),
            net_payable=Decimal("20000.00"),
            paid_amount=Decimal("20000.00"),
            payment_status="Paid"
        )

        # 1. Owner views payslip
        self.client.force_login(self.owner)
        res_owner = self.client.get(
            reverse("employee_payslip", kwargs={"employee_id": self.emp1.employee_id}),
            {"year": 2025, "month": 5}
        )
        self.assertEqual(res_owner.status_code, 200)
        self.assertContains(res_owner, "EMP-001")
        self.assertContains(res_owner, "Salary Payslip")

        # 2. Cashier is forbidden from payroll records
        self.client.force_login(self.emp1_user)
        res_self = self.client.get(
            reverse("employee_payslip", kwargs={"employee_id": self.emp1.employee_id}),
            {"year": 2025, "month": 5}
        )
        self.assertEqual(res_self.status_code, 403)

        # 3. Manager is forbidden from payroll records
        self.client.force_login(self.emp2_user)
        res_other = self.client.get(
            reverse("employee_payslip", kwargs={"employee_id": self.emp1.employee_id}),
            {"year": 2025, "month": 5}
        )
        self.assertEqual(res_other.status_code, 403)

    def test_future_joining_date_is_rejected(self):
        """Registering an employee with a future joining date is rejected."""
        self.client.force_login(self.owner)
        post_data = {
            "first_name": "Future",
            "last_name": "Employee",
            "username": "future_emp",
            "email": "future@apex.com",
            "password": "pass123456",
            "confirm_pass": "pass123456",
            "employee_id": "FUT-001",
            "Role": "Cashier",
            "joining_date": "2099-01-01",  # Future date
            "status": "active",
            "Phone": "9876543299",
            "monthaly_salary": "25000.00",
            "payment": "Paid",
        }
        res = self.client.post(reverse("employee"), post_data)
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "cannot be in the future")
        self.assertFalse(user_registration.objects.filter(employee_id="FUT-001").exists())

    def test_payroll_tenancy_isolation(self):
        """Salary payment from Company A cannot be viewed or leaked to Company B."""
        other_emp_user = User.objects.create_user(username="other_cashier", password="password123")
        other_emp = user_registration.objects.create(
            user=other_emp_user,
            company=self.other_company,
            employee_id="OTH-001",
            Role="Cashier",
            joining_date="2025-01-01",
            monthaly_salary=Decimal("30000.00")
        )
        SalaryPayment.objects.create(
            company=self.other_company,
            employee=other_emp,
            year=2025,
            month=6,
            basic_salary=Decimal("30000.00"),
            gross_salary=Decimal("30000.00"),
            net_payable=Decimal("30000.00"),
            paid_amount=Decimal("30000.00"),
            payment_status="Paid"
        )

        self.client.force_login(self.owner)
        res = self.client.get(reverse("employee_payslip", kwargs={"employee_id": "OTH-001"}))
        self.assertEqual(res.status_code, 404)

