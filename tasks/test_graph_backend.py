import json
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.mail import EmailMultiAlternatives, send_mail
from django.test import TestCase, override_settings
from sections.models import Section

from . import graph_backend
from .models import EmailLog, Task
from .notifications import notify_assigned

User = get_user_model()
GRAPH = dict(
    EMAIL_BACKEND="tasks.graph_backend.GraphEmailBackend",
    GRAPH_TENANT_ID="tenant-123", GRAPH_CLIENT_ID="client-456", GRAPH_CLIENT_SECRET="secret-789",
    GRAPH_SENDER="portal@contoso.com", SITE_URL="https://portal.contoso.com",
)


def fake_post(calls, send_status=202, send_body=b""):
    def _post(url, data, headers, timeout):
        calls.append((url, data, headers))
        if "oauth2/v2.0/token" in url:
            return 200, json.dumps({"access_token": "tok-abc", "expires_in": 3600}).encode()
        return send_status, send_body
    return _post


@override_settings(**GRAPH)
class GraphBackendTests(TestCase):
    def setUp(self):
        graph_backend._token.update(value=None, expires=0.0)

    def test_request_shape(self):
        calls = []
        with mock.patch.object(graph_backend, "_post", fake_post(calls)):
            msg = EmailMultiAlternatives("Hello", "plain body", "x@y.z", ["a@contoso.com"],
                                         reply_to=["boss@contoso.com"])
            msg.attach_alternative("<b>html body</b>", "text/html")
            self.assertEqual(msg.send(), 1)
        token_url, token_body, _ = calls[0]
        self.assertIn("/tenant-123/oauth2/v2.0/token", token_url)
        self.assertIn(b"client_id=client-456", token_body)
        self.assertIn(b"client_secret=secret-789", token_body)
        self.assertIn(b"graph.microsoft.com%2F.default", token_body)
        send_url, send_body, headers = calls[1]
        self.assertEqual(send_url, "https://graph.microsoft.com/v1.0/users/portal%40contoso.com/sendMail")
        self.assertEqual(headers["Authorization"], "Bearer tok-abc")
        payload = json.loads(send_body)
        self.assertEqual(payload["message"]["subject"], "Hello")
        self.assertEqual(payload["message"]["body"], {"contentType": "HTML", "content": "<b>html body</b>"})
        self.assertEqual(payload["message"]["toRecipients"], [{"emailAddress": {"address": "a@contoso.com"}}])
        self.assertEqual(payload["message"]["replyTo"], [{"emailAddress": {"address": "boss@contoso.com"}}])

    def test_plain_text_message_and_display_name_addresses(self):
        calls = []
        with mock.patch.object(graph_backend, "_post", fake_post(calls)):
            send_mail("Hi", "just text", None, ["Chikondi Banda <c@contoso.com>"])
        payload = json.loads(calls[1][1])
        self.assertEqual(payload["message"]["body"]["contentType"], "Text")
        self.assertEqual(payload["message"]["toRecipients"][0]["emailAddress"]["address"], "c@contoso.com")

    def test_token_is_reused_for_several_emails(self):
        calls = []
        with mock.patch.object(graph_backend, "_post", fake_post(calls)):
            send_mail("1", "a", None, ["a@contoso.com"])
            send_mail("2", "b", None, ["b@contoso.com"])
        self.assertEqual(sum("oauth2" in c[0] for c in calls), 1)
        self.assertEqual(sum("sendMail" in c[0] for c in calls), 2)

    def test_expired_token_is_refreshed_once_on_401(self):
        calls, answers = [], iter([401, 202])
        def _post(url, data, headers, timeout):
            calls.append(url)
            if "oauth2" in url:
                return 200, json.dumps({"access_token": "tok", "expires_in": 3600}).encode()
            return next(answers), b""
        with mock.patch.object(graph_backend, "_post", _post):
            self.assertEqual(send_mail("1", "a", None, ["a@contoso.com"]), 1)
        self.assertEqual(sum("oauth2" in u for u in calls), 2)

    def test_graph_error_is_reported_clearly(self):
        body = json.dumps({"error": {"code": "ErrorAccessDenied", "message": "Access is denied.\nrequest-id: 1"}}).encode()
        with mock.patch.object(graph_backend, "_post", fake_post([], 403, body)):
            with self.assertRaises(graph_backend.GraphEmailError) as cm:
                send_mail("1", "a", None, ["a@contoso.com"])
        self.assertIn("ErrorAccessDenied", str(cm.exception))
        self.assertNotIn("request-id", str(cm.exception))

    def test_bad_client_secret_is_reported_clearly(self):
        def _post(url, data, headers, timeout):
            return 401, json.dumps({"error": "invalid_client",
                                    "error_description": "AADSTS7000215: Invalid client secret provided.\nTrace ID: x"}).encode()
        with mock.patch.object(graph_backend, "_post", _post):
            with self.assertRaises(graph_backend.GraphEmailError) as cm:
                send_mail("1", "a", None, ["a@contoso.com"])
        self.assertIn("AADSTS7000215", str(cm.exception))

    @override_settings(GRAPH_CLIENT_SECRET="your-client-secret-value")
    def test_unfilled_placeholder_is_caught(self):
        with self.assertRaises(graph_backend.GraphEmailError) as cm:
            send_mail("1", "a", None, ["a@contoso.com"])
        self.assertIn("GRAPH_CLIENT_SECRET is not set", str(cm.exception))

    def test_assignment_email_goes_through_graph_and_is_logged(self):
        section = Section.objects.create(name="CS Core")
        boss = User.objects.create_user("boss", email="boss@contoso.com")
        eng = User.objects.create_user("eng", email="eng@contoso.com")
        task = Task.objects.create(title="Patch HLR", owning_section=section, creator=boss, assignee=eng)
        calls = []
        with mock.patch.object(graph_backend, "_post", fake_post(calls)):
            self.assertTrue(notify_assigned(task.pk, boss))
        payload = json.loads(calls[-1][1])
        self.assertEqual(payload["message"]["toRecipients"][0]["emailAddress"]["address"], "eng@contoso.com")
        self.assertEqual(payload["message"]["body"]["contentType"], "HTML")
        self.assertIn(f"https://portal.contoso.com/tasks/{task.pk}/", payload["message"]["body"]["content"])
        self.assertTrue(EmailLog.objects.filter(kind="assignment", ok=True).exists())

    def test_failure_is_logged_not_raised_by_the_portal(self):
        section = Section.objects.create(name="CS Core")
        boss = User.objects.create_user("boss", email="boss@contoso.com")
        eng = User.objects.create_user("eng", email="eng@contoso.com")
        task = Task.objects.create(title="Patch HLR", owning_section=section, creator=boss, assignee=eng)
        with mock.patch.object(graph_backend, "_post", fake_post([], 403, b'{"error":{"code":"ErrorAccessDenied","message":"nope"}}')):
            self.assertFalse(notify_assigned(task.pk, boss))
        log = EmailLog.objects.get()
        self.assertFalse(log.ok)
        self.assertIn("ErrorAccessDenied", log.error)
