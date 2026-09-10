from __future__ import annotations

from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError

from apps.academics.models import ClassSection, Department, Subject, Year
from apps.accounts.forms import StyledFormMixin
from apps.attendance.models import AttendanceSession
from apps.face.imaging import validate_upload


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    """FileField that accepts the whole selection, not just the last file."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultipleFileInput(attrs={"accept": "image/*", "multiple": True}))
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        clean_one = super().clean
        if isinstance(data, (list, tuple)):
            return [clean_one(item, initial) for item in data]
        return [] if data in self.empty_values else [clean_one(data, initial)]


class SessionDetailsForm(StyledFormMixin, forms.Form):
    """Branch → section → subject → period → date, limited to what this teacher takes."""

    department = forms.ModelChoiceField(
        queryset=Department.objects.none(), label="Branch", empty_label="Select branch"
    )
    year = forms.TypedChoiceField(label="Year", coerce=int, choices=())
    section = forms.ModelChoiceField(
        queryset=ClassSection.objects.none(), label="Section", empty_label="Select section"
    )
    subject = forms.ModelChoiceField(
        queryset=Subject.objects.none(), label="Subject", empty_label="Select subject"
    )
    period = forms.TypedChoiceField(label="Period", coerce=int, choices=())
    date = forms.DateField(
        label="Date",
        widget=forms.DateInput(attrs={"type": "date"}),
        help_text="Defaults to today.",
    )
    images = MultipleFileField(
        label="Classroom photos",
        help_text=(
            f"{settings.MIN_SESSION_IMAGES}–{settings.MAX_SESSION_IMAGES} photos of the "
            f"room, taken from different angles so everyone is visible in at least one."
        ),
    )

    def __init__(self, *args, teacher=None, **kwargs):
        self.teacher = teacher
        super().__init__(*args, **kwargs)

        # Two policies, chosen by RESTRICT_TEACHER_TO_ASSIGNMENTS: either a
        # teacher is held to their TeacherAssignment rows, or any teacher may
        # take any active class. ``assignment_pairs`` is None under the open
        # policy, which is what clean() reads as "no pairing to enforce".
        self.restricted = settings.RESTRICT_TEACHER_TO_ASSIGNMENTS

        if self.restricted:
            assignments = (
                teacher.assignments.select_related("section__department", "subject")
                if teacher
                else []
            )
            self.assignment_pairs = {(a.section_id, a.subject_id) for a in assignments}
            sections = ClassSection.objects.filter(id__in={a.section_id for a in assignments})
            subjects = Subject.objects.filter(id__in={a.subject_id for a in assignments})
            departments = Department.objects.filter(
                id__in={a.section.department_id for a in assignments}
            )
            years = sorted({a.section.year for a in assignments})
        else:
            self.assignment_pairs = None
            sections = ClassSection.objects.filter(is_active=True)
            subjects = Subject.objects.all()
            departments = Department.objects.filter(id__in=sections.values("department_id"))
            years = sorted(set(sections.values_list("year", flat=True)))

        self.fields["department"].queryset = departments
        self.fields["section"].queryset = sections.select_related("department")
        self.fields["subject"].queryset = subjects
        self.fields["year"].choices = [("", "Select year")] + [
            (year, Year(year).label) for year in years
        ]
        self.fields["period"].choices = [("", "Select period")] + [
            (p, f"Period {p}") for p in range(1, settings.PERIODS_PER_DAY + 1)
        ]
        # With 36 sections in the system, "CSE-3B" alone is hard to scan; the
        # dropdown is filtered to one branch and year anyway, so show the letter.
        # A teacher who takes more than one year would see "Section A" once per
        # year before that filtering happens, so name the year in that case.
        spans_years = len(years) > 1
        self.fields["section"].label_from_instance = (
            lambda section: (
                f"{section.get_section_letter_display()} · {section.get_year_display()}"
                if spans_years
                else section.get_section_letter_display()
            )
        )

    def clean_images(self):
        files = self.cleaned_data["images"]
        count = len(files)
        if count < settings.MIN_SESSION_IMAGES:
            raise ValidationError(
                f"Attach at least {settings.MIN_SESSION_IMAGES} photos — one angle "
                f"usually leaves someone hidden behind another student."
            )
        if count > settings.MAX_SESSION_IMAGES:
            raise ValidationError(
                f"That's {count} photos. The limit is {settings.MAX_SESSION_IMAGES}."
            )
        for uploaded in files:
            validate_upload(uploaded)
        return files

    def clean(self):
        cleaned = super().clean()
        section = cleaned.get("section")
        subject = cleaned.get("subject")
        period = cleaned.get("period")
        date = cleaned.get("date")
        department = cleaned.get("department")
        year = cleaned.get("year")

        # Branch and year narrow the section list in the browser; re-check them
        # here so a hand-built POST can't pair a section with the wrong pair.
        if section and department and section.department_id != department.id:
            self.add_error(
                "section",
                f"{section.name} isn't a {department.code} section.",
            )
        elif section and year and section.year != year:
            self.add_error(
                "section",
                f"{section.name} is a {section.get_year_display()} section, "
                f"not {Year(year).label}.",
            )

        if section and subject:
            # Belt and braces: the querysets already limit the options, but a
            # crafted POST could still pair a section with an unassigned subject.
            # Under the open policy there is no pairing to enforce.
            if self.assignment_pairs is not None and (
                section.id, subject.id
            ) not in self.assignment_pairs:
                self.add_error(
                    "subject", f"You aren't assigned to teach {subject.code} to {section.name}."
                )
            elif period and date:
                clash = AttendanceSession.objects.filter(
                    section=section, subject=subject, period=period, date=date
                ).first()
                if clash is not None:
                    self.add_error(
                        "period",
                        f"Attendance for {section.name} · {subject.code} · period {period} "
                        f"on {date:%d %b %Y} already exists ({clash.get_status_display()}).",
                    )
                    self.clashing_session = clash
        return cleaned


class AddImagesForm(StyledFormMixin, forms.Form):
    """Attach more photos to an existing draft session."""

    images = MultipleFileField(label="More photos")

    def __init__(self, *args, session=None, **kwargs):
        self.session = session
        super().__init__(*args, **kwargs)

    def clean_images(self):
        files = self.cleaned_data["images"]
        if not files:
            raise ValidationError("Choose at least one photo to add.")
        existing = self.session.images.count() if self.session else 0
        if existing + len(files) > settings.MAX_SESSION_IMAGES:
            raise ValidationError(
                f"This session already has {existing} photos and the limit is "
                f"{settings.MAX_SESSION_IMAGES}."
            )
        for uploaded in files:
            validate_upload(uploaded)
        return files
