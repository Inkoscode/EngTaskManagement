from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST
from sections.models import Section

from .forms import CommentForm, PrivateNoteForm, StatusForm, TaskForm
from .models import Task
from .permissions import (
    attach_permissions, can_comment, can_create_task, can_edit_task,
    can_update_status, is_involved,
)
from .notifications import notify_assigned
from .services import log_changes, log_created, snapshot


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


# ---------------------------------------------------------------- general dashboard
@login_required
def overview(request):
    """General dashboard: every task, every team, visible to every logged-in user."""
    today = timezone.localdate()
    tasks = Task.objects.select_related("owning_section", "assignee")

    q = request.GET.get("q", "").strip()
    status = request.GET.get("status", "")
    priority = request.GET.get("priority", "")
    section = request.GET.get("section", "")
    mine = request.GET.get("mine", "")
    overdue = request.GET.get("overdue", "")

    if q:
        tasks = tasks.filter(Q(title__icontains=q) | Q(system_ref__icontains=q))
    if status in dict(Task.STATUS_CHOICES):
        tasks = tasks.filter(status=status)
    else:
        status = ""
    if priority in dict(Task.PRIORITY_CHOICES):
        tasks = tasks.filter(priority=priority)
    else:
        priority = ""
    if section.isascii() and section.isdigit():
        tasks = tasks.filter(owning_section_id=int(section))
    else:
        section = ""
    if mine:
        tasks = tasks.filter(assignee=request.user)
        mine = "1"
    if overdue:
        tasks = tasks.filter(status__in=Task.OPEN_STATUSES, due_date__lt=today)
        overdue = "1"

    filters = {"q": q, "status": status, "priority": priority,
               "section": section, "mine": mine, "overdue": overdue}
    page_obj = Paginator(tasks, 25).get_page(request.GET.get("page"))

    stats = Task.objects.aggregate(
        open_count=Count("id", filter=Q(status__in=Task.OPEN_STATUSES)),
        blocked=Count("id", filter=Q(status="blocked")),
        overdue=Count("id", filter=Q(status__in=Task.OPEN_STATUSES, due_date__lt=today)),
        done=Count("id", filter=Q(status="done")),
    )
    sections = Section.objects.annotate(
        total=Count("tasks", distinct=True),
        open_count=Count("tasks", filter=Q(tasks__status__in=Task.OPEN_STATUSES), distinct=True),
        blocked=Count("tasks", filter=Q(tasks__status="blocked"), distinct=True),
        overdue=Count("tasks", filter=Q(tasks__status__in=Task.OPEN_STATUSES,
                                         tasks__due_date__lt=today), distinct=True),
    )
    return render(request, "tasks/overview.html", {
        "page_obj": page_obj,
        "filters": filters,
        "base_qs": urlencode({k: v for k, v in filters.items() if v}),
        "stats": stats,
        "sections": sections,
        "status_choices": Task.STATUS_CHOICES,
        "priority_choices": Task.PRIORITY_CHOICES,
        "today": today,
    })


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

    base = Task.objects.filter(owning_section__in=my_sections).select_related(
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
        Task.objects.filter(assignee=user, status__in=Task.OPEN_STATUSES)
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
    involved = is_involved(user, task)
    ctx = {
        "task": task,
        "today": timezone.localdate(),
        "involved": involved,
        "can_edit": can_edit_task(user, task),
        "can_status": can_update_status(user, task),
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
    else:
        messages.error(request, "That status is not valid.")
    return _back(request, pk)


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
