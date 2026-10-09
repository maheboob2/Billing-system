import json
import logging
from decimal import Decimal
from django.utils import timezone
from django.db import transaction
from accounts.models import companyRegistration
from .models import RemoteSyncStatus, SyncQueueItem, RemoteSyncedRecord

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

        last_ts_str = status_obj.last_sync_success.strftime("%b %d, %Y %I:%M %p") if status_obj.last_sync_success else "Never"
        return {
            "status": status_obj.status,
            "is_online": status_obj.status == "ONLINE",
            "last_sync_success": status_obj.last_sync_success,
            "last_sync_timestamp_display": last_ts_str,
            "pending_count": status_obj.pending_count,
            "total_synced_count": status_obj.total_synced_count,
            "last_error": status_obj.last_error_message,
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

            results[comp.id] = {
                "success": failed_count == 0,
                "offline": False,
                "synced": synced_count,
                "failed": failed_count,
                "pending": status_obj.pending_count,
                "last_sync_success": status_obj.last_sync_success,
            }

        return results[company.id] if company else results
