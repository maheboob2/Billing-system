"""
Management command: run_sync_worker

Durable background synchronization worker for Offline-First Retail POS.
Pushes queued transactional outbox records (Sales, Payments, Returns, Purchases,
Customers, Stock Movements) and materialized business snapshots to the authenticated
Cloud Sync API.

Usage:
    python manage.py run_sync_worker
    python manage.py run_sync_worker --once
    python manage.py run_sync_worker --interval 15 --company 1
"""

import sys
import time
import signal
import logging
from django.core.management.base import BaseCommand
from django.utils import timezone
from accounts.models import companyRegistration
from dashboard.sync_service import SyncService
from dashboard.auto_sync import ProcessWorkerLock

logger = logging.getLogger("dashboard.sync_worker")


class Command(BaseCommand):
    help = "Run the offline-first background cloud synchronization worker daemon."

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._interrupted = False

    def add_arguments(self, parser):
        parser.add_argument(
            "--interval",
            type=int,
            default=30,
            help="Polling interval between sync runs in seconds (default: 30).",
        )
        parser.add_argument(
            "--once",
            action="store_true",
            help="Run a single sync pass across eligible stores and exit immediately.",
        )
        parser.add_argument(
            "--company",
            type=str,
            default="",
            help="Limit synchronization to a specific Company ID or Company Name.",
        )

    def _handle_signal(self, signum, frame):
        self._interrupted = True
        self.stdout.write(self.style.WARNING("\n[SYNC-WORKER] Shutdown signal received. Finishing active cycle..."))

    def handle(self, *args, **options):
        interval = options["interval"]
        run_once = options["once"]
        company_filter = options["company"].strip()

        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

        # Acquire lock to ensure only one standalone sync worker runs concurrently
        lock = ProcessWorkerLock()
        if not lock.acquire():
            self.stdout.write(
                self.style.WARNING("[SYNC-WORKER] Another sync worker already holds the process lock. Exiting cleanly.")
            )
            return

        self.stdout.write(
            self.style.SUCCESS(
                f"[SYNC-WORKER] Starting Offline-First Cloud Sync Worker\n"
                f"  Interval: {interval}s | Run Once: {run_once} | Filter: {company_filter or 'ALL'}\n"
                f"  Durable Outbox -> Authenticated Cloud API -> Idempotent Ingestion\n"
                f"  (Press Ctrl+C to terminate gracefully)"
            )
        )

        try:
            while not self._interrupted:
                self._run_sync_cycle(company_filter)

                if run_once or self._interrupted:
                    break

                # Sleep in short increments for responsive interrupt handling
                for _ in range(max(1, interval)):
                    if self._interrupted:
                        break
                    time.sleep(1)

        finally:
            lock.release()
            self.stdout.write(self.style.SUCCESS("[SYNC-WORKER] Worker shutdown complete."))

    def _run_sync_cycle(self, company_filter):
        now_str = timezone.now().strftime("%Y-%m-%d %H:%M:%S")

        companies = companyRegistration.objects.all()
        if company_filter:
            if company_filter.isdigit():
                companies = companies.filter(id=int(company_filter))
            else:
                companies = companies.filter(company_name__icontains=company_filter)

        if not companies.exists():
            self.stdout.write(self.style.WARNING(f"[{now_str}] No matching companies found for sync."))
            return

        for company in companies:
            try:
                res = SyncService.run_sync(company=company)
                success = res.get("success", False)
                offline = res.get("offline", False)
                synced = res.get("synced", 0)
                pending = res.get("pending", 0)
                failed = res.get("failed", 0)
                checkpoint = res.get("checkpoint", 0)

                if success and not offline:
                    status_badge = self.style.SUCCESS("ONLINE OK")
                    details = f"synced: {synced}, pending: {pending}, checkpoint: {checkpoint}"
                elif offline:
                    status_badge = self.style.WARNING("OFFLINE (BUFFERING)")
                    details = f"pending in outbox: {pending}, retry backoff active"
                else:
                    status_badge = self.style.ERROR("SYNC FAILED")
                    details = f"failed: {failed}, pending: {pending}, retry backoff active"

                self.stdout.write(
                    f"[{now_str}] [{company.company_name} (ID: {company.id})] {status_badge} - {details}"
                )
            except Exception as e:
                self.stderr.write(
                    self.style.ERROR(f"[{now_str}] [{company.company_name}] Error during sync: {e}")
                )
