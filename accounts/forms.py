from django.contrib.auth.forms import PasswordResetForm
from django.template.loader import render_to_string

from tasks.models import EmailLog
from tasks.notifications import _deliver


class PortalPasswordResetForm(PasswordResetForm):
    """Sends the reset link through the portal's own email setup (SMTP or Graph) and logs the result.
    If the mail server is down the person still sees the normal "check your inbox" page, and the
    failure is recorded under Email logs in the admin."""

    def send_mail(self, subject_template_name, email_template_name, context, from_email, to_email,
                  html_email_template_name=None):
        subject = "".join(render_to_string(subject_template_name, context).splitlines())
        text = render_to_string(email_template_name, context)
        html = render_to_string(html_email_template_name, context) if html_email_template_name else None
        _deliver(EmailLog.PASSWORD_RESET, to_email, subject, text, html)
