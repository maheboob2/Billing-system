import os
import sys
import time
import json
import threading
from decimal import Decimal

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django
django.setup()

import requests
from django.db import connection
from django.contrib.auth.models import User
from accounts.models import companyRegistration
from inventory.models import Category, Supplier, Product, StockMovement
from sales.models import Sale, SaleItem, Invoice, Payment, POSScannerSession, POSScannerScan, CheckoutIdempotency
from sales.services import SalesService

BASE_URL = "http://127.0.0.1:8000"

def get_auth_session(username, password):
    s = requests.Session()
    # Fetch login page to get CSRF token
    r = s.get(f"{BASE_URL}/accounts/")
    csrf = s.cookies.get("csrftoken")
    r_post = s.post(
        f"{BASE_URL}/accounts/",
        data={"username": username, "password": password, "csrfmiddlewaretoken": csrf},
        headers={"Referer": f"{BASE_URL}/accounts/"},
        allow_redirects=True,
    )
    assert r_post.status_code == 200, f"Login failed for {username}: status {r_post.status_code}"
    # Update csrf token from cookies
    csrf = s.cookies.get("csrftoken")
    s.headers.update({"X-CSRFToken": csrf, "Referer": f"{BASE_URL}/cart/"})
    return s

def run_all_adversarial_tests():
    print("=" * 70)
    print("ANTIGRAVITY — PHASE 2: ADVERSARIAL POS REGRESSION TEST SUITE")
    print(f"Database Engine: {connection.settings_dict['ENGINE']}")
    print(f"Database Name: {connection.settings_dict['NAME']}")
    print(f"Local Server: {BASE_URL}")
    print("=" * 70)

    # Setup or retrieve test company & users
    company = companyRegistration.objects.first()
    assert company, "No company registered in database."

    # Company 2 for tenant isolation
    company2 = companyRegistration.objects.exclude(id=company.id).first()
    if not company2:
        company2 = companyRegistration.objects.create(
            company_name="Second Tenant Co",
            email="tenant2@example.com",
            phone="9876543210",
            address_line="456 Outer Ring Rd",
            city="Bangalore",
            state="Karnataka",
            pincode="560001",
            gst_number="29AAAAA0000A1Z5",
        )

    # Ensure two cashier users for company 1
    cashier_user1 = User.objects.filter(is_active=True).first()
    cashier_user2 = User.objects.filter(is_active=True).exclude(id=cashier_user1.id).first()
    if not cashier_user2:
        cashier_user2 = User.objects.create_user(
            username="cashier_two",
            email="cashier2@example.com",
            password="pass1234_cashier2",
            first_name="Counter",
            last_name="Two",
        )
        from employee.models import User_Registration
        User_Registration.objects.create(
            user=cashier_user2,
            company=company,
            role="Cashier",
            phone="9111111112",
        )
    else:
        # Reset password to known test password
        cashier_user2.set_password("pass1234_cashier2")
        cashier_user2.save()

    cashier_user1.set_password("pass1234")
    cashier_user1.save()

    # User for company 2
    user_comp2 = User.objects.filter(user_registration__company=company2).first()
    if not user_comp2:
        user_comp2 = User.objects.create_user(
            username="comp2_cashier",
            email="comp2_cashier@example.com",
            password="pass1234_comp2",
        )
        from employee.models import User_Registration
        User_Registration.objects.create(
            user=user_comp2,
            company=company2,
            role="Cashier",
            phone="9222222222",
        )
    else:
        user_comp2.set_password("pass1234_comp2")
        user_comp2.save()

    session1 = get_auth_session(cashier_user1.username, "pass1234")
    session2 = get_auth_session(cashier_user2.username, "pass1234_cashier2")
    session_comp2 = get_auth_session(user_comp2.username, "pass1234_comp2")

    category = Category.objects.filter(company=company).first()
    supplier = Supplier.objects.filter(company=company).first()

    category2 = Category.objects.filter(company=company2).first()
    if not category2:
        category2 = Category.objects.create(company=company2, name="Tenant2 Category")
    supplier2 = Supplier.objects.filter(company=company2).first()
    if not supplier2:
        supplier2 = Supplier.objects.create(company=company2, name="Tenant2 Supplier", phone="9988776655")

    # =========================================================================
    # SECTION 1: CONCURRENT CHECKOUT RACE TEST
    # =========================================================================
    print("\n" + "=" * 50)
    print("SECTION 1: CONCURRENT CHECKOUT RACE TEST")
    print("=" * 50)

    ts = int(time.time())
    race_product = Product.objects.create(
        company=company,
        name=f"Race Test Milk {ts}",
        product_code=f"RACE-{ts}",
        barcode=f"88{ts%10000000000:010d}",
        category=category,
        supplier=supplier,
        purchase_price=Decimal("40.00"),
        selling_price=Decimal("60.00"),
        gst_percentage=Decimal("5.00"),
        current_stock=Decimal("10.00"),
        unit="pcs",
        status=True,
    )
    race_pid = race_product.id
    print(f"Created Race Product ID {race_pid} with initial stock = 10.00")

    # Two concurrent requests: Cashier A sells 7, Cashier B sells 7
    results_s1 = {}
    barrier_http = threading.Barrier(2)

    def do_http_checkout(cashier_label, session_obj, res_key):
        payload = {
            "items": [{"product_id": race_pid, "quantity": 7.0}],
            "discount_type": "FLAT",
            "discount_value": 0.0,
            "payments": [{"payment_method": "CASH", "amount": 441.0}],  # 7 * 60 + 5% GST = 420 + 21 = 441
            "notes": f"Concurrent test by {cashier_label}",
        }
        barrier_http.wait()
        t0 = time.perf_counter()
        resp = session_obj.post(f"{BASE_URL}/api/sales/checkout/", json=payload)
        t_el = time.perf_counter() - t0
        try:
            body = resp.json()
        except Exception:
            body = resp.text
        results_s1[res_key] = {
            "status_code": resp.status_code,
            "body": body,
            "elapsed": round(t_el, 3),
        }

    th1 = threading.Thread(target=do_http_checkout, args=("Cashier 1", session1, "cashier_1"))
    th2 = threading.Thread(target=do_http_checkout, args=("Cashier 2", session2, "cashier_2"))

    th1.start()
    th2.start()
    th1.join()
    th2.join()

    race_product.refresh_from_db()
    final_stock_1 = race_product.current_stock
    race_sales = Sale.objects.filter(items__product_id=race_pid)
    race_movements = StockMovement.objects.filter(product_id=race_pid)

    print(f"Cashier 1 response: Status {results_s1['cashier_1']['status_code']} ({results_s1['cashier_1']['elapsed']}s)")
    if results_s1['cashier_1']['status_code'] == 201:
        print(f"  -> Cashier 1 SUCCEEDED: Sale {results_s1['cashier_1']['body'].get('sale', {}).get('sale_number')}")
    else:
        print(f"  -> Cashier 1 REJECTED: {results_s1['cashier_1']['body']}")

    print(f"Cashier 2 response: Status {results_s1['cashier_2']['status_code']} ({results_s1['cashier_2']['elapsed']}s)")
    if results_s1['cashier_2']['status_code'] == 201:
        print(f"  -> Cashier 2 SUCCEEDED: Sale {results_s1['cashier_2']['body'].get('sale', {}).get('sale_number')}")
    else:
        print(f"  -> Cashier 2 REJECTED: {results_s1['cashier_2']['body']}")

    print(f"Final DB Stock: {final_stock_1} (Initial: 10.00)")
    print(f"Sales Recorded: {race_sales.count()}")
    print(f"Stock Movements Recorded: {race_movements.count()}")

    assert final_stock_1 >= Decimal("0.00"), f"FAILED: Stock went negative! {final_stock_1}"
    successes = sum(1 for r in results_s1.values() if r["status_code"] == 201)
    assert successes == 1, f"FAILED: Expected exactly 1 checkout to succeed, got {successes}"
    assert final_stock_1 == Decimal("3.00"), f"FAILED: Expected final stock 3.00, got {final_stock_1}"
    assert race_movements.count() == 1, f"FAILED: Expected 1 StockMovement, got {race_movements.count()}"
    assert race_movements.first().quantity == Decimal("-7.00")
    print(">>> SECTION 1 PASS: At most one checkout succeeded. No negative stock. Audit ledger reconciled.")

    # =========================================================================
    # SECTION 2: QUANTITY MANIPULATION TESTS
    # =========================================================================
    print("\n" + "=" * 50)
    print("SECTION 2: QUANTITY MANIPULATION TESTS")
    print("=" * 50)

    q_prod = Product.objects.create(
        company=company,
        name=f"Qty Test Item {ts}",
        product_code=f"QTY-{ts}",
        barcode=f"77{ts%10000000000:010d}",
        category=category,
        supplier=supplier,
        purchase_price=Decimal("20.00"),
        selling_price=Decimal("50.00"),
        gst_percentage=Decimal("0.00"),
        current_stock=Decimal("10.00"),
        unit="pcs",
        status=True,
    )
    q_pid = q_prod.id

    def try_checkout_qty(label, qty_val, expected_status=400):
        payload = {
            "items": [{"product_id": q_pid, "quantity": qty_val}],
            "payments": [{"payment_method": "CASH", "amount": 50.0}],
        }
        resp = session1.post(f"{BASE_URL}/api/sales/checkout/", json=payload)
        status_ok = resp.status_code == expected_status
        print(f"  [{'PASS' if status_ok else 'FAIL'}] {label}: Status {resp.status_code} (Expected {expected_status})")
        if not status_ok:
            print(f"       Response: {resp.text}")
        assert status_ok, f"Failed on {label}"
        return resp

    # 2.1 Negative quantity
    try_checkout_qty("Negative quantity (-5)", -5, 400)

    # 2.2 Zero quantity
    try_checkout_qty("Zero quantity (0)", 0, 400)

    # 2.3 Invalid non-numeric quantity
    try_checkout_qty("Invalid non-numeric ('abc')", "abc", 400)

    # 2.4 Very large quantity beyond stock (999999)
    try_checkout_qty("Huge quantity (999999)", 999999, 400)

    # 2.5 Duplicate product lines exceeding stock: 6 + 6 = 12 (stock is 10)
    payload_dup_exceed = {
        "items": [
            {"product_id": q_pid, "quantity": 6.0},
            {"product_id": q_pid, "quantity": 6.0},
        ],
        "payments": [{"payment_method": "CASH", "amount": 600.0}],
    }
    resp_dup = session1.post(f"{BASE_URL}/api/sales/checkout/", json=payload_dup_exceed)
    assert resp_dup.status_code == 400, f"Expected 400 for duplicate lines exceeding stock, got {resp_dup.status_code}"
    assert "Insufficient stock" in resp_dup.text, f"Expected Insufficient stock error, got: {resp_dup.text}"
    print(f"  [PASS] Duplicate lines exceeding cumulative stock (6 + 6 > 10) rejected: HTTP 400 ('{resp_dup.json().get('error')}')")

    # 2.6 Duplicate product lines within stock: 3 + 4 = 7 (stock is 10)
    payload_dup_ok = {
        "items": [
            {"product_id": q_pid, "quantity": 3.0},
            {"product_id": q_pid, "quantity": 4.0},
        ],
        "payments": [{"payment_method": "CASH", "amount": 350.0}],
    }
    resp_dup_ok = session1.post(f"{BASE_URL}/api/sales/checkout/", json=payload_dup_ok)
    assert resp_dup_ok.status_code == 201, f"Expected 201 for duplicate lines within stock, got {resp_dup_ok.status_code}"
    q_prod.refresh_from_db()
    assert q_prod.current_stock == Decimal("3.00"), f"Expected 3.00 stock remaining, got {q_prod.current_stock}"
    print(f"  [PASS] Duplicate lines within stock (3 + 4 = 7 <= 10) succeeded: Remaining stock = {q_prod.current_stock}")

    # 2.7 Valid decimal quantity: 1.50 units (stock is now 3.00)
    payload_decimal = {
        "items": [{"product_id": q_pid, "quantity": 1.50}],
        "payments": [{"payment_method": "CASH", "amount": 75.0}],
    }
    resp_dec = session1.post(f"{BASE_URL}/api/sales/checkout/", json=payload_decimal)
    assert resp_dec.status_code == 201, f"Expected 201 for decimal quantity, got {resp_dec.status_code}"
    q_prod.refresh_from_db()
    assert q_prod.current_stock == Decimal("1.50"), f"Expected 1.50 stock, got {q_prod.current_stock}"
    print(f"  [PASS] Decimal quantity (1.50) processed correctly: Remaining stock = {q_prod.current_stock}")

    # 2.8 Stale cart stock value: Cart requests 2.00 units, but DB only has 1.50 left
    payload_stale = {
        "items": [{"product_id": q_pid, "quantity": 2.00}],
        "payments": [{"payment_method": "CASH", "amount": 100.0}],
    }
    resp_stale = session1.post(f"{BASE_URL}/api/sales/checkout/", json=payload_stale)
    assert resp_stale.status_code == 400, f"Expected 400 for stale cart stock, got {resp_stale.status_code}"
    assert "Insufficient stock" in resp_stale.text, f"Expected Insufficient stock, got {resp_stale.text}"
    q_prod.refresh_from_db()
    assert q_prod.current_stock == Decimal("1.50"), "DB Stock was corrupted!"
    print(f"  [PASS] Stale cart quantity (requested 2.00 with DB stock 1.50) rejected with HTTP 400")
    print(">>> SECTION 2 PASS: All quantity manipulations safely validated by server.")

    # =========================================================================
    # SECTION 3: IDEMPOTENCY AND RETRY TESTS
    # =========================================================================
    print("\n" + "=" * 50)
    print("SECTION 3: IDEMPOTENCY AND RETRY TESTS")
    print("=" * 50)

    idem_prod = Product.objects.create(
        company=company,
        name=f"Idem Test Item {ts}",
        product_code=f"IDEM-{ts}",
        barcode=f"66{ts%10000000000:010d}",
        category=category,
        supplier=supplier,
        purchase_price=Decimal("10.00"),
        selling_price=Decimal("20.00"),
        gst_percentage=Decimal("0.00"),
        current_stock=Decimal("10.00"),
        unit="pcs",
        status=True,
    )
    idem_pid = idem_prod.id
    idem_key = f"TEST-IDEM-KEY-{ts}"

    payload_idem = {
        "items": [{"product_id": idem_pid, "quantity": 2.0}],
        "payments": [{"payment_method": "CASH", "amount": 40.0}],
        "notes": "Idempotency test sale",
    }
    headers_idem = {"Idempotency-Key": idem_key}

    # 3.1 First submission
    r_first = session1.post(f"{BASE_URL}/api/sales/checkout/", json=payload_idem, headers=headers_idem)
    assert r_first.status_code == 201, f"First checkout failed: {r_first.status_code} {r_first.text}"
    sale_data_1 = r_first.json()["sale"]
    sale_id_1 = sale_data_1["id"]
    sale_num_1 = sale_data_1["sale_number"]
    idem_prod.refresh_from_db()
    assert idem_prod.current_stock == Decimal("8.00")
    print(f"  [PASS] Attempt 1: Created Sale {sale_num_1} (ID {sale_id_1}), DB stock = {idem_prod.current_stock}")

    # 3.2 Second identical submission with same Idempotency-Key
    r_replay = session1.post(f"{BASE_URL}/api/sales/checkout/", json=payload_idem, headers=headers_idem)
    assert r_replay.status_code == 201, f"Replay failed: {r_replay.status_code} {r_replay.text}"
    assert r_replay.headers.get("X-Idempotent-Replay") == "true", "Missing X-Idempotent-Replay header!"
    sale_data_2 = r_replay.json()["sale"]
    assert sale_data_2["id"] == sale_id_1, f"Replay created new sale! {sale_data_2['id']} vs {sale_id_1}"
    assert sale_data_2["sale_number"] == sale_num_1

    # Verify no double stock deduction occurred
    idem_prod.refresh_from_db()
    assert idem_prod.current_stock == Decimal("8.00"), f"Double stock deduction occurred! Stock is {idem_prod.current_stock}"
    sales_cnt = Sale.objects.filter(items__product_id=idem_pid).count()
    assert sales_cnt == 1, f"Expected exactly 1 Sale record, got {sales_cnt}"
    movements_cnt = StockMovement.objects.filter(product_id=idem_pid).count()
    assert movements_cnt == 1, f"Expected exactly 1 StockMovement record, got {movements_cnt}"
    print(f"  [PASS] Attempt 2 (Identical Payload): Replayed successfully with X-Idempotent-Replay=true. Exactly 1 Sale, 1 StockMovement, stock remains 8.00")

    # 3.3 Repeat with same Idempotency-Key but MODIFIED payload
    payload_modified = {
        "items": [{"product_id": idem_pid, "quantity": 5.0}], # Modified quantity
        "payments": [{"payment_method": "CASH", "amount": 100.0}],
        "notes": "Different payload with reused key",
    }
    r_conflict = session1.post(f"{BASE_URL}/api/sales/checkout/", json=payload_modified, headers=headers_idem)
    assert r_conflict.status_code == 409, f"Expected 409 Conflict, got {r_conflict.status_code}: {r_conflict.text}"
    assert "Idempotency key reused with a different request payload" in r_conflict.text
    idem_prod.refresh_from_db()
    assert idem_prod.current_stock == Decimal("8.00"), "Stock was modified on conflict!"
    assert Sale.objects.filter(items__product_id=idem_pid).count() == 1, "New sale was created on conflict!"
    print(f"  [PASS] Attempt 3 (Modified Payload): Rejected with HTTP 409 Conflict. Zero new transactions created.")
    print(">>> SECTION 3 PASS: Idempotency replay and conflict detection verified.")

    # =========================================================================
    # SECTION 4: SCANNER REGRESSION TESTS
    # =========================================================================
    print("\n" + "=" * 50)
    print("SECTION 4: SCANNER REGRESSION TESTS")
    print("=" * 50)

    scan_prod_active = Product.objects.create(
        company=company,
        name=f"Scanner Milk Active {ts}",
        product_code=f"SKU-SCAN-{ts}",
        barcode=f"55{ts%10000000000:010d}",
        category=category,
        supplier=supplier,
        purchase_price=Decimal("25.00"),
        selling_price=Decimal("40.00"),
        gst_percentage=Decimal("5.00"),
        current_stock=Decimal("5.00"),
        unit="pcs",
        status=True,
    )
    scan_prod_inactive = Product.objects.create(
        company=company,
        name=f"Scanner Milk Inactive {ts}",
        product_code=f"SKU-INACT-{ts}",
        barcode=f"44{ts%10000000000:010d}",
        category=category,
        supplier=supplier,
        purchase_price=Decimal("25.00"),
        selling_price=Decimal("40.00"),
        gst_percentage=Decimal("5.00"),
        current_stock=Decimal("10.00"),
        unit="pcs",
        status=False,
    )

    # 4.1 Exact barcode lookup
    r_bc = session1.get(f"{BASE_URL}/api/products/lookup/?code={scan_prod_active.barcode}")
    assert r_bc.status_code == 200, f"Barcode lookup failed: {r_bc.status_code}"
    assert r_bc.json()["product"]["id"] == scan_prod_active.id
    print(f"  [PASS] Exact barcode lookup ({scan_prod_active.barcode}) -> HTTP 200 OK")

    # 4.2 Exact SKU lookup
    r_sku = session1.get(f"{BASE_URL}/api/products/lookup/?code={scan_prod_active.product_code}")
    assert r_sku.status_code == 200, f"SKU lookup failed: {r_sku.status_code}"
    assert r_sku.json()["product"]["id"] == scan_prod_active.id
    print(f"  [PASS] Exact SKU lookup ({scan_prod_active.product_code}) -> HTTP 200 OK")

    # 4.3 Partial barcode does not fuzzy match
    partial_bc = scan_prod_active.barcode[:6]
    r_part = session1.get(f"{BASE_URL}/api/products/lookup/?code={partial_bc}")
    assert r_part.status_code == 404, f"Partial barcode returned {r_part.status_code} instead of 404!"
    assert r_part.json().get("found") is False
    print(f"  [PASS] Partial barcode ({partial_bc}) -> HTTP 404 (No fuzzy matching)")

    # 4.4 Typing 'A' or 'AM' into barcode lookup endpoint does NOT return products
    r_a = session1.get(f"{BASE_URL}/api/products/lookup/?code=A")
    assert r_a.status_code == 404
    r_am = session1.get(f"{BASE_URL}/api/products/lookup/?code=AM")
    assert r_am.status_code == 404
    print(f"  [PASS] Partial letters ('A', 'AM') in barcode endpoint -> HTTP 404 (No autocomplete)")

    # 4.5 Product name in barcode lookup does NOT work
    r_pname = session1.get(f"{BASE_URL}/api/products/lookup/?code={scan_prod_active.name}")
    assert r_pname.status_code == 404
    print(f"  [PASS] Product name in barcode lookup -> HTTP 404")

    # 4.6 Product name search works only in separate catalog search control
    r_search = session1.get(f"{BASE_URL}/api/products/?search={scan_prod_active.name}")
    assert r_search.status_code == 200
    search_ids = [p["id"] for p in r_search.json()]
    assert scan_prod_active.id in search_ids
    print(f"  [PASS] Product name in catalog search endpoint -> HTTP 200 ({len(search_ids)} items found)")

    # 4.7 Inactive product lookup returns 400 (active=False)
    r_inact = session1.get(f"{BASE_URL}/api/products/lookup/?code={scan_prod_inactive.barcode}")
    assert r_inact.status_code == 400, f"Expected 400 for inactive product, got {r_inact.status_code}"
    assert r_inact.json().get("active") is False
    print(f"  [PASS] Inactive product lookup -> HTTP 400 ('{r_inact.json().get('error')}')")

    # 4.8 Unknown barcode returns 404 (found=False)
    r_unk = session1.get(f"{BASE_URL}/api/products/lookup/?code=999999999999999")
    assert r_unk.status_code == 404
    assert r_unk.json().get("found") is False
    print(f"  [PASS] Unknown barcode -> HTTP 404 ('{r_unk.json().get('error')}')")
    print(">>> SECTION 4 PASS: Barcode scanner regression testing verified.")

    # =========================================================================
    # SECTION 5: MULTI-COUNTER ISOLATION TESTS
    # =========================================================================
    print("\n" + "=" * 50)
    print("SECTION 5: MULTI-COUNTER ISOLATION TESTS")
    print("=" * 50)

    # 5.1 Scanner pairing session isolation between Counter 1 and Counter 2
    r_sess1 = session1.post(f"{BASE_URL}/api/pos/scanner-session/create/")
    assert r_sess1.status_code == 201, f"Counter 1 scanner session creation failed: {r_sess1.status_code} {r_sess1.text}"
    tok1 = r_sess1.json()["session_token"]

    r_sess2 = session2.post(f"{BASE_URL}/api/pos/scanner-session/create/")
    assert r_sess2.status_code == 201, f"Counter 2 scanner session creation failed: {r_sess2.status_code} {r_sess2.text}"
    tok2 = r_sess2.json()["session_token"]
    assert tok1 != tok2, "Session tokens collided!"
    print(f"  [PASS] Created distinct scanner sessions: Counter 1 ({tok1[:8]}...), Counter 2 ({tok2[:8]}...)")

    # Phone scanner submits a scan to Counter 1
    unauth_phone = requests.Session()
    r_scan1 = unauth_phone.post(
        f"{BASE_URL}/api/pos/scanner-session/{tok1}/scan/",
        json={"barcode": scan_prod_active.barcode, "device_info": "Phone Counter 1"}
    )
    assert r_scan1.status_code in [200, 201], f"Phone scan failed: {r_scan1.status_code} {r_scan1.text}"

    # Poll Counter 1: scan must be present
    r_poll1 = session1.get(f"{BASE_URL}/api/pos/scanner-session/{tok1}/poll/")
    assert r_poll1.status_code == 200
    scans1 = r_poll1.json()["scans"]
    assert scan_prod_active.barcode in scans1, f"Scan missing from Counter 1: {scans1}"

    # Poll Counter 2: scan must NOT be present
    r_poll2 = session2.get(f"{BASE_URL}/api/pos/scanner-session/{tok2}/poll/")
    assert r_poll2.status_code == 200
    scans2 = r_poll2.json()["scans"]
    assert len(scans2) == 0, f"Counter 2 received scans from Counter 1! Leak detected: {scans2}"
    print(f"  [PASS] Scanner scan delivered strictly to Counter 1 (Counter 2 scans: 0)")

    # 5.2 Company / Tenant isolation
    # Create product in Company 2
    prod_c2 = Product.objects.create(
        company=company2,
        name=f"Company2 Exclusive Product {ts}",
        product_code=f"C2-PROD-{ts}",
        barcode=f"33{ts%10000000000:010d}",
        category=category2,
        supplier=supplier2,
        purchase_price=Decimal("15.00"),
        selling_price=Decimal("30.00"),
        gst_percentage=Decimal("0.00"),
        current_stock=Decimal("10.00"),
        unit="pcs",
        status=True,
    )

    # Cashier 1 (Company 1) tries to lookup Company 2's product
    r_leak_lookup = session1.get(f"{BASE_URL}/api/products/lookup/?code={prod_c2.barcode}")
    assert r_leak_lookup.status_code == 404, "Cashier 1 looked up Company 2's product!"
    print(f"  [PASS] Cross-tenant product lookup rejected: HTTP 404")

    # Cashier 1 tries to checkout Company 2's product
    payload_cross_tenant = {
        "items": [{"product_id": prod_c2.id, "quantity": 1.0}],
        "payments": [{"payment_method": "CASH", "amount": 30.0}],
    }
    r_leak_checkout = session1.post(f"{BASE_URL}/api/sales/checkout/", json=payload_cross_tenant)
    assert r_leak_checkout.status_code == 400
    assert "do not exist in your company catalog" in r_leak_checkout.text
    print(f"  [PASS] Cross-tenant checkout rejected: HTTP 400 ('{r_leak_checkout.json().get('error')}')")

    # 5.3 Invoice and Sale numbering sequencing per tenant
    s1, inv1 = SalesService.checkout(
        company=company,
        cashier=cashier_user1,
        items_data=[{"product_id": scan_prod_active.id, "quantity": 1.0}],
        payments_data=[{"payment_method": "CASH", "amount": 42.0}],
    )
    s2, inv2 = SalesService.checkout(
        company=company,
        cashier=cashier_user2,
        items_data=[{"product_id": scan_prod_active.id, "quantity": 1.0}],
        payments_data=[{"payment_method": "CASH", "amount": 42.0}],
    )

    # Company 2 sale
    s_c2, inv_c2 = SalesService.checkout(
        company=company2,
        cashier=user_comp2,
        items_data=[{"product_id": prod_c2.id, "quantity": 1.0}],
        payments_data=[{"payment_method": "CASH", "amount": 30.0}],
    )

    prefix_comp1 = f"-{company.id:03d}-"
    prefix_comp2 = f"-{company2.id:03d}-"

    assert prefix_comp1 in inv1.invoice_number and prefix_comp1 in inv2.invoice_number
    assert prefix_comp2 in inv_c2.invoice_number
    seq1 = int(inv1.invoice_number.split("-")[-1])
    seq2 = int(inv2.invoice_number.split("-")[-1])
    assert seq2 == seq1 + 1, f"Sequencing mismatch: {seq1} -> {seq2}"
    print(f"  [PASS] Company 1 invoices: {inv1.invoice_number} -> {inv2.invoice_number}")
    print(f"  [PASS] Company 2 invoice:  {inv_c2.invoice_number}")
    print(">>> SECTION 5 PASS: Multi-counter and tenant isolation verified.")

    print("\n" + "=" * 70)
    print("ALL ADVERSARIAL POS REGRESSION TESTS COMPLETED SUCCESSFULLY!")
    print("=" * 70)

if __name__ == "__main__":
    run_all_adversarial_tests()
