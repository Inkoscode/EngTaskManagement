# ------------------------------------------------------------------
# EMAIL  -> paste this at the BOTTOM of core_portal/settings.py
# The real values live in core_portal/email_settings.py
# ------------------------------------------------------------------

# Defaults used when email_settings.py is missing: emails are printed in the terminal, not sent.
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
DEFAULT_FROM_EMAIL = "portal@localhost"
SITE_URL = "http://127.0.0.1:8000"
EMAIL_TIMEOUT = 20  # seconds; stops a dead mail server from hanging a page

try:
    from .email_settings import *  # noqa: F401,F403
    EMAIL_BACKEND = (
        "tasks.graph_backend.GraphEmailBackend" if EMAIL_METHOD == "graph"
        else "django.core.mail.backends.smtp.EmailBackend"
    )
except ImportError:
    pass
