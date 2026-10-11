from django.contrib import admin
from django.urls import path, include
from tasks import views as task_views

urlpatterns = [
    # Django admin
    path("admin/", admin.site.urls),

    # Normal user login and logout
    path("accounts/", include("accounts.urls")),

    # Homepage
    path("", task_views.home, name="home"),

    # Task management
    path("tasks/", include("tasks.urls")),
]