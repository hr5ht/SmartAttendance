"""Student face enrollment: the quality gates a capture must pass.

Each capture is judged on its own (one face, sharp, well lit, big enough) and
against what is already stored (not a duplicate of the student's own samples,
not somebody else in the same section).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.face import embeddings as emb
from apps.face.imaging import crop, laplacian_variance, mean_brightness
from apps.face.models import FaceEnrollment, PoseLabel
from apps.face.services import Detection, FaceEngineError, ModelFilesMissing, get_engine
from apps.students.models import Student

logger = logging.getLogger("apps.face")


# The wizard's script. One capture per step, in this order.
CAPTURE_STEPS = [
    {
        "index": 0,
        "pose": PoseLabel.FRONT,
        "title": "Look straight at the camera",
        "hint": "Hold the phone at eye level, face the lens, neutral expression.",
    },
    {
        "index": 1,
        "pose": PoseLabel.LEFT,
        "title": "Turn slightly to your left",
        "hint": "Just a small turn — about 20°. Keep your eyes on the screen.",
    },
    {
        "index": 2,
        "pose": PoseLabel.RIGHT,
        "title": "Turn slightly to your right",
        "hint": "Same again, the other way. Don't turn all the way to your side.",
    },
    {
        "index": 3,
        "pose": PoseLabel.UP,
        "title": "Tilt your chin up a little",
        "hint": "A small tilt up, as if looking at the top of the screen.",
    },
    {
        "index": 4,
        "pose": PoseLabel.ALT,
        "title": "Straight on, somewhere different",
        "hint": "Face the camera again, but move to another spot or lighting — "
                "near a window, or a different wall behind you.",
    },
]


def steps() -> list[dict]:
    """The configured number of steps, in order."""
    return CAPTURE_STEPS[: settings.ENROLLMENT_STEPS]


def step_config(index: int) -> dict | None:
    for step in steps():
        if step["index"] == index:
            return step
    return None


@dataclass
class CaptureResult:
    """Outcome of one capture attempt, ready to be turned into JSON."""

    ok: bool
    code: str
    message: str
    enrollment: FaceEnrollment | None = None
    detail: dict = field(default_factory=dict)


def _reject(code: str, message: str, **detail) -> CaptureResult:
    return CaptureResult(ok=False, code=code, message=message, detail=detail)


def _pick_face(detections: list[Detection]) -> CaptureResult | Detection:
    if not detections:
        return _reject(
            "NO_FACE",
            "No face found. Hold the phone at arm's length, make sure your whole "
            "face is inside the frame, and try again.",
        )
    if len(detections) > 1:
        return _reject(
            "MULTIPLE_FACES",
            f"{len(detections)} faces in the picture. You need to be alone in the "
            f"frame — ask anyone behind you to step out of shot.",
            count=len(detections),
        )
    return detections[0]


def check_quality(detection: Detection, image_bgr: np.ndarray) -> CaptureResult | None:
    """Return a rejection, or None when the capture is good enough to keep."""
    if detection.det_score < settings.MIN_ENROLL_DET_SCORE:
        return _reject(
            "LOW_CONFIDENCE",
            "The camera couldn't see your face clearly. Move somewhere brighter "
            "and make sure nothing is covering your face.",
            det_score=round(detection.det_score, 3),
        )

    if detection.width < settings.MIN_ENROLL_FACE_PIXELS:
        return _reject(
            "FACE_TOO_SMALL",
            f"You're too far from the camera — your face is {int(detection.width)} "
            f"pixels wide and needs to be at least {settings.MIN_ENROLL_FACE_PIXELS}. "
            f"Bring the phone closer.",
            face_width=int(detection.width),
        )

    face = crop(image_bgr, detection.bbox, pad=0.1)
    brightness = mean_brightness(face)
    if brightness < settings.MIN_ENROLL_BRIGHTNESS:
        return _reject(
            "TOO_DARK",
            "The picture is too dark. Face a window or turn a light on — don't "
            "stand with a bright light behind you.",
            brightness=round(brightness, 1),
        )
    if brightness > settings.MAX_ENROLL_BRIGHTNESS:
        return _reject(
            "TOO_BRIGHT",
            "The picture is washed out. Move out of direct light and try again.",
            brightness=round(brightness, 1),
        )

    sharpness = laplacian_variance(face)
    if sharpness < settings.MIN_ENROLL_BLUR_VARIANCE:
        return _reject(
            "TOO_BLURRY",
            "That came out blurry. Hold still, wait for the camera to focus, "
            "then take it again.",
            sharpness=round(sharpness, 1),
        )
    return None


def check_not_duplicate(student: Student, vector: np.ndarray, exclude_step: int | None):
    """Reject a frame that is all but identical to one already stored."""
    existing = student.face_enrollments.all()
    if exclude_step is not None:
        existing = existing.exclude(step_index=exclude_step)
    rows = list(existing)
    if not rows:
        return None

    gallery = np.stack([emb.from_bytes(row.embedding) for row in rows])
    scores = emb.cosine_similarity_matrix(vector[None, :], gallery)[0]
    best = int(np.argmax(scores))
    if float(scores[best]) > settings.DUPLICATE_EMBEDDING_THRESHOLD:
        matched = rows[best]
        return _reject(
            "DUPLICATE",
            "That's the same shot you already submitted for "
            f"“{matched.get_pose_display().lower()}”. Change your angle or your "
            "surroundings so this capture adds something new.",
            similarity=round(float(scores[best]), 3),
            duplicate_of_step=matched.step_index,
        )
    return None


def check_not_another_student(student: Student, vector: np.ndarray):
    """Catch a student enrolling a friend's face.

    Compared against every other student in the same section who has any samples
    stored, not only the fully enrolled ones — a half-finished impostor should
    still be caught.
    """
    rows = list(
        FaceEnrollment.objects.filter(student__section_id=student.section_id)
        .exclude(student_id=student.pk)
        .select_related("student")
    )
    if not rows:
        return None

    gallery = np.stack([emb.from_bytes(row.embedding) for row in rows])
    scores = emb.cosine_similarity_matrix(vector[None, :], gallery)[0]
    best = int(np.argmax(scores))
    score = float(scores[best])
    if score >= settings.SIMILARITY_THRESHOLD:
        other = rows[best].student
        return _reject(
            "MATCHES_OTHER_STUDENT",
            "This face is already enrolled under a different student in your "
            "section. Your account has been flagged for an administrator to "
            "check — please speak to your class teacher.",
            similarity=round(score, 3),
            other_student_id=other.pk,
        )
    return None


def _flag_for_review(student: Student, result: CaptureResult) -> None:
    other_id = result.detail.get("other_student_id")
    note = (
        f"{timezone.now():%Y-%m-%d %H:%M} — enrollment capture matched student "
        f"#{other_id} at similarity {result.detail.get('similarity')}."
    )
    student.flagged_for_review = True
    student.flag_reason = f"{student.flag_reason}\n{note}".strip()
    student.save(update_fields=["flagged_for_review", "flag_reason"])
    logger.warning(
        "enrollment blocked: student %s (%s) matched student #%s at %.3f",
        student.pk, student.roll_no, other_id, result.detail.get("similarity", 0.0),
    )


@transaction.atomic
def process_capture(student: Student, step_index: int, normalised) -> CaptureResult:
    """Run every gate, and store the sample when it passes.

    ``normalised`` is an ``imaging.NormalisedImage`` — already re-encoded, EXIF
    stripped, and carrying the decoded pixels.
    """
    step = step_config(step_index)
    if step is None:
        return _reject("BAD_STEP", "That isn't one of the enrollment steps.")

    try:
        detections = get_engine().detect(normalised.array)
    except ModelFilesMissing as exc:
        logger.error("enrollment unavailable: %s", exc)
        return _reject(
            "MODEL_MISSING",
            "Face setup is temporarily unavailable because the recognition models "
            "aren't installed on the server. Please tell your administrator.",
        )
    except FaceEngineError as exc:
        logger.exception("face engine failure during enrollment")
        return _reject("ENGINE_ERROR", f"The face detector failed: {exc}")

    picked = _pick_face(detections)
    if isinstance(picked, CaptureResult):
        return picked
    detection = picked

    for check in (
        lambda: check_quality(detection, normalised.array),
        lambda: check_not_duplicate(student, detection.embedding, step_index),
        lambda: check_not_another_student(student, detection.embedding),
    ):
        rejection = check()
        if rejection is not None:
            if rejection.code == "MATCHES_OTHER_STUDENT":
                _flag_for_review(student, rejection)
            return rejection

    # A retake replaces the sample already held for this step.
    student.face_enrollments.filter(step_index=step_index).delete()

    face = crop(normalised.array, detection.bbox, pad=0.1)
    enrollment = FaceEnrollment(
        student=student,
        det_score=detection.det_score,
        quality_score=laplacian_variance(face),
        brightness=mean_brightness(face),
        face_width=int(detection.width),
        pose=step["pose"],
        step_index=step_index,
    )
    enrollment.set_embedding(detection.embedding)
    enrollment.image.save(normalised.file.name, normalised.file, save=False)
    enrollment.save()

    student.refresh_enrollment_state()
    logger.info(
        "enrollment step %s stored for %s (det=%.3f width=%d sharpness=%.1f)",
        step_index, student.roll_no, detection.det_score,
        int(detection.width), enrollment.quality_score,
    )
    return CaptureResult(
        ok=True, code="OK", message=f"{step['title']} — saved.", enrollment=enrollment
    )


def progress(student: Student) -> dict:
    """What the wizard needs to render: which steps are done and what's next."""
    done = set(student.face_enrollments.values_list("step_index", flat=True))
    all_steps = steps()
    remaining = [s for s in all_steps if s["index"] not in done]
    return {
        "done": len(done),
        "total": len(all_steps),
        "completed_steps": sorted(done),
        "next_step": remaining[0] if remaining else None,
        "complete": not remaining,
    }
