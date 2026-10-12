from django.contrib import admin
from .models import Comment, EmailLog, Membership, Task, TaskEvent, WeeklyReport


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ("user", "section", "role")
    list_filter = ("section", "role")


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ("title", "owning_section", "assignee", "priority", "status", "due_date")
    list_filter = ("owning_section", "status", "priority")
    search_fields = ("title", "system_ref")


@admin.register(TaskEvent)
class TaskEventAdmin(admin.ModelAdmin):
    list_display = ("task", "actor", "field", "old_value", "new_value", "at")
    readonly_fields = [f.name for f in TaskEvent._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False  # the audit log should be tamper-resistant


admin.site.register(Comment)
# PrivateNote is intentionally NOT registered, so admins can't browse private notes casually.


@admin.register(EmailLog)
class EmailLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "kind", "recipient", "subject", "ok")
    list_filter = ("kind", "ok")
    search_fields = ("recipient", "subject", "error")
    readonly_fields = [f.name for f in EmailLog._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(WeeklyReport)
class WeeklyReportAdmin(admin.ModelAdmin):
    list_display = ("subject", "week_start", "status", "recipient_count", "failed_count", "sent_at")
    list_filter = ("status",)
    readonly_fields = ("data", "status", "sent_at", "sent_by", "recipient_count", "failed_count", "created_by")
    # Reports are reviewed and sent from the portal's Reports page, not from here.
