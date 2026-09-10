"""Enroll seeded students from the sample group photo — development only.

This exists so the classroom pipeline can be driven end to end without
recruiting real people. The embeddings are produced by the real model from real
faces; what is synthetic is only the *pairing* of a face to a seeded student,
and the five "poses" are five different crops of the same person rather than
five genuine head positions.

Never run this against real data — it writes enrollment samples that claim to be
students who never sat in front of a camera.
"""
from __future__ import annotations

import io
from pathlib import Path

import cv2
import numpy as np
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from PIL import Image

from apps.face.enrollment import steps
from apps.face.imaging import crop as crop_box
from apps.face.imaging import laplacian_variance, mean_brightness
from apps.face.models import FaceEnrollment
from apps.face.services import get_engine
from apps.students.models import Student

# Five crops of the same face: enough variation that the embeddings differ,
# while staying gentle enough not to trip the blur gate.
CROP_VARIANTS = [
    {"pad": 0.85, "width": 380, "shift": 0},
    {"pad": 0.95, "width": 400, "shift": -6},
    {"pad": 0.95, "width": 400, "shift": 6},
    {"pad": 1.10, "width": 430, "shift": 0},
    {"pad": 0.80, "width": 370, "shift": 3},
]


def sample_photo() -> Path:
    import insightface

    return Path(insightface.__file__).parent / "data" / "images" / "t1.jpg"


def variant_crop(image, bbox, pad: float, width: int, shift: int) -> np.ndarray:
    x1, y1, x2, y2 = bbox
    w, h = x2 - x1, y2 - y1
    x1 = max(0, int(x1 - w * pad + shift))
    y1 = max(0, int(y1 - h * pad))
    x2 = min(image.shape[1], int(x2 + w * pad + shift))
    y2 = min(image.shape[0], int(y2 + h * pad))
    patch = image[y1:y2, x1:x2]
    if patch.size == 0:
        return patch
    scale = width / patch.shape[1]
    return cv2.resize(patch, (width, int(patch.shape[0] * scale)), interpolation=cv2.INTER_CUBIC)


def to_jpeg(array_bgr: np.ndarray) -> ContentFile:
    rgb = cv2.cvtColor(array_bgr, cv2.COLOR_BGR2RGB)
    buffer = io.BytesIO()
    Image.fromarray(rgb).save(buffer, format="JPEG", quality=92)
    return ContentFile(buffer.getvalue(), name="seeded.jpg")


class Command(BaseCommand):
    help = "Development fixture: enroll seeded students from the sample group photo."

    def add_arguments(self, parser):
        parser.add_argument("--section", help="Section name, e.g. CSE-3B.")
        parser.add_argument(
            "--force", action="store_true",
            help="Overwrite existing enrollments for the students it touches.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        photo = sample_photo()
        if not photo.exists():
            raise CommandError(f"Sample photo not found at {photo}.")

        engine = get_engine()
        image = cv2.imread(str(photo))
        faces = engine.detect(image)
        if not faces:
            raise CommandError("No faces detected in the sample photo.")

        students = Student.objects.active().select_related("section")
        if options["section"]:
            students = students.filter(section__name=options["section"])
        students = list(students.order_by("section__name", "roll_no")[: len(faces)])

        if not students:
            raise CommandError(
                "No students to enroll. Run 'manage.py seed_demo' first, or check "
                "the --section name."
            )

        already = [s for s in students if s.face_enrollments.exists()]
        if already and not options["force"]:
            raise CommandError(
                f"{len(already)} of these students already have enrollment samples "
                f"({', '.join(s.roll_no for s in already)}). Re-run with --force to "
                f"replace them."
            )

        self.stdout.write(
            self.style.WARNING(
                "Development fixture: these samples pair real faces from the sample "
                "photo with seeded students. Do not run this on real data."
            )
        )

        enrolled = 0
        for student, face in zip(students, faces):
            student.face_enrollments.all().delete()

            for step, variant in zip(steps(), CROP_VARIANTS):
                patch = variant_crop(image, face.bbox, **variant)
                detections = engine.detect(patch)
                if not detections:
                    continue
                detection = detections[0]
                face_patch = crop_box(patch, detection.bbox, pad=0.1)

                enrollment = FaceEnrollment(
                    student=student,
                    det_score=detection.det_score,
                    quality_score=laplacian_variance(face_patch),
                    brightness=mean_brightness(face_patch),
                    face_width=int(detection.width),
                    pose=step["pose"],
                    step_index=step["index"],
                )
                enrollment.set_embedding(detection.embedding)
                enrollment.image.save("seeded.jpg", to_jpeg(patch), save=False)
                enrollment.save()

            student.refresh_enrollment_state()
            state = "enrolled" if student.face_enrolled else "INCOMPLETE"
            self.stdout.write(
                f"  {student.roll_no} {student.name} ({student.section.name}): "
                f"{student.face_enrollments.count()} samples — {state}"
            )
            enrolled += 1 if student.face_enrolled else 0

        self.stdout.write(
            self.style.SUCCESS(
                f"Enrolled {enrolled} of {len(students)} students from {len(faces)} "
                f"faces in the sample photo."
            )
        )
        self.stdout.write(
            f"Upload {photo} as a classroom photo to see the pipeline match them."
        )
