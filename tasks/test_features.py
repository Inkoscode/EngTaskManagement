import csv
import io
import re
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from sections.models import Section

from .models import EmailLog, Membership, Notification, PrivateNote, Task, TaskEvent
from .notifications import personal_block
from .reports import build_report_data, week_bounds

User = get_user_model()


@override_settings(DEFAULT_FROM_EMAIL="portal@example.com", SITE_URL="https://portal.example.com")
class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.cs = Section.objects.create(name="CS Core")
        cls.ps = Section.objects.create(name="PS Core")
        mk = lambda n, **kw: User.objects.create_user(n, email=f"{n}@example.com", password="pw", **kw)
        cls.boss = mk("boss", is_overall_boss=True)
        cls.cs_boss, cls.cs_eng, cls.cs_eng2, cls.ps_eng = mk("cs_boss"), mk("cs_eng"), mk("cs_eng2"), mk("ps_eng")
        cls.no_mail = User.objects.create_user("no_mail", password="pw")
        for u, s, r in [(cls.cs_boss, cls.cs, "team_boss"), (cls.cs_eng, cls.cs, "engineer"),
                        (cls.cs_eng2, cls.cs, "engineer"), (cls.no_mail, cls.cs, "engineer"),
                        (cls.ps_eng, cls.ps, "engineer")]:
            Membership.objects.create(user=u, section=s, role=r)

    def task(self, title, **kw):
        kw.setdefault("owning_section", self.cs)
        kw.setdefault("creator", self.cs_boss)
        return Task.objects.create(title=title, **kw)

    def login(self, user):
        self.client.force_login(user)

    def post_status(self, task, **data):
        return self.client.post(reverse("task_status", args=[task.pk]), data)


# ============================================================ 3. blocked reason
class BlockedReasonTests(Base):
    def payload(self, **over):
        data = {"title": "New", "priority": "medium", "status": "todo",
                "owning_section": self.cs.pk, "assignee": self.cs_eng.pk}
        data.update(over)
        return data

    def test_create_form_needs_a_reason_when_blocked(self):
        self.login(self.cs_boss)
        r = self.client.post(reverse("task_create"), self.payload(status="blocked"))
        self.assertContains(r, "Say why this task is blocked")
        self.assertFalse(Task.objects.filter(title="New").exists())
        self.client.post(reverse("task_create"), self.payload(status="blocked", blocked_reason="  Waiting for licence  "))
        self.assertEqual(Task.objects.get(title="New").blocked_reason, "Waiting for licence")

    def test_a_reason_is_dropped_when_the_task_is_not_blocked(self):
        self.login(self.cs_boss)
        self.client.post(reverse("task_create"), self.payload(status="todo", blocked_reason="stale text"))
        self.assertEqual(Task.objects.get(title="New").blocked_reason, "")

    def test_status_change_to_blocked_needs_a_reason_and_is_logged(self):
        t = self.task("Patch", assignee=self.cs_eng)
        self.login(self.cs_eng)
        r = self.post_status(t, status="blocked")
        t.refresh_from_db()
        self.assertEqual(t.status, "todo")
        self.assertIn("Say why the task is blocked", [str(m) for m in r.wsgi_request._messages][0])
        self.post_status(t, status="blocked", blocked_reason="Vendor has not shipped the card")
        t.refresh_from_db()
        self.assertEqual((t.status, t.blocked_reason), ("blocked", "Vendor has not shipped the card"))
        fields = set(TaskEvent.objects.filter(task=t).values_list("field", flat=True))
        self.assertTrue({"status", "blocked_reason"} <= fields)

    def test_leaving_blocked_clears_the_reason_and_resubmitting_keeps_it(self):
        t = self.task("Stuck", assignee=self.cs_eng, status="blocked", blocked_reason="No access")
        self.login(self.cs_eng)
        self.post_status(t, status="blocked")            # same status, no new reason: keeps the old one
        t.refresh_from_db()
        self.assertEqual(t.blocked_reason, "No access")
        self.post_status(t, status="in_progress")
        t.refresh_from_db()
        self.assertEqual((t.status, t.blocked_reason), ("in_progress", ""))

    def test_reason_is_shown_on_the_task_the_table_and_the_report(self):
        t = self.task("Stuck", assignee=self.cs_eng, status="blocked", blocked_reason="No access to the lab")
        self.login(self.cs_eng)
        self.assertContains(self.client.get(reverse("task_detail", args=[t.pk])), "No access to the lab")
        self.assertContains(self.client.get(reverse("task_list")), "No access to the lab")
        self.assertContains(self.client.get(reverse("my_dashboard")), "No access to the lab")
        data = build_report_data(*week_bounds())
        cs = next(x for x in data["teams"] if x["name"] == "CS Core")
        self.assertEqual(cs["blocked"]["rows"][0]["reason"], "No access to the lab")

    def test_history_uses_readable_names(self):
        t = self.task("Stuck", assignee=self.cs_eng)
        self.login(self.cs_eng)
        self.post_status(t, status="blocked", blocked_reason="No access")
        self.assertContains(self.client.get(reverse("task_detail", args=[t.pk])), "Blocked reason:")


# ============================================================ 4. archive
class ArchiveTests(Base):
    def test_who_can_archive(self):
        mine = self.task("Mine", creator=self.cs_eng, assignee=self.cs_eng2)
        self.login(self.cs_eng)                                    # the creator
        self.assertEqual(self.client.post(reverse("task_archive", args=[mine.pk])).status_code, 302)
        mine.refresh_from_db()
        self.assertTrue(mine.is_archived)
        other = self.task("Other", creator=self.cs_boss, assignee=self.cs_eng2)
        for who in (self.cs_eng2, self.ps_eng):                    # plain assignee, outsider
            self.login(who)
            self.assertEqual(self.client.post(reverse("task_archive", args=[other.pk])).status_code, 403)
        self.login(self.cs_boss)
        self.assertEqual(self.client.post(reverse("task_archive", args=[other.pk])).status_code, 302)
        self.assertEqual(self.client.get(reverse("task_archive", args=[other.pk])).status_code, 405)

    def test_archived_tasks_leave_every_view_but_can_be_found(self):
        self.task("Live task", assignee=self.cs_eng)
        old = self.task("Old task", assignee=self.cs_eng, archived_at=timezone.now())
        self.login(self.cs_eng)
        for name in ("task_list", "my_dashboard", "calendar"):
            page = self.client.get(reverse(name))
            self.assertNotContains(page, "Old task", msg_prefix=name)
        self.assertContains(self.client.get(reverse("task_list")), "Live task")
        self.assertEqual(self.client.get(reverse("task_list")).context["stats"]["open_count"], 1)
        found = self.client.get(reverse("task_list"), {"archived": "1"})
        self.assertContains(found, "Old task")
        self.assertNotContains(found, "Live task")
        self.assertEqual(personal_block(self.cs_eng)["total"], 1)
        self.assertEqual(self.client.get(reverse("task_detail", args=[old.pk])).status_code, 200)

    def test_archived_task_is_read_only_until_restored(self):
        t = self.task("Old", assignee=self.cs_eng, archived_at=timezone.now())
        self.login(self.cs_boss)
        self.assertEqual(self.client.get(reverse("task_edit", args=[t.pk])).status_code, 403)
        self.assertEqual(self.post_status(t, status="done").status_code, 403)
        self.assertContains(self.client.get(reverse("task_detail", args=[t.pk])), "Restore task")
        self.client.post(reverse("task_unarchive", args=[t.pk]))
        t.refresh_from_db()
        self.assertFalse(t.is_archived)
        self.assertEqual(self.client.get(reverse("task_edit", args=[t.pk])).status_code, 200)
        self.assertEqual(list(TaskEvent.objects.filter(task=t, field="archived").values_list("new_value", flat=True)),
                         ["Restored"])

    def test_archiving_is_logged(self):
        t = self.task("Old")
        self.login(self.cs_boss)
        self.client.post(reverse("task_archive", args=[t.pk]))
        self.assertTrue(TaskEvent.objects.filter(task=t, field="archived", new_value="Archived", actor=self.cs_boss).exists())

    def test_weekly_report_counts_archived_finished_work_but_not_archived_open_work(self):
        done = self.task("Finished", assignee=self.cs_eng)
        self.login(self.cs_eng)
        self.post_status(done, status="done")
        self.login(self.cs_boss)
        self.client.post(reverse("task_archive", args=[done.pk]))
        self.task("Dropped", archived_at=timezone.now())
        data = build_report_data(*week_bounds())
        cs = next(x for x in data["teams"] if x["name"] == "CS Core")
        self.assertEqual([r["title"] for r in cs["completed"]["rows"]], ["Finished"])
        self.assertEqual(cs["open"], 0)


# ============================================================ 5 + 7. comment emails and the bell
class NotificationTests(Base):
    def comment(self, task, who, body="Please check the logs"):
        self.login(who)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("add_comment", args=[task.pk]), {"body": body})

    def test_comment_notifies_assignee_and_creator_but_not_the_author(self):
        t = self.task("Patch", creator=self.cs_boss, assignee=self.cs_eng)
        self.comment(t, self.cs_eng2)
        self.assertEqual(sorted(m.to[0] for m in mail.outbox), ["cs_boss@example.com", "cs_eng@example.com"])
        m = mail.outbox[0]
        self.assertEqual(m.from_email, "portal@example.com")
        self.assertEqual(m.reply_to, ["cs_eng2@example.com"])
        self.assertIn("Please check the logs", m.body)
        self.assertIn(f"https://portal.example.com/tasks/{t.pk}/", m.body)
        self.assertEqual(Notification.objects.filter(kind="comment").count(), 2)
        self.assertEqual(EmailLog.objects.filter(kind="comment", ok=True).count(), 2)

    def test_no_notification_to_yourself(self):
        t = self.task("Mine", creator=self.cs_boss, assignee=self.cs_boss)
        self.comment(t, self.cs_boss)
        self.assertEqual((len(mail.outbox), Notification.objects.count()), (0, 0))
        t2 = self.task("Two", creator=self.cs_boss, assignee=self.cs_eng)
        self.comment(t2, self.cs_eng)
        self.assertEqual([m.to[0] for m in mail.outbox], ["cs_boss@example.com"])

    def test_people_without_email_still_get_the_bell(self):
        t = self.task("Patch", creator=self.cs_boss, assignee=self.no_mail)
        self.comment(t, self.cs_boss)  # author is creator; assignee has no email
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(Notification.objects.get().user, self.no_mail)

    def test_private_notes_notify_nobody(self):
        t = self.task("Patch", creator=self.cs_boss, assignee=self.cs_eng)
        self.login(self.cs_eng2)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("add_private_note", args=[t.pk]), {"body": "secret"})
        self.assertEqual((PrivateNote.objects.count(), len(mail.outbox), Notification.objects.count()), (1, 0, 0))

    def test_assignment_creates_a_bell_notification_even_without_email(self):
        self.login(self.cs_boss)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("task_create"), {"title": "For you", "priority": "medium", "status": "todo",
                                                       "owning_section": self.cs.pk, "assignee": self.no_mail.pk})
        n = Notification.objects.get()
        self.assertEqual((n.user, n.kind), (self.no_mail, "assigned"))
        self.assertIn("For you", n.message)
        self.assertEqual(len(mail.outbox), 0)

    def test_bell_shows_the_unread_count_and_clears_when_the_task_is_opened(self):
        t = self.task("Patch", assignee=self.cs_eng)
        Notification.objects.create(user=self.cs_eng, task=t, kind="assigned", message="Boss assigned you: Patch")
        Notification.objects.create(user=self.cs_eng2, task=t, kind="assigned", message="not mine")
        self.login(self.cs_eng)
        page = self.client.get(reverse("task_list"))
        self.assertContains(page, 'class="bell-count">1<')
        self.client.get(reverse("task_detail", args=[t.pk]))
        self.assertNotContains(self.client.get(reverse("task_list")), 'class="bell-count"')
        self.assertTrue(Notification.objects.get(user=self.cs_eng).read_at)
        self.assertIsNone(Notification.objects.get(user=self.cs_eng2).read_at)

    def test_notification_page_and_mark_all_read(self):
        t = self.task("Patch")
        for i in range(3):
            Notification.objects.create(user=self.cs_eng, task=t, kind="comment", message=f"Msg {i}")
        Notification.objects.create(user=self.cs_eng2, task=t, kind="comment", message="Someone else's")
        self.login(self.cs_eng)
        page = self.client.get(reverse("notification_list"))
        self.assertContains(page, "Msg 2")
        self.assertNotContains(page, "Someone else's")
        self.assertContains(page, "3 unread")
        self.client.post(reverse("notifications_read_all"))
        self.assertContains(self.client.get(reverse("notification_list")), "all caught up")
        self.assertEqual(Notification.objects.filter(user=self.cs_eng2, read_at__isnull=True).count(), 1)
        self.assertEqual(self.client.get(reverse("notifications_read_all")).status_code, 405)


# ============================================================ 6. sorting
class SortingTests(Base):
    def titles(self, **params):
        r = self.client.get(reverse("task_list"), params)
        self.assertEqual(r.status_code, 200)
        return [t.title for t in r.context["page_obj"]]

    def setUp(self):
        self.login(self.cs_eng)

    def test_priority_sorts_by_urgency_not_alphabet(self):
        for title, p in [("a", "low"), ("b", "critical"), ("c", "medium"), ("d", "high")]:
            self.task(title, priority=p)
        self.assertEqual(self.titles(sort="priority", dir="asc"), ["b", "d", "c", "a"])
        self.assertEqual(self.titles(sort="priority", dir="desc"), ["a", "c", "d", "b"])

    def test_due_dates_sort_with_empty_ones_always_last(self):
        self.task("late", due_date="2026-01-09")
        self.task("early", due_date="2026-01-01")
        self.task("none")
        self.assertEqual(self.titles(sort="due", dir="asc"), ["early", "late", "none"])
        self.assertEqual(self.titles(sort="due", dir="desc"), ["late", "early", "none"])

    def test_status_follows_the_workflow_order(self):
        for s in ("done", "review", "todo", "in_progress"):
            self.task(s, status=s)
        self.assertEqual(self.titles(sort="status"), ["todo", "in_progress", "review", "done"])

    def test_title_ignores_case_and_unassigned_goes_last(self):
        self.task("banana", assignee=self.cs_eng2)
        self.task("Apple", assignee=self.cs_eng)
        self.task("cherry")
        self.assertEqual(self.titles(sort="title"), ["Apple", "banana", "cherry"])
        self.assertEqual(self.titles(sort="assignee"), ["Apple", "banana", "cherry"])
        self.assertEqual(self.titles(sort="assignee", dir="desc"), ["banana", "Apple", "cherry"])

    def test_bad_values_are_ignored(self):
        self.task("x")
        self.assertEqual(self.titles(sort="password; drop table", dir="sideways"), ["x"])

    def test_heading_links_flip_the_direction_and_keep_filters(self):
        self.task("x")
        r = self.client.get(reverse("task_list"), {"status": "todo", "sort": "title", "dir": "asc"})
        cols = {c["key"]: c for c in r.context["sort_columns"]}
        self.assertIn("sort=title&dir=desc", cols["title"]["url"])
        self.assertIn("sort=due&dir=asc", cols["due"]["url"])
        self.assertIn("status=todo", cols["due"]["url"])
        self.assertContains(r, 'aria-sort="ascending"')
        self.assertIn("sort=title", r.context["base_qs"])


# ============================================================ 2. export
class ExportTests(Base):
    def rows(self, **params):
        r = self.client.get(reverse("task_export"), params)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r["Content-Type"].startswith("text/csv"))
        self.assertTrue(r.content.startswith(b"\xef\xbb\xbf"))
        return list(csv.reader(io.StringIO(r.content.decode("utf-8-sig")))), r

    def test_login_required(self):
        self.assertEqual(self.client.get(reverse("task_export")).status_code, 302)

    def test_file_contents_and_name(self):
        self.task("One", assignee=self.cs_eng, priority="high", due_date="2026-12-01")
        self.login(self.cs_eng)
        rows, r = self.rows()
        self.assertEqual(rows[0][:7], ["ID", "Title", "Team", "Assignee", "Created by", "Priority", "Status"])
        self.assertEqual(rows[1][1:7], ["One", "CS Core", "cs_eng", "cs_boss", "High", "To do"])
        self.assertEqual(rows[1][10], "2026-12-01")
        self.assertIn(f"tasks-{timezone.localdate().isoformat()}.csv", r["Content-Disposition"])

    def test_export_follows_the_filters_and_skips_archived(self):
        self.task("CS one")
        self.task("PS one", owning_section=self.ps, creator=self.ps_eng)
        self.task("Gone", archived_at=timezone.now())
        self.login(self.cs_eng)
        self.assertEqual([r[1] for r in self.rows(section=self.ps.pk)[0][1:]], ["PS one"])
        self.assertEqual(sorted(r[1] for r in self.rows()[0][1:]), ["CS one", "PS one"])
        rows = self.rows(archived="1")[0]
        self.assertEqual([r[1] for r in rows[1:]], ["Gone"])
        self.assertEqual(rows[1][13], "Yes")

    def test_spreadsheet_formulas_are_defused(self):
        self.task("=HYPERLINK(\"http://evil\",\"x\")", system_ref="@SUM(A1)", description="+1")
        self.login(self.cs_eng)
        row = self.rows()[0][1]
        self.assertTrue(row[1].startswith("'="))
        self.assertTrue(row[11].startswith("'@"))


# ============================================================ 8. calendar
class CalendarTests(Base):
    START = "2026-10-12"   # a Monday; the window runs to 8 Nov

    def rows(self, **params):
        params.setdefault("start", self.START)
        r = self.client.get(reverse("calendar"), params)
        self.assertEqual(r.status_code, 200)
        found = {row["title"]: row for g in r.context["groups"] for row in g["rows"]}
        return found, r

    def test_bar_position_and_due_marker(self):
        self.task("Inside", planned_start="2026-10-15", planned_finish="2026-10-21", due_date="2026-10-21")
        self.task("Starts early", planned_start="2026-10-01", planned_finish="2026-10-14")
        self.task("Runs on", planned_start="2026-11-05", planned_finish="2026-12-20")
        self.login(self.cs_eng)
        rows, r = self.rows()
        a = rows["Inside"]
        self.assertEqual((a["start_col"], a["span"], a["due_col"]), (5, 7, 11))   # 3 days in, +2 for the label column
        self.assertFalse(a["cut_left"] or a["cut_right"])
        b = rows["Starts early"]
        self.assertEqual((b["start_col"], b["span"], b["cut_left"]), (2, 3, True))
        c = rows["Runs on"]
        self.assertEqual((c["start_col"], c["span"], c["cut_right"]), (26, 4, True))
        self.assertEqual(r.context["start"].isoformat(), self.START)
        self.assertEqual(len(r.context["days"]), 28)

    def test_due_date_only_tasks_and_filters(self):
        self.task("Due only", due_date="2026-10-20")
        self.task("Not planned")                                       # no dates at all
        self.task("Far away", planned_start="2027-03-01", planned_finish="2027-03-09")
        self.task("Finished", planned_start="2026-10-13", planned_finish="2026-10-14", status="done")
        self.task("Hidden", planned_start="2026-10-13", planned_finish="2026-10-14", archived_at=timezone.now())
        self.task("PS work", owning_section=self.ps, creator=self.ps_eng, due_date="2026-10-22")
        self.login(self.cs_eng)
        rows, r = self.rows()
        self.assertEqual(sorted(rows), ["Due only", "PS work"])
        self.assertEqual((rows["Due only"]["start_col"], rows["Due only"]["span"], rows["Due only"]["due_col"]), (10, 1, 10))
        self.assertEqual(r.context["no_dates"], 1)
        self.assertEqual(sorted(self.rows(done="1")[0]), ["Due only", "Finished", "PS work"])
        self.assertEqual(sorted(self.rows(team=self.ps.pk)[0]), ["PS work"])
        self.assertEqual(sorted(self.rows(mine="1")[0]), [])

    def test_bad_dates_fall_back_to_this_week_and_navigation_moves_four_weeks(self):
        self.login(self.cs_eng)
        r = self.client.get(reverse("calendar"), {"start": "not-a-date"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context["start"], week_bounds()[0])
        r = self.client.get(reverse("calendar"), {"start": "2026-10-14"})   # a Wednesday: snaps to Monday
        self.assertEqual(r.context["start"].isoformat(), self.START)
        self.assertIn("start=2026-11-09", r.context["next_url"])
        self.assertIn("start=2026-09-14", r.context["prev_url"])

    def test_today_marker(self):
        self.login(self.cs_eng)
        monday = week_bounds()[0].isoformat()
        r = self.client.get(reverse("calendar"), {"start": monday})
        self.assertEqual(r.context["today_col"], (timezone.localdate() - week_bounds()[0]).days + 2)


# ============================================================ 9. workload
class WorkloadTests(Base):
    def test_who_can_see_it(self):
        for who in (self.cs_eng, self.ps_eng):
            self.login(who)
            self.assertEqual(self.client.get(reverse("workload")).status_code, 403)
        self.login(self.cs_boss)
        r = self.client.get(reverse("workload"))
        self.assertEqual([t["name"] for t in r.context["teams"]], ["CS Core"])
        self.assertContains(self.client.get(reverse("task_list")), reverse("workload"))
        self.login(self.cs_eng)
        self.assertNotContains(self.client.get(reverse("task_list")), 'href="/tasks/workload/"')
        self.login(self.boss)
        self.assertEqual([t["name"] for t in self.client.get(reverse("workload")).context["teams"]], ["CS Core", "PS Core"])

    def test_counts(self):
        yesterday = timezone.localdate() - timedelta(days=1)
        self.task("Late", assignee=self.cs_eng, due_date=yesterday)
        self.task("Stuck", assignee=self.cs_eng, status="blocked", blocked_reason="x")
        finished = self.task("Finished", assignee=self.cs_eng)
        self.task("Busy", assignee=self.cs_eng2, status="in_progress", due_date=timezone.localdate() + timedelta(days=3))
        self.task("Nobody")
        self.task("Archived", assignee=self.cs_eng, archived_at=timezone.now())
        self.login(self.cs_eng)
        self.post_status(finished, status="done")
        self.login(self.cs_boss)
        team = self.client.get(reverse("workload")).context["teams"][0]
        rows = {r["name"]: r for r in team["rows"]}
        self.assertEqual({k: rows["cs_eng"][k] for k in ("open", "blocked", "overdue", "finished")},
                         {"open": 2, "blocked": 1, "overdue": 1, "finished": 1})
        self.assertEqual((rows["cs_eng2"]["open"], rows["cs_eng2"]["due_soon"]), (1, 1))
        self.assertEqual(rows["cs_boss"]["open"], 0)
        self.assertEqual((team["unassigned"], team["open_total"]), (1, 3))
        self.assertEqual(team["rows"][0]["name"], "cs_eng")           # busiest first
        self.assertEqual(rows["cs_eng"]["pct"], 100)


# ============================================================ 1. forgot password
class PasswordResetTests(Base):
    def test_full_flow(self):
        self.client.post(reverse("password_reset"), {"email": "cs_eng@example.com"})
        self.assertEqual(len(mail.outbox), 1)
        m = mail.outbox[0]
        self.assertEqual((m.to, m.from_email), (["cs_eng@example.com"], "portal@example.com"))
        self.assertIn("Your username: cs_eng", m.body)
        link = re.search(r"https://portal\.example\.com(/accounts/reset/\S+)", m.body)
        self.assertTrue(link, m.body)                                   # link uses SITE_URL, not 127.0.0.1
        self.assertIn("https://portal.example.com/accounts/reset/", m.alternatives[0][0])
        self.assertTrue(EmailLog.objects.filter(kind="password_reset", ok=True, recipient="cs_eng@example.com").exists())
        step = self.client.get(link.group(1))
        self.assertEqual(step.status_code, 302)
        done = self.client.post(step["Location"], {"new_password1": "N3w-strong-pass-77", "new_password2": "N3w-strong-pass-77"})
        self.assertRedirects(done, reverse("password_reset_complete"))
        self.assertTrue(self.client.login(username="cs_eng", password="N3w-strong-pass-77"))
        self.assertEqual(self.client.get(link.group(1)).status_code, 200)   # used link now says invalid
        self.assertContains(self.client.get(link.group(1), follow=True), "no longer works")

    def test_unknown_address_looks_the_same_and_sends_nothing(self):
        r = self.client.post(reverse("password_reset"), {"email": "nobody@example.com"})
        self.assertRedirects(r, reverse("password_reset_done"))
        self.assertEqual(len(mail.outbox), 0)
        self.assertContains(self.client.get(reverse("password_reset_done")), "If an account exists")

    def test_inactive_users_cannot_reset(self):
        self.cs_eng2.is_active = False
        self.cs_eng2.save()
        self.client.post(reverse("password_reset"), {"email": "cs_eng2@example.com"})
        self.assertEqual(len(mail.outbox), 0)

    @override_settings(EMAIL_BACKEND="tasks.test_notifications.BrokenBackend")
    def test_mail_server_down_does_not_crash_and_is_logged(self):
        r = self.client.post(reverse("password_reset"), {"email": "cs_eng@example.com"})
        self.assertRedirects(r, reverse("password_reset_done"))
        self.assertFalse(EmailLog.objects.get(kind="password_reset").ok)

    def test_bad_link_and_login_page_link(self):
        self.assertContains(self.client.get("/accounts/reset/Mg/bad-token/"), "no longer works")
        self.assertContains(self.client.get(reverse("login")), reverse("password_reset"))
        self.assertContains(self.client.get(reverse("password_reset")), "Send reset link")
