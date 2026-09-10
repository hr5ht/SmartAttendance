from __future__ import annotations

from django.conf import settings
from django.db import models
from django.urls import reverse

from apps.academics.models import ClassSection, Subject, Teacher
from apps.face.models import EmbeddingMixin
from apps.face.storage import face_crop_path, session_image_path
from apps.students.models import Student


class SessionStatus(models.TextChoices):
    DRAFT = "DRAFT", "Draft"
    CONFIRMED = "CONFIRMED", "Confirmed"


class ImageStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    PROCESSING = "PROCESSING", "Processing"
    DONE = "DONE", "Processed"
    FAILED = "FAILED", "Failed"


class AttendanceStatus(models.TextChoices):
    PRESENT = "PRESENT", "Present"
    ABSENT = "ABSENT", "Absent"


class RecordSource(models.TextChoices):
    AUTO = "AUTO", "Automatic"
    MANUAL = "MANUAL", "Manual"


class AttendanceSessionQuerySet(models.QuerySet):
    def visible_to(self, user):
        """Permission enforced in the queryset, not just the template."""
        if not user.is_authenticated:
            return self.none()
        if user.is_admin_role:
            return self
        if user.is_teacher_role:
            teacher = getattr(user, "teacher_profile", None)
            if teacher is None:
                return self.none()
            if not settings.RESTRICT_TEACHER_TO_ASSIGNMENTS:
                # Any teacher may take any class, so ownership is the only test:
                # they still cannot reach a session another teacher recorded.
                return self.filter(teacher=teacher)
            assignments = teacher.assignments.values_list("section_id", "subject_id")
            pairs = list(assignments)
            if not pairs:
                return self.none()
            condition = models.Q()
            for section_id, subject_id in pairs:
                condition |= models.Q(section_id=section_id, subject_id=subject_id)
            return self.filter(condition, teacher=teacher)
        return self.none()


class AttendanceSession(models.Model):
    section = models.ForeignKey(ClassSection, on_delete=models.PROTECT, related_name="sessions")
    subject = models.ForeignKey(Subject, on_delete=models.PROTECT, related_name="sessions")
    teacher = models.ForeignKey(Teacher, on_delete=models.PROTECT, related_name="sessions")
    period = models.PositiveSmallIntegerField()
    date = models.DateField()
    status = models.CharField(
        max_length=16, choices=SessionStatus.choices, default=SessionStatus.DRAFT
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    email_sent = models.BooleanField(default=False)
    email_error = models.TextField(blank=True)
    last_processed_at = models.DateTimeField(null=True, blank=True)

    objects = AttendanceSessionQuerySet.as_manager()

    class Meta:
        ordering = ["-date", "-period"]
        constraints = [
            models.UniqueConstraint(
                fields=["section", "subject", "period", "date"], name="uniq_session_slot"
            )
        ]

    def __str__(self) -> str:
        return f"{self.section} / {self.subject.code} / P{self.period} / {self.date:%d-%b-%Y}"

    def get_absolute_url(self) -> str:
        return reverse("attendance:session_detail", args=[self.pk])

    @property
    def is_draft(self) -> bool:
        return self.status == SessionStatus.DRAFT

    @property
    def is_confirmed(self) -> bool:
        return self.status == SessionStatus.CONFIRMED

    @property
    def subject_line(self) -> str:
        return (
            f"Attendance — {self.section.name} / {self.subject.short_label} / "
            f"Period {self.period} / {self.date:%d-%b-%Y}"
        )

    # -- roster helpers ---------------------------------------------------
    def roster(self):
        return Student.objects.active().for_section(self.section)

    def enrolled_roster(self):
        return Student.objects.enrolled().for_section(self.section)

    def unenrolled_roster(self):
        return self.roster().filter(face_enrolled=False)

    def counts(self) -> dict[str, int | float]:
        records = self.records.all()
        total = len(records)
        present = sum(1 for r in records if r.status == AttendanceStatus.PRESENT)
        manual = sum(1 for r in records if r.source == RecordSource.MANUAL)
        return {
            "total": total,
            "present": present,
            "absent": total - present,
            "manual": manual,
            "auto": present - manual if present >= manual else 0,
            "percentage": round(100 * present / total, 1) if total else 0.0,
        }


class SessionImage(models.Model):
    session = models.ForeignKey(
        AttendanceSession, on_delete=models.CASCADE, related_name="images"
    )
    image = models.ImageField(upload_to=session_image_path)
    width = models.PositiveIntegerField(default=0)
    height = models.PositiveIntegerField(default=0)
    status = models.CharField(
        max_length=16, choices=ImageStatus.choices, default=ImageStatus.PENDING
    )
    error_message = models.TextField(blank=True)
    faces_detected = models.PositiveIntegerField(default=0)
    faces_matched = models.PositiveIntegerField(default=0)
    faces_skipped_small = models.PositiveIntegerField(default=0)
    elapsed_seconds = models.FloatField(default=0.0)
    order = models.PositiveSmallIntegerField(default=0)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self) -> str:
        return f"Image {self.order + 1} of session {self.session_id}"

    @property
    def is_failed(self) -> bool:
        return self.status == ImageStatus.FAILED


class DetectedFace(EmbeddingMixin):
    """One detected face. Unmatched faces are kept so the teacher can review them."""

    session_image = models.ForeignKey(
        SessionImage, on_delete=models.CASCADE, related_name="faces"
    )
    bbox_x1 = models.FloatField()
    bbox_y1 = models.FloatField()
    bbox_x2 = models.FloatField()
    bbox_y2 = models.FloatField()
    det_score = models.FloatField()
    crop = models.ImageField(upload_to=face_crop_path, blank=True, null=True)
    matched_student = models.ForeignKey(
        Student,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="detected_faces",
    )
    similarity = models.FloatField(null=True, blank=True)
    margin = models.FloatField(null=True, blank=True)
    runner_up_student = models.ForeignKey(
        Student,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="runner_up_faces",
    )
    reject_reason = models.CharField(max_length=64, blank=True)
    assigned_manually = models.BooleanField(default=False)

    class Meta:
        ordering = ["session_image", "-det_score"]

    def __str__(self) -> str:
        who = self.matched_student.name if self.matched_student else "UNKNOWN"
        return f"Face #{self.pk} → {who}"

    @property
    def is_unknown(self) -> bool:
        return self.matched_student_id is None

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        return (self.bbox_x1, self.bbox_y1, self.bbox_x2, self.bbox_y2)

    @property
    def width(self) -> float:
        return self.bbox_x2 - self.bbox_x1

    @property
    def confidence_percent(self) -> int:
        return round(100 * self.similarity) if self.similarity is not None else 0


class AttendanceRecord(models.Model):
    session = models.ForeignKey(
        AttendanceSession, on_delete=models.CASCADE, related_name="records"
    )
    student = models.ForeignKey(Student, on_delete=models.PROTECT, related_name="records")
    status = models.CharField(
        max_length=16, choices=AttendanceStatus.choices, default=AttendanceStatus.ABSENT
    )
    source = models.CharField(
        max_length=16, choices=RecordSource.choices, default=RecordSource.AUTO
    )
    confidence = models.FloatField(null=True, blank=True)
    matched_face = models.ForeignKey(
        DetectedFace,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="attendance_records",
    )
    overridden_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="overridden_records",
    )
    override_reason = models.CharField(max_length=200, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["student__roll_no"]
        constraints = [
            models.UniqueConstraint(fields=["session", "student"], name="uniq_session_student")
        ]

    def __str__(self) -> str:
        return f"{self.student.roll_no} · {self.status}"

    @property
    def is_present(self) -> bool:
        return self.status == AttendanceStatus.PRESENT

    @property
    def confidence_percent(self) -> int:
        return round(100 * self.confidence) if self.confidence is not None else 0
