from django import forms
from django.contrib.auth import get_user_model
from sections.models import Section
from .models import Task, Comment, PrivateNote, Membership, WeeklyReport

User = get_user_model()


class DateInput(forms.DateInput):
    input_type = "date"

    def __init__(self, **kwargs):
        kwargs.setdefault("format", "%Y-%m-%d")
        super().__init__(**kwargs)


def _person(user):
    return user.get_full_name() or user.username


def _clean_reason(status, reason, add_error, instance):
    """A task can only be Blocked with a reason. Any other status clears the reason."""
    reason = (reason or "").strip()
    if status != "blocked":
        return ""
    if not reason and instance is not None and instance.pk and instance.status == "blocked":
        reason = instance.blocked_reason  # re-saving a blocked task keeps its existing reason
    if not reason:
        add_error("blocked_reason", "Say why this task is blocked.")
    return reason


class AssigneeSelect(forms.Select):
    """Tags each person with the teams they belong to (data-sections="1,3") so the page can
    show only the members of the team chosen above. The server still validates the choice."""
    section_map = {}

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex=subindex, attrs=attrs)
        ids = self.section_map.get(getattr(value, "value", value))
        if ids:
            option["attrs"]["data-sections"] = ",".join(str(i) for i in ids)
        return option


class TaskForm(forms.ModelForm):
    class Meta:
        model = Task
        fields = ["title", "description", "priority", "status", "blocked_reason", "owning_section",
                  "assignee", "planned_start", "planned_finish", "due_date", "system_ref"]
        labels = {"owning_section": "Team"}
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
            "blocked_reason": forms.Textarea(attrs={"rows": 2}),
            "assignee": AssigneeSelect(),
            "planned_start": DateInput(),
            "planned_finish": DateInput(),
            "due_date": DateInput(),
        }

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        if user.is_overall_boss:
            sections = Section.objects.all()
        else:
            # Any member (engineer or team boss) may work in their own section(s).
            sections = Section.objects.filter(memberships__user=user).distinct()
        self.fields["owning_section"].queryset = sections

        # Only offer people who belong to one of the sections this user can use.
        self.fields["assignee"].queryset = (
            User.objects.filter(is_active=True, memberships__section__in=sections)
            .distinct().order_by("first_name", "last_name", "username")
        )
        self.fields["assignee"].label_from_instance = _person
        self.fields["assignee"].help_text = "Only members of the selected team are listed."

        # Which team(s) each person belongs to, so the page can filter the list by team.
        section_map = {}
        rows = Membership.objects.filter(section__in=sections, user__is_active=True)
        for user_id, section_id in rows.values_list("user_id", "section_id"):
            section_map.setdefault(user_id, []).append(section_id)
        self.fields["assignee"].widget.section_map = section_map

        # Convenience: a user with a single team gets it pre-selected on new tasks.
        if not self.instance.pk and sections.count() == 1:
            self.fields["owning_section"].initial = sections.first()

    def clean(self):
        data = super().clean()
        section, assignee = data.get("owning_section"), data.get("assignee")
        if section and assignee and not Membership.objects.filter(user=assignee, section=section).exists():
            self.add_error("assignee", "Assignee must be a member of the owning team.")
        data["blocked_reason"] = _clean_reason(data.get("status"), data.get("blocked_reason"),
                                               self.add_error, self.instance)
        return data


class StatusForm(forms.ModelForm):
    class Meta:
        model = Task
        fields = ["status", "blocked_reason"]
        labels = {"blocked_reason": "Why is it blocked?"}
        widgets = {"blocked_reason": forms.Textarea(attrs={"rows": 2})}

    def clean(self):
        data = super().clean()
        data["blocked_reason"] = _clean_reason(data.get("status"), data.get("blocked_reason"),
                                               self.add_error, self.instance)
        return data


class CommentForm(forms.ModelForm):
    class Meta:
        model = Comment
        fields = ["body"]
        widgets = {"body": forms.Textarea(attrs={"rows": 3})}


class PrivateNoteForm(forms.ModelForm):
    class Meta:
        model = PrivateNote
        fields = ["body"]
        widgets = {"body": forms.Textarea(attrs={"rows": 3})}



class ReportForm(forms.ModelForm):
    class Meta:
        model = WeeklyReport
        fields = ["subject", "intro"]
        labels = {"subject": "Email subject", "intro": "Message at the top"}
        widgets = {"intro": forms.Textarea(attrs={"rows": 5})}
