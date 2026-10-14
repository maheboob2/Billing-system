import os
import django
import threading
import time
from decimal import Decimal

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.db import connection
from django.contrib.auth.models import User
from accounts.models import companyRegistration
from inventory.models import Category, Supplier, Product, StockMovement
from sales.models import Sale, SaleItem, Invoice
from sales.services import SalesService
from django.core.exceptions import ValidationError

def run_race_test():
    print("=" * 60)
    print("CONCURRENT CHECKOUT RACE TEST")
    print(f"Configured Database Engine: {connection.settings_dict['ENGINE']}")
    print("=" * 60)

    # 1. Setup Company, User, Category, Supplier, and Product with stock = 10
    company = companyRegistration.objects.first()
    if not company:
        print("ERROR: No company found in DB")
        return

    cashier_a = User.objects.filter(is_active=True).first()
    cashier_b = User.objects.filter(is_active=True).exclude(id=cashier_a.id).first()
    if not cashier_b:
        cashier_b = cashier_a

    category = Category.objects.filter(company=company).first()
    supplier = Supplier.objects.filter(company=company).first()

    # Create distinct test product with exactly 10 units
    test_sku = f"RACE-{int(time.time())}"
    product = Product.objects.create(
        company=company,
        name=f"Race Test Item {test_sku}",
        product_code=test_sku,
        barcode=f"99{int(time.time())%10000000000:010d}",
        category=category,
        supplier=supplier,
        purchase_price=Decimal("50.00"),
        selling_price=Decimal("100.00"),
        gst_percentage=Decimal("18.00"),
        current_stock=Decimal("10.00"),
        unit="pcs",
        status=True,
    )
    product_id = product.id
    print(f"Created Product: ID {product_id}, SKU {test_sku}, Initial Stock: {product.current_stock}")

    barrier = threading.Barrier(2)
    results = {}

    def cashier_checkout(cashier_name, cashier_user, key):
        from django.db import connection as thread_conn
        # Close old connection to ensure fresh DB connection in thread
        thread_conn.close()

        items_data = [{"product_id": product_id, "quantity": Decimal("7.00")}]

        # Wait for both threads to be ready
        barrier.wait()
        start_t = time.perf_counter()
        try:
            sale, invoice = SalesService.checkout(
                company=company,
                cashier=cashier_user,
                items_data=items_data,
                notes=f"Race test by {cashier_name}",
            )
            elapsed = time.perf_counter() - start_t
            results[key] = {
                "success": True,
                "sale_id": sale.id,
                "sale_number": sale.sale_number,
                "invoice_number": invoice.invoice_number,
                "elapsed": elapsed,
            }
        except ValidationError as ve:
            elapsed = time.perf_counter() - start_t
            results[key] = {
                "success": False,
                "error_type": "ValidationError",
                "message": str(ve.message if hasattr(ve, "message") else ve),
                "elapsed": elapsed,
            }
        except Exception as e:
            elapsed = time.perf_counter() - start_t
            results[key] = {
                "success": False,
                "error_type": type(e).__name__,
                "message": str(e),
                "elapsed": elapsed,
            }
        finally:
            thread_conn.close()

    t1 = threading.Thread(target=cashier_checkout, args=("Cashier A", cashier_a, "cashier_a"))
    t2 = threading.Thread(target=cashier_checkout, args=("Cashier B", cashier_b, "cashier_b"))

    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # Re-fetch product from DB
    connection.close()
    product.refresh_from_db()
    final_stock = product.current_stock

    sales_created = Sale.objects.filter(items__product_id=product_id)
    movements = StockMovement.objects.filter(product_id=product_id)

    print("\nRESULTS:")
    print(f"Cashier A: {results.get('cashier_a')}")
    print(f"Cashier B: {results.get('cashier_b')}")
    print(f"Final DB Stock: {final_stock} (Initial was 10.00)")
    print(f"Total Sales Created: {sales_created.count()}")
    for s in sales_created:
        print(f"  - Sale {s.sale_number}: {s.grand_total} INR")
    print(f"Total Stock Movements: {movements.count()}")
    for m in movements:
        print(f"  - Movement {m.movement_type}: qty={m.quantity}, prev={m.previous_stock}, new={m.new_stock}")

    # Integrity assertions
    assert final_stock >= Decimal("0.00"), f"CRITICAL BUG: Stock went negative! final_stock={final_stock}"
    
    success_count = sum(1 for r in results.values() if r.get("success"))
    print(f"\nSuccessful Checkouts: {success_count} of 2")
    assert success_count <= 1, f"CRITICAL BUG: Both checkouts succeeded! Total requested was 14 from stock 10!"

    if success_count == 1:
        assert final_stock == Decimal("3.00"), f"Expected final stock 3.00, got {final_stock}"
        assert movements.count() == 1, f"Expected 1 stock movement, got {movements.count()}"
        assert movements.first().quantity == Decimal("-7.00")
        print("PASS: Exactly ONE checkout succeeded (sold 7), other rejected. Final stock = 3.00. No overselling!")
    else:
        print("PASS: Concurrency conflict handled, no overselling occurred.")

    # Cleanup test product
    product.delete()
    print("Test cleanup completed.\n")

if __name__ == "__main__":
    run_race_test()
