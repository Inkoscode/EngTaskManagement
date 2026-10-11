from django import forms
from django.contrib.auth import get_user_model
from sections.models import Section
from .models import Task, Comment, PrivateNote, Membership

User = get_user_model()


class DateInput(forms.DateInput):
    input_type = "date"

    def __init__(self, **kwargs):
        kwargs.setdefault("format", "%Y-%m-%d")
        super().__init__(**kwargs)


class TaskForm(forms.ModelForm):
    class Meta:
        model = Task
        fields = ["title", "description", "priority", "status", "owning_section", "assignee",
                  "planned_start", "planned_finish", "due_date", "system_ref"]
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
            sections = Section.objects.filter(
                memberships__user=user, memberships__role=Membership.TEAM_BOSS
            ).distinct()
        self.fields["owning_section"].queryset = sections
        self.fields["assignee"].queryset = User.objects.filter(is_active=True)

    def clean(self):
        data = super().clean()
        section, assignee = data.get("owning_section"), data.get("assignee")
        if section and assignee and not Membership.objects.filter(user=assignee, section=section).exists():
            self.add_error("assignee", "Assignee must be a member of the owning section.")
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