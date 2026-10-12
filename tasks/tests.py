from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from sections.models import Section

from .models import Membership, PrivateNote, Task, TaskEvent

User = get_user_model()


class PortalTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.cs = Section.objects.create(name="CS")
        cls.mech = Section.objects.create(name="Mechanical")
        mk = lambda name, **kw: User.objects.create_user(name, password="pw", **kw)
        cls.boss = mk("boss", is_overall_boss=True)
        cls.cs_boss, cls.cs_eng, cls.cs_eng2, cls.mech_eng = (
            mk("cs_boss"), mk("cs_eng"), mk("cs_eng2"), mk("mech_eng"))
        Membership.objects.create(user=cls.cs_boss, section=cls.cs, role=Membership.TEAM_BOSS)
        Membership.objects.create(user=cls.cs_eng, section=cls.cs)
        Membership.objects.create(user=cls.cs_eng2, section=cls.cs)
        Membership.objects.create(user=cls.mech_eng, section=cls.mech)
        cls.cs_task = Task.objects.create(title="CS firmware", owning_section=cls.cs,
                                          creator=cls.cs_boss, assignee=cls.cs_eng)
        cls.mech_task = Task.objects.create(title="Mech bracket", owning_section=cls.mech,
                                            creator=cls.mech_eng, assignee=cls.mech_eng)

    def login(self, user):
        self.client.force_login(user)

    def payload(self, **over):
        data = {"title": "New thing", "priority": "medium", "status": "todo",
                "owning_section": self.cs.pk, "assignee": self.cs_eng2.pk}
        data.update(over)
        return data

    # ---- general dashboard
    def test_anonymous_is_sent_to_login(self):
        r = self.client.get(reverse("task_list"))
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login/", r["Location"])

    def test_everyone_sees_every_team_on_overview(self):
        self.login(self.mech_eng)
        r = self.client.get(reverse("task_list"))
        self.assertContains(r, "CS firmware")
        self.assertContains(r, "Mech bracket")

    def test_bad_filter_values_do_not_crash(self):
        self.login(self.cs_eng)
        r = self.client.get(reverse("task_list"), {"section": "abc", "status": "zzz",
                                                   "priority": "\u00b2", "page": "x"})
        self.assertEqual(r.status_code, 200)

    def test_section_filter(self):
        self.login(self.cs_eng)
        r = self.client.get(reverse("task_list"), {"section": self.mech.pk})
        self.assertContains(r, "Mech bracket")
        self.assertNotContains(r, "CS firmware")

    def test_home_redirects_logged_in_user(self):
        self.login(self.cs_eng)
        self.assertRedirects(self.client.get("/"), reverse("task_list"), fetch_redirect_response=False)

    # ---- my dashboard
    def test_my_dashboard_shows_only_my_team(self):
        self.login(self.cs_eng)
        r = self.client.get(reverse("my_dashboard"))
        self.assertContains(r, "CS firmware")
        self.assertNotContains(r, "Mech bracket")

    def test_overall_boss_dashboard_shows_all_teams(self):
        self.login(self.boss)
        r = self.client.get(reverse("my_dashboard"))
        self.assertContains(r, "CS firmware")
        self.assertContains(r, "Mech bracket")

    # ---- creating and assigning
    def test_engineer_can_create_and_assign_to_teammate(self):
        self.login(self.cs_eng)
        self.assertEqual(self.client.get(reverse("task_create")).status_code, 200)
        r = self.client.post(reverse("task_create"), self.payload())
        t = Task.objects.get(title="New thing")
        self.assertRedirects(r, reverse("task_detail", args=[t.pk]))
        self.assertEqual((t.creator, t.assignee), (self.cs_eng, self.cs_eng2))
        self.assertTrue(TaskEvent.objects.filter(task=t, field="created").exists())

    def test_cannot_assign_outside_team_or_create_in_other_team(self):
        self.login(self.cs_eng)
        r = self.client.post(reverse("task_create"), self.payload(assignee=self.mech_eng.pk))
        self.assertEqual(r.status_code, 200)
        r = self.client.post(reverse("task_create"), self.payload(owning_section=self.mech.pk))
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Task.objects.filter(title="New thing").exists())

    def test_finish_before_start_is_rejected(self):
        self.login(self.cs_eng)
        r = self.client.post(reverse("task_create"), self.payload(
            planned_start="2026-05-10", planned_finish="2026-05-01"))
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Task.objects.filter(title="New thing").exists())

    def test_user_without_team_cannot_create(self):
        loner = User.objects.create_user("loner", password="pw")
        self.login(loner)
        self.assertEqual(self.client.get(reverse("task_create")).status_code, 403)

    # ---- editing and status
    def test_engineer_cannot_edit_someone_elses_task(self):
        self.login(self.cs_eng)
        self.assertEqual(self.client.get(reverse("task_edit", args=[self.cs_task.pk])).status_code, 403)

    def test_creator_and_team_boss_can_edit(self):
        self.login(self.cs_eng)
        self.client.post(reverse("task_create"), self.payload(title="Mine"))
        mine = Task.objects.get(title="Mine")
        self.assertEqual(self.client.get(reverse("task_edit", args=[mine.pk])).status_code, 200)
        self.login(self.cs_boss)
        self.assertEqual(self.client.get(reverse("task_edit", args=[self.cs_task.pk])).status_code, 200)

    def test_edit_logs_each_changed_field(self):
        self.login(self.cs_boss)
        self.client.post(reverse("task_edit", args=[self.cs_task.pk]),
                         self.payload(title="CS firmware", priority="high", assignee=self.cs_eng.pk))
        events = TaskEvent.objects.filter(task=self.cs_task)
        self.assertEqual([e.field for e in events], ["priority"])

    def test_assignee_can_update_status_and_it_is_logged(self):
        self.login(self.cs_eng)
        r = self.client.post(reverse("task_status", args=[self.cs_task.pk]),
                             {"status": "in_progress", "next": reverse("my_dashboard")})
        self.assertRedirects(r, reverse("my_dashboard"), fetch_redirect_response=False)
        self.cs_task.refresh_from_db()
        self.assertEqual(self.cs_task.status, "in_progress")
        self.assertTrue(TaskEvent.objects.filter(task=self.cs_task, field="status", actor=self.cs_eng).exists())

    def test_non_assignee_teammate_cannot_update_status(self):
        self.login(self.cs_eng2)
        r = self.client.post(reverse("task_status", args=[self.cs_task.pk]), {"status": "done"})
        self.assertEqual(r.status_code, 403)

    def test_status_next_url_must_be_local(self):
        self.login(self.cs_eng)
        r = self.client.post(reverse("task_status", args=[self.cs_task.pk]),
                             {"status": "review", "next": "https://evil.example/"})
        self.assertRedirects(r, reverse("task_detail", args=[self.cs_task.pk]), fetch_redirect_response=False)

    # ---- other team's task, comments, private notes
    def test_outsider_can_view_but_not_comment(self):
        self.login(self.mech_eng)
        r = self.client.get(reverse("task_detail", args=[self.cs_task.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.context["involved"])
        self.assertNotIn("comments", r.context)
        self.assertEqual(self.client.post(reverse("add_comment", args=[self.cs_task.pk]),
                                          {"body": "hi"}).status_code, 403)

    def test_teammate_can_comment(self):
        self.login(self.cs_eng2)
        self.client.post(reverse("add_comment", args=[self.cs_task.pk]), {"body": "on it"})
        self.assertEqual(self.cs_task.comments.count(), 1)

    def test_private_notes_stay_private(self):
        self.login(self.cs_eng)
        self.client.post(reverse("add_private_note", args=[self.cs_task.pk]), {"body": "secret plan"})
        self.assertEqual(PrivateNote.objects.count(), 1)
        self.login(self.cs_boss)
        r = self.client.get(reverse("task_detail", args=[self.cs_task.pk]))
        self.assertNotContains(r, "secret plan")
        self.login(self.cs_eng)
        self.assertContains(self.client.get(reverse("task_detail", args=[self.cs_task.pk])), "secret plan")
