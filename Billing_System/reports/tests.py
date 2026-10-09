from decimal import Decimal
import datetime
from django.test import TestCase
from django.contrib.auth.models import User
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework import status

from accounts.models import companyRegistration
from employee.models import user_registration
from inventory.models import Category, Supplier, Product, StockMovement
from purchases.models import Purchase, PurchaseItem
from sales.models import Customer, Sale, SaleItem, Payment, Invoice


class ReportsAPITests(TestCase):
    def setUp(self):
        # 1. Company A & Users
        self.owner_a = User.objects.create_user(username="owner_comp_a", password="password123")
        self.company_a = companyRegistration.objects.create(
            owner=self.owner_a,
            company_name="Alpha Retail Ltd",
            address_line="100 MG Road",
            city="Bengaluru",
            state="Karnataka",
            country="India",
            pincode="560001",
            company_email="alpha@example.com",
            phone="9100000001",
            alternate_phone="9100000002",
            gst_number="29AAAAA0000A1Z1",
            business_info="Retail Superstore"
        )

        self.manager_a_user = User.objects.create_user(username="manager_comp_a", password="password123")
        self.manager_a_emp = user_registration.objects.create(
            user=self.manager_a_user,
            company=self.company_a,
            employee_id="MGR01",
            Role="Manager",
            Phone="9100000003",
            Alternate_phone="9100000004",
            address="Manager quarters",
            joining_date="2026-01-01",
            monthaly_salary=45000.00,
            payment="Paid",
            status="active"
        )

        self.cashier_a_user = User.objects.create_user(username="cashier_comp_a", password="password123")
        self.cashier_a_emp = user_registration.objects.create(
            user=self.cashier_a_user,
            company=self.company_a,
            employee_id="CSH01",
            Role="Cashier",
            Phone="9100000005",
            Alternate_phone="9100000006",
            address="Cashier quarters",
            joining_date="2026-01-05",
            monthaly_salary=22000.00,
            payment="Paid",
            status="active"
        )

        # 2. Company B & Users (for tenant isolation tests)
        self.owner_b = User.objects.create_user(username="owner_comp_b", password="password123")
        self.company_b = companyRegistration.objects.create(
            owner=self.owner_b,
            company_name="Beta Retailers",
            address_line="200 Brigade Road",
            city="Bengaluru",
            state="Karnataka",
            country="India",
            pincode="560025",
            company_email="beta@example.com",
            phone="9200000001",
            alternate_phone="9200000002",
            gst_number="29BBBBB0000B1Z2",
            business_info="Beta Electronics"
        )

        # 3. Company A Catalog & Inventory
        self.cat_a = Category.objects.create(company=self.company_a, name="Beverages")
        self.sup_a = Supplier.objects.create(company=self.company_a, name="Alpha Supplier", phone="9100000007")

        self.prod_a1 = Product.objects.create(
            company=self.company_a,
            category=self.cat_a,
            supplier=self.sup_a,
            name="Organic Orange Juice",
            product_code="OJ-100",
            purchase_price=Decimal("40.00"),
            selling_price=Decimal("80.00"),
            current_stock=Decimal("150.00"),
            minimum_stock=Decimal("20.00"),
            gst_percentage=Decimal("18.00"),
            status=True
        )

        self.prod_a2 = Product.objects.create(
            company=self.company_a,
            category=self.cat_a,
            supplier=self.sup_a,
            name="Sparkling Water",
            product_code="SW-200",
            purchase_price=Decimal("15.00"),
            selling_price=Decimal("30.00"),
            current_stock=Decimal("5.00"),  # Low stock (<= minimum_stock 10)
            minimum_stock=Decimal("10.00"),
            gst_percentage=Decimal("5.00"),
            status=True
        )

        # Stock movements
        StockMovement.objects.create(
            company=self.company_a,
            product=self.prod_a1,
            movement_type="PURCHASE",
            quantity=Decimal("150.00"),
            previous_stock=Decimal("0.00"),
            new_stock=Decimal("150.00"),
            reference="PO-001",
            user=self.owner_a
        )

        # 4. Company A Customer & Sales
        self.cust_a = Customer.objects.create(
            company=self.company_a,
            name="Rahul Sharma",
            phone="9876543210",
            credit_balance=Decimal("500.00"),
            credit_limit=Decimal("5000.00")
        )

        self.sale_a = Sale.objects.create(
            company=self.company_a,
            customer=self.cust_a,
            cashier=self.cashier_a_user,
            sale_number="SALE-A-001",
            subtotal=Decimal("160.00"),
            discount_type="FLAT",
            discount_value=Decimal("10.00"),
            discount_amount=Decimal("10.00"),
            tax_amount=Decimal("27.00"),
            cgst_amount=Decimal("13.50"),
            sgst_amount=Decimal("13.50"),
            igst_amount=Decimal("0.00"),
            grand_total=Decimal("177.00"),
            paid_amount=Decimal("177.00"),
            balance_due=Decimal("0.00"),
            sale_status="COMPLETED",
            payment_status="PAID"
        )

        SaleItem.objects.create(
            sale=self.sale_a,
            product=self.prod_a1,
            product_name=self.prod_a1.name,
            product_code=self.prod_a1.product_code,
            quantity=Decimal("2.00"),
            unit_price=Decimal("80.00"),
            cost_price=Decimal("40.00"),
            gst_percentage=Decimal("18.00"),
            subtotal=Decimal("160.00"),
            discount_amount=Decimal("10.00"),
            tax_amount=Decimal("27.00"),
            total=Decimal("177.00")
        )

        Payment.objects.create(
            company=self.company_a,
            sale=self.sale_a,
            payment_method="CASH",
            amount=Decimal("177.00"),
            received_by=self.cashier_a_user
        )

        # 5. Company A Purchases
        today = timezone.now().date()
        self.purchase_a = Purchase.objects.create(
            company=self.company_a,
            supplier=self.sup_a,
            invoice_number="PO-A-001",
            purchase_date=today,
            status="RECEIVED",
            notes="Initial stock order"
        )

        PurchaseItem.objects.create(
            purchase=self.purchase_a,
            product=self.prod_a1,
            quantity=Decimal("50.00"),
            purchase_price=Decimal("40.00"),
            gst_percentage=Decimal("18.00")
        )

        # 6. Company B Catalog & Sales (Distinct)
        self.cat_b = Category.objects.create(company=self.company_b, name="Gadgets")
        self.sup_b = Supplier.objects.create(company=self.company_b, name="Beta Supplier", phone="9200000003")
        self.prod_b = Product.objects.create(
            company=self.company_b,
            category=self.cat_b,
            supplier=self.sup_b,
            name="Beta Smartphone",
            product_code="BETA-PHONE",
            purchase_price=Decimal("8000.00"),
            selling_price=Decimal("12000.00"),
            current_stock=Decimal("10.00"),
            minimum_stock=Decimal("2.00"),
            gst_percentage=Decimal("18.00"),
            status=True
        )

        self.sale_b = Sale.objects.create(
            company=self.company_b,
            cashier=self.owner_b,
            sale_number="SALE-B-001",
            subtotal=Decimal("12000.00"),
            grand_total=Decimal("14160.00"),
            paid_amount=Decimal("14160.00"),
            balance_due=Decimal("0.00"),
            sale_status="COMPLETED",
            payment_status="PAID"
        )

        self.client = APIClient()

    # -------------------------------------------------------------
    # 1. Sales Report API Tests
    # -------------------------------------------------------------
    def test_sales_report_api_success(self):
        self.client.force_authenticate(user=self.manager_a_user)
        response = self.client.get("/api/reports/sales/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        data = response.data
        self.assertIn("summary", data)
        self.assertIn("period", data)
        self.assertIn("daily_breakdown", data)
        self.assertIn("top_products", data)
        self.assertIn("payment_method_breakdown", data)

        summary = data["summary"]
        self.assertEqual(Decimal(str(summary["total_revenue"])), Decimal("177.00"))
        self.assertEqual(Decimal(str(summary["total_subtotal"])), Decimal("160.00"))
        self.assertEqual(Decimal(str(summary["total_discount"])), Decimal("10.00"))
        self.assertEqual(Decimal(str(summary["total_tax"])), Decimal("27.00"))
        self.assertEqual(summary["transaction_count"], 1)

        # Top products
        self.assertEqual(len(data["top_products"]), 1)
        self.assertEqual(data["top_products"][0]["product_code"], "OJ-100")

        # Payment methods
        self.assertEqual(len(data["payment_method_breakdown"]), 1)
        self.assertEqual(data["payment_method_breakdown"][0]["payment_method"], "CASH")

    def test_sales_report_date_filtering(self):
        self.client.force_authenticate(user=self.manager_a_user)
        today = timezone.now().date()
        yesterday = today - datetime.timedelta(days=1)
        tomorrow = today + datetime.timedelta(days=1)

        # Valid date filter that includes today
        res = self.client.get(f"/api/reports/sales/?start_date={yesterday}&end_date={tomorrow}")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["summary"]["transaction_count"], 1)

        # Filter outside the sale range
        past_start = today - datetime.timedelta(days=30)
        past_end = today - datetime.timedelta(days=10)
        res_empty = self.client.get(f"/api/reports/sales/?start_date={past_start}&end_date={past_end}")
        self.assertEqual(res_empty.status_code, status.HTTP_200_OK)
        self.assertEqual(res_empty.data["summary"]["transaction_count"], 0)

        # Invalid date format
        res_invalid = self.client.get("/api/reports/sales/?start_date=invalid-date")
        self.assertEqual(res_invalid.status_code, status.HTTP_400_BAD_REQUEST)

        # start_date later than end_date
        res_order = self.client.get(f"/api/reports/sales/?start_date={tomorrow}&end_date={yesterday}")
        self.assertEqual(res_order.status_code, status.HTTP_400_BAD_REQUEST)

    # -------------------------------------------------------------
    # 2. Inventory Report API Tests
    # -------------------------------------------------------------
    def test_inventory_report_api_success(self):
        self.client.force_authenticate(user=self.manager_a_user)
        response = self.client.get("/api/reports/inventory/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        data = response.data
        summary = data["summary"]
        self.assertEqual(summary["total_products"], 2)
        self.assertEqual(summary["active_products"], 2)
        self.assertEqual(summary["low_stock_count"], 1)  # prod_a2 stock=5 <= min_stock=10

        # Cost value: 150*40 + 5*15 = 6000 + 75 = 6075.00
        self.assertEqual(Decimal(str(summary["total_cost_value"])), Decimal("6075.00"))
        # Selling value: 150*80 + 5*30 = 12000 + 150 = 12150.00
        self.assertEqual(Decimal(str(summary["total_selling_value"])), Decimal("12150.00"))

        # Low stock list
        self.assertEqual(len(data["low_stock_products"]), 1)
        self.assertEqual(data["low_stock_products"][0]["product_code"], "SW-200")

        # Movement by type
        self.assertTrue(len(data["movement_by_type"]) >= 1)

    # -------------------------------------------------------------
    # 3. Purchase Report API Tests
    # -------------------------------------------------------------
    def test_purchase_report_api_success(self):
        self.client.force_authenticate(user=self.manager_a_user)
        response = self.client.get("/api/reports/purchases/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        data = response.data
        summary = data["summary"]
        self.assertEqual(summary["total_purchases"], 1)
        self.assertEqual(summary["received_count"], 1)

        # Purchase subtotal: 50 * 40 = 2000.00
        self.assertEqual(Decimal(str(summary["total_subtotal"])), Decimal("2000.00"))
        # GST: 2000 * 18% = 360.00
        self.assertEqual(Decimal(str(summary["total_gst"])), Decimal("360.00"))
        self.assertEqual(Decimal(str(summary["grand_total"])), Decimal("2360.00"))

        # By supplier
        self.assertEqual(len(data["by_supplier"]), 1)
        self.assertEqual(data["by_supplier"][0]["purchase__supplier__name"], "Alpha Supplier")

    # -------------------------------------------------------------
    # 4. Financial Report API Tests
    # -------------------------------------------------------------
    def test_financial_report_api_success(self):
        self.client.force_authenticate(user=self.manager_a_user)
        response = self.client.get("/api/reports/financial/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        data = response.data
        pl = data["profit_loss"]
        # Revenue = 177.00, Tax = 27.00, Revenue ex-tax = 150.00
        self.assertEqual(Decimal(str(pl["total_revenue"])), Decimal("177.00"))
        self.assertEqual(Decimal(str(pl["total_tax_collected"])), Decimal("27.00"))
        self.assertEqual(Decimal(str(pl["revenue_ex_tax"])), Decimal("150.00"))

        # COGS: 2 qty * 40 cost = 80.00
        self.assertEqual(Decimal(str(pl["total_cogs"])), Decimal("80.00"))
        # Gross profit: 150.00 - 80.00 = 70.00
        self.assertEqual(Decimal(str(pl["gross_profit"])), Decimal("70.00"))

        # Receivables
        rec = data["receivables"]
        self.assertEqual(Decimal(str(rec["credit_outstanding_total"])), Decimal("500.00"))
        self.assertEqual(rec["credit_customers_count"], 1)

    # -------------------------------------------------------------
    # 5. Tax Report API Tests
    # -------------------------------------------------------------
    def test_tax_report_api_success(self):
        self.client.force_authenticate(user=self.manager_a_user)
        response = self.client.get("/api/reports/tax/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        data = response.data
        out_tax = data["output_tax"]
        self.assertEqual(Decimal(str(out_tax["total_tax_collected"])), Decimal("27.00"))
        self.assertEqual(Decimal(str(out_tax["total_cgst"])), Decimal("13.50"))
        self.assertEqual(Decimal(str(out_tax["total_sgst"])), Decimal("13.50"))

        inp_tax = data["input_tax"]
        # Purchase tax: 50 * 40 * 18% = 360.00
        self.assertEqual(Decimal(str(inp_tax["total_purchase_tax"])), Decimal("360.00"))

        # Net GST: 27.00 - 360.00 = -333.00
        self.assertEqual(Decimal(str(data["net_gst_payable"])), Decimal("-333.00"))

        # Tax by slab
        self.assertEqual(len(data["tax_by_slab"]), 1)
        self.assertEqual(data["tax_by_slab"][0]["gst_percentage"], Decimal("18.00"))

    # -------------------------------------------------------------
    # 6. Authorization Tests
    # -------------------------------------------------------------
    def test_report_authorization_owner_and_manager_allowed(self):
        # Owner of Company A
        self.client.force_authenticate(user=self.owner_a)
        res_owner = self.client.get("/api/reports/sales/")
        self.assertEqual(res_owner.status_code, status.HTTP_200_OK)

        # Manager of Company A
        self.client.force_authenticate(user=self.manager_a_user)
        res_mgr = self.client.get("/api/reports/sales/")
        self.assertEqual(res_mgr.status_code, status.HTTP_200_OK)

    def test_report_authorization_cashier_forbidden(self):
        # Cashier must be blocked from report APIs
        self.client.force_authenticate(user=self.cashier_a_user)
        endpoints = [
            "/api/reports/sales/",
            "/api/reports/inventory/",
            "/api/reports/purchases/",
            "/api/reports/financial/",
            "/api/reports/tax/",
        ]
        for ep in endpoints:
            res = self.client.get(ep)
            self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN, f"Failed for {ep}")

    def test_report_authorization_unauthenticated_rejected(self):
        self.client.logout()
        res = self.client.get("/api/reports/sales/")
        self.assertIn(res.status_code, [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN])

    # -------------------------------------------------------------
    # 7. Tenant Isolation Tests
    # -------------------------------------------------------------
    def test_report_tenant_isolation(self):
        # Owner of Company B should only see Company B's data, none of Company A's
        self.client.force_authenticate(user=self.owner_b)

        # Sales report for Company B
        res_sales = self.client.get("/api/reports/sales/")
        self.assertEqual(res_sales.status_code, status.HTTP_200_OK)
        # Company B has grand_total = 14160.00, not 177.00
        self.assertEqual(Decimal(str(res_sales.data["summary"]["total_revenue"])), Decimal("14160.00"))
        # Does not see Company A's top product
        prod_codes = [p["product_code"] for p in res_sales.data["top_products"]]
        self.assertNotIn("OJ-100", prod_codes)

        # Inventory report for Company B
        res_inv = self.client.get("/api/reports/inventory/")
        self.assertEqual(res_inv.status_code, status.HTTP_200_OK)
        self.assertEqual(res_inv.data["summary"]["total_products"], 1)

        # Purchase report for Company B (Company B has 0 purchases)
        res_pur = self.client.get("/api/reports/purchases/")
        self.assertEqual(res_pur.status_code, status.HTTP_200_OK)
        self.assertEqual(res_pur.data["summary"]["total_purchases"], 0)

    # -------------------------------------------------------------
    # 8. Reports Dashboard Template View Tests
    # -------------------------------------------------------------
    def test_reports_template_view_accessible(self):
        # Manager gets 200 OK
        self.client.force_login(user=self.manager_a_user)
        response = self.client.get("/reports/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "reports/reports.html")
        self.assertContains(response, "Reports")
