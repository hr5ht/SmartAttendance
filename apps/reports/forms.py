from __future__ import annotations

from django import forms

from apps.academics.models import ClassSection, Subject
from apps.accounts.forms import StyledFormMixin


class ReportFilterForm(StyledFormMixin, forms.Form):
    """Narrow the report. Every field is optional — the unfiltered view is the
    whole of what the signed-in user is allowed to see."""

    section = forms.ModelChoiceField(
        queryset=ClassSection.objects.none(), required=False,
        label="Class", empty_label="All classes",
    )
    subject = forms.ModelChoiceField(
        queryset=Subject.objects.none(), required=False,
        label="Subject", empty_label="All subjects",
    )
    date_from = forms.DateField(
        required=False, label="From", widget=forms.DateInput(attrs={"type": "date"})
    )
    date_to = forms.DateField(
        required=False, label="To", widget=forms.DateInput(attrs={"type": "date"})
    )

    def __init__(self, *args, sessions=None, **kwargs):
        super().__init__(*args, **kwargs)
        # Only offer values that actually occur in the sessions this user can
        # see, so no filter can produce an empty report for no visible reason.
        if sessions is not None:
            self.fields["section"].queryset = (
                ClassSection.objects.filter(id__in=sessions.values("section_id"))
                .select_related("department")
                .order_by("name")
            )
            self.fields["subject"].queryset = Subject.objects.filter(
                id__in=sessions.values("subject_id")
            ).order_by("code")
        self.fields["section"].label_from_instance = lambda section: section.label

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("date_from"), cleaned.get("date_to")
        if start and end and start > end:
            self.add_error("date_to", "The end date is before the start date.")
        return cleaned

    def filter(self, sessions):
        """Apply the cleaned filters to a session queryset."""
        if not self.is_valid():
            return sessions
        data = self.cleaned_data
        if data.get("section"):
            sessions = sessions.filter(section=data["section"])
        if data.get("subject"):
            sessions = sessions.filter(subject=data["subject"])
        if data.get("date_from"):
            sessions = sessions.filter(date__gte=data["date_from"])
        if data.get("date_to"):
            sessions = sessions.filter(date__lte=data["date_to"])
        return sessions
