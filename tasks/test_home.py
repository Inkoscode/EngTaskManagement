from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

LINKS = [
    {"name": "Network Monitor", "description": "Alarms & dashboards",
     "url": "https://nms.example.com/?a=1&b=2", "icon": "icons/nms.png"},
    {"name": "Ticketing", "url": "https://tickets.example.com"},
]


class HomePageTests(TestCase):
    @override_settings(PORTAL_LINKS=LINKS)
    def test_links_render_safely(self):
        r = self.client.get("/")
        self.assertContains(r, 'class="sys"', count=2)
        self.assertContains(r, 'href="https://nms.example.com/?a=1&amp;b=2"')   # URL is escaped
        self.assertContains(r, "Alarms &amp; dashboards")                       # text is escaped
        self.assertContains(r, 'target="_blank" rel="noopener noreferrer"', count=2)
        self.assertContains(r, "/static/icons/nms.png")                         # custom icon
        self.assertContains(r, 'href="#systems"')                               # nav link appears

    @override_settings(PORTAL_LINKS=[])
    def test_section_hidden_when_there_are_no_links(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, 'id="systems"')
        self.assertNotContains(r, 'href="#systems"')

    def test_logo_on_home_and_on_logged_in_pages(self):
        self.assertContains(self.client.get("/"), "/static/icons/logo.")  # logo.svg or logo.png
        user = get_user_model().objects.create_user("u", password="pw")
        self.client.force_login(user)
        self.assertContains(self.client.get(reverse("task_list")), "/static/icons/logo.")

    def test_logged_in_users_still_go_to_the_dashboard(self):
        user = get_user_model().objects.create_user("u", password="pw")
        self.client.force_login(user)
        self.assertRedirects(self.client.get("/"), reverse("task_list"), fetch_redirect_response=False)
