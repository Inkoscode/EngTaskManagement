"""
Who can do what.

  * Everyone logged in can SEE every task (the general dashboard).
  * "Involved" people (members of the owning section, the assignee, the overall boss)
    can also see comments / history and can comment.
  * Any section member can CREATE a task in their own section and assign it to a teammate.
  * EDIT / REASSIGN: overall boss, the section's team boss, or the task's creator (if still a member).
  * STATUS: anyone who can edit, plus the assignee.
"""
from django.db.models import Q

from .models import Membership, Task


def visible_tasks(user):
    """Tasks that are 'mine': my sections' tasks plus anything assigned to me."""
    if user.is_overall_boss:
        return Task.objects.all()
    section_ids = user.memberships.values_list("section_id", flat=True)
    return Task.objects.filter(Q(owning_section_id__in=section_ids) | Q(assignee=user)).distinct()


def is_member(user, section_id):
    return user.memberships.filter(section_id=section_id).exists()


def is_team_boss(user, section_id):
    return Membership.objects.filter(user=user, section_id=section_id, role=Membership.TEAM_BOSS).exists()


def is_involved(user, task):
    return user.is_overall_boss or task.assignee_id == user.id or is_member(user, task.owning_section_id)


def can_create_task(user):
    return user.is_overall_boss or user.memberships.exists()


def can_edit_task(user, task):
    if user.is_overall_boss or is_team_boss(user, task.owning_section_id):
        return True
    return task.creator_id == user.id and is_member(user, task.owning_section_id)


def can_update_status(user, task):
    return can_edit_task(user, task) or task.assignee_id == user.id


def can_comment(user, task):
    return is_involved(user, task)


def attach_permissions(user, tasks):
    """Evaluate a queryset once and set task.can_edit / task.can_status with ONE membership query."""
    tasks = list(tasks)
    roles = dict(user.memberships.values_list("section_id", "role"))
    for t in tasks:
        role = roles.get(t.owning_section_id)
        editable = (
            user.is_overall_boss
            or role == Membership.TEAM_BOSS
            or (role is not None and t.creator_id == user.id)
        )
        t.can_edit = editable
        t.can_status = editable or t.assignee_id == user.id
    return tasks


def can_manage_reports(user):
    """Who may draft, edit and send weekly reports: the overall boss and admin staff."""
    return user.is_active and (user.is_overall_boss or user.is_staff)
