from django.contrib import admin
from .models import Comment, Membership, PrivateNote, Task, TaskEvent


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


admin.site.register(Comment)
# PrivateNote is intentionally NOT registered, so admins can't browse private notes casually.