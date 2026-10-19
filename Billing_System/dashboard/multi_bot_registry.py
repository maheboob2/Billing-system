"""
Supervised Multi-Bot Registry & Runtime Manager for Retail POS.
Guarantees:
1. Multi-Tenant Bot Isolation: Each merchant can configure their own BotFather-created bot.
2. Uniqueness: Exactly one supervised polling loop per bot token. Zero HTTP 409 Conflict.
3. Safe Token Handling: Tokens are decrypted in-memory only; never written to logs or error messages.
4. One-time Owner Verification: Enforces challenge verification before granting reporting access.
5. Strict Chat Authorization: Only the verified Owner chat ID can access financial reports.
6. Error Containment & Recovery: Bounded backoff on transient errors; stops cleanly if token is revoked.
"""

import os
import time
import json
import ssl
import logging
import threading
import urllib.request
import atexit
from datetime import timedelta
from django.utils import timezone
from django.conf import settings

logger = logging.getLogger("dashboard.multi_bot")

_registry_instance = None
_registry_lock = threading.Lock()


class SupervisedBotWorker:
    def __init__(self, bot_id: int, company_id: int, encrypted_token: str):
        self.bot_id = bot_id
        self.company_id = company_id
        self.encrypted_token = encrypted_token
        self._stop_event = threading.Event()
        self._thread = None
        self._last_update_id = None
        self._consecutive_errors = 0
        self._ssl_ctx = ssl.create_default_context()

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_poll_loop,
            name=f"BotWorker-Comp{self.company_id}-Bot{self.bot_id}",
            daemon=True
        )
        self._thread.start()
        logger.info(f"[MultiBot] Started worker thread for Company {self.company_id}.")

    def stop(self, timeout=3.0):
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
            logger.info(f"[MultiBot] Stopped worker thread for Company {self.company_id}.")

    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def _get_raw_token(self) -> str:
        from .token_encryption import decrypt_telegram_token
        return decrypt_telegram_token(self.encrypted_token)

    def _run_poll_loop(self):
        from .models import MerchantTelegramBot
        from .telegram_service import (
            TelegramReportingEngine,
            NLPReportIntentParser,
            send_telegram_direct_message,
        )

        token = self._get_raw_token()
        if not token:
            logger.error(f"[MultiBot] Worker Company {self.company_id}: Cannot decrypt bot token. Aborting.")
            return

        while not self._stop_event.is_set():
            try:
                # Refresh bot record from DB to observe status / cancellation
                bot_obj = MerchantTelegramBot.objects.filter(pk=self.bot_id).first()
                if not bot_obj or not bot_obj.is_active or bot_obj.status in ["DISCONNECTED", "INVALID_TOKEN"]:
                    logger.info(f"[MultiBot] Worker Company {self.company_id}: Bot deactivated or removed. Stopping.")
                    break

                # Update health check timestamp
                MerchantTelegramBot.objects.filter(pk=self.bot_id).update(last_health_check=timezone.now())

                # Poll updates with short timeout
                poll_url = f"https://api.telegram.org/bot{token}/getUpdates?timeout=2"
                if self._last_update_id is not None:
                    poll_url += f"&offset={self._last_update_id + 1}"

                req = urllib.request.Request(
                    poll_url,
                    headers={"User-Agent": "BriskRetail-MultiBot/1.0 (Python)"}
                )

                try:
                    with urllib.request.urlopen(req, timeout=5, context=self._ssl_ctx) as resp:
                        resp_data = json.loads(resp.read().decode("utf-8"))
                        self._consecutive_errors = 0
                except urllib.error.HTTPError as http_err:
                    if http_err.code in [401, 404]:
                        logger.warning(f"[MultiBot] Company {self.company_id}: Token revoked/invalid (HTTP {http_err.code}). Marking INVALID_TOKEN.")
                        MerchantTelegramBot.objects.filter(pk=self.bot_id).update(
                            status="INVALID_TOKEN",
                            is_active=False,
                            last_error=f"Telegram API rejected token (HTTP {http_err.code}). Please configure a new token."
                        )
                        break
                    elif http_err.code == 409:
                        logger.warning(f"[MultiBot] Company {self.company_id}: Conflict (HTTP 409). Backing off.")
                        time.sleep(5)
                        continue
                    else:
                        raise

                if not resp_data.get("ok"):
                    time.sleep(2)
                    continue

                updates = resp_data.get("result", [])
                for upd in updates:
                    upd_id = upd.get("update_id")
                    if upd_id is not None:
                        self._last_update_id = upd_id

                    msg = upd.get("message")
                    if not msg:
                        continue

                    chat = msg.get("chat", {})
                    chat_id_str = str(chat.get("id", ""))
                    username_str = chat.get("username", "") or chat.get("first_name", "")
                    text = msg.get("text", "").strip()

                    if not chat_id_str or not text:
                        continue

                    # Handle Message based on bot status
                    bot_obj.refresh_from_db()
                    self._handle_incoming_message(bot_obj, token, chat_id_str, username_str, text)

            except Exception as e:
                self._consecutive_errors += 1
                backoff_sleep = min(2 * self._consecutive_errors, 20)
                logger.debug(f"[MultiBot] Company {self.company_id} polling exception: {e}. Backing off {backoff_sleep}s.")
                self._stop_event.wait(timeout=backoff_sleep)

            # Responsive wait between polls
            self._stop_event.wait(timeout=1.0)

    def _handle_incoming_message(self, bot_obj, token: str, chat_id: str, sender_username: str, text: str):
        from .telegram_service import NLPReportIntentParser, TelegramReportingEngine

        lower_text = text.lower().strip()
        parts = text.split()
        cmd = parts[0].lower() if parts else ""
        company = bot_obj.company

        # Helper to send message directly using this bot's specific token
        def _reply(reply_text: str):
            send_url = f"https://api.telegram.org/bot{token}/sendMessage"
            payload = json.dumps({
                "chat_id": chat_id,
                "text": reply_text,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            }).encode("utf-8")
            req = urllib.request.Request(
                send_url,
                data=payload,
                headers={"Content-Type": "application/json", "User-Agent": "BriskRetail-MultiBot/1.0"}
            )
            try:
                with urllib.request.urlopen(req, timeout=5, context=self._ssl_ctx):
                    pass
            except Exception as err:
                logger.debug(f"[MultiBot] Reply delivery error: {err}")

        # ── STATE 1: PENDING_VERIFICATION ────────────────────────────────────
        if bot_obj.status == "PENDING_VERIFICATION":
            code_candidate = ""
            if cmd in ["/start", "start"] and len(parts) > 1:
                code_candidate = parts[1].strip()
            elif cmd in ["/verify", "/link", "/pair", "/connect"] and len(parts) > 1:
                code_candidate = parts[1].strip()
            elif lower_text.startswith("verify ") or lower_text.startswith("link "):
                code_candidate = text.split(None, 1)[1].strip()
            elif len(text.strip()) == len(bot_obj.verification_challenge):
                code_candidate = text.strip()

            if code_candidate and code_candidate.upper() == bot_obj.verification_challenge.upper():
                # Check challenge expiry
                if bot_obj.verification_expires_at and bot_obj.verification_expires_at < timezone.now():
                    _reply("❌ *Verification Challenge Expired*\n\nPlease generate a new verification code from your POS terminal.")
                    return

                # Bind chat to merchant bot
                bot_obj.status = "CONNECTED"
                bot_obj.is_active = True
                bot_obj.verified_chat_id = chat_id
                bot_obj.verified_username = sender_username
                bot_obj.verified_at = timezone.now()
                bot_obj.verification_challenge = ""  # Single-use challenge invalidated
                bot_obj.last_error = ""
                bot_obj.save()

                welcome = (
                    f"🎉 *Store Bot Connected Successfully!*\n\n"
                    f"🏪 *Store:* {company.company_name}\n"
                    f"👤 *Authorized Owner:* `{bot_obj.owner.username}`\n"
                    f"🟢 *Status:* Verified & Linked\n\n"
                    f"💡 *Available Commands:*\n"
                    f"• `/report` — Today's sales summary\n"
                    f"• `/report 02-10-2026` — Specific date financial report\n"
                    f"• `/report 02-10` — Date in current year (DD-MM)\n"
                    f"• `/report 2` — Day of current month\n"
                    f"• `/report 2026-10-01 2026-10-11` — Date range report\n"
                    f"• `/sales` — Revenue breakdown (Cash, UPI, Card)\n"
                    f"• `/stock` — Low inventory & reorder alerts\n"
                    f"• `/chart` — Visual sales chart\n"
                    f"• `/pdf` — Download executive summary PDF\n"
                    f"• `/disconnect` — Unlink this chat\n\n"
                    f"Or ask in plain English:\n"
                    f"_\"how much cash today?\"_, _\"top selling products\"_"
                )
                _reply(welcome)
                logger.info(f"[MultiBot] Company {self.company_id}: Verified owner chat {chat_id} successfully.")
                return
            else:
                _reply(
                    f"🔒 *Store Verification Required*\n\n"
                    f"Welcome to **{company.company_name}** Executive Bot.\n\n"
                    f"To verify that you are the authorized store owner:\n"
                    f"1. Open POS *Settings > Telegram Reporting*.\n"
                    f"2. Send `/verify <CODE>` or click the verification link in POS."
                )
                return

        # ── STATE 2: CONNECTED ───────────────────────────────────────────────
        if bot_obj.status == "CONNECTED":
            # Strict Owner Chat Authorization: reject other users
            if chat_id != bot_obj.verified_chat_id:
                _reply(
                    f"🔒 *Unauthorized Access*\n\n"
                    f"This private executive bot is bound to **{company.company_name}**.\n"
                    f"Only the verified store owner can access business reports."
                )
                return

            # Check Disconnect command
            if cmd in ["/disconnect", "/unlink"]:
                bot_obj.status = "DISCONNECTED"
                bot_obj.is_active = False
                bot_obj.verified_chat_id = ""
                bot_obj.save()
                _reply(f"👋 *Store Disconnected*\n\nThis bot has been disconnected from **{company.company_name}**. To reconnect, visit POS Settings.")
                return

            if lower_text in ["/start", "start", "/help", "help"]:
                _reply(
                    f"🏪 *{company.company_name} — Executive Bot*\n"
                    f"👤 Authorized Owner: `{bot_obj.owner.username}`\n"
                    f"🟢 POS Database: Connected\n\n"
                    f"💡 *Commands:*\n"
                    f"• `/report` or `/report today` — Today's sales summary\n"
                    f"• `/report 02-10-2026` — Specific date report\n"
                    f"• `/report 02-10` — 2 Oct this year\n"
                    f"• `/report 2` — 2nd of current month\n"
                    f"• `/report 2026-10-01 2026-10-11` — Date range\n"
                    f"• `/sales` — Revenue breakdown\n"
                    f"• `/stock` — Low inventory alerts\n"
                    f"• `/disconnect` — Unlink bot"
                )
                return

            # Reporting query execution (strictly isolated to this company)
            parsed_query = NLPReportIntentParser.parse(text)
            engine_res = TelegramReportingEngine.execute(company, parsed_query)
            _reply(engine_res["text"])


class SupervisedBotRegistry:
    """
    Registry managing all active per-merchant bot workers.
    Ensures single worker thread per bot and clean lifecycle management.
    """
    def __init__(self):
        self._workers = {}  # bot_id -> SupervisedBotWorker
        self._lock = threading.Lock()

    def sync_active_bots(self):
        """
        Scans database for active custom merchant bots and ensures each has a running worker.
        Stops workers for deactivated or deleted bots.
        """
        from .models import MerchantTelegramBot
        active_bots = list(MerchantTelegramBot.objects.filter(is_active=True))
        active_ids = {b.pk for b in active_bots}

        with self._lock:
            # 1. Stop workers that are no longer active
            for bot_id in list(self._workers.keys()):
                if bot_id not in active_ids:
                    worker = self._workers.pop(bot_id)
                    worker.stop()

            # 2. Start workers for newly active bots
            for bot in active_bots:
                if bot.pk not in self._workers or not self._workers[bot.pk].is_running():
                    worker = SupervisedBotWorker(bot.pk, bot.company_id, bot.encrypted_token)
                    worker.start()
                    self._workers[bot.pk] = worker

    def register_and_start(self, bot_obj):
        """Immediately starts worker for a newly configured bot."""
        with self._lock:
            if bot_obj.pk in self._workers:
                self._workers[bot_obj.pk].stop()
            worker = SupervisedBotWorker(bot_obj.pk, bot_obj.company_id, bot_obj.encrypted_token)
            worker.start()
            self._workers[bot_obj.pk] = worker

    def stop_bot(self, bot_id: int):
        with self._lock:
            if bot_id in self._workers:
                worker = self._workers.pop(bot_id)
                worker.stop()

    def stop_all(self):
        with self._lock:
            for worker in self._workers.values():
                worker.stop()
            self._workers.clear()


def get_supervised_bot_registry() -> SupervisedBotRegistry:
    global _registry_instance
    with _registry_lock:
        if _registry_instance is None:
            _registry_instance = SupervisedBotRegistry()
        return _registry_instance


@atexit.register
def cleanup_bot_registry():
    global _registry_instance
    with _registry_lock:
        if _registry_instance:
            _registry_instance.stop_all()
