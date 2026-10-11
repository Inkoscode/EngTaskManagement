from django.urls import path
from . import views

urlpatterns = [
    path("tasks/", views.task_list, name="task_list"),
    path("tasks/new/", views.task_create, name="task_create"),
    path("tasks/<int:pk>/", views.task_detail, name="task_detail"),
    path("tasks/<int:pk>/edit/", views.task_edit, name="task_edit"),
    path("tasks/<int:pk>/status/", views.task_status, name="task_status"),
    path("tasks/<int:pk>/comment/", views.add_comment, name="add_comment"),
    path("tasks/<int:pk>/note/", views.add_private_note, name="add_private_note"),
]