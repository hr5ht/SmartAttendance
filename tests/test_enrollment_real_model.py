"""End-to-end enrollment against the real InsightFace model.

These use the group photo bundled with insightface, so they need the buffalo_l
weights on disk. They are slow; run with -m "not slow" to skip them.
"""
import io
from pathlib import Path

import cv2
import numpy as np
import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from apps.accounts.models import Role, User
from apps.face.enrollment import process_capture
from apps.face.imaging import normalise
from apps.face.services import InsightFaceEngine, set_engine
from apps.students.models import Student

pytestmark = [pytest.mark.slow, pytest.mark.django_db]


def sample_photo() -> Path:
    import insightface

    return Path(insightface.__file__).parent / "data" / "images" / "t1.jpg"


@pytest.fixture(scope="module")
def engine():
    """One real engine for the whole module — loading it takes seconds."""
    real = InsightFaceEngine()
    try:
        real.preload()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"face models unavailable: {exc}")
    set_engine(real)
    yield real
    set_engine(None)


def upload(array_bgr, name="capture.jpg"):
    rgb = cv2.cvtColor(array_bgr, cv2.COLOR_BGR2RGB)
    buffer = io.BytesIO()
    Image.fromarray(rgb).save(buffer, format="JPEG", quality=95)
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/jpeg")


def face_crop(engine, index=0, pad=0.9, target_width=380, shift=0):
    """Cut one person out of the group photo and scale it up into a 'selfie'.

    The faces in the sample are only ~107 px wide, so some upscaling is needed to
    clear MIN_ENROLL_FACE_PIXELS. Keep it gentle — interpolating past about 1.4x
    softens the crop enough to trip the blur gate, which is the fixture's fault
    rather than the pipeline's (a native crop measures ~200 on the same metric).
    """
    image = cv2.imread(str(sample_photo()))
    faces = engine.detect(image)
    assert len(faces) >= index + 1, "sample photo did not yield enough faces"

    x1, y1, x2, y2 = faces[index].bbox
    w, h = x2 - x1, y2 - y1
    x1 -= w * pad + shift; x2 += w * pad - shift
    y1 -= h * pad; y2 += h * pad
    x1 = max(0, int(x1)); y1 = max(0, int(y1))
    x2 = min(image.shape[1], int(x2)); y2 = min(image.shape[0], int(y2))

    cropped = image[y1:y2, x1:x2]
    scale = target_width / cropped.shape[1]
    return cv2.resize(
        cropped, (target_width, int(cropped.shape[0] * scale)), interpolation=cv2.INTER_CUBIC
    )


@pytest.fixture
def student(db, section):
    user = User.objects.create_user("cse3b101", password="pw-student-123", role=Role.STUDENT)
    return Student.objects.create(
        user=user, roll_no="CSE3B101", name="Real Person", section=section
    )


@pytest.fixture
def classmate(db, section):
    user = User.objects.create_user("cse3b102", password="pw-student-123", role=Role.STUDENT)
    return Student.objects.create(
        user=user, roll_no="CSE3B102", name="Other Person", section=section
    )


def test_a_real_face_passes_every_gate(engine, student):
    result = process_capture(student, 0, normalise(upload(face_crop(engine, 0))))
    assert result.ok, f"{result.code}: {result.message}"

    sample = result.enrollment
    assert sample.det_score > 0.7
    assert sample.face_width >= 120
    vector = sample.get_embedding()
    assert vector.shape == (512,)
    assert np.linalg.norm(vector) == pytest.approx(1.0, abs=1e-4)


def test_the_full_group_photo_is_rejected_as_multiple_faces(engine, student):
    image = cv2.imread(str(sample_photo()))
    result = process_capture(student, 0, normalise(upload(image)))
    assert result.code == "MULTIPLE_FACES"
    assert result.detail["count"] >= 2


def test_a_photo_with_nobody_in_it_is_rejected(engine, student):
    noise = np.random.default_rng(0).integers(80, 180, (600, 480, 3), dtype=np.uint8)
    result = process_capture(student, 0, normalise(upload(noise)))
    assert result.code == "NO_FACE"


def test_the_identical_frame_is_caught_as_a_duplicate(engine, student):
    crop = face_crop(engine, 0)
    assert process_capture(student, 0, normalise(upload(crop))).ok

    result = process_capture(student, 1, normalise(upload(crop)))
    assert result.code == "DUPLICATE"
    assert result.detail["similarity"] > 0.98


def test_two_different_people_do_not_collide(engine, student, classmate):
    """Different faces must stay below the recognition threshold."""
    assert process_capture(classmate, 0, normalise(upload(face_crop(engine, 0)))).ok

    result = process_capture(student, 0, normalise(upload(face_crop(engine, 1))))
    assert result.ok, f"a different person was wrongly matched: {result.message}"


def test_submitting_a_classmates_face_is_blocked(engine, student, classmate):
    """The same person, framed differently, must still be recognised as them."""
    assert process_capture(classmate, 0, normalise(upload(face_crop(engine, 0)))).ok

    # Same face, wider crop and a slight shift: not a duplicate frame, same identity.
    impostor = face_crop(engine, 0, pad=1.2, target_width=440, shift=6)
    result = process_capture(student, 0, normalise(upload(impostor)))

    assert result.code == "MATCHES_OTHER_STUDENT", f"got {result.code}: {result.message}"
    assert result.detail["other_student_id"] == classmate.pk
    student.refresh_from_db()
    assert student.flagged_for_review is True
