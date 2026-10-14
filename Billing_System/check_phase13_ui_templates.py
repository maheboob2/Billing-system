import os
import sys

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django
django.setup()

import requests

BASE_URL = "http://127.0.0.1:8000"

def audit_ui_pages():
    print("=================================================================")
    print("PHASE 13: UI QUALITY & TEMPLATE AUDIT")
    print("=================================================================")
    
    s = requests.Session()
    s.get(f"{BASE_URL}/accounts/")
    csrf = s.cookies.get("csrftoken")
    s.post(
        f"{BASE_URL}/accounts/",
        data={"username": "qa_owner_99", "password": "Password@123", "csrfmiddlewaretoken": csrf},
        headers={"Referer": f"{BASE_URL}/accounts/"}
    )

    pages = [
        ("/accounts/", "Login & Authentication"),
        ("/cart/", "POS Retail Billing Cart"),
        ("/product/", "Product Catalog & Inventory"),
        ("/category/", "Category Taxonomy"),
        ("/purchase/", "Purchase Orders & Receiving"),
        ("/sales/", "Sales Ledger & Invoices"),
        ("/returns/", "Returns & Refund Management"),
        ("/reports/", "Executive Financial Analytics"),
        ("/settings/", "General POS Settings"),
        ("/settings/scanner/", "Scanner Hardware & LAN Pairing"),
        ("/employee/", "Team & Employee Roster"),
    ]

    for path, name in pages:
        r = s.get(f"{BASE_URL}{path}")
        assert r.status_code == 200, f"Page {path} ({name}) failed with status {r.status_code}!"
        # Check for HTML5 doctype and proper closing
        assert "<!DOCTYPE html" in r.text or "<!doctype html" in r.text, f"Page {path} missing DOCTYPE"
        assert "</html>" in r.text, f"Page {path} missing closing html tag"
        # Check that no raw template tags leaked
        assert "{%" not in r.text and "%}" not in r.text, f"Page {path} leaked raw django template tags!"
        # Check that no unhandled exceptions or error tracebacks are shown
        assert "Traceback" not in r.text and "DoesNotExist" not in r.text
        print(f"  ✓ {path:22} | 200 OK | {name} (Size: {len(r.text)} bytes)")

    print("=================================================================")
    print("PHASE 13 UI QUALITY AUDIT: ALL 11 PRIMARY VIEWS VERIFIED 200 OK!")
    print("=================================================================")

if __name__ == "__main__":
    audit_ui_pages()
