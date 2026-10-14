from urllib.parse import urlparse

from django.conf import settings
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView, PasswordResetView
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.views.decorators.http import require_POST

from .forms import PortalPasswordResetForm


class UserLoginView(LoginView):
    template_name = "registration/login.html"
    redirect_authenticated_user = True


class PortalPasswordResetView(PasswordResetView):
    form_class = PortalPasswordResetForm
    template_name = "registration/password_reset_form.html"
    email_template_name = "registration/password_reset_email.txt"
    html_email_template_name = "registration/password_reset_email.html"
    subject_template_name = "registration/password_reset_subject.txt"
    success_url = reverse_lazy("password_reset_done")

    @property
    def extra_email_context(self):
        """Build the link in the email from SITE_URL, so it points at the real portal address."""
        site = urlparse(getattr(settings, "SITE_URL", ""))
        if site.scheme and site.netloc:
            return {"protocol": site.scheme, "domain": site.netloc}
        return None


@login_required
@require_POST
def logout_view(request):
    logout(request)
    return redirect("login")
