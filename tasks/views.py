import csv
from collections import Counter
from datetime import date, timedelta
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Case, Count, F, IntegerField, OrderBy, Q, Value, When
from django.db.models.functions import Lower
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST
from sections.models import Section

from .forms import CommentForm, PrivateNoteForm, StatusForm, TaskForm
from .models import Membership, Notification, Task, TaskEvent
from .notifications import notify_assigned, notify_commented
from .permissions import (
    attach_permissions, can_archive_task, can_comment, can_create_task, can_edit_task,
    can_update_status, can_view_workload, is_involved,
)
from .services import log_changes, log_created, log_event, snapshot


def home(request):
    if request.user.is_authenticated:
        return redirect("task_list")
    return render(request, "home.html", {"systems": getattr(settings, "PORTAL_LINKS", [])})


def _back(request, fallback_pk):
    """Return to the page the form was submitted from (safely), else to the task."""
    nxt = request.POST.get("next", "")
    if nxt and url_has_allowed_host_and_scheme(
        nxt, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return redirect(nxt)
    return redirect("task_detail", pk=fallback_pk)


# ---------------------------------------------------------------- filtering and sorting
SORT_COLUMNS = [("title", "Task"), ("team", "Team"), ("assignee", "Assignee"),
                ("priority", "Priority"), ("status", "Status"), ("due", "Due")]
SORT_KEYS = {key for key, _ in SORT_COLUMNS}


def _rank(choices):
    """1, 2, 3... following the order of the choices, so 'priority' sorts by urgency not alphabet."""
    return Case(*[When(**{field: value}, then=Value(i)) for i, (field, value) in enumerate(choices, start=1)],
                default=Value(0), output_field=IntegerField())


def _apply_sort(tasks, key, direction):
    desc = direction == "desc"
    if key == "title":
        order = [OrderBy(Lower("title"), descending=desc)]
    elif key == "team":
        order = [OrderBy(Lower("owning_section__name"), descending=desc)]
    elif key == "assignee":
        order = [OrderBy(Lower("assignee__username"), descending=desc, nulls_last=True)]
    elif key == "priority":  # ascending = most urgent first
        tasks = tasks.annotate(sort_rank=_rank([("priority", v) for v, _ in reversed(Task.PRIORITY_CHOICES)]))
        order = [OrderBy(F("sort_rank"), descending=desc)]
    elif key == "status":    # ascending = workflow order
        tasks = tasks.annotate(sort_rank=_rank([("status", v) for v, _ in Task.STATUS_CHOICES]))
        order = [OrderBy(F("sort_rank"), descending=desc)]
    else:                    # due date: tasks without one always go last
        order = [OrderBy(F("due_date"), descending=desc, nulls_last=True)]
    return tasks.order_by(*order, "-created_at", "-id")


def _task_filters(request, today):
    """Read the filter and sort options from the address bar. Bad values are ignored, never an error."""
    g = request.GET
    tasks = Task.objects.archived() if g.get("archived") else Task.objects.active()
    tasks = tasks.select_related("owning_section", "assignee")

    q = g.get("q", "").strip()
    if q:
        tasks = tasks.filter(Q(title__icontains=q) | Q(system_ref__icontains=q))
    status = g.get("status", "")
    if status in dict(Task.STATUS_CHOICES):
        tasks = tasks.filter(status=status)
    else:
        status = ""
    priority = g.get("priority", "")
    if priority in dict(Task.PRIORITY_CHOICES):
        tasks = tasks.filter(priority=priority)
    else:
        priority = ""
    section = g.get("section", "")
    if section.isascii() and section.isdigit():
        tasks = tasks.filter(owning_section_id=int(section))
    else:
        section = ""
    mine = "1" if g.get("mine") else ""
    if mine:
        tasks = tasks.filter(assignee=request.user)
    overdue = "1" if g.get("overdue") else ""
    if overdue:
        tasks = tasks.filter(status__in=Task.OPEN_STATUSES, due_date__lt=today)
    sort = g.get("sort", "")
    direction = "desc" if g.get("dir") == "desc" else "asc"
    if sort in SORT_KEYS:
        tasks = _apply_sort(tasks, sort, direction)
    else:
        sort, direction = "", ""

    filters = {"q": q, "status": status, "priority": priority, "section": section, "mine": mine,
               "overdue": overdue, "archived": "1" if g.get("archived") else "", "sort": sort, "dir": direction}
    return tasks, filters


# ---------------------------------------------------------------- general dashboard
@login_required
def overview(request):
    """General dashboard: every task, every team, visible to every logged-in user."""
    today = timezone.localdate()
    tasks, filters = _task_filters(request, today)
    page_obj = Paginator(tasks, 25).get_page(request.GET.get("page"))

    # Clickable column headings: the first click sorts ascending, the next one flips it.
    kept = {k: v for k, v in filters.items() if v and k not in ("sort", "dir")}
    sort_columns = []
    for key, label in SORT_COLUMNS:
        active = filters["sort"] == key
        next_dir = "desc" if (active and filters["dir"] == "asc") else "asc"
        sort_columns.append({
            "key": key, "label": label, "active": active, "dir": filters["dir"] if active else "",
            "aria": "ascending" if filters["dir"] == "asc" else "descending",
            "url": "?" + urlencode({**kept, "sort": key, "dir": next_dir}),
        })

    live = Task.objects.active()
    stats = live.aggregate(
        open_count=Count("id", filter=Q(status__in=Task.OPEN_STATUSES)),
        blocked=Count("id", filter=Q(status="blocked")),
        overdue=Count("id", filter=Q(status__in=Task.OPEN_STATUSES, due_date__lt=today)),
        done=Count("id", filter=Q(status="done")),
    )
    not_archived = Q(tasks__archived_at__isnull=True)
    sections = Section.objects.annotate(
        total=Count("tasks", filter=not_archived, distinct=True),
        open_count=Count("tasks", filter=not_archived & Q(tasks__status__in=Task.OPEN_STATUSES), distinct=True),
        blocked=Count("tasks", filter=not_archived & Q(tasks__status="blocked"), distinct=True),
        overdue=Count("tasks", filter=not_archived & Q(tasks__status__in=Task.OPEN_STATUSES,
                                                       tasks__due_date__lt=today), distinct=True),
    )
    return render(request, "tasks/overview.html", {
        "page_obj": page_obj,
        "filters": filters,
        "base_qs": urlencode({k: v for k, v in filters.items() if v}),
        "sort_columns": sort_columns,
        "stats": stats,
        "sections": sections,
        "status_choices": Task.STATUS_CHOICES,
        "priority_choices": Task.PRIORITY_CHOICES,
        "today": today,
    })


# ---------------------------------------------------------------- export
def _csv_safe(value):
    """Stop spreadsheet formulas: text starting with = + - @ would otherwise run when opened in Excel."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


@login_required
def task_export(request):
    """Download the tasks matching the current filters as a CSV file that opens in Excel."""
    today = timezone.localdate()
    tasks, _ = _task_filters(request, today)
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="tasks-{today.isoformat()}.csv"'
    response.write("\ufeff")  # lets Excel read accents and symbols correctly
    out = csv.writer(response)
    out.writerow(["ID", "Title", "Team", "Assignee", "Created by", "Priority", "Status", "Blocked reason",
                  "Planned start", "Planned finish", "Due date", "System reference", "Created", "Archived"])
    for t in tasks.select_related("creator").iterator():
        out.writerow([_csv_safe(v) for v in (
            t.pk, t.title, t.owning_section.name,
            (t.assignee.get_full_name() or t.assignee.username) if t.assignee else "",
            t.creator.get_full_name() or t.creator.username,
            t.get_priority_display(), t.get_status_display(), t.blocked_reason,
            t.planned_start or "", t.planned_finish or "", t.due_date or "", t.system_ref,
            timezone.localtime(t.created_at).strftime("%Y-%m-%d %H:%M"),
            "Yes" if t.is_archived else "",
        )])
    return response


# ---------------------------------------------------------------- my dashboard
@login_required
def my_dashboard(request):
    """My team's board plus the tasks assigned to me. Add / assign from here."""
    user = request.user
    today = timezone.localdate()

    if user.is_overall_boss:
        my_sections = Section.objects.all()
    else:
        my_sections = Section.objects.filter(memberships__user=user).distinct()

    base = Task.objects.active().filter(owning_section__in=my_sections).select_related(
        "owning_section", "assignee")
    open_tasks = attach_permissions(user, base.filter(status__in=Task.OPEN_STATUSES))
    done_tasks = attach_permissions(user, base.filter(status="done")[:10])  # newest 10 only
    board_tasks = open_tasks + done_tasks
    columns = [
        (key, label, [t for t in board_tasks if t.status == key])
        for key, label in Task.STATUS_CHOICES
    ]

    assigned = attach_permissions(
        user,
        Task.objects.active().filter(assignee=user, status__in=Task.OPEN_STATUSES)
        .select_related("owning_section", "assignee"),
    )
    return render(request, "tasks/dashboard.html", {
        "my_sections": my_sections,
        "columns": columns,
        "assigned": assigned,
        "status_choices": Task.STATUS_CHOICES,
        "team_open": len(open_tasks),
        "team_blocked": sum(1 for t in open_tasks if t.status == "blocked"),
        "team_overdue": sum(1 for t in open_tasks if t.due_date and t.due_date < today),
        "can_create": can_create_task(user),
        "today": today,
    })


# ---------------------------------------------------------------- task pages
@login_required
def task_detail(request, pk):
    task = get_object_or_404(
        Task.objects.select_related("owning_section", "assignee", "creator"), pk=pk
    )
    user = request.user
    # Opening a task counts as reading its bell notifications.
    Notification.objects.filter(user=user, task=task, read_at__isnull=True).update(read_at=timezone.now())
    involved = is_involved(user, task)
    ctx = {
        "task": task,
        "today": timezone.localdate(),
        "involved": involved,
        "can_edit": can_edit_task(user, task),
        "can_status": can_update_status(user, task),
        "can_archive": can_archive_task(user, task),
        "status_form": StatusForm(instance=task),
    }
    if involved:  # comments, history and private notes are for the people doing the work
        ctx.update({
            "comments": task.comments.select_related("author"),
            "events": task.events.select_related("actor"),
            "notes": task.private_notes.filter(author=user),
            "comment_form": CommentForm(),
            "note_form": PrivateNoteForm(),
        })
    return render(request, "tasks/task_detail.html", ctx)


@login_required
def task_create(request):
    if not can_create_task(request.user):
        raise PermissionDenied
    form = TaskForm(request.POST or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            task = form.save(commit=False)
            task.creator = request.user
            task.save()
            log_created(task, request.user)
            transaction.on_commit(lambda pk=task.pk, actor=request.user: notify_assigned(pk, actor))
        messages.success(request, "Task created.")
        return redirect("task_detail", pk=task.pk)
    return render(request, "tasks/task_form.html", {"form": form, "title": "New task"})


@login_required
def task_edit(request, pk):
    task = get_object_or_404(Task, pk=pk)
    if not can_edit_task(request.user, task):
        raise PermissionDenied
    before = snapshot(task)  # must be taken before the form touches the instance
    old_assignee_id = task.assignee_id
    form = TaskForm(request.POST or None, instance=task, user=request.user)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            task = form.save()
            log_changes(before, task, request.user)
            if task.assignee_id != old_assignee_id:
                transaction.on_commit(lambda pk=task.pk, actor=request.user: notify_assigned(pk, actor))
        messages.success(request, "Task updated.")
        return redirect("task_detail", pk=task.pk)
    return render(request, "tasks/task_form.html", {"form": form, "title": f"Edit: {task.title}"})


@login_required
@require_POST
def task_status(request, pk):
    task = get_object_or_404(Task, pk=pk)
    if not can_update_status(request.user, task):
        raise PermissionDenied
    before = snapshot(task)
    form = StatusForm(request.POST, instance=task)
    if form.is_valid():
        with transaction.atomic():
            task = form.save()
            log_changes(before, task, request.user)
        messages.success(request, f"Status set to {task.get_status_display()}.")
    elif "blocked_reason" in form.errors:
        messages.error(request, "Say why the task is blocked. Open the task, choose Blocked and write the reason.")
    else:
        messages.error(request, "That status is not valid.")
    return _back(request, pk)


@login_required
@require_POST
def task_archive(request, pk):
    task = get_object_or_404(Task, pk=pk)
    if not can_archive_task(request.user, task):
        raise PermissionDenied
    if not task.is_archived:
        with transaction.atomic():
            task.archived_at = timezone.now()
            task.save(update_fields=["archived_at"])
            log_event(task, request.user, "archived", "", "Archived")
        messages.success(request, "Task archived. It no longer shows on the dashboards.")
    return redirect("task_detail", pk=pk)


@login_required
@require_POST
def task_unarchive(request, pk):
    task = get_object_or_404(Task, pk=pk)
    if not can_archive_task(request.user, task):
        raise PermissionDenied
    if task.is_archived:
        with transaction.atomic():
            task.archived_at = None
            task.save(update_fields=["archived_at"])
            log_event(task, request.user, "archived", "Archived", "Restored")
        messages.success(request, "Task restored to the dashboards.")
    return redirect("task_detail", pk=pk)


@login_required
@require_POST
def add_comment(request, pk):
    task = get_object_or_404(Task, pk=pk)
    if not can_comment(request.user, task):
        raise PermissionDenied
    form = CommentForm(request.POST)
    if form.is_valid():
        c = form.save(commit=False)
        c.task, c.author = task, request.user
        c.save()
        transaction.on_commit(lambda cid=c.pk: notify_commented(cid))
    else:
        messages.error(request, "Write something before posting a comment.")
    return redirect("task_detail", pk=pk)


@login_required
@require_POST
def add_private_note(request, pk):
    task = get_object_or_404(Task, pk=pk)
    if not can_comment(request.user, task):
        raise PermissionDenied
    form = PrivateNoteForm(request.POST)
    if form.is_valid():
        n = form.save(commit=False)
        n.task, n.author = task, request.user
        n.save()
        messages.success(request, "Private note saved. Only you can see it.")
    else:
        messages.error(request, "Write something before saving a note.")
    return redirect("task_detail", pk=pk)


# ---------------------------------------------------------------- bell notifications
@login_required
def notification_list(request):
    items = request.user.notifications.select_related("task")[:100]
    unread = request.user.notifications.filter(read_at__isnull=True).count()
    return render(request, "tasks/notifications.html", {"items": items, "unread": unread})


@login_required
@require_POST
def notifications_read_all(request):
    request.user.notifications.filter(read_at__isnull=True).update(read_at=timezone.now())
    return redirect("notification_list")


# ---------------------------------------------------------------- calendar / timeline
CALENDAR_DAYS = 28  # four weeks per screen


@login_required
def calendar(request):
    """A four-week timeline: a bar from planned start to planned finish, and a marker on the due date."""
    today = timezone.localdate()
    try:
        picked = date.fromisoformat(request.GET.get("start", ""))
    except ValueError:
        picked = today
    start = picked - timedelta(days=picked.weekday())          # always begins on a Monday
    end = start + timedelta(days=CALENDAR_DAYS - 1)

    team = request.GET.get("team", "")
    team = team if team.isascii() and team.isdigit() else ""
    mine = "1" if request.GET.get("mine") else ""
    show_done = "1" if request.GET.get("done") else ""

    tasks = Task.objects.active().select_related("owning_section", "assignee")
    if team:
        tasks = tasks.filter(owning_section_id=int(team))
    if mine:
        tasks = tasks.filter(assignee=request.user)
    if not show_done:
        tasks = tasks.exclude(status="done")

    dated = Q(planned_start__isnull=False) | Q(planned_finish__isnull=False) | Q(due_date__isnull=False)
    no_dates = tasks.exclude(dated).count()

    groups = {}
    for t in tasks.filter(dated):
        first = t.planned_start or t.planned_finish or t.due_date
        last = t.planned_finish or t.planned_start or t.due_date
        if first > last:
            first, last = last, first
        bar = first <= end and last >= start
        due_inside = bool(t.due_date and start <= t.due_date <= end)
        if not (bar or due_inside):
            continue
        row = {"id": t.pk, "title": t.title, "status": t.status, "status_label": t.get_status_display(),
               "assignee": (t.assignee.get_full_name() or t.assignee.username) if t.assignee else "Unassigned",
               "sort": first, "start_col": None, "span": None, "cut_left": False, "cut_right": False,
               "due_col": (t.due_date - start).days + 2 if due_inside else None, "due": t.due_date,
               "overdue": bool(t.due_date and t.due_date < today and t.status != "done"),
               "first": first, "last": last}
        if bar:
            shown_first, shown_last = max(first, start), min(last, end)
            row.update(start_col=(shown_first - start).days + 2, span=(shown_last - shown_first).days + 1,
                       cut_left=first < start, cut_right=last > end)
        groups.setdefault(t.owning_section.name, []).append(row)

    days = [{"date": start + timedelta(days=i), "today": start + timedelta(days=i) == today,
             "weekend": (start + timedelta(days=i)).weekday() >= 5} for i in range(CALENDAR_DAYS)]
    base = {k: v for k, v in {"team": team, "mine": mine, "done": show_done}.items() if v}
    return render(request, "tasks/calendar.html", {
        "groups": [{"name": name, "rows": sorted(rows, key=lambda r: (r["sort"], r["title"].lower()))}
                   for name, rows in sorted(groups.items(), key=lambda kv: kv[0].lower())],
        "days": days,
        "weeks": [start + timedelta(days=7 * i) for i in range(CALENDAR_DAYS // 7)],
        "start": start, "end": end, "today": today,
        "today_col": (today - start).days + 2 if start <= today <= end else None,
        "prev_url": "?" + urlencode({**base, "start": (start - timedelta(days=CALENDAR_DAYS)).isoformat()}),
        "next_url": "?" + urlencode({**base, "start": (start + timedelta(days=CALENDAR_DAYS)).isoformat()}),
        "today_url": "?" + urlencode(base),
        "sections": Section.objects.all(),
        "filters": {"team": team, "mine": mine, "done": show_done},
        "no_dates": no_dates,
        "has_rows": bool(groups),
    })


# ---------------------------------------------------------------- team workload
@login_required
def workload(request):
    """Open tasks per person, for team bosses (their teams) and the overall boss (all teams)."""
    user = request.user
    if not can_view_workload(user):
        raise PermissionDenied
    if user.is_overall_boss:
        sections = Section.objects.all()
    else:
        sections = Section.objects.filter(memberships__user=user,
                                          memberships__role=Membership.TEAM_BOSS).distinct()
    today = timezone.localdate()
    soon = today + timedelta(days=7)
    done_ids = list(TaskEvent.objects.filter(
        field="status", new_value="done", at__gte=timezone.now() - timedelta(days=14)
    ).values_list("task_id", flat=True))

    teams = []
    for section in sections:
        live = Task.objects.active().filter(owning_section=section)
        per_person = {row["assignee"]: row for row in live.filter(assignee__isnull=False).values("assignee").annotate(
            open_count=Count("id", filter=Q(status__in=Task.OPEN_STATUSES)),
            blocked=Count("id", filter=Q(status="blocked")),
            overdue=Count("id", filter=Q(status__in=Task.OPEN_STATUSES, due_date__lt=today)),
            due_soon=Count("id", filter=Q(status__in=Task.OPEN_STATUSES, due_date__gte=today, due_date__lte=soon)),
        )}
        finished = Counter(live.filter(status="done", id__in=done_ids).values_list("assignee_id", flat=True))
        rows = []
        members = (Membership.objects.filter(section=section, user__is_active=True).select_related("user")
                   .order_by("user__first_name", "user__last_name", "user__username"))
        for m in members:
            c = per_person.get(m.user_id, {})
            rows.append({"name": m.user.get_full_name() or m.user.username, "role": m.get_role_display(),
                         "open": c.get("open_count", 0), "blocked": c.get("blocked", 0),
                         "overdue": c.get("overdue", 0), "due_soon": c.get("due_soon", 0),
                         "finished": finished.get(m.user_id, 0)})
        rows.sort(key=lambda r: (-r["open"], r["name"].lower()))
        busiest = max([r["open"] for r in rows] or [0])
        for r in rows:
            r["pct"] = round(100 * r["open"] / busiest) if busiest else 0
        teams.append({"name": section.name, "rows": rows,
                      "unassigned": live.filter(assignee__isnull=True, status__in=Task.OPEN_STATUSES).count(),
                      "open_total": sum(r["open"] for r in rows)})
    return render(request, "tasks/workload.html", {"teams": teams})
