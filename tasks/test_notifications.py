from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.mail.backends.base import BaseEmailBackend
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from sections.models import Section

from .models import EmailLog, Membership, Task, WeeklyReport
from .reports import week_bounds

User = get_user_model()


class BrokenBackend(BaseEmailBackend):
    """Pretends the mail server is down."""
    def send_messages(self, messages):
        raise ConnectionRefusedError("mail server down")


@override_settings(DEFAULT_FROM_EMAIL="portal@example.com", SITE_URL="https://portal.example.com")
class EmailTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.cs = Section.objects.create(name="CS Core")
        cls.ps = Section.objects.create(name="PS Core")
        mk = lambda n, **kw: User.objects.create_user(n, email=f"{n}@example.com", password="pw", **kw)
        cls.boss = mk("boss", is_overall_boss=True)
        cls.cs_boss, cls.cs_eng, cls.cs_eng2, cls.ps_eng = mk("cs_boss"), mk("cs_eng"), mk("cs_eng2"), mk("ps_eng")
        cls.no_mail = User.objects.create_user("no_mail", password="pw")  # no email address
        for u, s, r in [(cls.cs_boss, cls.cs, "team_boss"), (cls.cs_eng, cls.cs, "engineer"),
                        (cls.cs_eng2, cls.cs, "engineer"), (cls.ps_eng, cls.ps, "engineer"),
                        (cls.no_mail, cls.cs, "engineer")]:
            Membership.objects.create(user=u, section=s, role=r)
        cls.task = Task.objects.create(title="CS firmware", owning_section=cls.cs, creator=cls.cs_boss,
                                       assignee=cls.cs_eng)

    def create_payload(self, **over):
        data = {"title": "Upgrade HLR", "priority": "high", "status": "todo",
                "owning_section": self.cs.pk, "assignee": self.cs_eng2.pk, "due_date": "2026-12-01",
                "description": "Do the upgrade & verify <b>now</b>"}
        data.update(over)
        return data

    # ---------------- assignment emails
    def test_assignment_email_sent_from_admin_address(self):
        self.client.force_login(self.cs_eng)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("task_create"), self.create_payload())
        self.assertEqual(len(mail.outbox), 1)
        m = mail.outbox[0]
        self.assertEqual(m.to, ["cs_eng2@example.com"])
        self.assertEqual(m.from_email, "portal@example.com")
        self.assertEqual(m.reply_to, ["cs_eng@example.com"])
        self.assertIn("Upgrade HLR", m.subject)
        t = Task.objects.get(title="Upgrade HLR")
        self.assertIn(f"https://portal.example.com/tasks/{t.pk}/", m.body)
        html = m.alternatives[0][0]
        self.assertIn("&lt;b&gt;now&lt;/b&gt;", html)  # user text is escaped in the HTML version
        self.assertTrue(EmailLog.objects.filter(kind="assignment", ok=True, task=t).exists())

    def test_no_email_for_self_assignment_or_missing_address(self):
        self.client.force_login(self.cs_eng)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("task_create"), self.create_payload(title="Mine", assignee=self.cs_eng.pk))
            self.client.post(reverse("task_create"), self.create_payload(title="Nobody home", assignee=self.no_mail.pk))
            self.client.post(reverse("task_create"), self.create_payload(title="Unassigned", assignee=""))
        self.assertEqual(len(mail.outbox), 0)

    def test_reassigning_sends_email_but_other_edits_do_not(self):
        self.client.force_login(self.cs_boss)
        url = reverse("task_edit", args=[self.task.pk])
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(url, self.create_payload(title="CS firmware", assignee=self.cs_eng.pk))  # unchanged assignee
        self.assertEqual(len(mail.outbox), 0)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(url, self.create_payload(title="CS firmware", assignee=self.cs_eng2.pk))
        self.assertEqual([m.to for m in mail.outbox], [["cs_eng2@example.com"]])

    @override_settings(EMAIL_BACKEND="tasks.test_notifications.BrokenBackend")
    def test_mail_failure_does_not_break_the_page_and_is_logged(self):
        self.client.force_login(self.cs_eng)
        with self.captureOnCommitCallbacks(execute=True):
            r = self.client.post(reverse("task_create"), self.create_payload())
        self.assertEqual(r.status_code, 302)
        self.assertTrue(Task.objects.filter(title="Upgrade HLR").exists())
        log = EmailLog.objects.get(kind="assignment")
        self.assertFalse(log.ok)
        self.assertIn("mail server down", log.error)

    # ---------------- reports: access and drafts
    def test_only_managers_reach_reports(self):
        for u in (self.cs_eng, self.cs_boss):
            self.client.force_login(u)
            self.assertEqual(self.client.get(reverse("report_list")).status_code, 403)
            self.assertEqual(self.client.post(reverse("report_generate")).status_code, 403)
        self.client.force_login(self.boss)
        self.assertEqual(self.client.get(reverse("report_list")).status_code, 200)
        staff = User.objects.create_user("staff", password="pw", is_staff=True)
        self.client.force_login(staff)
        self.assertEqual(self.client.get(reverse("report_list")).status_code, 200)

    def test_generate_creates_one_draft_per_week(self):
        self.client.force_login(self.boss)
        r = self.client.post(reverse("report_generate"), {"week_of": timezone.localdate().isoformat()})
        report = WeeklyReport.objects.get()
        self.assertRedirects(r, reverse("report_detail", args=[report.pk]))
        self.assertEqual(report.status, "draft")
        self.assertEqual(report.week_start, week_bounds()[0])
        self.assertEqual([t["name"] for t in report.data["teams"]], ["CS Core", "PS Core"])
        self.client.post(reverse("report_generate"), {"week_of": timezone.localdate().isoformat()})
        self.assertEqual(WeeklyReport.objects.count(), 1)
        self.assertEqual(self.client.get(reverse("report_detail", args=[report.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("report_list")).status_code, 200)

    def test_report_numbers(self):
        today = timezone.localdate()
        Task.objects.create(title="Late one", owning_section=self.cs, creator=self.cs_boss,
                            assignee=self.cs_eng, due_date=today - timedelta(days=3))
        Task.objects.create(title="Stuck", owning_section=self.ps, creator=self.ps_eng,
                            assignee=self.ps_eng, status="blocked")
        self.client.force_login(self.cs_eng)  # finishing a task logs a 'done' event
        self.client.post(reverse("task_status", args=[self.task.pk]), {"status": "done"})
        self.client.force_login(self.boss)
        self.client.post(reverse("report_generate"))
        d = WeeklyReport.objects.get().data
        cs = next(t for t in d["teams"] if t["name"] == "CS Core")
        self.assertEqual([r["title"] for r in cs["completed"]["rows"]], ["CS firmware"])
        self.assertEqual([r["title"] for r in cs["overdue"]["rows"]], ["Late one"])
        self.assertEqual(d["totals"]["blocked"], 1)
        self.assertEqual(d["totals"]["completed"], 1)

    # ---------------- reports: edit, test, send
    def make_draft(self):
        self.client.force_login(self.boss)
        self.client.post(reverse("report_generate"))
        return WeeklyReport.objects.get()

    def test_edits_are_saved_when_sending(self):
        report = self.make_draft()
        self.client.post(reverse("report_detail", args=[report.pk]),
                         {"subject": "Friday update", "intro": "Great week, team.", "action": "send"})
        report.refresh_from_db()
        self.assertEqual((report.subject, report.status), ("Friday update", "sent"))
        self.assertTrue(all(m.subject == "Friday update" for m in mail.outbox))
        self.assertIn("Great week, team.", mail.outbox[0].body)

    def test_send_goes_to_every_active_user_with_email_and_only_once(self):
        report = self.make_draft()
        self.ps_eng.is_active = False
        self.ps_eng.save()
        data = {"subject": report.subject, "intro": "Hi", "action": "send"}
        self.client.post(reverse("report_detail", args=[report.pk]), data)
        recipients = sorted(m.to[0] for m in mail.outbox)
        self.assertEqual(recipients, ["boss@example.com", "cs_boss@example.com",
                                      "cs_eng2@example.com", "cs_eng@example.com"])
        report.refresh_from_db()
        self.assertEqual((report.status, report.recipient_count, report.failed_count), ("sent", 4, 0))
        self.assertEqual(report.sent_by, self.boss)
        self.client.post(reverse("report_detail", args=[report.pk]), data)  # double click / refresh
        self.assertEqual(len(mail.outbox), 4)

    def test_each_person_gets_their_own_tasks_section(self):
        report = self.make_draft()
        self.client.post(reverse("report_detail", args=[report.pk]),
                         {"subject": report.subject, "intro": "", "action": "send"})
        by_to = {m.to[0]: m for m in mail.outbox}
        self.assertIn("Your tasks: 1 open", by_to["cs_eng@example.com"].body)
        self.assertIn("CS firmware", by_to["cs_eng@example.com"].body)
        self.assertNotIn("Your tasks", by_to["cs_eng2@example.com"].body)
        self.assertIn("text/html", by_to["cs_eng@example.com"].alternatives[0][1])

    def test_test_email_goes_only_to_the_admin(self):
        report = self.make_draft()
        self.client.post(reverse("report_detail", args=[report.pk]),
                         {"subject": "Draft subject", "intro": "x", "action": "test"})
        self.assertEqual([m.to for m in mail.outbox], [["boss@example.com"]])
        self.assertTrue(mail.outbox[0].subject.startswith("[TEST]"))
        report.refresh_from_db()
        self.assertEqual(report.status, "draft")

    def test_sent_report_cannot_be_edited_or_resent(self):
        report = self.make_draft()
        self.client.post(reverse("report_detail", args=[report.pk]),
                         {"subject": "Final", "intro": "", "action": "send"})
        n = len(mail.outbox)
        self.client.post(reverse("report_detail", args=[report.pk]),
                         {"subject": "Changed", "intro": "", "action": "save"})
        report.refresh_from_db()
        self.assertEqual(report.subject, "Final")
        self.assertEqual(len(mail.outbox), n)
        self.assertEqual(self.client.get(reverse("report_detail", args=[report.pk])).status_code, 200)

    @override_settings(EMAIL_BACKEND="tasks.test_notifications.BrokenBackend")
    def test_total_failure_keeps_it_a_draft_so_you_can_retry(self):
        report = self.make_draft()
        self.client.post(reverse("report_detail", args=[report.pk]),
                         {"subject": report.subject, "intro": "", "action": "send"})
        report.refresh_from_db()
        self.assertEqual(report.status, "draft")
        self.assertEqual(EmailLog.objects.filter(kind="weekly", ok=False).count(), 5)  # boss, cs_boss, cs_eng, cs_eng2, ps_eng
        self.assertEqual(self.client.get(reverse("report_detail", args=[report.pk])).status_code, 200)
