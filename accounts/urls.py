from django.contrib.auth import views as auth_views
from django.urls import path, reverse_lazy

from .views import PortalPasswordResetView, UserLoginView, logout_view

urlpatterns = [
    path("login/", UserLoginView.as_view(), name="login"),
    path("logout/", logout_view, name="logout"),

    # Forgot password: ask for the email, send a link, choose a new password
    path("password-reset/", PortalPasswordResetView.as_view(), name="password_reset"),
    path("password-reset/sent/",
         auth_views.PasswordResetDoneView.as_view(template_name="registration/password_reset_done.html"),
         name="password_reset_done"),
    path("reset/<uidb64>/<token>/",
         auth_views.PasswordResetConfirmView.as_view(
             template_name="registration/password_reset_confirm.html",
             success_url=reverse_lazy("password_reset_complete")),
         name="password_reset_confirm"),
    path("reset/done/",
         auth_views.PasswordResetCompleteView.as_view(template_name="registration/password_reset_complete.html"),
         name="password_reset_complete"),
]
