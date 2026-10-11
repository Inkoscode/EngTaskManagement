from .models import TaskEvent

TRACKED_FIELDS = [
    "title", "description", "priority", "status", "owning_section",
    "assignee", "planned_start", "planned_finish", "due_date", "system_ref",
]


def snapshot(task):
    out = {}
    for f in TRACKED_FIELDS:
        v = getattr(task, f)
        out[f] = "" if v is None else str(v)[:255]
    return out


def log_changes(before, task, actor):
    after = snapshot(task)
    events = [
        TaskEvent(task=task, actor=actor, field=f, old_value=before[f], new_value=after[f])
        for f in TRACKED_FIELDS if before[f] != after[f]
    ]
    TaskEvent.objects.bulk_create(events)


def log_created(task, actor):
    TaskEvent.objects.create(task=task, actor=actor, field="created", new_value=task.title)