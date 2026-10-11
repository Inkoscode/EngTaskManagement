from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from sections.models import Section

from .forms import CommentForm, PrivateNoteForm, StatusForm, TaskForm
from .models import Task
from .permissions import can_create_task, can_edit_task, can_update_status, visible_tasks  # pyright: ignore[reportMissingImports]
from .services import log_changes, log_created, snapshot


@login_required
def task_list(request):
    tasks = visible_tasks(request.user).select_related("owning_section", "assignee")
    status = request.GET.get("status", "")
    priority = request.GET.get("priority", "")
    section = request.GET.get("section", "")
    mine = request.GET.get("mine", "")
    if status:
        tasks = tasks.filter(status=status)
    if priority:
        tasks = tasks.filter(priority=priority)
    if section:
        tasks = tasks.filter(owning_section_id=section)
    if mine:
        tasks = tasks.filter(assignee=request.user)
    return render(request, "tasks/task_list.html", {
        "tasks": tasks,
        "sections": Section.objects.all(),
        "status_choices": Task.STATUS_CHOICES,
        "priority_choices": Task.PRIORITY_CHOICES,
        "filters": {"status": status, "priority": priority, "section": section, "mine": mine},
        "can_create": can_create_task(request.user),
    })


@login_required
def task_detail(request, pk):
    task = get_object_or_404(
        visible_tasks(request.user).select_related("owning_section", "assignee", "creator"), pk=pk
    )
    return render(request, "tasks/task_detail.html", {
        "task": task,
        "comments": task.comments.select_related("author"),
        "events": task.events.select_related("actor"),
        "notes": task.private_notes.filter(author=request.user),
        "comment_form": CommentForm(),
        "note_form": PrivateNoteForm(),
        "status_form": StatusForm(instance=task),
        "can_edit": can_edit_task(request.user, task),
        "can_status": can_update_status(request.user, task),
    })


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
        return redirect("task_detail", pk=task.pk)
    return render(request, "tasks/task_form.html", {"form": form, "title": "New task"})


@login_required
def task_edit(request, pk):
    task = get_object_or_404(visible_tasks(request.user), pk=pk)
    if not can_edit_task(request.user, task):
        raise PermissionDenied
    before = snapshot(task)
    form = TaskForm(request.POST or None, instance=task, user=request.user)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            task = form.save()
            log_changes(before, task, request.user)
        return redirect("task_detail", pk=task.pk)
    return render(request, "tasks/task_form.html", {"form": form, "title": f"Edit: {task.title}"})


@login_required
@require_POST
def task_status(request, pk):
    task = get_object_or_404(visible_tasks(request.user), pk=pk)
    if not can_update_status(request.user, task):
        raise PermissionDenied
    before = snapshot(task)
    form = StatusForm(request.POST, instance=task)
    if form.is_valid():
        with transaction.atomic():
            task = form.save()
            log_changes(before, task, request.user)
    return redirect("task_detail", pk=pk)


@login_required
@require_POST
def add_comment(request, pk):
    task = get_object_or_404(visible_tasks(request.user), pk=pk)
    form = CommentForm(request.POST)
    if form.is_valid():
        c = form.save(commit=False)
        c.task, c.author = task, request.user
        c.save()
    return redirect("task_detail", pk=pk)


@login_required
@require_POST
def add_private_note(request, pk):
    task = get_object_or_404(visible_tasks(request.user), pk=pk)
    form = PrivateNoteForm(request.POST)
    if form.is_valid():
        n = form.save(commit=False)
        n.task, n.author = task, request.user
        n.save()
    return redirect("task_detail", pk=pk)

from django.shortcuts import redirect, render


def home(request):
    if request.user.is_authenticated:
        return redirect("task_list")

    return render(request, "home.html")