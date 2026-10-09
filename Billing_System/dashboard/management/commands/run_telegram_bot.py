import time
import json
import urllib.request
import urllib.parse
from django.core.management.base import BaseCommand
from django.conf import settings
from dashboard.telegram_service import (
    is_bot_token_configured,
    handle_telegram_command,
    dispatch_outbound_messages,
    test_telegram_connection,
)


class Command(BaseCommand):
    help = "Run the Telegram Bot long-polling daemon to listen for messages and dispatch reports."

    def add_arguments(self, parser):
        parser.add_argument(
            "--once",
            action="store_true",
            help="Poll once for pending updates and exit (useful for testing or cron jobs).",
        )
        parser.add_argument(
            "--timeout",
            type=int,
            default=15,
            help="Long-polling timeout in seconds (default: 15).",
        )

    def handle(self, *args, **options):
        if not is_bot_token_configured():
            self.stderr.write(
                self.style.ERROR(
                    "TELEGRAM_BOT_TOKEN is not configured in .env. Cannot start Telegram Bot daemon."
                )
            )
            return

        token = getattr(settings, "TELEGRAM_BOT_TOKEN", "").strip()

        # 1. Verify bot connectivity
        conn_res = test_telegram_connection()
        if not conn_res["success"]:
            self.stderr.write(
                self.style.ERROR(f"Telegram connection check failed: {conn_res['message']}")
            )
            return

        bot_username = conn_res.get("bot_username", "@Bot")
        self.stdout.write(
            self.style.SUCCESS(
                f"[BOT] Telegram Bot connected successfully: {bot_username}\n"
                f"[LISTENING] Listening for incoming messages from users...\n"
                f"   (Press Ctrl+C to stop)"
            )
        )

        poll_timeout = options["timeout"]
        run_once = options["once"]
        last_update_id = None

        while True:
            try:
                # 1. Fetch updates from Telegram Bot API (getUpdates)
                url = f"https://api.telegram.org/bot{token}/getUpdates?timeout={poll_timeout}"
                if last_update_id is not None:
                    url += f"&offset={last_update_id + 1}"

                req = urllib.request.Request(
                    url,
                    headers={"User-Agent": "BillingSystem-POS/1.0 (Python)"}
                )

                try:
                    with urllib.request.urlopen(req, timeout=poll_timeout + 5) as resp:
                        data = json.loads(resp.read().decode("utf-8"))
                except Exception as net_err:
                    self.stdout.write(f"Connection timeout or temporary error: {net_err}. Retrying...")
                    time.sleep(3)
                    if run_once:
                        break
                    continue

                if not data.get("ok"):
                    self.stderr.write(f"Telegram API error: {data.get('description')}")
                    time.sleep(5)
                    if run_once:
                        break
                    continue

                updates = data.get("result", [])
                for upd in updates:
                    update_id = upd.get("update_id")
                    if update_id is not None:
                        last_update_id = update_id

                    # Check for text message
                    message = upd.get("message") or upd.get("edited_message")
                    if not message:
                        continue

                    chat = message.get("chat", {})
                    chat_id = chat.get("id")
                    from_user = message.get("from", {})
                    username = from_user.get("username", from_user.get("first_name", "User"))
                    text = message.get("text", "").strip()

                    if not chat_id or not text:
                        continue

                    self.stdout.write(
                        self.style.NOTICE(f"[MSG] [{bot_username}] From @{username} (chat_id: {chat_id}): '{text}'")
                    )

                    # Process command through authoritative tenant-isolated handler
                    try:
                        reply = handle_telegram_command(chat_id, text)
                        self.stdout.write(f"   -> Reply dispatched ({len(reply)} chars)")
                    except Exception as handler_err:
                        self.stderr.write(
                            self.style.ERROR(f"Error handling message: {str(handler_err)}")
                        )

                # 2. Dispatch any queued outbound messages
                dispatched = dispatch_outbound_messages()
                if dispatched > 0:
                    self.stdout.write(self.style.SUCCESS(f"Dispatched {dispatched} queued outbound messages."))

                if run_once:
                    self.stdout.write("Single poll cycle complete (--once). Exiting.")
                    break

                time.sleep(0.5)

            except KeyboardInterrupt:
                self.stdout.write(self.style.SUCCESS("\nTelegram bot daemon stopped by user."))
                break
            except Exception as e:
                self.stderr.write(self.style.ERROR(f"Unexpected error in bot polling loop: {str(e)}"))
                time.sleep(5)
                if run_once:
                    break
