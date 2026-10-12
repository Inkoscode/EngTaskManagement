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


class TaskForm(forms.ModelForm):
    class Meta:
        model = Task
        fields = ["title", "description", "priority", "status", "owning_section", "assignee",
                  "planned_start", "planned_finish", "due_date", "system_ref"]
        labels = {"owning_section": "Team"}
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
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
            User.objects.filter(is_active=True, memberships__section__in=sections).distinct()
        )
        self.fields["assignee"].label_from_instance = _person

        # Convenience: a user with a single team gets it pre-selected on new tasks.
        if not self.instance.pk and sections.count() == 1:
            self.fields["owning_section"].initial = sections.first()

    def clean(self):
        data = super().clean()
        section, assignee = data.get("owning_section"), data.get("assignee")
        if section and assignee and not Membership.objects.filter(user=assignee, section=section).exists():
            self.add_error("assignee", "Assignee must be a member of the owning team.")
        return data


class StatusForm(forms.ModelForm):
    class Meta:
        model = Task
        fields = ["status"]


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
