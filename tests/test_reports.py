"""Reporting: only confirmed sessions count, and each role sees its own slice."""
import datetime as dt

import pytest
from django.urls import reverse

from apps.academics.models import Teacher, TeacherAssignment
from apps.accounts.models import Role, User
from apps.attendance.models import (
    AttendanceRecord,
    AttendanceSession,
    AttendanceStatus,
    SessionStatus,
)
from apps.students.models import Student

INDEX = reverse("reports:index")
EXPORT = reverse("reports:export_csv")
MINE = reverse("reports:my_attendance")


@pytest.fixture
def students(db, section):
    made = []
    for index in range(1, 3):
        roll = f"CSE3B{index:03d}"
        user = User.objects.create_user(roll.lower(), password="pw-student-123", role=Role.STUDENT)
        made.append(
            Student.objects.create(
                user=user, roll_no=roll, name=f"Student {index}", section=section
            )
        )
    return made


def make_session(teacher, section, subject, day, period, status, marks):
    """``marks`` maps a student to the status they were given."""
    session = AttendanceSession.objects.create(
        section=section, subject=subject, teacher=teacher,
        period=period, date=day, status=status,
    )
    for student, mark in marks.items():
        AttendanceRecord.objects.create(session=session, student=student, status=mark)
    return session


@pytest.fixture
def history(teacher, section, subject, students):
    """Three confirmed classes and one draft that must not be counted."""
    first, second = students
    day = dt.date(2026, 9, 7)
    make_session(teacher, section, subject, day, 1, SessionStatus.CONFIRMED,
                 {first: AttendanceStatus.PRESENT, second: AttendanceStatus.PRESENT})
    make_session(teacher, section, subject, day + dt.timedelta(days=1), 1, SessionStatus.CONFIRMED,
                 {first: AttendanceStatus.PRESENT, second: AttendanceStatus.ABSENT})
    make_session(teacher, section, subject, day + dt.timedelta(days=2), 1, SessionStatus.CONFIRMED,
                 {first: AttendanceStatus.PRESENT, second: AttendanceStatus.ABSENT})
    make_session(teacher, section, subject, day + dt.timedelta(days=3), 1, SessionStatus.DRAFT,
                 {first: AttendanceStatus.ABSENT, second: AttendanceStatus.ABSENT})
    return students


# ------------------------------------------------------------------ totals


def test_only_confirmed_sessions_are_counted(client, teacher, history):
    client.force_login(teacher.user)
    rows = {row["roll_no"]: row for row in client.get(INDEX).context["rows"]}

    # Four sessions exist; the draft is excluded, so every student has 3 marks.
    assert rows["CSE3B001"]["held"] == 3
    assert rows["CSE3B001"]["present"] == 3
    assert rows["CSE3B001"]["percentage"] == 100.0
    assert rows["CSE3B002"]["present"] == 1
    assert rows["CSE3B002"]["absent"] == 2
    assert rows["CSE3B002"]["percentage"] == 33.3


def test_low_attendance_is_flagged(client, teacher, history, settings):
    settings.LOW_ATTENDANCE_PERCENT = 75.0
    client.force_login(teacher.user)
    response = client.get(INDEX)
    rows = {row["roll_no"]: row for row in response.context["rows"]}

    assert rows["CSE3B001"]["is_low"] is False
    assert rows["CSE3B002"]["is_low"] is True
    assert response.context["summary"]["low_count"] == 1


def test_summary_totals(client, teacher, history):
    client.force_login(teacher.user)
    summary = client.get(INDEX).context["summary"]
    assert summary["sessions"] == 3
    assert summary["students"] == 2
    assert summary["marks"] == 6        # 2 students x 3 confirmed sessions
    assert summary["present"] == 4
    assert summary["percentage"] == 66.7


def test_a_session_with_no_confirmations_reports_nothing(client, teacher, section, subject, students):
    make_session(teacher, section, subject, dt.date(2026, 9, 7), 1, SessionStatus.DRAFT,
                 {students[0]: AttendanceStatus.PRESENT})
    client.force_login(teacher.user)
    response = client.get(INDEX)
    assert response.context["rows"] == []
    assert response.context["summary"]["sessions"] == 0


# ----------------------------------------------------------------- filters


def test_date_filter_narrows_the_report(client, teacher, history):
    client.force_login(teacher.user)
    response = client.get(INDEX, {"date_from": "2026-09-08", "date_to": "2026-09-08"})
    rows = {row["roll_no"]: row for row in response.context["rows"]}
    assert rows["CSE3B001"]["held"] == 1
    assert response.context["summary"]["sessions"] == 1


def test_subject_filter_narrows_the_report(client, teacher, section, subject, other_subject, history):
    TeacherAssignment.objects.create(teacher=teacher, section=section, subject=other_subject)
    make_session(teacher, section, other_subject, dt.date(2026, 9, 10), 2,
                 SessionStatus.CONFIRMED, {history[0]: AttendanceStatus.ABSENT})

    client.force_login(teacher.user)
    response = client.get(INDEX, {"subject": other_subject.pk})
    rows = {row["roll_no"]: row for row in response.context["rows"]}
    assert rows["CSE3B001"]["held"] == 1
    assert rows["CSE3B001"]["present"] == 0


def test_a_backwards_date_range_is_rejected(client, teacher, history):
    client.force_login(teacher.user)
    response = client.get(INDEX, {"date_from": "2026-09-09", "date_to": "2026-09-01"})
    assert response.context["form"].errors["date_to"]


# ------------------------------------------------------------------- scope


def test_a_teacher_only_sees_their_own_sessions(client, teacher, dept, section, subject, history):
    other_user = User.objects.create_user("t.other", password="pw-other-123", role=Role.TEACHER)
    other = Teacher.objects.create(user=other_user, department=dept, employee_id="EMP-7")

    client.force_login(other_user)
    response = client.get(INDEX)
    assert response.context["rows"] == []
    assert response.context["summary"]["sessions"] == 0


def test_an_admin_sees_every_session(client, admin_user, history):
    client.force_login(admin_user)
    response = client.get(INDEX)
    assert response.context["summary"]["sessions"] == 3
    assert len(response.context["rows"]) == 2


def test_students_cannot_reach_the_staff_report(client, students):
    client.force_login(students[0].user)
    assert client.get(INDEX).status_code == 403


# ------------------------------------------------------------------ export


def test_csv_export_matches_the_page(client, teacher, history):
    client.force_login(teacher.user)
    response = client.get(EXPORT)
    assert response.status_code == 200
    assert response["Content-Type"] == "text/csv"
    assert "attachment" in response["Content-Disposition"]

    lines = response.content.decode().strip().splitlines()
    assert lines[0].startswith("Roll number,Name,Class")
    assert len(lines) == 3                       # header + two students
    assert "CSE3B002,Student 2,CSE-3B,3,1,2,33.3" in response.content.decode()


def test_csv_export_honours_the_filters(client, teacher, history):
    client.force_login(teacher.user)
    response = client.get(EXPORT, {"date_from": "2026-09-08", "date_to": "2026-09-08"})
    body = response.content.decode()
    assert "CSE3B001,Student 1,CSE-3B,1,1,0,100.0" in body


# --------------------------------------------------------------- student view


def test_a_student_sees_their_own_record(client, history):
    student = history[1]
    client.force_login(student.user)
    response = client.get(MINE)

    assert response.status_code == 200
    assert response.context["held"] == 3
    assert response.context["present"] == 1
    assert response.context["percentage"] == 33.3
    assert response.context["is_low"] is True
    assert len(response.context["subjects"]) == 1
    assert response.context["subjects"][0]["percentage"] == 33.3


def test_a_student_with_no_confirmed_classes_sees_an_empty_state(client, students):
    client.force_login(students[0].user)
    response = client.get(MINE)
    assert response.context["held"] == 0
    assert response.context["records"] == []


def test_teachers_cannot_reach_the_student_report(client, teacher):
    client.force_login(teacher.user)
    assert client.get(MINE).status_code == 403
