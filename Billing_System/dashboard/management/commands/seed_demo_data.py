import calendar
from datetime import date, datetime, timedelta
from decimal import Decimal
import random

from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from django.db import transaction
from django.utils import timezone

from accounts.models import companyRegistration
from inventory.models import Category, Supplier, Product
from employee.models import user_registration, SalaryPayment
from purchases.models import Purchase, PurchaseItem
from sales.models import Customer, Sale, SaleItem, Payment


class Command(BaseCommand):
    help = "Seed realistic, internally consistent college demonstration dataset for Retail POS."

    def add_arguments(self, parser):
        parser.add_argument(
            "--company-name",
            type=str,
            default="Apex Retail Supermarket",
            help="Name of the demonstration company."
        )
        parser.add_argument(
            "--owner-username",
            type=str,
            default="apex_owner",
            help="Username for the demo owner."
        )
        parser.add_argument(
            "--password",
            type=str,
            default="ApexDemo@2026",
            help="Default password for demonstration accounts."
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Simulate generation without writing to database."
        )

    def handle(self, *args, **options):
        company_name = options["company_name"]
        owner_username = options["owner_username"]
        password = options["password"]
        dry_run = options["dry_run"]

        self.stdout.write(self.style.MIGRATE_HEADING("=== SEEDING COLLEGE DEMO DATASET ==="))
        self.stdout.write(f"Target Store: {company_name}")
        self.stdout.write(f"Owner User:   {owner_username}")
        self.stdout.write(f"Dry Run:      {dry_run}")

        if dry_run:
            self.stdout.write(self.style.SUCCESS("Dry-run validation successful. No database records written."))
            return

        with transaction.atomic():
            # 1. Company & Owner
            owner_user, created_user = User.objects.get_or_create(
                username=owner_username,
                defaults={
                    "first_name": "Vikram",
                    "last_name": "Mehta",
                    "email": "vikram.mehta@apexretail.in",
                    "is_staff": True,
                }
            )
            if created_user:
                owner_user.set_password(password)
                owner_user.save()
            else:
                self.stdout.write(self.style.WARNING(f"Existing user '{owner_username}' found. Existing credentials preserved."))

            company, created = companyRegistration.objects.get_or_create(
                owner=owner_user,
                defaults={
                    "company_name": company_name,
                    "address_line": "Shop 4-5, High Street Commercial Plaza",
                    "city": "Pune",
                    "state": "Maharashtra",
                    "country": "India",
                    "pincode": "411001",
                    "company_email": "contact@apexretail.in",
                    "phone": "9822012345",
                    "alternate_phone": "9822054321",
                    "gst_number": "27AAPCA1234F1Z5",
                    "pan_number": "AAPCA1234F",
                    "business_info": "Premier Neighborhood Grocery, FMCG & Daily Essentials Supermarket.",
                }
            )
            if not created:
                self.stdout.write(f"Using existing company: {company.company_name} (id={company.id})")

            # 2. Staff Accounts
            staff_specs = [
                {
                    "username": "apex_mgr",
                    "first_name": "Rohan",
                    "last_name": "Deshmukh",
                    "role": "Manager",
                    "emp_id": "APX-MGR01",
                    "joining_date": date(2025, 1, 10),
                    "salary": Decimal("45000.00"),
                    "phone": "9822099001",
                },
                {
                    "username": "apex_cashier1",
                    "first_name": "Ananya",
                    "last_name": "Iyer",
                    "role": "Cashier",
                    "emp_id": "APX-CSH01",
                    "joining_date": date(2025, 2, 1),
                    "salary": Decimal("25000.00"),
                    "phone": "9822099002",
                },
                {
                    "username": "apex_cashier2",
                    "first_name": "Karan",
                    "last_name": "Verma",
                    "role": "Cashier",
                    "emp_id": "APX-CSH02",
                    "joining_date": date(2025, 3, 15),
                    "salary": Decimal("24000.00"),
                    "phone": "9822099003",
                },
                {
                    "username": "apex_stock",
                    "first_name": "Suresh",
                    "last_name": "Patil",
                    "role": "Stock Manager",
                    "emp_id": "APX-STK01",
                    "joining_date": date(2025, 1, 20),
                    "salary": Decimal("28000.00"),
                    "phone": "9822099004",
                },
                {
                    "username": "apex_worker",
                    "first_name": "Deepak",
                    "last_name": "Shinde",
                    "role": "Worker",
                    "emp_id": "APX-WRK01",
                    "joining_date": date(2025, 4, 1),
                    "salary": Decimal("18000.00"),
                    "phone": "9822099005",
                },
            ]

            created_employees = []
            for spec in staff_specs:
                u, created_u = User.objects.get_or_create(
                    username=spec["username"],
                    defaults={
                        "first_name": spec["first_name"],
                        "last_name": spec["last_name"],
                        "email": f"{spec['username']}@apexretail.in",
                    }
                )
                if created_u:
                    u.set_password(password)
                    u.save()

                emp, _ = user_registration.objects.get_or_create(
                    user=u,
                    company=company,
                    defaults={
                        "employee_id": spec["emp_id"],
                        "Role": spec["role"],
                        "joining_date": spec["joining_date"],
                        "monthaly_salary": spec["salary"],
                        "payment": "Paid",
                        "status": "active",
                        "Phone": spec["phone"],
                        "Alternate_phone": spec["phone"],
                        "address": "Pune, Maharashtra",
                        "Gender": "Male" if spec["role"] != "Cashier" or "Ananya" not in spec["first_name"] else "Female",
                    }
                )
                created_employees.append(emp)

            self.stdout.write(f"Seeded {len(created_employees)} employees.")

            # 3. Suppliers
            supplier_specs = [
                ("SUP-HUL01", "Hindustan Unilever Distribution", "hul.dist@pune.in", "9823000001"),
                ("SUP-NES01", "Nestle Consumer Supply Co.", "nestle.supply@pune.in", "9823000002"),
                ("SUP-ITC01", "ITC Foods & Essentials Ltd.", "itc.foods@pune.in", "9823000003"),
                ("SUP-AML01", "Amul Dairy Cooperative Logistics", "amul.logistics@pune.in", "9823000004"),
            ]
            suppliers = []
            for code, name, email, phone in supplier_specs:
                s, _ = Supplier.objects.get_or_create(
                    company=company,
                    name=name,
                    defaults={"phone": phone, "email": email, "address": "Market Yard, Pune"}
                )
                suppliers.append(s)

            # 4. Categories
            category_names = ["Groceries & Staples", "Beverages & Drinks", "Dairy & Refrigerated", "Snacks & Biscuits", "Home & Personal Care"]
            categories = {}
            for cname in category_names:
                cat, _ = Category.objects.get_or_create(company=company, name=cname)
                categories[cname] = cat

            # 5. Products & Barcodes
            product_specs = [
                ("8901030383848", "Tata Tea Premium 500g", "Groceries & Staples", suppliers[0], Decimal("175.00"), Decimal("220.00"), Decimal("65.00"), Decimal("15.00")),
                ("8901063012227", "Britannia Good Day Cookies 120g", "Snacks & Biscuits", suppliers[2], Decimal("28.00"), Decimal("35.00"), Decimal("120.00"), Decimal("20.00")),
                ("8901491101837", "Maggi 2-Minute Noodles 280g", "Groceries & Staples", suppliers[1], Decimal("42.00"), Decimal("54.00"), Decimal("95.00"), Decimal("15.00")),
                ("8901725181222", "Amul Taaza Homogenised Milk 1L", "Dairy & Refrigerated", suppliers[3], Decimal("58.00"), Decimal("72.00"), Decimal("45.00"), Decimal("10.00")),
                ("8901058852332", "Nescafe Classic Coffee 100g", "Beverages & Drinks", suppliers[1], Decimal("220.00"), Decimal("275.00"), Decimal("40.00"), Decimal("10.00")),
                ("8902080000018", "Fortune Sunlite Sunflower Oil 1L", "Groceries & Staples", suppliers[2], Decimal("125.00"), Decimal("155.00"), Decimal("80.00"), Decimal("15.00")),
                ("8901233024888", "Dettol Antiseptic Liquid 250ml", "Home & Personal Care", suppliers[0], Decimal("110.00"), Decimal("138.00"), Decimal("50.00"), Decimal("10.00")),
                ("8901030018443", "Surf Excel Easy Wash Detergent 1kg", "Home & Personal Care", suppliers[0], Decimal("118.00"), Decimal("145.00"), Decimal("60.00"), Decimal("15.00")),
                ("8901499008891", "Cadbury Dairy Milk Silk 150g", "Snacks & Biscuits", suppliers[1], Decimal("140.00"), Decimal("175.00"), Decimal("55.00"), Decimal("10.00")),
                ("8901725131012", "Amul Butter Pasteurized 500g", "Dairy & Refrigerated", suppliers[3], Decimal("225.00"), Decimal("275.00"), Decimal("35.00"), Decimal("8.00")),
                ("8906007280016", "Parle-G Glucose Biscuits 800g", "Snacks & Biscuits", suppliers[2], Decimal("68.00"), Decimal("85.00"), Decimal("110.00"), Decimal("25.00")),
                ("8901058863116", "KitKat 4-Finger Wafer 38g", "Snacks & Biscuits", suppliers[1], Decimal("22.00"), Decimal("30.00"), Decimal("75.00"), Decimal("15.00")),
                ("8902080012028", "Aashirvaad Shudh Chakki Atta 5kg", "Groceries & Staples", suppliers[2], Decimal("210.00"), Decimal("260.00"), Decimal("40.00"), Decimal("10.00")),
                ("8901764012211", "Coca-Cola PET Bottle 750ml", "Beverages & Drinks", suppliers[1], Decimal("32.00"), Decimal("40.00"), Decimal("85.00"), Decimal("20.00")),
                ("8901233010119", "Colgate Total Toothpaste 120g", "Home & Personal Care", suppliers[0], Decimal("82.00"), Decimal("105.00"), Decimal("70.00"), Decimal("15.00")),
            ]

            seeded_products = []
            for barcode, pname, catname, supp, pprice, sprice, stock, min_stock in product_specs:
                prod, _ = Product.objects.get_or_create(
                    company=company,
                    barcode=barcode,
                    defaults={
                        "name": pname,
                        "product_code": f"PROD-{barcode[-6:]}",
                        "category": categories[catname],
                        "supplier": supp,
                        "purchase_price": pprice,
                        "selling_price": sprice,
                        "current_stock": stock,
                        "minimum_stock": min_stock,
                        "status": True,
                        "unit": "pcs",
                        "gst_percentage": Decimal("5.00"),
                    }
                )
                seeded_products.append(prod)

            self.stdout.write(f"Seeded {len(seeded_products)} products with valid EAN-13 barcodes.")

            # 6. Customers
            customer_specs = [
                ("Rahul Sharma", "9822011223", "rahul.s@gmail.com"),
                ("Priya Patel", "9822044556", "priya.p@gmail.com"),
                ("Amit Kulkarni", "9822077889", "amit.k@gmail.com"),
                ("Sneha Deshmukh", "9822099001", "sneha.d@gmail.com"),
            ]
            customers = []
            for cname, cphone, cemail in customer_specs:
                c, _ = Customer.objects.get_or_create(
                    company=company,
                    phone=cphone,
                    defaults={"name": cname, "email": cemail, "credit_balance": Decimal("0.00")}
                )
                customers.append(c)

            # 7. Completed Sales and Transactions
            cashier_user = created_employees[1].user  # apex_cashier1
            today = timezone.now().date()
            if not Sale.objects.filter(company=company).exists():
                for idx in range(1, 6):
                    sale_date = timezone.now() - timedelta(days=5 - idx, hours=2)
                    cust = customers[idx % len(customers)]
                    sale_no = f"INV-APX-{idx:04d}"

                    p1 = seeded_products[idx % len(seeded_products)]
                    p2 = seeded_products[(idx + 2) % len(seeded_products)]

                    qty1 = Decimal("2.00")
                    qty2 = Decimal("1.00")
                    subtotal = (p1.selling_price * qty1) + (p2.selling_price * qty2)
                    gst = subtotal * Decimal("0.05")
                    grand_total = subtotal + gst

                    sale = Sale.objects.create(
                        company=company,
                        customer=cust,
                        cashier=cashier_user,
                        sale_number=sale_no,
                        subtotal=subtotal,
                        tax_amount=gst,
                        discount_amount=Decimal("0.00"),
                        grand_total=grand_total,
                        paid_amount=grand_total,
                        payment_status="PAID",
                        sale_status="COMPLETED",
                        created_at=sale_date,
                    )

                    SaleItem.objects.create(
                        sale=sale,
                        product=p1,
                        product_name=p1.name,
                        product_code=p1.product_code,
                        quantity=qty1,
                        unit_price=p1.selling_price,
                        cost_price=p1.purchase_price,
                        subtotal=p1.selling_price * qty1,
                        tax_amount=(p1.selling_price * qty1) * Decimal("0.05"),
                        total=(p1.selling_price * qty1) * Decimal("1.05"),
                        gst_percentage=Decimal("5.00"),
                    )

                    SaleItem.objects.create(
                        sale=sale,
                        product=p2,
                        product_name=p2.name,
                        product_code=p2.product_code,
                        quantity=qty2,
                        unit_price=p2.selling_price,
                        cost_price=p2.purchase_price,
                        subtotal=p2.selling_price * qty2,
                        tax_amount=(p2.selling_price * qty2) * Decimal("0.05"),
                        total=(p2.selling_price * qty2) * Decimal("1.05"),
                        gst_percentage=Decimal("5.00"),
                    )

                    Payment.objects.create(
                        company=company,
                        sale=sale,
                        amount=grand_total,
                        payment_method="UPI" if idx % 2 == 0 else "CASH",
                        received_by=cashier_user,
                    )
                self.stdout.write("Seeded 5 sample checkout sales and invoices.")

            # 8. Salary History (April 2025 through March 2026)
            payroll_records_created = 0
            # Target range: April 2025 to March 2026
            history_periods = []
            # 2025 months 4..12
            for m in range(4, 13):
                history_periods.append((2025, m))
            # 2026 months 1..3
            for m in range(1, 4):
                history_periods.append((2026, m))

            for yr, mo in history_periods:
                _, last_day = calendar.monthrange(yr, mo)
                period_end = date(yr, mo, last_day)

                for emp in created_employees:
                    if emp.joining_date <= period_end:
                        basic = emp.monthaly_salary
                        allowances = Decimal("1500.00") if emp.Role in ["Manager", "Stock Manager"] else Decimal("500.00")
                        deductions = Decimal("200.00")  # Standard professional tax
                        gross = basic + allowances
                        net = gross - deductions

                        # Create realistic disbursement patterns
                        # Early months: all Paid; recent months: some Paid, some Partial
                        if yr == 2026 and mo == 3:
                            # Partially disbursed in March 2026
                            paid = net if emp.Role in ["Worker", "Cashier"] else (net - Decimal("10000.00"))
                            pstatus = "Paid" if paid >= net else "Partial"
                        else:
                            paid = net
                            pstatus = "Paid"

                        ref = f"DEMO-SEED-NEFT-{yr}{mo:02d}-{emp.employee_id}"
                        pdate = date(yr, mo, min(28, last_day))

                        SalaryPayment.objects.update_or_create(
                            company=company,
                            employee=emp,
                            year=yr,
                            month=mo,
                            defaults={
                                "basic_salary": basic,
                                "allowances": allowances,
                                "deductions": deductions,
                                "gross_salary": gross,
                                "net_payable": net,
                                "paid_amount": paid,
                                "payment_status": pstatus,
                                "payment_method": "Bank Transfer",
                                "payment_reference": ref,
                                "payment_date": pdate,
                                "notes": f"[DEMO SEED] Educational presentation salary record for {calendar.month_name[mo]} {yr}.",
                            }
                        )
                        payroll_records_created += 1

            self.stdout.write(f"Seeded {payroll_records_created} monthly salary history records.")

        self.stdout.write(self.style.SUCCESS("\n=== DEMO DATASET CREATED SUCCESSFULLY ==="))
        self.stdout.write("Safe Test Credentials:")
        self.stdout.write(f"  Owner:    username: '{owner_username}'     password: '{password}'")
        self.stdout.write(f"  Manager:  username: 'apex_mgr'        password: '{password}'")
        self.stdout.write(f"  Cashier:  username: 'apex_cashier1'   password: '{password}'")
        self.stdout.write("Barcode Samples:")
        self.stdout.write("  8901030383848 (Tata Tea Premium 500g)")
        self.stdout.write("  8901063012227 (Britannia Good Day Cookies 120g)")
        self.stdout.write("  8901491101837 (Maggi 2-Minute Noodles 280g)")
        self.stdout.write("  8901725181222 (Amul Taaza Milk 1L)")
        self.stdout.write("  8902080000018 (Fortune Sunflower Oil 1L)")
