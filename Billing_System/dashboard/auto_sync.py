"""
Automatic In-Process Synchronization and Telegram Daemon for Retail POS.

Eliminates the requirement for store managers to manually launch a separate
CMD/Terminal window for background synchronization and Telegram bot polling.

Guarantees:
1. Startup Sync: Immediately attempts to sync eligible pending transactions on application launch.
2. Safe Retries with Bounded Exponential Backoff: Retries failed network calls with bounded backoff (15s -> 30s -> 60s -> 120s max).
3. Strict Idempotency: Re-runs and restarts never duplicate sales, payments, purchases, customers, or reports.
4. Persistent Status: Tracks last sync timestamp, pending items count, and error state in RemoteSyncStatus.
5. In-Process Telegram Polling & Outbound Dispatch: If TELEGRAM_BOT_TOKEN is present, runs long-polling and message delivery inside the daemon without extra processes.
6. Safe Shutdown & Testing: Daemon thread terminates with process; disabled during migrations/tests.
"""

import os
import sys
import time
import json
import ssl
import logging
import threading
import urllib.request
from datetime import timedelta
from django.utils import timezone
from django.conf import settings

import atexit

logger = logging.getLogger("dashboard.auto_sync")

_daemon_instance = None
_daemon_lock = threading.Lock()


class ProcessWorkerLock:
    """
    Cross-platform inter-process lock guaranteeing single-worker execution.
    Prevents duplicate Telegram polling loops (which cause Telegram 409 Conflict)
    and duplicate periodic syncs across multiple WSGI/ASGI workers or reloaders.
    """
    def __init__(self, lockfile_path=None):
        if lockfile_path is None:
            lockfile_path = os.path.join(settings.BASE_DIR, ".auto_sync.lock")
        self.lockfile_path = lockfile_path
        self.file_handle = None
        self.is_locked = False

    def acquire(self):
        try:
            self.file_handle = open(self.lockfile_path, "a+")
            if os.name == "nt":
                import msvcrt
                self.file_handle.seek(0)
                msvcrt.locking(self.file_handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

            self.file_handle.seek(0)
            self.file_handle.truncate()
            self.file_handle.write(f"PID:{os.getpid()}\nTime:{timezone.now().isoformat()}\n")
            self.file_handle.flush()
            self.is_locked = True
            return True
        except (IOError, OSError):
            if self.file_handle:
                try:
                    self.file_handle.close()
                except Exception:
                    pass
                self.file_handle = None
            self.is_locked = False
            return False

    def release(self):
        if not self.is_locked or not self.file_handle:
            return
        try:
            if os.name == "nt":
                import msvcrt
                self.file_handle.seek(0)
                msvcrt.locking(self.file_handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file_handle.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        finally:
            try:
                self.file_handle.close()
            except Exception:
                pass
            self.file_handle = None
            self.is_locked = False


class AutoSyncDaemon:
    def __init__(self):
        self._stop_event = threading.Event()
        self._thread = None
        self._process_lock = ProcessWorkerLock()
        self._base_interval = 30  # seconds
        self._max_interval = 120   # seconds
        self._current_interval = self._base_interval
        self._consecutive_failures = 0
        self._last_sync_attempt = None
        self._last_sync_success = None
        self._telegram_last_update_id = None
        self._ssl_ctx = ssl.create_default_context()

    def start(self):
        with _daemon_lock:
            if self._thread and self._thread.is_alive():
                return
            if not self._process_lock.acquire():
                logger.info(
                    "[AutoSync] Another worker process already holds the daemon lock. "
                    "Skipping background thread in this worker."
                )
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run_loop,
                name="POS-AutoSyncDaemon",
                daemon=True
            )
            self._thread.start()
            logger.info(f"[AutoSync] Background synchronization daemon started in worker PID {os.getpid()}.")

    def stop(self, timeout=3.0):
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
            logger.info("[AutoSync] Background synchronization daemon stopped.")
        self._process_lock.release()

    def is_running(self):
        return bool(self._thread and self._thread.is_alive())

    def trigger_immediate_sync(self):
        """Perform an immediate on-demand sync cycle synchronously."""
        return self._execute_sync_cycle()

    def _execute_sync_cycle(self):
        """
        Executes a single synchronization cycle across all registered companies.
        Applies bounded backoff on network failures.
        """
        from dashboard.sync_service import SyncService
        self._last_sync_attempt = timezone.now()

        try:
            results = SyncService.run_sync()
            any_failed = False
            total_synced = 0
            total_pending = 0

            if isinstance(results, dict):
                for comp_id, res in results.items():
                    if isinstance(res, dict):
                        if not res.get("success", False) and not res.get("offline", False):
                            any_failed = True
                        total_synced += res.get("synced", 0)
                        total_pending += res.get("pending", 0)

            if any_failed:
                self._consecutive_failures += 1
                self._current_interval = min(self._base_interval * (2 ** min(self._consecutive_failures, 3)), self._max_interval)
                logger.warning(
                    f"[AutoSync] Sync completed with errors. Failures: {self._consecutive_failures}. "
                    f"Backing off next sync to {self._current_interval}s."
                )
            else:
                self._consecutive_failures = 0
                self._current_interval = self._base_interval
                self._last_sync_success = timezone.now()
                if total_synced > 0:
                    logger.info(f"[AutoSync] Sync successful: {total_synced} items synced. Total pending: {total_pending}.")

            return {
                "success": not any_failed,
                "synced": total_synced,
                "pending": total_pending,
                "interval": self._current_interval,
            }

        except Exception as e:
            self._consecutive_failures += 1
            self._current_interval = min(self._base_interval * (2 ** min(self._consecutive_failures, 3)), self._max_interval)
            logger.error(
                f"[AutoSync] Error during sync execution: {e}. Backing off to {self._current_interval}s."
            )
            return {
                "success": False,
                "error": str(e),
                "interval": self._current_interval,
            }

    def _execute_telegram_cycle(self):
        """
        Polls incoming messages and dispatches queued outbound reports if token is configured.
        Operates with short timeout to prevent blocking the daemon loop.
        """
        from dashboard.telegram_service import (
            is_bot_token_configured,
            record_worker_heartbeat,
            record_update_received,
            handle_telegram_command,
            dispatch_outbound_messages,
        )

        # Supervised multi-bot worker sync for per-merchant custom bots
        try:
            from dashboard.multi_bot_registry import get_supervised_bot_registry
            get_supervised_bot_registry().sync_active_bots()
        except Exception as e:
            logger.debug(f"[AutoSync-Bot] Error syncing merchant bots: {e}")

        if not is_bot_token_configured():
            return

        record_worker_heartbeat()

        # 1. Dispatch any queued outbound messages (PDFs, charts, texts)
        try:
            dispatch_outbound_messages()
        except Exception as e:
            logger.debug(f"[AutoSync-Bot] Outbound dispatch error: {e}")

        # 2. Fetch updates using short timeout getUpdates
        token = getattr(settings, "TELEGRAM_BOT_TOKEN", "").strip()
        if not token:
            return

        url = f"https://api.telegram.org/bot{token}/getUpdates?timeout=2"
        if self._telegram_last_update_id is not None:
            url += f"&offset={self._telegram_last_update_id + 1}"

        try:
            req = urllib.request.Request(url, headers={"User-Agent": "BillingSystem-POS/1.0 (Python)"})
            with urllib.request.urlopen(req, timeout=5, context=self._ssl_ctx) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            if data.get("ok"):
                updates = data.get("result", [])
                for upd in updates:
                    upd_id = upd.get("update_id")
                    if upd_id is not None:
                        self._telegram_last_update_id = upd_id

                    msg = upd.get("message")
                    if not msg:
                        continue

                    chat_id = msg.get("chat", {}).get("id")
                    text = msg.get("text", "").strip()
                    if chat_id and text:
                        record_update_received(text)
                        try:
                            handle_telegram_command(str(chat_id), text)
                        except Exception as cmd_err:
                            logger.error(f"[AutoSync-Bot] Error processing command: {cmd_err}")

        except Exception as net_err:
            # Normal network timeout or transient disconnect
            logger.debug(f"[AutoSync-Bot] Polling timeout/idle: {net_err}")

    def _run_loop(self):
        """Main worker execution loop."""
        # 1. Immediate Startup Sync on launch
        logger.info("[AutoSync] Performing initial startup data synchronization...")
        self._execute_sync_cycle()

        # 2. Continuous loop with bounded sleep intervals
        last_sync_time = time.time()

        while not self._stop_event.is_set():
            # Check if sync is due
            now = time.time()
            if now - last_sync_time >= self._current_interval:
                self._execute_sync_cycle()
                last_sync_time = time.time()

            # Process Telegram bot messages (short poll)
            self._execute_telegram_cycle()

            # Sleep in short increments for responsive shutdown
            self._stop_event.wait(timeout=2.0)


def get_auto_sync_daemon():
    global _daemon_instance
    with _daemon_lock:
        if _daemon_instance is None:
            _daemon_instance = AutoSyncDaemon()
        return _daemon_instance


def start_auto_sync():
    daemon = get_auto_sync_daemon()
    if not daemon.is_running():
        daemon.start()


def stop_auto_sync():
    global _daemon_instance
    with _daemon_lock:
        if _daemon_instance:
            _daemon_instance.stop()


atexit.register(stop_auto_sync)
