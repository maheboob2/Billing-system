"""
Demo Session Isolation Middleware for College Demonstrations.

RFC 6265 Origin Boundary:
Ordinary Django session cookies (`sessionid`) are stored per-browser-profile, partitioned
by Origin (hostname + port). When multiple tabs share the same origin (e.g. localhost:8000),
logging in on Tab 2 overwrites Tab 1's cookie in the browser's cookie jar.

In College Demo Mode:
1. Multi-Origin Host Isolation:
   Tab 1: http://localhost:8000 (Owner)
   Tab 2: http://127.0.0.1:8000 (Manager)
   Tab 3: http://127.0.0.2:8000 (Cashier)
   The browser maintains completely independent cookie jars for each distinct loopback host,
   allowing independent sessions across tabs within the same browser window and profile.

2. Test-Client & API Support:
   Accepts `X-Demo-Session-Key` header during automated tests or API testing to bind
   to a specific session without cookie crosstalk.
"""

import logging
from importlib import import_module
from django.conf import settings
from django.contrib.auth import get_user

logger = logging.getLogger(__name__)


class DemoSessionIsolationMiddleware:
    """
    Middleware providing session isolation diagnostics and optional header-based session scoping
    when COLLEGE_DEMO_MODE is True. Zero overhead when COLLEGE_DEMO_MODE is False.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        is_demo_mode = getattr(settings, "COLLEGE_DEMO_MODE", False)
        if not is_demo_mode:
            return self.get_response(request)

        # Inspect request origin and host
        raw_host = request.get_host().split(":")[0].lower()
        if raw_host == "localhost":
            demo_role_hint = "Owner"
        elif raw_host == "127.0.0.1":
            demo_role_hint = "Manager"
        elif raw_host == "127.0.0.2":
            demo_role_hint = "Cashier"
        elif raw_host == "127.0.0.3":
            demo_role_hint = "Worker"
        else:
            demo_role_hint = "General"

        request.demo_mode = True
        request.demo_role_hint = demo_role_hint

        # Header-based session scoping for automated testing clients
        custom_session_key = request.headers.get("X-Demo-Session-Key")
        if custom_session_key and hasattr(request, "session"):
            try:
                engine = getattr(settings, "SESSION_ENGINE", "django.contrib.sessions.backends.db")
                session_store = import_module(engine).SessionStore(session_key=custom_session_key)
                if session_store.exists(custom_session_key):
                    request.session = session_store
                    request.user = get_user(request)
            except Exception as e:
                logger.debug("Demo custom session lookup error: %s", e)

        response = self.get_response(request)

        # Annotate demo response headers for browser/client inspection
        response["X-Demo-Mode"] = "active"
        response["X-Demo-Host-Role"] = demo_role_hint
        return response
