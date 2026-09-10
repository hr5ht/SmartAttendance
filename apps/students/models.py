from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.academics.models import ClassSection


class StudentQuerySet(models.QuerySet):
    def active(self):
        return self.filter(is_active=True)

    def enrolled(self):
        """Students whose face gallery is complete — the matcher's candidates."""
        return self.filter(is_active=True, face_enrolled=True)

    def for_section(self, section):
        return self.filter(section=section)


class Student(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="student_profile"
    )
    roll_no = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=150)
    section = models.ForeignKey(ClassSection, on_delete=models.PROTECT, related_name="students")
    email = models.EmailField(blank=True)
    is_active = models.BooleanField(default=True)
    face_enrolled = models.BooleanField(default=False)
    enrollment_completed_at = models.DateTimeField(null=True, blank=True)
    flagged_for_review = models.BooleanField(
        default=False,
        help_text="Set when an enrollment capture matched another student's face.",
    )
    flag_reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = StudentQuerySet.as_manager()

    class Meta:
        ordering = ["section__name", "roll_no"]

    def __str__(self) -> str:
        return f"{self.roll_no} — {self.name}"

    @property
    def enrollment_count(self) -> int:
        return self.face_enrollments.count()

    @property
    def enrollment_target(self) -> int:
        return settings.ENROLLMENT_STEPS

    @property
    def enrollment_progress_percent(self) -> int:
        target = self.enrollment_target or 1
        return min(100, round(100 * self.enrollment_count / target))

    def refresh_enrollment_state(self) -> bool:
        """Recompute ``face_enrolled`` from the stored samples. Returns the flag."""
        complete = self.face_enrollments.count() >= settings.ENROLLMENT_STEPS
        if complete != self.face_enrolled:
            self.face_enrolled = complete
            self.enrollment_completed_at = timezone.now() if complete else None
            self.save(update_fields=["face_enrolled", "enrollment_completed_at"])
        return self.face_enrolled


class ResetRequestStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    APPROVED = "APPROVED", "Approved"
    REJECTED = "REJECTED", "Rejected"


class EnrollmentResetRequest(models.Model):
    """A student asking for their face gallery to be cleared; admin must approve."""

    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name="reset_requests")
    reason = models.TextField(blank=True)
    status = models.CharField(
        max_length=16, choices=ResetRequestStatus.choices, default=ResetRequestStatus.PENDING
    )
    created_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="decided_reset_requests",
    )
    decision_note = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Reset request · {self.student.roll_no} · {self.status}"

    @property
    def is_pending(self) -> bool:
        return self.status == ResetRequestStatus.PENDING
