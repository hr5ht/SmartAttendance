"""The session pipeline: gallery scoping, matching, records and failure paths."""
import datetime as dt

import numpy as np
import pytest
from django.urls import reverse

from apps.accounts.models import Role, User
from apps.attendance.models import (
    AttendanceRecord,
    AttendanceSession,
    AttendanceStatus,
    DetectedFace,
    ImageStatus,
    RecordSource,
    SessionImage,
    SessionStatus,
)
from apps.attendance.pipeline import PipelineError, build_session_gallery, process_session
from apps.face.matching import RejectReason
from apps.face.models import FaceEnrollment, PoseLabel
from apps.face.services import Detection, ModelFilesMissing
from apps.students.models import Student
from tests.conftest import FakeEngine, make_photo, random_embedding, unit

DAY = dt.date(2026, 9, 7)


# --------------------------------------------------------------------- helpers


def make_student(section, roll_no, name, embedding=None, samples=5):
    """A student, optionally with a completed face gallery around `embedding`."""
    user = User.objects.create_user(roll_no.lower(), password="pw-student-123", role=Role.STUDENT)
    student = Student.objects.create(
        user=user, roll_no=roll_no, name=name, section=section
    )
    if embedding is not None:
        for step in range(samples):
            enrollment = FaceEnrollment(
                student=student,
                det_score=0.9,
                quality_score=200.0,
                brightness=130.0,
                face_width=180,
                pose=PoseLabel.FRONT,
                step_index=step,
            )
            # Small per-sample jitter, as five real poses would produce.
            enrollment.set_embedding(unit(embedding + 0.04 * step * random_embedding(seed=90 + step)))
            enrollment.save()
        student.refresh_enrollment_state()
    return student


def make_session(teacher, section, subject, period=3, images=1):
    session = AttendanceSession.objects.create(
        section=section, subject=subject, teacher=teacher, period=period, date=DAY
    )
    for order in range(images):
        SessionImage.objects.create(
            session=session, image=make_photo(f"room{order}.jpg"), order=order,
            width=640, height=480,
        )
    return session


def detections(*embeddings, det_score=0.9, width=200):
    return [
        Detection(bbox=(10, 10, 10 + width, 10 + width), det_score=det_score, embedding=vector)
        for vector in embeddings
    ]


@pytest.fixture
def faces():
    return {name: random_embedding(seed=index) for index, name in enumerate("abcd")}


# ------------------------------------------------------------- gallery scoping


def test_gallery_holds_only_enrolled_students_of_this_section(
    db, teacher, section, other_section, subject, faces
):
    here = make_student(section, "CSE3B001", "Aarav", faces["a"])
    not_enrolled = make_student(section, "CSE3B002", "Diya")            # no samples
    elsewhere = make_student(other_section, "CSE3A001", "Rahul", faces["b"])

    session = make_session(teacher, section, subject)
    students, enrollments = build_session_gallery(session)

    assert list(students) == [here]
    assert not_enrolled not in students
    assert elsewhere not in students
    assert {e.student_id for e in enrollments} == {here.pk}
    assert len(enrollments) == 5


def test_a_student_from_another_section_is_never_matched(
    db, teacher, section, other_section, subject, faces, fake_engine
):
    """The section filter is the accuracy lever: an outsider's face stays UNKNOWN."""
    make_student(section, "CSE3B001", "Aarav", faces["a"])
    outsider = make_student(other_section, "CSE3A001", "Rahul", faces["b"])

    session = make_session(teacher, section, subject)
    fake_engine.detections = detections(faces["b"])   # the outsider walks past
    report = process_session(session)

    assert report.faces_detected == 1
    assert report.faces_matched == 0
    assert not AttendanceRecord.objects.filter(student=outsider).exists()
    assert DetectedFace.objects.get().matched_student is None


def test_a_student_who_never_enrolled_is_skipped_and_marked_absent(
    db, teacher, section, subject, faces, fake_engine
):
    enrolled = make_student(section, "CSE3B001", "Aarav", faces["a"])
    never = make_student(section, "CSE3B002", "Diya")

    session = make_session(teacher, section, subject)
    fake_engine.detections = detections(faces["a"])
    process_session(session)

    assert session.records.get(student=enrolled).status == AttendanceStatus.PRESENT
    record = session.records.get(student=never)
    assert record.status == AttendanceStatus.ABSENT
    assert record.confidence is None
    assert never in session.unenrolled_roster()


# ------------------------------------------------------------------- behaviour


def test_matched_faces_become_present_and_the_rest_absent(
    db, teacher, section, subject, faces, fake_engine
):
    aarav = make_student(section, "CSE3B001", "Aarav", faces["a"])
    diya = make_student(section, "CSE3B002", "Diya", faces["b"])
    ishaan = make_student(section, "CSE3B003", "Ishaan", faces["c"])

    session = make_session(teacher, section, subject)
    fake_engine.detections = detections(faces["a"], faces["b"])
    report = process_session(session)

    assert report.present == 2 and report.absent == 1
    assert session.records.get(student=aarav).status == AttendanceStatus.PRESENT
    assert session.records.get(student=diya).status == AttendanceStatus.PRESENT
    assert session.records.get(student=ishaan).status == AttendanceStatus.ABSENT
    assert session.records.get(student=aarav).source == RecordSource.AUTO
    assert session.records.get(student=aarav).matched_face is not None


def test_one_student_per_face_within_an_image(
    db, teacher, section, subject, faces, fake_engine
):
    """Two faces cannot both be the same person; the weaker becomes UNKNOWN."""
    aarav = make_student(section, "CSE3B001", "Aarav", faces["a"])
    make_student(section, "CSE3B002", "Diya", faces["b"])

    session = make_session(teacher, section, subject)
    # An exact hit plus a weaker look-alike of the same person.
    weaker = unit(faces["a"] + 0.35 * random_embedding(seed=42))
    fake_engine.detections = detections(weaker, faces["a"])
    process_session(session)

    matched = DetectedFace.objects.filter(matched_student=aarav)
    assert matched.count() == 1
    kept = matched.get()
    loser = DetectedFace.objects.exclude(pk=kept.pk).get()
    assert kept.similarity > loser.similarity
    assert loser.matched_student is None
    assert loser.reject_reason == RejectReason.COLLISION
    assert session.records.get(student=aarav).status == AttendanceStatus.PRESENT


def test_union_across_images_keeps_the_highest_confidence(
    db, teacher, section, subject, faces, fake_engine
):
    aarav = make_student(section, "CSE3B001", "Aarav", faces["a"])
    diya = make_student(section, "CSE3B002", "Diya", faces["b"])

    session = make_session(teacher, section, subject, images=2)

    # Image 1 sees a weak Aarav; image 2 sees him clearly, plus Diya.
    weak = unit(faces["a"] + 0.4 * random_embedding(seed=11))
    queue = [detections(weak), detections(faces["a"], faces["b"])]

    class Sequenced(FakeEngine):
        def detect(self, image_bgr):
            return queue.pop(0) if queue else []

    from apps.face import services
    services.set_engine(Sequenced())
    try:
        process_session(session)
    finally:
        services.set_engine(fake_engine)

    aarav_record = session.records.get(student=aarav)
    assert aarav_record.status == AttendanceStatus.PRESENT
    # The better of the two sightings is the one recorded.
    assert aarav_record.confidence == pytest.approx(
        max(f.similarity for f in DetectedFace.objects.filter(matched_student=aarav))
    )
    assert session.records.get(student=diya).status == AttendanceStatus.PRESENT


def test_faces_that_are_too_small_or_too_uncertain_are_skipped(
    db, teacher, section, subject, faces, fake_engine, settings
):
    settings.MIN_FACE_PIXELS = 40
    settings.MIN_DETECTION_SCORE = 0.5
    make_student(section, "CSE3B001", "Aarav", faces["a"])
    session = make_session(teacher, section, subject)

    fake_engine.detections = [
        Detection(bbox=(0, 0, 20, 20), det_score=0.9, embedding=faces["a"]),   # too small
        Detection(bbox=(0, 0, 200, 200), det_score=0.2, embedding=faces["a"]),  # too unsure
    ]
    report = process_session(session)

    assert report.skipped_too_small == 1
    assert report.skipped_low_score == 1
    assert report.faces_detected == 0
    assert not DetectedFace.objects.exists()
    assert session.images.first().faces_skipped_small == 1


def test_detected_faces_keep_their_geometry_and_embedding(
    db, teacher, section, subject, faces, fake_engine
):
    make_student(section, "CSE3B001", "Aarav", faces["a"])
    session = make_session(teacher, section, subject)
    fake_engine.detections = [
        Detection(bbox=(12.0, 34.0, 212.0, 264.0), det_score=0.88, embedding=faces["a"])
    ]
    process_session(session)

    face = DetectedFace.objects.get()
    assert (face.bbox_x1, face.bbox_y1, face.bbox_x2, face.bbox_y2) == (12.0, 34.0, 212.0, 264.0)
    assert face.det_score == pytest.approx(0.88)
    assert face.get_embedding().shape == (512,)
    assert face.crop  # a thumbnail for the review strip


# ---------------------------------------------------------------- reprocessing


def test_reprocessing_replaces_detections_instead_of_doubling_them(
    db, teacher, section, subject, faces, fake_engine
):
    make_student(section, "CSE3B001", "Aarav", faces["a"])
    session = make_session(teacher, section, subject)
    fake_engine.detections = detections(faces["a"])

    process_session(session)
    process_session(session)

    assert DetectedFace.objects.count() == 1
    assert AttendanceRecord.objects.count() == 1


def test_reprocessing_preserves_a_manual_override(
    db, teacher, section, subject, faces, fake_engine
):
    """Phase 4 depends on this: a teacher's decision survives a re-run."""
    aarav = make_student(section, "CSE3B001", "Aarav", faces["a"])
    diya = make_student(section, "CSE3B002", "Diya", faces["b"])
    session = make_session(teacher, section, subject)

    fake_engine.detections = detections(faces["a"])
    process_session(session)

    # Teacher marks Diya present by hand even though she wasn't recognised.
    record = session.records.get(student=diya)
    record.status = AttendanceStatus.PRESENT
    record.source = RecordSource.MANUAL
    record.override_reason = "Sat behind a pillar"
    record.save()

    process_session(session)

    record.refresh_from_db()
    assert record.status == AttendanceStatus.PRESENT
    assert record.source == RecordSource.MANUAL
    assert record.override_reason == "Sat behind a pillar"
    assert session.records.get(student=aarav).source == RecordSource.AUTO


# -------------------------------------------------------------- failure paths


def test_a_section_with_nobody_enrolled_refuses_rather_than_marking_all_absent(
    db, teacher, section, subject, fake_engine
):
    make_student(section, "CSE3B001", "Aarav")   # no face setup
    session = make_session(teacher, section, subject)

    with pytest.raises(PipelineError) as caught:
        process_session(session)

    assert caught.value.code == "EMPTY_GALLERY"
    assert "face setup" in caught.value.message
    assert not AttendanceRecord.objects.exists()   # no silent all-absent register


def test_a_session_with_no_photos_is_refused(db, teacher, section, subject, faces, fake_engine):
    make_student(section, "CSE3B001", "Aarav", faces["a"])
    session = make_session(teacher, section, subject, images=0)

    with pytest.raises(PipelineError) as caught:
        process_session(session)
    assert caught.value.code == "NO_IMAGES"


def test_missing_model_files_stop_the_run_with_an_actionable_message(
    db, teacher, section, subject, faces
):
    from apps.face import services

    make_student(section, "CSE3B001", "Aarav", faces["a"])
    session = make_session(teacher, section, subject)

    class Broken(FakeEngine):
        def preload(self):
            raise ModelFilesMissing("weights absent")

    services.set_engine(Broken())
    try:
        with pytest.raises(PipelineError) as caught:
            process_session(session)
    finally:
        services.set_engine(None)

    assert caught.value.code == "MODEL_MISSING"
    assert "download_face_models" in caught.value.message


def test_no_faces_detected_leaves_everyone_absent_but_succeeds(
    db, teacher, section, subject, faces, fake_engine
):
    make_student(section, "CSE3B001", "Aarav", faces["a"])
    session = make_session(teacher, section, subject)
    fake_engine.detections = []

    report = process_session(session)
    assert report.faces_detected == 0
    assert report.present == 0 and report.absent == 1
    assert session.images.first().status == ImageStatus.DONE


def test_a_corrupt_photo_fails_only_itself(db, teacher, section, subject, faces, fake_engine):
    make_student(section, "CSE3B001", "Aarav", faces["a"])
    session = make_session(teacher, section, subject, images=2)
    fake_engine.detections = detections(faces["a"])

    # Truncate the first image on disk so decoding it blows up.
    bad = session.images.first()
    with open(bad.image.path, "wb") as handle:
        handle.write(b"not an image at all")

    report = process_session(session)

    bad.refresh_from_db()
    assert bad.status == ImageStatus.FAILED
    assert "could not be read" in bad.error_message
    assert report.failed_images
    # The good photo still ran, so attendance was still taken.
    assert session.images.last().status == ImageStatus.DONE
    assert session.records.get(student__roll_no="CSE3B001").status == AttendanceStatus.PRESENT


# ------------------------------------------------------------------ HTTP layer


def test_session_page_and_processing_endpoint(
    client, db, teacher, section, subject, faces, fake_engine
):
    make_student(section, "CSE3B001", "Aarav", faces["a"])
    session = make_session(teacher, section, subject)
    fake_engine.detections = detections(faces["a"])

    client.force_login(teacher.user)
    assert client.get(reverse("attendance:session_detail", args=[session.pk])).status_code == 200

    response = client.post(reverse("attendance:process_session", args=[session.pk]))
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["report"]["faces_matched"] == 1
    assert payload["report"]["present"] == 1


def test_processing_endpoint_reports_a_blocked_run_without_a_500(
    client, db, teacher, section, subject, fake_engine
):
    make_student(section, "CSE3B001", "Aarav")   # nobody enrolled
    session = make_session(teacher, section, subject)

    client.force_login(teacher.user)
    response = client.post(reverse("attendance:process_session", args=[session.pk]))

    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert response.json()["code"] == "EMPTY_GALLERY"


def test_status_endpoint_reports_per_image_progress(
    client, db, teacher, section, subject, faces, fake_engine
):
    make_student(section, "CSE3B001", "Aarav", faces["a"])
    session = make_session(teacher, section, subject, images=2)
    fake_engine.detections = detections(faces["a"])

    client.force_login(teacher.user)
    before = client.get(reverse("attendance:session_status", args=[session.pk])).json()
    assert before["total"] == 2 and before["done"] == 0 and before["finished"] is False

    client.post(reverse("attendance:process_session", args=[session.pk]))
    after = client.get(reverse("attendance:session_status", args=[session.pk])).json()
    assert after["done"] == 2 and after["finished"] is True


def test_a_confirmed_session_is_not_reprocessed(
    client, db, teacher, section, subject, faces, fake_engine
):
    make_student(section, "CSE3B001", "Aarav", faces["a"])
    session = make_session(teacher, section, subject)
    session.status = SessionStatus.CONFIRMED
    session.save()

    client.force_login(teacher.user)
    response = client.post(reverse("attendance:process_session", args=[session.pk]))
    assert response.status_code == 400
    assert response.json()["code"] == "CONFIRMED"


# ---------------------------------------------------------------- permissions


def test_another_teacher_cannot_open_or_process_the_session(
    client, db, teacher, dept, section, subject, faces, fake_engine
):
    from apps.academics.models import Teacher, TeacherAssignment

    make_student(section, "CSE3B001", "Aarav", faces["a"])
    session = make_session(teacher, section, subject)

    other_user = User.objects.create_user("t.other", password="pw-other-123", role=Role.TEACHER)
    other = Teacher.objects.create(user=other_user, department=dept, employee_id="EMP-9")
    TeacherAssignment.objects.create(teacher=other, section=section, subject=subject)

    client.force_login(other_user)
    for name in ("session_detail", "session_status"):
        assert client.get(reverse(f"attendance:{name}", args=[session.pk])).status_code == 404
    assert client.post(
        reverse("attendance:process_session", args=[session.pk])
    ).status_code == 404


def test_a_student_cannot_reach_the_session_pages(
    client, db, teacher, section, subject, faces, fake_engine
):
    make_student(section, "CSE3B001", "Aarav", faces["a"])
    session = make_session(teacher, section, subject)

    intruder = User.objects.create_user("cse3b900", password="pw-s-123", role=Role.STUDENT)
    client.force_login(intruder)

    assert client.get(reverse("attendance:session_detail", args=[session.pk])).status_code == 403
    assert client.post(
        reverse("attendance:process_session", args=[session.pk])
    ).status_code == 403
