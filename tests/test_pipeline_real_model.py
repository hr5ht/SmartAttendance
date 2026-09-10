"""The whole pipeline against the real model, on a real multi-face photo.

Enrolls students from crops of the bundled group photo, then feeds that same
photo in as the classroom image. Slow; needs the buffalo_l weights on disk.
"""
import datetime as dt
from pathlib import Path

import cv2
import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.accounts.models import Role, User
from apps.attendance.models import (
    AttendanceSession,
    AttendanceStatus,
    DetectedFace,
    SessionImage,
)
from apps.attendance.pipeline import process_session
from apps.face.enrollment import steps
from apps.face.imaging import normalise
from apps.face.management.commands.seed_face_enrollments import (
    CROP_VARIANTS,
    sample_photo,
    to_jpeg,
    variant_crop,
)
from apps.face.models import FaceEnrollment, PoseLabel
from apps.face.services import InsightFaceEngine, set_engine
from apps.students.models import Student

pytestmark = [pytest.mark.slow, pytest.mark.django_db]

DAY = dt.date(2026, 9, 7)


@pytest.fixture(scope="module")
def engine():
    real = InsightFaceEngine()
    try:
        real.preload()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"face models unavailable: {exc}")
    set_engine(real)
    yield real
    set_engine(None)


@pytest.fixture(scope="module")
def group_photo(engine):
    image = cv2.imread(str(sample_photo()))
    faces = engine.detect(image)
    assert len(faces) >= 4, "sample photo should contain several faces"
    return image, faces


def enroll_from_face(engine, image, bbox, section, roll_no, name) -> Student:
    """Build a real 5-sample gallery for one person out of the group photo."""
    user = User.objects.create_user(roll_no.lower(), password="pw-student-123", role=Role.STUDENT)
    student = Student.objects.create(user=user, roll_no=roll_no, name=name, section=section)

    for step, variant in zip(steps(), CROP_VARIANTS):
        patch = variant_crop(image, bbox, **variant)
        found = engine.detect(patch)
        if not found:
            continue
        enrollment = FaceEnrollment(
            student=student,
            det_score=found[0].det_score,
            quality_score=200.0,
            brightness=130.0,
            face_width=int(found[0].width),
            pose=step["pose"],
            step_index=step["index"],
        )
        enrollment.set_embedding(found[0].embedding)
        enrollment.image.save("seeded.jpg", to_jpeg(patch), save=False)
        enrollment.save()

    student.refresh_enrollment_state()
    assert student.face_enrolled, f"{roll_no} did not get a full gallery"
    return student


def attach_photo(session, path: Path):
    upload = SimpleUploadedFile("room.jpg", path.read_bytes(), content_type="image/jpeg")
    normalised = normalise(upload)
    return SessionImage.objects.create(
        session=session, image=normalised.file, order=0,
        width=normalised.width, height=normalised.height,
    )


def test_real_photo_matches_the_enrolled_students(
    engine, group_photo, teacher, section, subject
):
    image, faces = group_photo
    enrolled = [
        enroll_from_face(engine, image, faces[index].bbox, section,
                         f"CSE3B10{index}", f"Person {index}")
        for index in range(3)
    ]

    session = AttendanceSession.objects.create(
        section=section, subject=subject, teacher=teacher, period=3, date=DAY
    )
    attach_photo(session, sample_photo())

    report = process_session(session)

    assert report.faces_detected == len(faces)
    assert report.gallery_students == 3
    assert report.gallery_samples == 15

    for student in enrolled:
        record = session.records.get(student=student)
        assert record.status == AttendanceStatus.PRESENT, f"{student.roll_no} was missed"
        assert record.confidence >= 0.45
        assert record.matched_face is not None

    # The people in the photo who were never enrolled stay unknown, not guessed at.
    assert DetectedFace.objects.filter(matched_student__isnull=True).count() == len(faces) - 3


def test_each_face_is_matched_to_the_right_person(
    engine, group_photo, teacher, section, subject
):
    """Guards against the matcher being confidently wrong rather than merely unsure."""
    image, faces = group_photo
    students = [
        enroll_from_face(engine, image, faces[index].bbox, section,
                         f"CSE3B20{index}", f"Person {index}")
        for index in range(3)
    ]

    session = AttendanceSession.objects.create(
        section=section, subject=subject, teacher=teacher, period=4, date=DAY
    )
    attach_photo(session, sample_photo())
    process_session(session)

    # Each detected face sits at a known place in the photo; the student matched
    # to it must be the one enrolled from that same place.
    for index, student in enumerate(students):
        face = DetectedFace.objects.get(matched_student=student)
        expected = faces[index].bbox
        assert abs(face.bbox_x1 - expected[0]) < 5, (
            f"{student.roll_no} matched a face at x={face.bbox_x1}, "
            f"but was enrolled from x={expected[0]}"
        )


def test_an_unenrolled_person_in_the_photo_is_marked_absent(
    engine, group_photo, teacher, section, subject
):
    image, faces = group_photo
    present = enroll_from_face(
        engine, image, faces[0].bbox, section, "CSE3B301", "Enrolled Person"
    )
    # In the room and in the photo, but never completed face setup.
    absent_user = User.objects.create_user("cse3b302", password="pw-s-123", role=Role.STUDENT)
    absent = Student.objects.create(
        user=absent_user, roll_no="CSE3B302", name="Unenrolled Person", section=section
    )

    session = AttendanceSession.objects.create(
        section=section, subject=subject, teacher=teacher, period=5, date=DAY
    )
    attach_photo(session, sample_photo())
    process_session(session)

    assert session.records.get(student=present).status == AttendanceStatus.PRESENT
    assert session.records.get(student=absent).status == AttendanceStatus.ABSENT
    assert absent in session.unenrolled_roster()
