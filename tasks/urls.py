from django.urls import path
from . import report_views, views

# The project urls.py mounts this file under "tasks/", so no "tasks/" prefix here.
urlpatterns = [
    path("", views.overview, name="task_list"),            # general dashboard (everyone, all teams)
    path("dashboard/", views.my_dashboard, name="my_dashboard"),  # my team's board + my assignments
    path("new/", views.task_create, name="task_create"),
    path("reports/", report_views.report_list, name="report_list"),
    path("reports/generate/", report_views.report_generate, name="report_generate"),
    path("reports/<int:pk>/", report_views.report_detail, name="report_detail"),
    path("<int:pk>/", views.task_detail, name="task_detail"),
    path("<int:pk>/edit/", views.task_edit, name="task_edit"),
    path("<int:pk>/status/", views.task_status, name="task_status"),
    path("<int:pk>/comment/", views.add_comment, name="add_comment"),
    path("<int:pk>/note/", views.add_private_note, name="add_private_note"),
]
