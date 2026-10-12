from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from tasks.models import Membership
from .models import User


class MembershipInline(admin.TabularInline):
    """Lets you pick the user's team(s) and role on the same screen as the user."""
    model = Membership
    extra = 1
    verbose_name = "Team membership"
    verbose_name_plural = "Team memberships (which team, and engineer or team boss)"


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    inlines = [MembershipInline]
    fieldsets = UserAdmin.fieldsets + (("Portal", {"fields": ("is_overall_boss",)}),)
    add_fieldsets = UserAdmin.add_fieldsets + (
        ("Details", {"classes": ("wide",), "fields": ("first_name", "last_name", "email", "is_overall_boss")}),
    )
    list_display = ("username", "get_full_name", "email", "teams", "is_overall_boss", "is_staff", "is_active")
    list_filter = UserAdmin.list_filter + ("is_overall_boss", "memberships__section")

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("memberships__section")

    @admin.display(description="Teams")
    def teams(self, obj):
        return ", ".join(f"{m.section.name} ({m.get_role_display()})" for m in obj.memberships.all()) or "None"
