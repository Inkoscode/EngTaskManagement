from datetime import date

from django.utils import timezone

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .forms import ReportForm
from .models import WeeklyReport
from .notifications import report_recipients, send_report, send_test, site_url, users_missing_email
from .permissions import can_manage_reports
from .reports import create_or_refresh_draft, refresh_data, week_bounds


def _require_manager(user):
    if not can_manage_reports(user):
        raise PermissionDenied


@login_required
def report_list(request):
    _require_manager(request.user)
    this_start, _ = week_bounds()
    reports = WeeklyReport.objects.select_related("sent_by")
    return render(request, "tasks/reports.html", {
        "reports": reports,
        "this_week": reports.filter(week_start=this_start).first(),
        "today": timezone.localdate().isoformat(),
    })


@login_required
@require_POST
def report_generate(request):
    _require_manager(request.user)
    try:
        day = date.fromisoformat(request.POST.get("week_of", ""))
    except ValueError:
        day = None
    report, created = create_or_refresh_draft(day, request.user)
    if created:
        messages.success(request, "Draft created. Review it, edit the message, then send it.")
    elif report.status == WeeklyReport.SENT:
        messages.error(request, "That week's report has already been sent.")
    else:
        messages.success(request, "A draft for that week already existed, so its numbers were refreshed.")
    return redirect("report_detail", pk=report.pk)


@login_required
def report_detail(request, pk):
    _require_manager(request.user)
    report = get_object_or_404(WeeklyReport, pk=pk)
    is_draft = report.status == WeeklyReport.DRAFT
    form = ReportForm(request.POST or None, instance=report) if is_draft else None

    if request.method == "POST":
        if not is_draft:
            messages.error(request, "This report was already sent and can no longer be changed.")
            return redirect("report_detail", pk=pk)
        if form.is_valid():
            form.save()  # always save the edits first, whichever button was pressed
            action = request.POST.get("action", "save")
            if action == "refresh":
                refresh_data(report)
                messages.success(request, "Numbers refreshed from the latest task data.")
            elif action == "test":
                if send_test(report, request.user):
                    messages.success(request, f"Test email sent to {request.user.email}.")
                elif not request.user.email:
                    messages.error(request, "Your user has no email address. Add one in the admin first.")
                else:
                    messages.error(request, "The test email could not be sent. See the email log in the admin.")
            elif action == "send":
                result = send_report(report, request.user)
                if result is None:
                    messages.error(request, "This report was already sent.")
                elif result["reverted"]:
                    messages.error(request, "No emails could be delivered, so the report is still a draft. "
                                            "Check the email settings and the email log, then try again.")
                elif result["failed"]:
                    messages.error(request, f"Sent to {result['sent']} people, but {result['failed']} failed. "
                                            "See the delivery log below.")
                else:
                    messages.success(request, f"Report sent to {result['sent']} people.")
            else:
                messages.success(request, "Draft saved.")
            return redirect("report_detail", pk=pk)

    return render(request, "tasks/report_detail.html", {
        "report": report,
        "form": form,
        "data": report.data,
        "personal": None,
        "site_url": site_url(),
        "recipient_count": report_recipients().count(),
        "missing_email": list(users_missing_email()[:15]),
        "logs": report.email_logs.all()[:100],
    })
