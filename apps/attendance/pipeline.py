"""The classroom pipeline: photos in, attendance records out.

Runs synchronously. The candidate gallery is built from the face-enrolled
students of the session's own section and nothing else — that restriction is the
main thing keeping accuracy up, so it lives in one place and is tested directly.
"""
from __future__ import annotations

import io
import logging
import time
from dataclasses import dataclass, field

import cv2
import numpy as np
from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone
from PIL import Image, ImageOps, UnidentifiedImageError

from apps.attendance.models import (
    AttendanceRecord,
    AttendanceStatus,
    DetectedFace,
    ImageStatus,
    RecordSource,
)
from apps.face import matching
from apps.face.imaging import crop as crop_box
from apps.face.models import FaceEnrollment
from apps.face.services import FaceEngineError, ModelFilesMissing, get_engine

logger = logging.getLogger("apps.attendance")


class PipelineError(Exception):
    """Something stopped the whole run. The message is shown to the teacher."""

    code = "ERROR"

    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code


@dataclass
class PipelineReport:
    """What one run did, for the UI and for the log line."""

    images: int = 0
    faces_detected: int = 0
    faces_matched: int = 0
    faces_unknown: int = 0
    skipped_too_small: int = 0
    skipped_low_score: int = 0
    present: int = 0
    absent: int = 0
    gallery_students: int = 0
    gallery_samples: int = 0
    failed_images: list[str] = field(default_factory=list)
    elapsed_seconds: float = 0.0


def _decode(session_image) -> np.ndarray:
    """Read a stored session photo back as a BGR array."""
    try:
        with session_image.image.open("rb") as handle:
            data = handle.read()
        picture = Image.open(io.BytesIO(data))
        picture.load()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise PipelineError(
            f"Photo {session_image.order + 1} could not be read — it may have been "
            f"corrupted during upload. Remove it and upload the photo again.",
            code="UNREADABLE",
        ) from exc

    picture = ImageOps.exif_transpose(picture)
    if picture.mode != "RGB":
        picture = picture.convert("RGB")
    return cv2.cvtColor(np.asarray(picture), cv2.COLOR_RGB2BGR)


def _save_crop(face: DetectedFace, image_bgr: np.ndarray, bbox) -> None:
    """Store a thumbnail of the face so the teacher can review unknowns."""
    patch = crop_box(image_bgr, bbox, pad=0.15)
    if patch.size == 0:
        return
    rgb = cv2.cvtColor(patch, cv2.COLOR_BGR2RGB)
    buffer = io.BytesIO()
    Image.fromarray(rgb).save(buffer, format="JPEG", quality=85)
    face.crop.save(f"face-{face.pk}.jpg", ContentFile(buffer.getvalue()), save=True)


def build_session_gallery(session):
    """Enrollment rows for the face-enrolled students of *this* section only."""
    students = session.enrolled_roster()
    enrollments = list(
        FaceEnrollment.objects.filter(student__in=students).only("student_id", "embedding")
    )
    return students, enrollments


def process_session(session) -> PipelineReport:
    """Detect, match and write attendance for every image on the session.

    Safe to run more than once: previous detections and automatic records are
    replaced, while anything the teacher set by hand is left alone.
    """
    started = time.perf_counter()
    report = PipelineReport()

    images = list(session.images.all())
    if not images:
        raise PipelineError(
            "This session has no photos yet. Add 2–3 classroom photos first.",
            code="NO_IMAGES",
        )

    students, enrollments = build_session_gallery(session)
    if not enrollments:
        raise PipelineError(
            f"Nobody in {session.section.name} has finished face setup yet, so there "
            f"is nothing to match against. Students complete it themselves on their "
            f"phones — until then attendance for this class has to be taken by hand.",
            code="EMPTY_GALLERY",
        )

    gallery_ids, gallery = matching.build_gallery(enrollments)
    report.gallery_students = len(students)
    report.gallery_samples = len(gallery_ids)

    engine = get_engine()
    try:
        engine.preload()
    except ModelFilesMissing as exc:
        logger.error("pipeline blocked, models missing: %s", exc)
        raise PipelineError(
            "The face recognition models aren't installed on the server, so photos "
            "can't be processed. An administrator needs to run "
            "'manage.py download_face_models'.",
            code="MODEL_MISSING",
        ) from exc

    # Faces that survived matching, per student, across every image.
    best_by_student: dict[int, tuple[float, DetectedFace]] = {}

    for session_image in images:
        image_started = time.perf_counter()
        session_image.status = ImageStatus.PROCESSING
        session_image.error_message = ""
        session_image.save(update_fields=["status", "error_message"])

        # Old detections go first so a re-run cannot double-count.
        session_image.faces.all().delete()

        try:
            picture = _decode(session_image)
            detections = engine.detect(picture)
        except PipelineError as exc:
            # One bad photo must not sink the session.
            session_image.status = ImageStatus.FAILED
            session_image.error_message = exc.message
            session_image.elapsed_seconds = time.perf_counter() - image_started
            session_image.save(
                update_fields=["status", "error_message", "elapsed_seconds"]
            )
            report.failed_images.append(exc.message)
            continue
        except FaceEngineError as exc:
            session_image.status = ImageStatus.FAILED
            session_image.error_message = f"The face detector failed on this photo: {exc}"
            session_image.elapsed_seconds = time.perf_counter() - image_started
            session_image.save(
                update_fields=["status", "error_message", "elapsed_seconds"]
            )
            report.failed_images.append(session_image.error_message)
            continue

        # Faces too small or too uncertain are noise; count them so the teacher
        # can tell "nobody was detected" from "everyone was too far away".
        usable = []
        skipped_small_here = 0
        for detection in detections:
            if detection.det_score < settings.MIN_DETECTION_SCORE:
                report.skipped_low_score += 1
            elif detection.width < settings.MIN_FACE_PIXELS:
                report.skipped_too_small += 1
                skipped_small_here += 1
            else:
                usable.append(detection)

        results: list[matching.MatchResult] = []
        if usable:
            probes = np.stack([d.embedding for d in usable])
            results = matching.match_probes(
                probes,
                gallery_ids,
                gallery,
                settings.SIMILARITY_THRESHOLD,
                settings.MARGIN_THRESHOLD,
            )
            results = matching.resolve_collisions(results)

        matched_here = 0
        for detection, result in zip(usable, results):
            face = DetectedFace(
                session_image=session_image,
                bbox_x1=detection.bbox[0],
                bbox_y1=detection.bbox[1],
                bbox_x2=detection.bbox[2],
                bbox_y2=detection.bbox[3],
                det_score=detection.det_score,
                matched_student_id=result.student_id,
                similarity=result.similarity,
                margin=result.margin,
                runner_up_student_id=result.runner_up_id,
                reject_reason=result.reject_reason,
            )
            face.set_embedding(detection.embedding)
            face.save()
            _save_crop(face, picture, detection.bbox)

            if result.matched:
                matched_here += 1
                held = best_by_student.get(result.student_id)
                if held is None or result.similarity > held[0]:
                    best_by_student[result.student_id] = (result.similarity, face)

        session_image.faces_detected = len(usable)
        session_image.faces_matched = matched_here
        session_image.faces_skipped_small = skipped_small_here
        session_image.status = ImageStatus.DONE
        session_image.elapsed_seconds = time.perf_counter() - image_started
        session_image.save(
            update_fields=[
                "faces_detected", "faces_matched", "faces_skipped_small",
                "status", "elapsed_seconds",
            ]
        )

        report.images += 1
        report.faces_detected += len(usable)
        report.faces_matched += matched_here
        report.faces_unknown += len(usable) - matched_here

    _write_records(session, students, best_by_student, report)

    session.last_processed_at = timezone.now()
    session.save(update_fields=["last_processed_at"])

    report.elapsed_seconds = time.perf_counter() - started
    logger.info(
        "session %s processed: images=%s faces=%s matched=%s unknown=%s "
        "skipped_small=%s skipped_low_score=%s gallery=%s students/%s samples "
        "present=%s absent=%s elapsed=%.2fs",
        session.pk, report.images, report.faces_detected, report.faces_matched,
        report.faces_unknown, report.skipped_too_small, report.skipped_low_score,
        report.gallery_students, report.gallery_samples, report.present,
        report.absent, report.elapsed_seconds,
    )
    return report


@transaction.atomic
def _write_records(session, students, best_by_student, report: PipelineReport) -> None:
    """Union across images: present if matched anywhere, at the best confidence.

    Every active student in the section gets a record, not just the enrolled
    ones — a student who never finished face setup is absent, and needs a row
    saying so rather than being missing from the register entirely.
    """
    existing = {record.student_id: record for record in session.records.all()}

    for student in session.roster():
        record = existing.get(student.pk)
        hit = best_by_student.get(student.pk)

        if record is not None and record.source == RecordSource.MANUAL:
            # The teacher has already decided about this student; leave it be.
            if record.status == AttendanceStatus.PRESENT:
                report.present += 1
            else:
                report.absent += 1
            continue

        if hit is not None:
            similarity, face = hit
            status, confidence, matched_face = AttendanceStatus.PRESENT, similarity, face
            report.present += 1
        else:
            status, confidence, matched_face = AttendanceStatus.ABSENT, None, None
            report.absent += 1

        if record is None:
            AttendanceRecord.objects.create(
                session=session,
                student=student,
                status=status,
                source=RecordSource.AUTO,
                confidence=confidence,
                matched_face=matched_face,
            )
        else:
            record.status = status
            record.source = RecordSource.AUTO
            record.confidence = confidence
            record.matched_face = matched_face
            record.save(update_fields=["status", "source", "confidence", "matched_face"])
