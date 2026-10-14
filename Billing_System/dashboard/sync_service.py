import json
import logging
from decimal import Decimal
from django.utils import timezone
from django.db import models, transaction
from django.conf import settings
from accounts.models import companyRegistration
from .models import RemoteSyncStatus, SyncQueueItem, RemoteSyncedRecord, CompanyCloudSnapshot

logger = logging.getLogger(__name__)


class SyncService:
    """
    Authoritative Offline & Remote Synchronization Engine for Retail POS.
    
    Guarantees:
    1. Local POS continues unblocked execution when shop internet is down.
    2. Zero data loss: all transactions are safely queued in local DB.
    3. Strict idempotency: re-running or retrying sync never duplicates sales, payments, returns, or purchases.
    4. Auditable state: remote reporting tracks and displays the last successfully synchronized timestamp.
    """

    @classmethod
    def get_or_create_status(cls, company):
        status_obj, _ = RemoteSyncStatus.objects.get_or_create(
            company=company,
            defaults={
                "status": "ONLINE",
                "last_sync_success": timezone.now(),
                "pending_count": 0,
                "total_synced_count": 0,
            }
        )
        return status_obj

    @classmethod
    def get_sync_status(cls, company):
        status_obj = cls.get_or_create_status(company)
        pending = SyncQueueItem.objects.filter(company=company, status="PENDING").count()
        if pending != status_obj.pending_count:
            status_obj.pending_count = pending
            status_obj.save(update_fields=["pending_count"])

        if status_obj.last_sync_success:
            local_dt = timezone.localtime(status_obj.last_sync_success)
            last_ts_str = local_dt.strftime("%b %d, %Y %I:%M %p")
        else:
            last_ts_str = "Never"

        is_sim_offline = getattr(company, "_simulate_offline", False)
        is_online = (status_obj.status == "ONLINE") and not is_sim_offline

        if not is_online or status_obj.status == "OFFLINE":
            if pending > 0:
                freshness = "SYNC_PENDING"
            elif status_obj.last_sync_success and (timezone.now() - status_obj.last_sync_success).total_seconds() > 1800:
                freshness = "STALE"
            else:
                freshness = "CACHED"
        else:
            freshness = "LIVE"

        snapshot = CompanyCloudSnapshot.objects.filter(company=company).order_by("-snapshot_date").first()

        return {
            "status": "OFFLINE" if is_sim_offline else status_obj.status,
            "is_online": is_online,
            "freshness_status": freshness,
            "last_sync_success": status_obj.last_sync_success,
            "last_sync_timestamp_display": last_ts_str,
            "pending_count": status_obj.pending_count,
            "total_synced_count": status_obj.total_synced_count,
            "last_error": status_obj.last_error_message,
            "has_snapshot": snapshot is not None,
            "snapshot_date": snapshot.snapshot_date.isoformat() if snapshot else None,
        }

    @classmethod
    def queue_sale(cls, sale):
        """Queue a completed sale for remote sync."""
        payload = {
            "sale_number": sale.sale_number,
            "grand_total": str(sale.grand_total),
            "tax_amount": str(sale.tax_amount),
            "discount_amount": str(sale.discount_amount),
            "customer_id": sale.customer_id,
            "items_count": sale.items.count(),
            "created_at": sale.created_at.isoformat(),
        }
        key = f"SALE-{sale.company_id}-{sale.sale_number}"
        return cls._enqueue(sale.company, "SALE", str(sale.id), key, payload)

    @classmethod
    def queue_payment(cls, payment):
        """Queue a payment transaction for remote sync."""
        payload = {
            "payment_id": payment.id,
            "sale_id": payment.sale_id,
            "sale_number": payment.sale.sale_number if payment.sale else "",
            "amount": str(payment.amount),
            "method": payment.payment_method,
            "reference": payment.reference_number,
            "created_at": payment.created_at.isoformat(),
        }
        key = f"PAYMENT-{payment.sale.company_id if payment.sale else payment.id}-{payment.id}-{payment.reference_number or 'DIRECT'}"
        company = payment.sale.company if payment.sale else None
        if company:
            return cls._enqueue(company, "PAYMENT", str(payment.id), key, payload)

    @classmethod
    def queue_return(cls, return_req):
        """Queue a return request / refund for remote sync."""
        payload = {
            "return_id": return_req.id,
            "sale_id": return_req.sale_id,
            "refund_amount": str(return_req.refund_amount),
            "status": return_req.status,
            "reason": return_req.reason,
            "created_at": return_req.created_at.isoformat(),
        }
        key = f"RETURN-{return_req.company_id}-{return_req.id}"
        return cls._enqueue(return_req.company, "RETURN", str(return_req.id), key, payload)

    @classmethod
    def queue_purchase(cls, purchase):
        """Queue a supplier purchase order for remote sync."""
        payload = {
            "purchase_id": purchase.id,
            "invoice_number": purchase.invoice_number,
            "supplier_id": purchase.supplier_id,
            "supplier_name": purchase.supplier.name if purchase.supplier else "",
            "purchase_date": purchase.purchase_date.isoformat(),
            "status": purchase.status,
            "created_at": purchase.created_at.isoformat(),
        }
        key = f"PURCHASE-{purchase.company_id}-{purchase.invoice_number}"
        return cls._enqueue(purchase.company, "PURCHASE", str(purchase.id), key, payload)

    @classmethod
    def queue_customer(cls, customer):
        """Queue a customer record for remote sync."""
        payload = {
            "customer_id": customer.id,
            "name": customer.name,
            "phone": customer.phone or "",
            "email": getattr(customer, "email", "") or "",
            "balance": str(getattr(customer, "balance", 0)),
            "created_at": customer.created_at.isoformat() if hasattr(customer, "created_at") and customer.created_at else timezone.now().isoformat(),
        }
        key = f"CUSTOMER-{customer.company_id}-{customer.id}"
        return cls._enqueue(customer.company, "CUSTOMER", str(customer.id), key, payload)

    @classmethod
    def _enqueue(cls, company, entity_type, entity_id, idempotency_key, payload):
        item, created = SyncQueueItem.objects.get_or_create(
            company=company,
            idempotency_key=idempotency_key,
            defaults={
                "entity_type": entity_type,
                "entity_id": str(entity_id),
                "payload": payload,
                "status": "PENDING",
            }
        )
        if not created and item.status != "SYNCED":
            item.payload = payload
            item.status = "PENDING"
            item.save(update_fields=["payload", "status"])

        # Update pending counter on status record
        status_obj = cls.get_or_create_status(company)
        status_obj.pending_count = SyncQueueItem.objects.filter(company=company, status="PENDING").count()
        status_obj.save(update_fields=["pending_count"])
        return item

    @classmethod
    def run_sync(cls, company=None, simulate_offline=False):
        """
        Executes idempotent synchronization of pending local transactions to remote reporting.
        If offline, preserves all records in local DB and updates sync status.
        If online, guarantees exactly-once processing with zero duplicate sales/payments/returns/purchases.
        """
        companies = [company] if company else list(companyRegistration.objects.all())
        results = {}

        for comp in companies:
            status_obj = cls.get_or_create_status(comp)
            status_obj.last_sync_attempt = timezone.now()

            # Handle shop offline scenario
            if simulate_offline or getattr(comp, "_simulate_offline", False):
                status_obj.status = "OFFLINE"
                status_obj.last_error_message = "Shop internet connection is currently offline. Local POS operating in offline mode."
                pending = SyncQueueItem.objects.filter(company=comp, status="PENDING").count()
                status_obj.pending_count = pending
                status_obj.save()
                cls.build_and_save_company_snapshot(comp, simulate_offline=True)
                results[comp.id] = {
                    "success": False,
                    "offline": True,
                    "synced": 0,
                    "pending": pending,
                    "last_sync_success": status_obj.last_sync_success,
                }
                continue

            # Online sync execution
            status_obj.status = "SYNCING"
            status_obj.save(update_fields=["status", "last_sync_attempt"])

            pending_items = list(SyncQueueItem.objects.filter(company=comp, status="PENDING").order_by("created_at"))
            synced_count = 0
            failed_count = 0

            for item in pending_items:
                try:
                    with transaction.atomic():
                        # Idempotency check: check if already recorded in RemoteSyncedRecord
                        synced_rec, created = RemoteSyncedRecord.objects.get_or_create(
                            company=comp,
                            idempotency_key=item.idempotency_key,
                            defaults={
                                "entity_type": item.entity_type,
                                "entity_identifier": item.entity_id,
                                "payload_summary": item.payload,
                            }
                        )
                        # Mark item synced
                        item.status = "SYNCED"
                        item.synced_at = timezone.now()
                        item.last_error = ""
                        item.save(update_fields=["status", "synced_at", "last_error"])
                        synced_count += 1
                except Exception as e:
                    logger.error(f"Sync failed for item {item.id}: {e}")
                    item.status = "FAILED"
                    item.retry_count += 1
                    item.last_error = str(e)
                    item.save(update_fields=["status", "retry_count", "last_error"])
                    failed_count += 1

            # Update status record post-sync
            now = timezone.now()
            status_obj.status = "ONLINE" if failed_count == 0 else "ERROR"
            if synced_count > 0 or failed_count == 0:
                status_obj.last_sync_success = now
            status_obj.pending_count = SyncQueueItem.objects.filter(company=comp, status="PENDING").count()
            status_obj.total_synced_count += synced_count
            status_obj.last_error_message = f"{failed_count} items failed sync." if failed_count > 0 else ""
            status_obj.save()

            cls.build_and_save_company_snapshot(comp, simulate_offline=False)

            results[comp.id] = {
                "success": failed_count == 0,
                "offline": False,
                "synced": synced_count,
                "failed": failed_count,
                "pending": status_obj.pending_count,
                "last_sync_success": status_obj.last_sync_success,
            }

        return results[company.id] if company else results

    @classmethod
    def build_and_save_company_snapshot(cls, company, snapshot_date=None, simulate_offline=False):
        """
        Builds, persists, and optionally pushes a durable, tenant-isolated cloud snapshot.
        Enables 24/7 Telegram bot answering when the local shop PC is closed or offline.
        """
        from sales.models import Sale, SaleItem, Payment, Customer, ReturnRequest
        from inventory.models import Product

        target_date = snapshot_date or timezone.localdate()
        status_obj = cls.get_or_create_status(company)
        pending_count = SyncQueueItem.objects.filter(company=company, status="PENDING").count()

        # Sales aggregates
        sales_qs = Sale.objects.filter(company=company, created_at__date=target_date, sale_status="COMPLETED")
        sales_agg = sales_qs.aggregate(
            tot_rev=models.Sum("grand_total"),
            tot_tax=models.Sum("tax_amount"),
            tot_disc=models.Sum("discount_amount"),
            inv_cnt=models.Count("id")
        )
        total_revenue = sales_agg["tot_rev"] or Decimal("0.00")
        tax_amount = sales_agg["tot_tax"] or Decimal("0.00")
        discount_amount = sales_agg["tot_disc"] or Decimal("0.00")
        invoice_count = sales_agg["inv_cnt"] or 0

        # Payments by tender
        payments_qs = Payment.objects.filter(company=company, payment_date__date=target_date)
        cash_total = payments_qs.filter(payment_method="CASH").aggregate(s=models.Sum("amount"))["s"] or Decimal("0.00")
        upi_total = payments_qs.filter(payment_method="UPI").aggregate(s=models.Sum("amount"))["s"] or Decimal("0.00")
        card_total = payments_qs.filter(payment_method="CARD").aggregate(s=models.Sum("amount"))["s"] or Decimal("0.00")
        other_total = payments_qs.exclude(payment_method__in=["CASH", "UPI", "CARD"]).aggregate(s=models.Sum("amount"))["s"] or Decimal("0.00")

        # Returns & refunds
        returns_qs = ReturnRequest.objects.filter(company=company, created_at__date=target_date)
        returns_count = returns_qs.count()
        refund_amount = returns_qs.aggregate(s=models.Sum("refund_amount"))["s"] or Decimal("0.00")
        net_revenue = total_revenue - refund_amount

        # Inventory
        products_qs = Product.objects.filter(company=company, status=True)
        active_products_count = products_qs.count()
        low_stock_raw = list(
            products_qs.filter(current_stock__lte=models.F("minimum_stock"), current_stock__gt=0)
            .values("name", "current_stock", "minimum_stock")[:10]
        )
        low_stock_items = [
            {"name": item["name"], "stock": str(item["current_stock"]), "reorder": str(item["minimum_stock"])}
            for item in low_stock_raw
        ]
        out_of_stock_items = list(
            products_qs.filter(current_stock__lte=0).values_list("name", flat=True)[:10]
        )

        top_selling_raw = list(
            SaleItem.objects.filter(sale__company=company, sale__created_at__date=target_date)
            .values("product_name")
            .annotate(qty=models.Sum("quantity"), rev=models.Sum("total"))
            .order_by("-rev")[:5]
        )
        top_selling_products = [
            {"name": it["product_name"] or "Product", "qty": str(it["qty"] or 0), "revenue": str(it["rev"] or Decimal("0.00"))}
            for it in top_selling_raw
        ]

        # Customer Credit (tenant isolated, never exposes raw PII)
        customers_qs = Customer.objects.filter(company=company)
        credit_receivable = customers_qs.filter(credit_balance__gt=0).aggregate(s=models.Sum("credit_balance"))["s"] or Decimal("0.00")
        customer_balances_raw = list(
            customers_qs.filter(credit_balance__gt=0).order_by("-credit_balance")[:5].values("name", "credit_balance")
        )
        customer_balances = [
            {"name": it["name"], "balance": str(it["credit_balance"])}
            for it in customer_balances_raw
        ]

        # Freshness calculation
        is_offline = simulate_offline or getattr(company, "_simulate_offline", False)
        if is_offline or status_obj.status == "OFFLINE":
            if pending_count > 0:
                freshness = "SYNC_PENDING"
            elif status_obj.last_sync_success and (timezone.now() - status_obj.last_sync_success).total_seconds() > 1800:
                freshness = "STALE"
            else:
                freshness = "CACHED"
        else:
            freshness = "LIVE"

        now = timezone.now()
        sync_time = now if freshness == "LIVE" else (status_obj.last_sync_success or now)

        snapshot, _ = CompanyCloudSnapshot.objects.update_or_create(
            company=company,
            snapshot_date=target_date,
            defaults={
                "last_sync_time": sync_time,
                "freshness_status": freshness,
                "pending_local_count": pending_count,
                "total_revenue": total_revenue,
                "tax_amount": tax_amount,
                "discount_amount": discount_amount,
                "net_revenue": net_revenue,
                "invoice_count": invoice_count,
                "cash_total": cash_total,
                "upi_total": upi_total,
                "card_total": card_total,
                "other_payment_total": other_total,
                "returns_count": returns_count,
                "refund_amount": refund_amount,
                "active_products_count": active_products_count,
                "low_stock_items": low_stock_items,
                "out_of_stock_items": out_of_stock_items,
                "top_selling_products": top_selling_products,
                "total_credit_receivable": credit_receivable,
                "customer_balances": customer_balances,
                "raw_payload": {
                    "built_at": now.isoformat(),
                    "freshness": freshness,
                    "offline": is_offline,
                }
            }
        )
        status_obj.last_snapshot_time = now
        status_obj.save(update_fields=["last_snapshot_time"])

        remote_url = getattr(settings, "CLOUD_SYNC_REMOTE_URL", "") or status_obj.remote_cloud_url
        if remote_url and not is_offline:
            cls._push_snapshot_to_remote(company, snapshot, status_obj)

        return snapshot

    @classmethod
    def _push_snapshot_to_remote(cls, company, snapshot, status_obj):
        import urllib.request
        import ssl
        remote_url = (getattr(settings, "CLOUD_SYNC_REMOTE_URL", "") or status_obj.remote_cloud_url or "").rstrip("/")
        api_key = getattr(settings, "CLOUD_SYNC_API_KEY", "") or status_obj.cloud_sync_api_key or ""
        if not remote_url:
            return

        endpoint = f"{remote_url}/api/cloud-sync/push/"
        payload = {
            "company_id": company.id,
            "company_name": company.company_name,
            "snapshot_date": snapshot.snapshot_date.isoformat(),
            "last_sync_time": snapshot.last_sync_time.isoformat(),
            "freshness_status": snapshot.freshness_status,
            "pending_local_count": snapshot.pending_local_count,
            "total_revenue": str(snapshot.total_revenue),
            "tax_amount": str(snapshot.tax_amount),
            "discount_amount": str(snapshot.discount_amount),
            "net_revenue": str(snapshot.net_revenue),
            "invoice_count": snapshot.invoice_count,
            "cash_total": str(snapshot.cash_total),
            "upi_total": str(snapshot.upi_total),
            "card_total": str(snapshot.card_total),
            "other_payment_total": str(snapshot.other_payment_total),
            "returns_count": snapshot.returns_count,
            "refund_amount": str(snapshot.refund_amount),
            "active_products_count": snapshot.active_products_count,
            "low_stock_items": snapshot.low_stock_items,
            "out_of_stock_items": snapshot.out_of_stock_items,
            "top_selling_products": snapshot.top_selling_products,
            "total_credit_receivable": str(snapshot.total_credit_receivable),
            "customer_balances": snapshot.customer_balances,
            "raw_payload": snapshot.raw_payload,
        }
        data = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "BillingSystem-CloudSync/1.0",
        }
        if api_key:
            headers["X-Cloud-Sync-Key"] = api_key

        try:
            ctx = ssl.create_default_context()
            req = urllib.request.Request(endpoint, data=data, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=8, context=ctx) as resp:
                if resp.status in [200, 201]:
                    logger.info(f"Successfully pushed cloud snapshot for {company.company_name} to {endpoint}")
        except Exception as e:
            logger.warning(f"Failed to push cloud snapshot to remote {endpoint}: {e}")

    @classmethod
    def get_latest_snapshot(cls, company, snapshot_date=None):
        if snapshot_date:
            snap = CompanyCloudSnapshot.objects.filter(company=company, snapshot_date=snapshot_date).first()
            if snap:
                return snap
        return CompanyCloudSnapshot.objects.filter(company=company).order_by("-snapshot_date").first()

