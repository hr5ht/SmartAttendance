from __future__ import annotations

import numpy as np
from django.db import models

from apps.face import embeddings as emb
from apps.face.storage import enrollment_image_path
from apps.students.models import Student


class PoseLabel(models.TextChoices):
    FRONT = "FRONT", "Looking straight"
    LEFT = "LEFT", "Turned slightly left"
    RIGHT = "RIGHT", "Turned slightly right"
    UP = "UP", "Tilted up"
    ALT = "ALT", "Straight, different lighting"


class EmbeddingMixin(models.Model):
    """Shared float32-BLOB embedding storage."""

    embedding = models.BinaryField()

    class Meta:
        abstract = True

    def set_embedding(self, vector: np.ndarray) -> None:
        self.embedding = emb.to_bytes(vector)

    def get_embedding(self) -> np.ndarray:
        return emb.from_bytes(self.embedding)


class FaceEnrollment(EmbeddingMixin):
    """One accepted face sample for a student."""

    student = models.ForeignKey(
        Student, on_delete=models.CASCADE, related_name="face_enrollments"
    )
    image = models.ImageField(upload_to=enrollment_image_path)
    det_score = models.FloatField()
    quality_score = models.FloatField(
        help_text="Laplacian variance of the face crop — higher is sharper."
    )
    brightness = models.FloatField(default=0.0)
    face_width = models.PositiveIntegerField(default=0)
    pose = models.CharField(max_length=8, choices=PoseLabel.choices, default=PoseLabel.FRONT)
    step_index = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["student", "step_index"]
        constraints = [
            models.UniqueConstraint(
                fields=["student", "step_index"], name="uniq_student_enrollment_step"
            )
        ]

    def __str__(self) -> str:
        return f"{self.student.roll_no} · {self.get_pose_display()}"
