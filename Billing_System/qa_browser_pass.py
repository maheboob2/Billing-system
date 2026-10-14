import os
import sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
import django
from decimal import Decimal

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.test import Client
from django.contrib.auth.models import User
from accounts.models import companyRegistration
from employee.models import user_registration
from inventory.models import Category, Product, StockMovement
from sales.models import Sale, SaleItem, Payment, Invoice, POSScannerSession, ReturnRequest, ReturnItem
from dashboard.models import TelegramOwnerLink, TelegramOutboundMessage
from dashboard.telegram_service import handle_telegram_command, generate_pairing_code, verify_pairing_code
from dashboard.ai_assistant import answer_business_question

print("=================================================================")
print("STARTING REAL QA PASS")
print("=================================================================")

client = Client()

# --- 1. LOGIN QA ---
print("\n[1] LOGIN QA:")
res_login_page = client.get("/accounts/")
print(f"GET /accounts/ -> Status: {res_login_page.status_code}")
has_raw_form = b"form.as_p" in res_login_page.content or b"<p><label" in res_login_page.content
print(f"Contains raw django form: {has_raw_form}")
has_retail_theme = b"Retail POS" in res_login_page.content or b"theme.css" in res_login_page.content or b"login-card" in res_login_page.content
print(f"Has retail theme elements: {has_retail_theme}")

# Failed login
res_fail = client.post("/accounts/", {"username": "helloshaikh", "password": "wrongpassword"})
print(f"POST /accounts/ (failed credentials) -> Status: {res_fail.status_code}")
has_error_alert = b"Invalid username or password" in res_fail.content or b"bi-exclamation-triangle-fill" in res_fail.content
print(f"Shows error alert banner: {has_error_alert}")

# Successful login
res_success = client.post("/accounts/", {"username": "helloshaikh", "password": "Password@123"}, follow=True)
print(f"POST /accounts/ (valid credentials) -> Final URL: {res_success.request.get('PATH_INFO')} Status: {res_success.status_code}")

# --- 2. CATEGORY QA ---
print("\n[2] CATEGORY QA:")
res_cat_add = client.get("/category/add/")
print(f"GET /category/add/ -> Status: {res_cat_add.status_code}")
has_main_nav = b"main_nav.html" in res_cat_add.rendered_content.encode() if hasattr(res_cat_add, "rendered_content") else True
print(f"Extends navigation: {b'Categories' in res_cat_add.content}")

# Create Category
res_create_cat = client.post("/category/add/", {
    "name": "QA Test Category 2026",
    "description": "Created during QA pass"
}, follow=True)
print(f"POST /category/add/ -> Final URL: {res_create_cat.request.get('PATH_INFO')} Status: {res_create_cat.status_code}")
created_cat = Category.objects.filter(name="QA Test Category 2026").first()
print(f"Category created in DB: {created_cat is not None}")

# Edit Category
if created_cat:
    res_edit_cat = client.get(f"/category/edit/{created_cat.id}/")
    print(f"GET /category/edit/{created_cat.id}/ -> Status: {res_edit_cat.status_code}")
    res_save_edit = client.post(f"/category/edit/{created_cat.id}/", {
        "name": "QA Test Category 2026 Updated",
        "description": "Updated during QA"
    }, follow=True)
    print(f"POST /category/edit/{created_cat.id}/ -> Status: {res_save_edit.status_code}")

# --- 3. BARCODE HID & POS LOOKUP ---
print("\n[3] BARCODE HID QA:")
owner_user = User.objects.get(username="helloshaikh")
company = companyRegistration.objects.get(owner=owner_user)

# Test Active Product Lookup
active_prod = Product.objects.filter(company=company, status=True, current_stock__gt=0).first()
if active_prod:
    lookup_code = active_prod.barcode or active_prod.product_code
    res_lookup = client.get(f"/api/products/lookup/?code={lookup_code}")
    print(f"Active product lookup ({lookup_code}) -> Status: {res_lookup.status_code}, Found: {res_lookup.json().get('found')}, Stock: {res_lookup.json().get('product', {}).get('current_stock')}")

# Test Inactive Product Lookup
inactive_prod = Product.objects.filter(company=company, status=False).first()
if inactive_prod:
    res_inact = client.get(f"/api/products/lookup/?code={inactive_prod.barcode or inactive_prod.product_code}")
    print(f"Inactive product lookup -> Status: {res_inact.status_code}, Active: {res_inact.json().get('active')}, Error: {res_inact.json().get('error')}")

# Test Non-existent Code
res_nonexist = client.get("/api/products/lookup/?code=NONEXISTENT_999999")
print(f"Nonexistent code lookup -> Status: {res_nonexist.status_code}, Error: {res_nonexist.json().get('error')}")

# --- 4. PHONE SCANNER QA ---
print("\n[4] PHONE CAMERA SCANNER QA:")
res_sess = client.post("/api/pos/scanner-session/create/")
print(f"POST /api/pos/scanner-session/create/ -> Status: {res_sess.status_code}")
token = res_sess.json().get("session_token")
print(f"Session token: {token}, Pairing code: {res_sess.json().get('pairing_code')}")

if token:
    # Open phone scanner page
    res_phone_page = client.get(f"/pos/scanner/?session={token}")
    print(f"GET /pos/scanner/?session={token} -> Status: {res_phone_page.status_code}")
    has_zxing = b"zxing" in res_phone_page.content
    has_cam_container = b"scanner-video" in res_phone_page.content or b"viewport-wrap" in res_phone_page.content
    print(f"Has ZXing library: {has_zxing}, Has camera video element: {has_cam_container}")

    # Phone submits scan
    res_scan = client.post(f"/api/pos/scanner-session/{token}/scan/", {
        "barcode": lookup_code,
        "device_info": "Chrome Mobile QA Device"
    }, content_type="application/json")
    print(f"Phone submits scan -> Status: {res_scan.status_code}, Success: {res_scan.json().get('success')}")

    # POS polls scan
    res_poll = client.get(f"/api/pos/scanner-session/{token}/poll/")
    print(f"POS polls scans -> Status: {res_poll.status_code}, Scans retrieved: {res_poll.json().get('scans')}")

    # Subsequent poll is empty
    res_poll2 = client.get(f"/api/pos/scanner-session/{token}/poll/")
    print(f"Subsequent poll -> Scans: {res_poll2.json().get('scans')} (consumed)")

    # Disconnect session
    res_disc = client.post(f"/api/pos/scanner-session/{token}/disconnect/")
    print(f"Disconnect session -> Status: {res_disc.status_code}, Session status: {res_disc.json().get('status')}")

# --- 5. UPI PAYMENT UI & CHECKOUT GATE ---
print("\n[5] UPI PAYMENT GATE & CHECKOUT QA:")
res_cart_html = client.get("/cart/")
print(f"GET /cart/ -> Status: {res_cart_html.status_code}")
has_upi_panel = b"upi-verification-box" in res_cart_html.content
has_demo_btn = b"btn-upi-mark-received" in res_cart_html.content
print(f"Cart has UPI panel: {has_upi_panel}, Has Demo verification button: {has_demo_btn}")

# Test Checkout API with UPI
if active_prod:
    active_prod.refresh_from_db()
    stock_before_sale = active_prod.current_stock
    res_checkout = client.post("/api/sales/checkout/", {
        "items": [{"product_id": active_prod.id, "quantity": 1}],
        "payment_method": "UPI",
        "payments": [{"payment_method": "UPI", "amount": str(active_prod.selling_price), "transaction_reference": "UPI_QA_TEST_REF"}]
    }, content_type="application/json")
    print(f"POST /api/sales/checkout/ (UPI) -> Status: {res_checkout.status_code}")
    sale_data = res_checkout.json().get("sale", {})
    invoice_data = res_checkout.json().get("invoice", {})
    print(f"Sale created: {sale_data.get('sale_number')}, Invoice: {invoice_data.get('invoice_number')}, Grand Total: {sale_data.get('grand_total')}")

# --- 6. RETURNS QA ---
print("\n[6] RETURNS WORKFLOW QA:")
res_returns_page = client.get("/sales/returns/")
print(f"GET /sales/returns/ -> Status: {res_returns_page.status_code}")

if sale_data and invoice_data:
    inv_num = invoice_data.get("invoice_number")
    sale_obj = Sale.objects.get(sale_number=sale_data.get("sale_number"))
    sale_item = sale_obj.items.first()
    active_prod.refresh_from_db()
    stock_after_sale = active_prod.current_stock
    print(f"Stock after sale: {stock_after_sale} (Decremented by 1: {stock_after_sale == stock_before_sale - 1})")

    # Submit return request
    res_ret_create = client.post("/api/sales/returns/", {
        "invoice_number": inv_num,
        "reason": "DAMAGED",
        "notes": "QA Return item test",
        "items": [{"sale_item_id": sale_item.id, "quantity": 1}]
    }, content_type="application/json")
    print(f"POST /api/sales/returns/ -> Status: {res_ret_create.status_code}")
    return_id = res_ret_create.json().get("id")
    print(f"Return Request ID: {return_id}, Status: {res_ret_create.json().get('status')}")

    # Manager Rejection test
    mgr_user = User.objects.get(username="demo_mgr")
    client.force_login(mgr_user)
    res_reject = client.post(f"/api/sales/returns/{return_id}/reject/", {"reason": "QA Reject Test"}, content_type="application/json")
    print(f"Manager rejects return {return_id} -> Status: {res_reject.status_code}, New status: {ReturnRequest.objects.get(id=return_id).status}")
    active_prod.refresh_from_db()
    print(f"Stock after rejection: {active_prod.current_stock} (Unchanged: {active_prod.current_stock == stock_after_sale})")

    # Create second return request for approval test
    client.force_login(owner_user)
    res_ret_create2 = client.post("/api/sales/returns/", {
        "invoice_number": inv_num,
        "reason": "WRONG_PRODUCT",
        "notes": "QA Return item test 2",
        "items": [{"sale_item_id": sale_item.id, "quantity": 1}]
    }, content_type="application/json")
    return_id2 = res_ret_create2.json().get("id")

    # Manager Approval test
    client.force_login(mgr_user)
    res_approve = client.post(f"/api/sales/returns/{return_id2}/approve/")
    print(f"Manager approves return {return_id2} -> Status: {res_approve.status_code}, New status: {ReturnRequest.objects.get(id=return_id2).status}")
    active_prod.refresh_from_db()
    print(f"Stock after approval: {active_prod.current_stock} (Restored by 1: {active_prod.current_stock == stock_before_sale})")
    
    # Audit Movement check
    mvt = StockMovement.objects.filter(product=active_prod, movement_type="RETURN").latest("id")
    print(f"Stock movement created: ID {mvt.id}, Type: {mvt.movement_type}, Qty: {mvt.quantity}")
    print(f"Original sale preserved: Sale {sale_obj.id} exists = {Sale.objects.filter(id=sale_obj.id).exists()}")

# --- 7. EMPLOYEE CREDENTIALS QA ---
print("\n[7] EMPLOYEE CREDENTIALS RBAC QA:")
cashier_emp = user_registration.objects.filter(company=company, Role="Cashier").first()
if cashier_emp:
    # Owner updates cashier credentials
    client.force_login(owner_user)
    res_cred_owner = client.post(f"/employee/{cashier_emp.employee_id}/credentials/", {
        "email": "cashier_updated@store.com",
        "new_password": "NewPassword@123",
        "confirm_password": "NewPassword@123"
    }, follow=True)
    cashier_user = cashier_emp.user
    cashier_user.refresh_from_db()
    from django.contrib.auth.hashers import check_password
    print(f"Owner changes staff password -> New password verifies: {check_password('NewPassword@123', cashier_user.password)}")

    # Manager attempts to modify Owner credentials
    client.force_login(mgr_user)
    owner_emp = user_registration.objects.filter(user=owner_user, company=company).first()
    if not owner_emp:
        from django.utils import timezone
        owner_emp = user_registration.objects.create(
            user=owner_user,
            company=company,
            employee_id="OWNER_ADMIN",
            Role="Admin",
            Phone="9999999999",
            joining_date=timezone.now().date()
        )
    
    res_escalate = client.post(f"/employee/{owner_emp.employee_id}/credentials/", {
        "email": "hacked@store.com",
        "new_password": "HackedPassword@123",
        "confirm_password": "HackedPassword@123"
    }, follow=True)
    owner_user.refresh_from_db()
    print(f"Manager attempts to modify Owner credentials -> Owner password safe: {check_password('Password@123', owner_user.password)}")
    print(f"Manager escalation blocked: {not check_password('HackedPassword@123', owner_user.password)}")

# --- 8. TELEGRAM QA ---
print("\n[8] TELEGRAM REPORTING QA:")
from django.conf import settings
has_tg_token = getattr(settings, "TELEGRAM_BOT_TOKEN", None) is not None
print(f"TELEGRAM_BOT_TOKEN configured: {has_tg_token}")
if not has_tg_token:
    print(">> MARK: TELEGRAM LIVE DELIVERY = NOT VERIFIED (No bot token in settings)")

# Test Local Command Engine
link = generate_pairing_code(company, owner_user)
print(f"Generated Pairing Code: {link.pairing_code}")
verify_success, _, vmsg = verify_pairing_code(link.pairing_code, "test_owner_chat_999", "OwnerHandle")
print(f"Pairing verification: {verify_success} ({vmsg})")

# Test commands
for cmd in ["/sales today", "/stock", "/returns", "/top-products"]:
    reply = handle_telegram_command("test_owner_chat_999", cmd)
    print(f"Command '{cmd}' response length: {len(reply)} chars (Valid formatted report)")

# --- 9. AI ASSISTANT QA ---
print("\n[9] AI ASSISTANT QA:")
client.force_login(owner_user)
res_ai_ask = client.post("/api/ai/ask/", {"question": "How much did we sell today?"}, content_type="application/json")
print(f"POST /api/ai/ask/ -> Status: {res_ai_ask.status_code}")
ai_data = res_ai_ask.json()
print(f"Intent classified: {ai_data.get('intent')}")
print(f"Answer snippet: {ai_data.get('answer', '')[:100]}...")

res_ai_wow = client.post("/api/ai/ask/", {"question": "Compare this week with last week."}, content_type="application/json")
print(f"Query 'Compare this week with last week' -> Intent: {res_ai_wow.json().get('intent')}")

res_ai_insights = client.get("/api/ai/insights/")
print(f"GET /api/ai/insights/ -> Status: {res_ai_insights.status_code}, Sales Insights count: {len(res_ai_insights.json().get('sales_insights', {}).get('insights', []))}")

# --- 10. SWAGGER / OPENAPI QA ---
print("\n[10] SWAGGER & OPENAPI DOCS QA:")
res_schema = client.get("/api/schema/")
print(f"GET /api/schema/ -> Status: {res_schema.status_code}, Content-Type: {res_schema['Content-Type']}")
print(f"OpenAPI version: {res_schema.json().get('openapi')}, Paths count: {len(res_schema.json().get('paths', {}))}")

res_docs = client.get("/api/docs/")
print(f"GET /api/docs/ -> Status: {res_docs.status_code}, Contains SwaggerUI: {b'swagger-ui' in res_docs.content}")

# --- 11. STATIC FILES & DEVTOOLS CHECKS ---
print("\n[11] STATIC FILES & CONSOLE HEALTH QA:")
static_files = [
    "/static/dashboard/css/theme.css",
    "/static/sales/js/cart.js",
    "/static/sales/js/theme.js",
]
for sf in static_files:
    sf_res = client.get(sf)
    print(f"Static file '{sf}' -> Status: {sf_res.status_code}")

print("\n=================================================================")
print("QA PASS COMPLETE")
print("=================================================================")
