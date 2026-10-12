"""
All outgoing email lives here.

Emails are sent from settings.DEFAULT_FROM_EMAIL (the portal / admin address).
Every attempt is recorded in EmailLog, and a failed send never breaks the page that triggered it.
"""
import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import EmailMultiAlternatives, get_connection
from django.db.models import F
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from .models import EmailLog, Task, WeeklyReport

logger = logging.getLogger(__name__)
User = get_user_model()
PERSONAL_LIMIT = 10


def site_url():
    return getattr(settings, "SITE_URL", "").rstrip("/")


def absolute(path):
    return f"{site_url()}{path}"


def person(user):
    return user.get_full_name() or user.username


def _deliver(kind, to_email, subject, text, html=None, *, reply_to=None, connection=None,
             task=None, report=None):
    """Send one email and log the result. Returns True if the mail backend accepted it."""
    subject = " ".join(subject.split())  # no newlines in headers
    ok, error = True, ""
    try:
        msg = EmailMultiAlternatives(subject, text, settings.DEFAULT_FROM_EMAIL, [to_email],
                                     reply_to=reply_to or None, connection=connection)
        if html:
            msg.attach_alternative(html, "text/html")
        msg.send()
    except Exception as exc:  # SMTP down, bad password, bad address...
        ok, error = False, f"{type(exc).__name__}: {exc}"[:500]
        logger.exception("Email to %s failed", to_email)
    EmailLog.objects.create(kind=kind, recipient=to_email, subject=subject[:255], ok=ok,
                            error=error, task=task, report=report)
    return ok


# ------------------------------------------------------------------ task assigned
def notify_assigned(task_id, actor):
    """Tell the assignee they have a task. Skips self-assignment and people without an email."""
    task = (Task.objects.select_related("owning_section", "assignee", "creator")
            .filter(pk=task_id).first())
    if task is None or task.assignee is None:
        return False
    assignee = task.assignee
    if not assignee.is_active or not assignee.email:
        return False
    if actor is not None and assignee.pk == actor.pk:
        return False
    ctx = {
        "task": task,
        "assignee": assignee,
        "actor_name": person(actor) if actor is not None else "Someone",
        "task_url": absolute(reverse("task_detail", args=[task.pk])),
        "dashboard_url": absolute(reverse("my_dashboard")),
    }
    reply_to = [actor.email] if actor is not None and actor.email else None
    return _deliver(
        EmailLog.ASSIGNMENT, assignee.email, f"Task assigned to you: {task.title}",
        render_to_string("emails/assignment.txt", ctx),
        render_to_string("emails/assignment.html", ctx),
        reply_to=reply_to, task=task,
    )


# ------------------------------------------------------------------ weekly report
def report_recipients():
    return User.objects.filter(is_active=True).exclude(email="").order_by("username")


def users_missing_email():
    return User.objects.filter(is_active=True, email="").order_by("username")


def personal_block(user):
    """The 'Your tasks' section: open tasks assigned to this person."""
    today = timezone.localdate()
    qs = Task.objects.filter(assignee=user, status__in=Task.OPEN_STATUSES)
    total = qs.count()
    if not total:
        return None
    items = []
    for t in qs.select_related("owning_section").order_by(F("due_date").asc(nulls_last=True), "-created_at")[:PERSONAL_LIMIT]:
        items.append({
            "title": t.title,
            "team": t.owning_section.name,
            "due": f"{t.due_date.day} {t.due_date:%b}" if t.due_date else "",
            "overdue": bool(t.due_date and t.due_date < today),
            "path": reverse("task_detail", args=[t.pk]),
        })
    return {"total": total, "overdue": qs.filter(due_date__lt=today).count(),
            "items": items, "more": max(0, total - PERSONAL_LIMIT)}


def _weekly_content(report, user):
    ctx = {
        "report": report, "data": report.data, "personal": personal_block(user),
        "site_url": site_url(),
        "portal_url": absolute(reverse("task_list")),
    }
    return render_to_string("emails/weekly_report.txt", ctx), render_to_string("emails/weekly_report.html", ctx)


def send_test(report, admin_user):
    """Send the draft to the admin only, so they can see exactly what people will get."""
    if not admin_user.email:
        return False
    text, html = _weekly_content(report, admin_user)
    return _deliver(EmailLog.TEST, admin_user.email, f"[TEST] {report.subject}", text, html, report=report)


def send_report(report, sent_by):
    """
    Send the report to every active user with an email address.
    Returns None if it was already sent (so a double click cannot send twice), otherwise a result dict.
    """
    claimed = WeeklyReport.objects.filter(pk=report.pk, status=WeeklyReport.DRAFT).update(
        status=WeeklyReport.SENT, sent_at=timezone.now(), sent_by=sent_by)
    if not claimed:
        return None
    report.refresh_from_db()
    recipients = list(report_recipients())
    sent = failed = 0

    conn, open_error = get_connection(), ""
    try:
        conn.open()
    except Exception as exc:
        logger.exception("Could not open the email connection")
        conn, open_error = None, f"{type(exc).__name__}: {exc}"[:500]

    try:
        for user in recipients:
            if conn is None:
                EmailLog.objects.create(kind=EmailLog.WEEKLY, recipient=user.email, subject=report.subject[:255],
                                        ok=False, error=open_error, report=report)
                failed += 1
                continue
            text, html = _weekly_content(report, user)
            if _deliver(EmailLog.WEEKLY, user.email, report.subject, text, html, connection=conn, report=report):
                sent += 1
            else:
                failed += 1
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    reverted = bool(recipients) and sent == 0
    if reverted:  # nothing got through (e.g. wrong SMTP password): keep it as a draft to retry
        WeeklyReport.objects.filter(pk=report.pk).update(status=WeeklyReport.DRAFT, sent_at=None, sent_by=None)
    WeeklyReport.objects.filter(pk=report.pk).update(recipient_count=sent, failed_count=failed)
    report.refresh_from_db()
    return {"sent": sent, "failed": failed, "reverted": reverted}
