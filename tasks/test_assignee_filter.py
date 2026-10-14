from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from sections.models import Section

from .forms import TaskForm
from .models import Membership

User = get_user_model()


class AssigneeFilterTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.cs, cls.ps, cls.ip = (Section.objects.create(name=n) for n in ("CS Core", "PS Core", "IP Core"))
        mk = lambda n, first="": User.objects.create_user(n, password="pw", first_name=first)
        cls.boss = mk("boss")
        cls.cs_boss, cls.cs_eng, cls.ps_eng, cls.both = mk("cs_boss", "Amina"), mk("cs_eng", "Brian"), mk("ps_eng", "Chikondi"), mk("both", "Dalitso")
        for u, s, r in [(cls.cs_boss, cls.cs, "team_boss"), (cls.cs_eng, cls.cs, "engineer"),
                        (cls.ps_eng, cls.ps, "engineer"), (cls.both, cls.cs, "engineer"),
                        (cls.both, cls.ps, "engineer"), (cls.cs_boss, cls.ps, "engineer")]:
            Membership.objects.create(user=u, section=s, role=r)
        cls.boss.is_overall_boss = True
        cls.boss.save()

    def options(self, user):
        html = str(TaskForm(user=user)["assignee"])
        return html

    def test_each_person_is_tagged_with_their_teams(self):
        html = self.options(self.boss)
        self.assertIn(f'data-sections="{self.cs.pk}"', html)              # cs_eng: CS only
        self.assertIn(f'data-sections="{self.ps.pk}"', html)              # ps_eng: PS only
        both = {self.cs.pk, self.ps.pk}
        tagged = [seg for seg in html.split("<option")[1:] if "Dalitso" in seg][0]
        value = tagged.split('data-sections="')[1].split('"')[0]
        self.assertEqual({int(x) for x in value.split(",")}, both)        # both teams listed for "both"

    def test_people_outside_my_teams_are_not_offered_at_all(self):
        outsider = User.objects.create_user("ip_eng", first_name="Esther", password="pw")
        Membership.objects.create(user=outsider, section=self.ip, role="engineer")
        html = self.options(self.cs_boss)   # cs_boss belongs to CS and PS, not IP
        self.assertNotIn("Esther", html)
        self.assertIn("Esther", self.options(self.boss))  # the overall boss can see every team

    def test_people_are_sorted_by_name(self):
        html = self.options(self.boss)
        positions = [html.index(n) for n in ("Amina", "Brian", "Chikondi", "Dalitso")]
        self.assertEqual(positions, sorted(positions))

    def test_server_still_rejects_a_mismatched_assignee(self):
        self.client.force_login(self.cs_boss)
        data = {"title": "Tamper", "priority": "medium", "status": "todo",
                "owning_section": self.cs.pk, "assignee": self.ps_eng.pk}   # PS-only person on a CS task
        r = self.client.post(reverse("task_create"), data)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "must be a member of the owning team")

    def test_page_ships_the_filter_script(self):
        self.client.force_login(self.cs_boss)
        r = self.client.get(reverse("task_create"))
        self.assertContains(r, "id=\"help_assignee\"")
        self.assertContains(r, "getElementById('id_owning_section')")
