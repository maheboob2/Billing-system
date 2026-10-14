import os
import sys
import uuid
from decimal import Decimal
import threading

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django
django.setup()

import requests
from accounts.models import companyRegistration
from inventory.models import Product
from sales.models import Sale, Invoice

BASE_URL = "http://127.0.0.1:8000"

def run_multicounter_qa():
    print("=================================================================")
    print("PHASE 12: MULTI-COUNTER CONCURRENT SALES & TENANT ISOLATION QA")
    print("=================================================================")
    
    company = companyRegistration.objects.get(id=99)
    prod = Product.objects.get(company=company, product_code="PRD-COFFEE-01")
    prod.current_stock = Decimal("300.00")
    prod.save()

    results = []
    errors = []

    def checkout_worker(counter_id, cashier_uname):
        s = requests.Session()
        s.get(f"{BASE_URL}/accounts/")
        csrf = s.cookies.get("csrftoken")
        s.post(
            f"{BASE_URL}/accounts/",
            data={"username": cashier_uname, "password": "Password@123", "csrfmiddlewaretoken": csrf},
            headers={"Referer": f"{BASE_URL}/accounts/"}
        )
        csrf = s.cookies.get("csrftoken")
        
        idem_key = f"IDEM-MC-{counter_id}-{uuid.uuid4()}"
        payload = {
            "items": [{"product_id": prod.id, "quantity": 5}],
            "payment_method": "CASH",
            "paid_amount": 354.00,
            "idempotency_key": idem_key,
        }
        resp = s.post(
            f"{BASE_URL}/api/sales/checkout/",
            json=payload,
            headers={"X-CSRFToken": csrf}
        )
        if resp.status_code == 201:
            data = resp.json()
            results.append({
                "counter": counter_id,
                "sale_number": data["sale"]["sale_number"],
                "invoice_number": data["sale"]["invoice_number"],
                "grand_total": data["sale"]["grand_total"]
            })
        else:
            errors.append(f"Counter {counter_id} failed: {resp.status_code} - {resp.text}")

    # Launch 3 threads simulating simultaneous checkouts at Counters 1, 2, 3
    threads = []
    cashier_names = ["qa_cashier_99_1", "qa_cashier_99_2", "qa_cashier_99_3"]
    for i in range(3):
        t = threading.Thread(target=checkout_worker, args=(i+1, cashier_names[i]))
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    assert len(errors) == 0, f"Concurrent checkouts encountered errors: {errors}"
    assert len(results) == 3, f"Expected 3 checkouts, got {len(results)}"

    sale_nums = [r["sale_number"] for r in results]
    inv_nums = [r["invoice_number"] for r in results]

    assert len(set(sale_nums)) == 3, f"Sale numbers not unique across counters: {sale_nums}"
    assert len(set(inv_nums)) == 3, f"Invoice numbers not unique across counters: {inv_nums}"

    prod.refresh_from_db()
    # 3 checkouts of 5 items each = 15 deducted from 300.00 => 285.00
    assert prod.current_stock == Decimal("285.00"), f"Stock concurrency mismatch: expected 285.00, got {prod.current_stock}"

    print(f"  ✓ 3 Concurrent counter checkouts completed successfully:")
    for r in results:
        print(f"     Terminal {r['counter']}: Sale={r['sale_number']}, Invoice={r['invoice_number']}, Total=₹{r['grand_total']}")
    print(f"  ✓ Stock correctly deducted with row locking: 300.00 -> {prod.current_stock}")
    print("=================================================================")
    print("PHASE 12 MULTI-COUNTER CONCURRENCY PASSED!")
    print("=================================================================")

if __name__ == "__main__":
    run_multicounter_qa()
