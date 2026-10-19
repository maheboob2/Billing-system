import os
import sys
from django.apps import AppConfig


class DashboardConfig(AppConfig):
    name = 'dashboard'

    def ready(self):
        # Exclude management commands like migrate, test, makemigrations, collectstatic, check, run_sync_worker, run_telegram_bot
        skip_commands = [
            "migrate", "makemigrations", "test", "createsuperuser",
            "collectstatic", "check", "shell", "run_sync_worker", "run_telegram_bot"
        ]
        is_management_cmd = any(cmd in sys.argv for cmd in skip_commands)

        # In runserver, only start in the reloader worker child (RUN_MAIN == 'true')
        # In production WSGI/ASGI, RUN_MAIN is not set, so check if not running management cmd
        is_runserver = "runserver" in sys.argv
        is_runserver_child = os.environ.get("RUN_MAIN") == "true"

        should_start = (is_runserver and is_runserver_child) or (not is_runserver and not is_management_cmd)

        from django.conf import settings
        enable_auto_sync = getattr(settings, "ENABLE_AUTO_SYNC", True)

        if should_start and enable_auto_sync:
            try:
                from dashboard.auto_sync import start_auto_sync
                start_auto_sync()
            except Exception as e:
                import logging
                logging.getLogger("dashboard.apps").warning(f"Could not initialize auto-sync daemon: {e}")

