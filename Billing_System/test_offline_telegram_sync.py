import django, os, sys
sys.stdout.reconfigure(encoding='utf-8')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from decimal import Decimal
from django.utils import timezone
from django.contrib.auth import get_user_model
from accounts.models import companyRegistration
from sales.models import Sale, SaleItem, Payment, Customer, Product
from purchases.models import Purchase, Supplier
from dashboard.models import TelegramOwnerLink, TelegramOutboundMessage, SyncQueueItem, RemoteSyncedRecord, RemoteSyncStatus
from dashboard.sync_service import SyncService
from dashboard.telegram_service import generate_pairing_code, verify_pairing_code, handle_telegram_command
from sales.services import SalesService

company = companyRegistration.objects.first()
user = get_user_model().objects.first()
prod = Product.objects.filter(company=company, current_stock__gt=5).first()

print("=== 1. TEST OFFLINE POS CHECKOUT & QUEUEING ===")
initial_pending = SyncQueueItem.objects.filter(company=company, status="PENDING").count()
sale, inv = SalesService.checkout(
    company=company,
    cashier=user,
    items_data=[{"product_id": prod.id, "quantity": 1, "unit_price": prod.selling_price}],
    payments_data=[{"payment_method": "CASH", "amount": prod.selling_price}]
)
print(f"Sale created locally: {sale.sale_number}, Total: {sale.grand_total}")
sale_queue_item = SyncQueueItem.objects.filter(company=company, entity_type="SALE", entity_id=str(sale.id)).first()
assert sale_queue_item is not None, "Sale must be enqueued in SyncQueueItem!"
assert sale_queue_item.status == "PENDING", "Item must be PENDING sync!"
print(f"Offline queue confirmed: Sale queued with key: {sale_queue_item.idempotency_key}")

print("\n=== 2. TEST TELEGRAM OFFLINE AWARENESS ===")
# Pair a test owner
link = generate_pairing_code(company, user)
code = link.pairing_code
test_chat = "owner_offline_test_999"
ok, paired_link, msg = verify_pairing_code(code, test_chat, "StoreOwner")
assert ok, "Pairing must succeed without password!"
print(f"Pairing successful without password. Chat ID bound: {test_chat}")

# Simulate offline state on remote sync
SyncService.run_sync(company, simulate_offline=True)
status_info = SyncService.get_sync_status(company)
assert not status_info["is_online"], "Status must report OFFLINE!"
print(f"Remote sync status reported: {status_info['status']}, Pending buffer: {status_info['pending_count']}")

# Query Telegram report while store is offline
offline_report = handle_telegram_command(test_chat, "today's report")
assert "Store Offline" in offline_report or "last sync" in offline_report.lower(), "Telegram report must display offline state & last sync timestamp!"
print("Telegram offline report verified (includes timestamp footer):")
print(offline_report[:240] + "...")

print("\n=== 3. TEST CLOUD SYNC RETURN & IDEMPOTENCY ===")
# Run online sync
res1 = SyncService.run_sync(company, simulate_offline=False)
print("Sync run 1 result:", res1)
assert res1["success"], "Sync run 1 must succeed!"
assert res1["synced"] > 0, "Must have synced items!"
assert res1["pending"] == 0, "Pending count must be 0!"

# Re-run sync immediately to test idempotency (no duplicates!)
pre_synced_count = RemoteSyncedRecord.objects.filter(company=company).count()
res2 = SyncService.run_sync(company, simulate_offline=False)
post_synced_count = RemoteSyncedRecord.objects.filter(company=company).count()
print("Sync run 2 (idempotency check):", res2)
assert res2["synced"] == 0, "Subsequent run must sync 0 duplicate items!"
assert pre_synced_count == post_synced_count, "Record count must remain identical (idempotent)!"
print(f"Strict idempotency verified: Zero duplicate transactions! (Total remote records: {post_synced_count})")

print("\n=== 4. TEST ALL NATURAL-LANGUAGE REPORTING QUERIES ===")
queries = [
    "today's report",
    "yesterday's report",
    "sales on 2026-10-08",
    "sales from 2026-10-01 to 2026-10-08",
    "how much cash did we collect today?",
    "show upi collections",
    "card payments",
    "customer credit balances",
    "supplier purchases report",
    "who are our top customers?",
    "returns and refunds status",
    "inventory stock overview",
    "top selling products",
    "compare this week with last week",
    "send me a pdf report",
    "show chart image"
]

for q in queries:
    resp = handle_telegram_command(test_chat, q)
    assert resp is not None and len(resp) > 10, f"Query '{q}' returned empty response!"
    assert "Cloud Sync:" in resp or "Store Offline:" in resp, f"Query '{q}' must include sync timestamp!"
    print(f"✓ Query: \"{q}\" -> Success ({len(resp)} chars)")

print("\n=== 5. VERIFY GENERATED PDF & CHART MEDIA ATTACHMENTS ===")
pdf_msg = TelegramOutboundMessage.objects.filter(company=company, media_type="DOCUMENT").first()
assert pdf_msg is not None, "PDF message must be created in Telegram outbound queue!"
assert os.path.exists(pdf_msg.media_path), f"Generated PDF file must exist at {pdf_msg.media_path}!"
print(f"✓ PDF Document attachment verified: {pdf_msg.media_filename} ({os.path.getsize(pdf_msg.media_path)} bytes)")

chart_msg = TelegramOutboundMessage.objects.filter(company=company, media_type="PHOTO").first()
assert chart_msg is not None, "Chart image message must be created in Telegram outbound queue!"
assert os.path.exists(chart_msg.media_path), f"Generated chart file must exist at {chart_msg.media_path}!"
print(f"✓ Chart Photo attachment verified: {chart_msg.media_filename} ({os.path.getsize(chart_msg.media_path)} bytes)")

print("\n=======================================================")
print("ALL 5 SYSTEM ARCHITECTURE REQUIREMENTS FULLY VERIFIED!")
print("=======================================================")
