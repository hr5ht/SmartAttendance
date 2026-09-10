from __future__ import annotations

import re

from django import forms

from apps.academics.models import ClassSection
from apps.accounts.forms import StyledFormMixin
from apps.accounts.models import User
from apps.students.models import Student


def username_for(roll_no: str) -> str:
    """Derive a login name from the roll number, uniquified if it collides."""
    base = re.sub(r"[^a-z0-9]", "", roll_no.lower()) or "student"
    candidate = base
    suffix = 1
    while User.objects.filter(username=candidate).exists():
        suffix += 1
        candidate = f"{base}{suffix}"
    return candidate


class StudentForm(StyledFormMixin, forms.ModelForm):
    """Admin-side enrollment. Credentials are generated, never typed here."""

    class Meta:
        model = Student
        fields = ["roll_no", "name", "section", "email"]
        labels = {"roll_no": "Roll number", "name": "Full name", "email": "Email address"}
        help_texts = {
            "roll_no": "Used as the login name, so it must be unique.",
            "email": "Optional. Used for notices — not required to sign in.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["section"].queryset = ClassSection.objects.filter(
            is_active=True
        ).select_related("department")
        self.fields["section"].label_from_instance = lambda section: section.label
        self.fields["email"].required = False

    def clean_roll_no(self):
        roll_no = self.cleaned_data["roll_no"].strip().upper()
        existing = Student.objects.filter(roll_no__iexact=roll_no)
        if self.instance.pk:
            existing = existing.exclude(pk=self.instance.pk)
        if existing.exists():
            raise forms.ValidationError(
                f"Roll number {roll_no} is already registered to another student."
            )
        return roll_no

    def clean_name(self):
        return " ".join(self.cleaned_data["name"].split())


class StudentSearchForm(forms.Form):
    """Roster filters. Not styled through the mixin — it renders inline."""

    q = forms.CharField(required=False)
    section = forms.ModelChoiceField(
        queryset=ClassSection.objects.select_related("department"), required=False
    )
    enrolled = forms.ChoiceField(
        required=False,
        choices=[("", "Any face status"), ("yes", "Face enrolled"), ("no", "Not enrolled")],
    )
