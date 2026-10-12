from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from sections.models import Section


class Membership(models.Model):
    ENGINEER = "engineer"
    TEAM_BOSS = "team_boss"
    ROLE_CHOICES = [(ENGINEER, "Engineer"), (TEAM_BOSS, "Team boss")]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memberships")
    section = models.ForeignKey(Section, on_delete=models.CASCADE, related_name="memberships")
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default=ENGINEER)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "section"], name="uniq_user_section")]

    def __str__(self):
        return f"{self.user} - {self.section} ({self.get_role_display()})"


class Task(models.Model):
    PRIORITY_CHOICES = [("low", "Low"), ("medium", "Medium"), ("high", "High"), ("critical", "Critical")]
    STATUS_CHOICES = [
        ("todo", "To do"),
        ("in_progress", "In progress"),
        ("blocked", "Blocked"),
        ("review", "In review"),
        ("done", "Done"),
    ]
    # Everything that is not finished. Used for "open" counts and overdue checks.
    OPEN_STATUSES = ["todo", "in_progress", "blocked", "review"]

    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    priority = models.CharField(max_length=10, choices=PRIORITY_CHOICES, default="medium")
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default="todo")
    owning_section = models.ForeignKey(Section, on_delete=models.PROTECT, related_name="tasks")
    assignee = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                 on_delete=models.SET_NULL, related_name="assigned_tasks")
    creator = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_tasks")
    planned_start = models.DateField(null=True, blank=True)
    planned_finish = models.DateField(null=True, blank=True)
    due_date = models.DateField(null=True, blank=True)
    system_ref = models.CharField("System reference", max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status"]), models.Index(fields=["due_date"])]

    def clean(self):
        if self.planned_start and self.planned_finish and self.planned_finish < self.planned_start:
            raise ValidationError("Planned finish cannot be before planned start.")

    def __str__(self):
        return self.title


class Comment(models.Model):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="comments")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]


class PrivateNote(models.Model):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="private_notes")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]


class TaskEvent(models.Model):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="events")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    field = models.CharField(max_length=50)
    old_value = models.CharField(max_length=255, blank=True)
    new_value = models.CharField(max_length=255, blank=True)
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-at"]


class WeeklyReport(models.Model):
    """A weekly summary. Starts as a draft; an admin reviews it and sends it to everyone."""
    DRAFT = "draft"
    SENT = "sent"
    STATUS_CHOICES = [(DRAFT, "Draft"), (SENT, "Sent")]

    week_start = models.DateField(unique=True)  # always a Monday
    week_end = models.DateField()
    subject = models.CharField(max_length=200)
    intro = models.TextField(blank=True, help_text="A short message shown at the top of the email.")
    data = models.JSONField(default=dict, blank=True)  # snapshot of the numbers shown in the preview
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=DRAFT)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="+")
    sent_at = models.DateTimeField(null=True, blank=True)
    sent_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                on_delete=models.SET_NULL, related_name="+")
    recipient_count = models.PositiveIntegerField(default=0)
    failed_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-week_start"]

    def __str__(self):
        return self.subject


class EmailLog(models.Model):
    """One row per email the portal tried to send, so failures are visible."""
    ASSIGNMENT = "assignment"
    WEEKLY = "weekly"
    TEST = "weekly_test"
    DRAFT_READY = "draft_ready"
    KIND_CHOICES = [(ASSIGNMENT, "Task assigned"), (WEEKLY, "Weekly report"),
                    (TEST, "Weekly report test"), (DRAFT_READY, "Draft ready")]

    kind = models.CharField(max_length=20, choices=KIND_CHOICES)
    recipient = models.CharField(max_length=254)
    subject = models.CharField(max_length=255)
    ok = models.BooleanField(default=True)
    error = models.TextField(blank=True)
    task = models.ForeignKey(Task, null=True, blank=True, on_delete=models.SET_NULL, related_name="email_logs")
    report = models.ForeignKey(WeeklyReport, null=True, blank=True, on_delete=models.SET_NULL,
                               related_name="email_logs")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.get_kind_display()} to {self.recipient} ({'ok' if self.ok else 'failed'})"
