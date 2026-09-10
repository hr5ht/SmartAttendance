"""Manual corrections, confirming a session, and the attendance notice."""
import datetime as dt

import numpy as np
import pytest
from django.core import mail
from django.core.files.base import ContentFile
from django.urls import reverse

from apps.attendance.models import (
    AttendanceRecord,
    AttendanceSession,
    AttendanceStatus,
    DetectedFace,
    RecordSource,
    SessionImage,
    SessionStatus,
)
from apps.attendance.overrides import OverrideError, assign_face, confirm, set_record_status
from apps.students.models import Student


@pytest.fixture
def session(db, teacher, section, subject):
    return AttendanceSession.objects.create(
        section=section, subject=subject, teacher=teacher, period=3,
        date=dt.date(2026, 9, 7),
    )


@pytest.fixture
def students(db, section):
    from apps.accounts.models import Role, User

    made = []
    for index in range(1, 4):
        roll = f"CSE3B{index:03d}"
        user = User.objects.create_user(roll.lower(), password="pw-student-123", role=Role.STUDENT)
        made.append(
            Student.objects.create(
                user=user, roll_no=roll, name=f"Student {index}",
                section=section, email=f"{roll.lower()}@example.edu",
            )
        )
    return made


@pytest.fixture
def processed(session, students):
    """A session with a record per student: the first present, the rest absent."""
    AttendanceRecord.objects.create(
        session=session, student=students[0], status=AttendanceStatus.PRESENT,
        source=RecordSource.AUTO, confidence=0.81,
    )
    for student in students[1:]:
        AttendanceRecord.objects.create(
            session=session, student=student, status=AttendanceStatus.ABSENT,
            source=RecordSource.AUTO,
        )
    return session


# ------------------------------------------------------------- overrides


def test_marking_a_student_present_by_hand(processed, students, teacher):
    record = set_record_status(
        processed, students[1].pk, AttendanceStatus.PRESENT, teacher.user, "arrived late"
    )
    assert record.status == AttendanceStatus.PRESENT
    assert record.source == RecordSource.MANUAL
    assert record.overridden_by == teacher.user
    assert record.override_reason == "arrived late"
    # A recognition score would misrepresent a decision the teacher made.
    assert record.confidence is None
    assert record.matched_face is None


def test_manual_records_survive_reprocessing(processed, students, teacher, fake_engine, settings):
    """The whole point of the MANUAL flag: a re-run must not undo the teacher."""
    from apps.attendance.pipeline import _write_records, PipelineReport
    from apps.face.models import FaceEnrollment
    from tests.conftest import random_embedding

    set_record_status(processed, students[1].pk, AttendanceStatus.PRESENT, teacher.user)
    for step in range(settings.ENROLLMENT_STEPS):
        enrollment = FaceEnrollment(
            student=students[0], det_score=0.9, quality_score=100, step_index=step
        )
        enrollment.set_embedding(random_embedding(seed=step))
        enrollment.image.save(f"e{step}.jpg", ContentFile(b"x"), save=False)
        enrollment.save()

    _write_records(processed, processed.roster(), {}, PipelineReport())

    kept = AttendanceRecord.objects.get(session=processed, student=students[1])
    assert kept.status == AttendanceStatus.PRESENT
    assert kept.source == RecordSource.MANUAL


def test_a_student_from_another_section_cannot_be_marked(processed, other_section, teacher, db):
    from apps.accounts.models import Role, User

    outsider = Student.objects.create(
        user=User.objects.create_user("cse3a099", role=Role.STUDENT),
        roll_no="CSE3A099", name="Outsider", section=other_section,
    )
    with pytest.raises(OverrideError, match="not on this section's roster"):
        set_record_status(processed, outsider.pk, AttendanceStatus.PRESENT, teacher.user)


def test_assigning_an_unknown_face_marks_the_student_present(processed, students, teacher):
    image = SessionImage.objects.create(session=processed, image="sessions/1/x.jpg")
    face = DetectedFace(
        session_image=image, bbox_x1=0, bbox_y1=0, bbox_x2=100, bbox_y2=120,
        det_score=0.9, reject_reason="BELOW_THRESHOLD",
    )
    face.set_embedding(np.zeros(512, dtype=np.float32))
    face.save()

    assigned = assign_face(processed, face.pk, students[2].pk, teacher.user)
    assigned.refresh_from_db()
    assert assigned.matched_student == students[2]
    assert assigned.assigned_manually is True
    assert assigned.reject_reason == ""

    record = AttendanceRecord.objects.get(session=processed, student=students[2])
    assert record.status == AttendanceStatus.PRESENT
    assert record.source == RecordSource.MANUAL
    assert record.matched_face == assigned


def test_a_face_from_another_session_cannot_be_assigned(processed, students, teacher, section, subject):
    other = AttendanceSession.objects.create(
        section=section, subject=subject, teacher=teacher, period=5, date=dt.date(2026, 9, 8)
    )
    image = SessionImage.objects.create(session=other, image="sessions/2/x.jpg")
    face = DetectedFace(
        session_image=image, bbox_x1=0, bbox_y1=0, bbox_x2=10, bbox_y2=10, det_score=0.9
    )
    face.set_embedding(np.zeros(512, dtype=np.float32))
    face.save()

    with pytest.raises(OverrideError, match="not part of this session"):
        assign_face(processed, face.pk, students[0].pk, teacher.user)


# --------------------------------------------------------------- confirm


def test_confirming_locks_the_session(processed, teacher):
    confirm(processed, teacher.user)
    processed.refresh_from_db()
    assert processed.status == SessionStatus.CONFIRMED
    assert processed.confirmed_at is not None
    assert processed.is_confirmed


def test_an_empty_session_cannot_be_confirmed(session, teacher):
    with pytest.raises(OverrideError, match="nothing to confirm"):
        confirm(session, teacher.user)


def test_a_confirmed_session_refuses_corrections(processed, students, teacher):
    confirm(processed, teacher.user)
    with pytest.raises(OverrideError, match="no longer be changed"):
        set_record_status(processed, students[0].pk, AttendanceStatus.ABSENT, teacher.user)


def test_confirming_twice_is_refused(processed, teacher):
    confirm(processed, teacher.user)
    with pytest.raises(OverrideError, match="already been confirmed"):
        confirm(processed, teacher.user)


# ----------------------------------------------------------------- email


def test_confirming_emails_the_register(client, processed, students, teacher, settings):
    settings.ATTENDANCE_EMAIL_CC = ["office@example.edu"]
    teacher.user.email = "anita@example.edu"
    teacher.user.save(update_fields=["email"])

    client.force_login(teacher.user)
    response = client.post(reverse("attendance:confirm_session", args=[processed.pk]))
    assert response.status_code == 302

    assert len(mail.outbox) == 1
    notice = mail.outbox[0]
    assert notice.to == ["anita@example.edu", "office@example.edu"]
    assert "CSE-3B" in notice.subject and "Period 3" in notice.subject
    # The absent list is what anybody reading this acts on.
    assert "ABSENT (2)" in notice.body
    assert students[1].roll_no in notice.body

    processed.refresh_from_db()
    assert processed.email_sent is True
    assert processed.email_error == ""


def test_a_failing_mail_server_does_not_undo_the_confirmation(
    client, processed, teacher, settings
):
    settings.EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    settings.EMAIL_HOST = "127.0.0.1"
    settings.EMAIL_PORT = 1  # nothing is listening
    settings.EMAIL_TIMEOUT = 1
    teacher.user.email = "anita@example.edu"
    teacher.user.save(update_fields=["email"])

    client.force_login(teacher.user)
    client.post(reverse("attendance:confirm_session", args=[processed.pk]))

    processed.refresh_from_db()
    assert processed.is_confirmed          # the register is safe
    assert processed.email_sent is False
    assert processed.email_error           # and the failure is recorded


def test_a_session_with_no_recipients_is_still_confirmed(client, processed, teacher, settings):
    settings.ATTENDANCE_EMAIL_CC = []
    teacher.user.email = ""
    teacher.user.save(update_fields=["email"])

    client.force_login(teacher.user)
    client.post(reverse("attendance:confirm_session", args=[processed.pk]))

    processed.refresh_from_db()
    assert processed.is_confirmed
    assert processed.email_sent is False
    assert "No address" in processed.email_error
    assert not mail.outbox


# ----------------------------------------------------------- through the view


def test_teacher_can_correct_and_confirm_from_the_page(client, processed, students, teacher):
    client.force_login(teacher.user)
    client.post(
        reverse("attendance:override_record", args=[processed.pk]),
        {"student": students[1].pk, "status": "PRESENT", "reason": "was in the back row"},
    )
    record = AttendanceRecord.objects.get(session=processed, student=students[1])
    assert record.status == AttendanceStatus.PRESENT

    client.post(reverse("attendance:confirm_session", args=[processed.pk]))
    processed.refresh_from_db()
    assert processed.is_confirmed
    assert processed.counts()["present"] == 2


def test_another_teacher_cannot_confirm_your_session(client, processed, dept, section, subject):
    from apps.academics.models import Teacher
    from apps.accounts.models import Role, User

    other_user = User.objects.create_user("t.other", password="pw-other-123", role=Role.TEACHER)
    Teacher.objects.create(user=other_user, department=dept, employee_id="EMP-9")

    client.force_login(other_user)
    assert client.post(
        reverse("attendance:confirm_session", args=[processed.pk])
    ).status_code == 404
    processed.refresh_from_db()
    assert not processed.is_confirmed


def test_confirmed_sessions_cannot_be_reprocessed_or_deleted(client, processed, teacher):
    confirm(processed, teacher.user)
    client.force_login(teacher.user)

    response = client.post(reverse("attendance:process_session", args=[processed.pk]))
    assert response.status_code == 400
    assert response.json()["code"] == "CONFIRMED"

    client.post(reverse("attendance:delete_session", args=[processed.pk]))
    assert AttendanceSession.objects.filter(pk=processed.pk).exists()
