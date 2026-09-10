from __future__ import annotations

from django.conf import settings
from django.db import models


class Department(models.Model):
    name = models.CharField(max_length=120, unique=True)
    code = models.CharField(max_length=12, unique=True)

    class Meta:
        ordering = ["code"]

    def __str__(self) -> str:
        return self.code


class Year(models.IntegerChoices):
    FIRST = 1, "1st year"
    SECOND = 2, "2nd year"
    THIRD = 3, "3rd year"
    FOURTH = 4, "4th year"


class SectionLetter(models.TextChoices):
    A = "A", "Section A"
    B = "B", "Section B"
    C = "C", "Section C"


class ClassSection(models.Model):
    """A teaching group: one branch, one year, one section letter — e.g. CSE-3B.

    ``name`` is derived from those three and kept as a short code, because it is
    what appears in email subjects, logs and every roster heading.
    """

    department = models.ForeignKey(Department, on_delete=models.PROTECT, related_name="sections")
    year = models.PositiveSmallIntegerField(choices=Year.choices, default=Year.FIRST)
    section_letter = models.CharField(
        max_length=1, choices=SectionLetter.choices, default=SectionLetter.A
    )
    name = models.CharField(
        max_length=32,
        unique=True,
        editable=False,
        help_text="Generated from branch, year and section — e.g. CSE-3B.",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["department__code", "year", "section_letter"]
        verbose_name = "class section"
        constraints = [
            models.UniqueConstraint(
                fields=["department", "year", "section_letter"],
                name="uniq_department_year_section",
            )
        ]

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        self.name = self.build_name(self.department, self.year, self.section_letter)
        super().save(*args, **kwargs)

    @staticmethod
    def build_name(department, year, section_letter) -> str:
        code = department.code if hasattr(department, "code") else department
        return f"{code}-{year}{section_letter}"

    @property
    def label(self) -> str:
        """Spelled out, for dropdowns and headings: 'CSE · 3rd year · Section B'."""
        return (
            f"{self.department.code} · {self.get_year_display()} · "
            f"{self.get_section_letter_display()}"
        )


class Subject(models.Model):
    department = models.ForeignKey(Department, on_delete=models.PROTECT, related_name="subjects")
    code = models.CharField(max_length=16, unique=True)
    name = models.CharField(max_length=120)

    class Meta:
        ordering = ["code"]

    def __str__(self) -> str:
        return f"{self.code} — {self.name}"

    @property
    def short_label(self) -> str:
        return self.name


class Teacher(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="teacher_profile"
    )
    department = models.ForeignKey(Department, on_delete=models.PROTECT, related_name="teachers")
    employee_id = models.CharField(max_length=32, unique=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["employee_id"]

    def __str__(self) -> str:
        return self.user.display_name

    @property
    def name(self) -> str:
        return self.user.display_name

    @property
    def email(self) -> str:
        return self.user.email


class TeacherAssignment(models.Model):
    """What a teacher is allowed to take: drives dropdowns AND permissions."""

    teacher = models.ForeignKey(Teacher, on_delete=models.CASCADE, related_name="assignments")
    section = models.ForeignKey(ClassSection, on_delete=models.CASCADE, related_name="assignments")
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE, related_name="assignments")

    class Meta:
        ordering = ["teacher", "section", "subject"]
        constraints = [
            models.UniqueConstraint(
                fields=["teacher", "section", "subject"], name="uniq_teacher_section_subject"
            )
        ]

    def __str__(self) -> str:
        return f"{self.teacher} · {self.section} · {self.subject.code}"


class Weekday(models.IntegerChoices):
    MONDAY = 0, "Monday"
    TUESDAY = 1, "Tuesday"
    WEDNESDAY = 2, "Wednesday"
    THURSDAY = 3, "Thursday"
    FRIDAY = 4, "Friday"
    SATURDAY = 5, "Saturday"
    SUNDAY = 6, "Sunday"


class TimetableSlot(models.Model):
    section = models.ForeignKey(ClassSection, on_delete=models.CASCADE, related_name="slots")
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE, related_name="slots")
    teacher = models.ForeignKey(Teacher, on_delete=models.CASCADE, related_name="slots")
    weekday = models.IntegerField(choices=Weekday.choices)
    period = models.PositiveSmallIntegerField()
    start_time = models.TimeField()
    end_time = models.TimeField()

    class Meta:
        ordering = ["weekday", "period"]
        constraints = [
            models.UniqueConstraint(
                fields=["section", "weekday", "period"], name="uniq_section_weekday_period"
            )
        ]

    def __str__(self) -> str:
        return f"{self.section} {self.get_weekday_display()} P{self.period} {self.subject.code}"
