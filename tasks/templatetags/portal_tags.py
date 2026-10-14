from django import template

from tasks.models import Notification
from tasks.permissions import can_view_workload as _can_view_workload

register = template.Library()


@register.simple_tag
def unread_notifications(user):
    """How many unread bell notifications this person has (0 for visitors)."""
    if not user.is_authenticated:
        return 0
    return Notification.objects.filter(user=user, read_at__isnull=True).count()


@register.simple_tag(name="can_view_workload")
def can_view_workload_tag(user):
    return user.is_authenticated and _can_view_workload(user)
