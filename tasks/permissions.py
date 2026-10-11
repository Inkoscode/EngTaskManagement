from django.db.models import Q
from .models import Membership, Task


def visible_tasks(user):
    if user.is_overall_boss:
        return Task.objects.all()
    section_ids = user.memberships.values_list("section_id", flat=True)
    return Task.objects.filter(Q(owning_section_id__in=section_ids) | Q(assignee=user)).distinct()


def is_team_boss(user, section_id):
    return Membership.objects.filter(user=user, section_id=section_id, role=Membership.TEAM_BOSS).exists()


def can_edit_task(user, task):
    return user.is_overall_boss or is_team_boss(user, task.owning_section_id)


def can_update_status(user, task):
    return can_edit_task(user, task) or task.assignee_id == user.id


def can_create_task(user):
    return user.is_overall_boss or user.memberships.filter(role=Membership.TEAM_BOSS).exists()