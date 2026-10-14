"""Builds the numbers for the weekly report and manages the draft."""
from datetime import timedelta

from django.urls import reverse
from django.utils import timezone
from sections.models import Section

from .models import Task, TaskEvent, WeeklyReport

LIMIT = 8  # names listed per section before "and N more"
DEFAULT_INTRO = "Hello team,\n\nHere is the weekly summary across all teams."


def week_bounds(day=None):
    """Monday and Sunday of the week containing `day` (default: today)."""
    day = day or timezone.localdate()
    start = day - timedelta(days=day.weekday())
    return start, start + timedelta(days=6)


def default_subject(start, end):
    return f"Weekly engineering report: {start.day} {start:%b} to {end.day} {end:%b %Y}"


def _label(d):
    return f"{d.day} {d:%b}"


def _rows(qs):
    total = qs.count()
    rows = []
    for t in qs.select_related("assignee")[:LIMIT]:
        rows.append({
            "title": t.title,
            "assignee": (t.assignee.get_full_name() or t.assignee.username) if t.assignee else "Unassigned",
            "due": _label(t.due_date) if t.due_date else "",
            "reason": t.blocked_reason if t.status == "blocked" else "",
            "path": reverse("task_detail", args=[t.pk]),
        })
    return {"rows": rows, "total": total, "more": max(0, total - len(rows))}


def build_report_data(start, end):
    today = timezone.localdate()
    done_ids = list(
        TaskEvent.objects.filter(field="status", new_value="done", at__date__gte=start, at__date__lte=end)
        .values_list("task_id", flat=True)
    )
    teams = []
    totals = {"created": 0, "completed": 0, "open": 0, "blocked": 0, "overdue": 0}
    for section in Section.objects.all():
        everything = Task.objects.filter(owning_section=section)   # includes archived: they still count as done
        tasks = everything.active()
        open_qs = tasks.filter(status__in=Task.OPEN_STATUSES)
        team = {
            "name": section.name,
            "created": everything.filter(created_at__date__gte=start, created_at__date__lte=end).count(),
            "open": open_qs.count(),
            "completed": _rows(everything.filter(pk__in=done_ids, status="done").order_by("-created_at")),
            "blocked": _rows(open_qs.filter(status="blocked")),
            "overdue": _rows(open_qs.filter(due_date__lt=today).order_by("due_date")),
            "upcoming": _rows(open_qs.filter(due_date__gt=end, due_date__lte=end + timedelta(days=7))
                              .order_by("due_date")),
        }
        teams.append(team)
        totals["created"] += team["created"]
        totals["completed"] += team["completed"]["total"]
        totals["open"] += team["open"]
        totals["blocked"] += team["blocked"]["total"]
        totals["overdue"] += team["overdue"]["total"]
    return {"generated_on": today.isoformat(), "totals": totals, "teams": teams}


def refresh_data(report):
    report.data = build_report_data(report.week_start, report.week_end)
    report.save(update_fields=["data"])


def create_or_refresh_draft(day=None, user=None):
    """
    Make the draft for the week containing `day`. If one already exists and is still a draft,
    refresh its numbers (keeping the subject and message). A sent report is never changed.
    Returns (report, created).
    """
    start, end = week_bounds(day)
    report = WeeklyReport.objects.filter(week_start=start).first()
    if report is None:
        report = WeeklyReport.objects.create(
            week_start=start, week_end=end, subject=default_subject(start, end),
            intro=DEFAULT_INTRO, data=build_report_data(start, end), created_by=user)
        return report, True
    if report.status == WeeklyReport.DRAFT:
        refresh_data(report)
    return report, False
