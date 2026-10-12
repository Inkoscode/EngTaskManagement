"""
Django email backend that sends through Microsoft Graph (Microsoft 365 / Outlook for work or school).

Why: Microsoft is switching off password sign-in for SMTP. Graph uses an app registration
(client id + secret) instead of a mailbox password and keeps working after that change.

Settings used (all set in core_portal/email_settings.py):
    GRAPH_TENANT_ID, GRAPH_CLIENT_ID, GRAPH_CLIENT_SECRET   from the Entra app registration
    GRAPH_SENDER                                            the mailbox the emails are sent from
    GRAPH_SAVE_TO_SENT (optional, default True)             keep a copy in that mailbox's Sent Items
    EMAIL_TIMEOUT (optional)                                seconds to wait for Microsoft

Only the Python standard library is used, so there is nothing extra to install.
"""
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from email.utils import parseaddr

from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend

TOKEN_URL = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
SEND_URL = "https://graph.microsoft.com/v1.0/users/{sender}/sendMail"

_lock = threading.Lock()
_token = {"value": None, "expires": 0.0}


class GraphEmailError(Exception):
    pass


def _post(url, data, headers, timeout):
    """One HTTP POST. Returns (status_code, body_bytes). Network problems raise GraphEmailError."""
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:  # 4xx / 5xx still carry a useful JSON body
        return exc.code, exc.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise GraphEmailError(f"Could not reach Microsoft: {exc}") from exc


def _setting(name):
    value = getattr(settings, name, "")
    if not value or "your-" in str(value) or "yourcompany" in str(value):
        raise GraphEmailError(f"{name} is not set. Fill it in in core_portal/email_settings.py.")
    return value


def _first_line(text):
    return str(text).strip().splitlines()[0] if text else ""


def _access_token(timeout, force_refresh=False):
    with _lock:
        if not force_refresh and _token["value"] and time.time() < _token["expires"] - 60:
            return _token["value"]
        body = urllib.parse.urlencode({
            "client_id": _setting("GRAPH_CLIENT_ID"),
            "client_secret": _setting("GRAPH_CLIENT_SECRET"),
            "scope": "https://graph.microsoft.com/.default",
            "grant_type": "client_credentials",
        }).encode()
        url = TOKEN_URL.format(tenant=urllib.parse.quote(str(_setting("GRAPH_TENANT_ID"))))
        status, raw = _post(url, body, {"Content-Type": "application/x-www-form-urlencoded"}, timeout)
        try:
            data = json.loads(raw or b"{}")
        except ValueError:
            data = {}
        if status != 200 or "access_token" not in data:
            reason = _first_line(data.get("error_description")) or data.get("error") or f"HTTP {status}"
            raise GraphEmailError(f"Microsoft refused the app sign-in: {reason}")
        _token["value"] = data["access_token"]
        _token["expires"] = time.time() + int(data.get("expires_in", 3600))
        return _token["value"]


def _recipients(addresses):
    return [{"emailAddress": {"address": parseaddr(a)[1]}} for a in addresses if parseaddr(a)[1]]


def build_payload(message):
    """Turn a Django EmailMessage into the JSON body Graph's sendMail expects."""
    html = None
    for content, mimetype in getattr(message, "alternatives", []):
        if mimetype == "text/html":
            html = content
    if html is None and getattr(message, "content_subtype", "plain") == "html":
        html = message.body
    body = ({"contentType": "HTML", "content": html} if html is not None
            else {"contentType": "Text", "content": message.body})
    msg = {"subject": message.subject, "body": body, "toRecipients": _recipients(message.to)}
    if message.cc:
        msg["ccRecipients"] = _recipients(message.cc)
    if message.bcc:
        msg["bccRecipients"] = _recipients(message.bcc)
    if message.reply_to:
        msg["replyTo"] = _recipients(message.reply_to)
    return {"message": msg, "saveToSentItems": bool(getattr(settings, "GRAPH_SAVE_TO_SENT", True))}


class GraphEmailBackend(BaseEmailBackend):
    def send_messages(self, email_messages):
        timeout = getattr(settings, "EMAIL_TIMEOUT", None) or 20
        sent = 0
        for message in email_messages:
            try:
                self._send_one(message, timeout)
                sent += 1
            except Exception:
                if not self.fail_silently:
                    raise
        return sent

    def _send_one(self, message, timeout):
        sender = _setting("GRAPH_SENDER")
        url = SEND_URL.format(sender=urllib.parse.quote(str(sender)))
        data = json.dumps(build_payload(message)).encode()
        for attempt in (1, 2):
            token = _access_token(timeout, force_refresh=(attempt == 2))
            headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            status, raw = _post(url, data, headers, timeout)
            if status == 401 and attempt == 1:  # token expired early: get a fresh one and retry once
                continue
            break
        if status == 202:  # Graph answers "202 Accepted" when it has queued the message
            return
        try:
            err = json.loads(raw or b"{}").get("error", {})
        except ValueError:
            err = {}
        code, text = err.get("code", f"HTTP {status}"), _first_line(err.get("message", ""))
        raise GraphEmailError(f"Microsoft Graph rejected the email: {code}. {text}".strip())
