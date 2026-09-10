"""Attendance reporting.

Only CONFIRMED sessions are counted. A draft is a teacher's working copy — the
footer promises that records are provisional until confirmed, so letting drafts
into a percentage would break that promise.
"""
from __future__ import annotations

import csv

from django.conf import settings
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone

from apps.accounts.permissions import student_required, teacher_required
from apps.attendance.models import (
    AttendanceRecord,
    AttendanceSession,
    AttendanceStatus,
    SessionStatus,
)
from apps.reports.forms import ReportFilterForm


def _confirmed_sessions(user):
    """Confirmed sessions this user may see — admins all, teachers their own."""
    return AttendanceSession.objects.visible_to(user).filter(
        status=SessionStatus.CONFIRMED
    )


def _percentage(present: int, held: int) -> float:
    return round(100 * present / held, 1) if held else 0.0


def student_rows(sessions) -> list[dict]:
    """One row per student: how many of these classes they were marked present in."""
    aggregated = (
        AttendanceRecord.objects.filter(session__in=sessions)
        .values(
            "student_id",
            "student__roll_no",
            "student__name",
            "student__section__name",
        )
        .annotate(
            held=Count("id"),
            present=Count("id", filter=Q(status=AttendanceStatus.PRESENT)),
        )
        .order_by("student__section__name", "student__roll_no")
    )
    rows = []
    for row in aggregated:
        percentage = _percentage(row["present"], row["held"])
        rows.append(
            {
                "student_id": row["student_id"],
                "roll_no": row["student__roll_no"],
                "name": row["student__name"],
                "section": row["student__section__name"],
                "held": row["held"],
                "present": row["present"],
                "absent": row["held"] - row["present"],
                "percentage": percentage,
                "is_low": percentage < settings.LOW_ATTENDANCE_PERCENT,
            }
        )
    return rows


def _summary(sessions, rows: list[dict]) -> dict:
    held = sum(row["held"] for row in rows)
    present = sum(row["present"] for row in rows)
    return {
        "sessions": sessions.count(),
        "students": len(rows),
        "marks": held,
        "present": present,
        "percentage": _percentage(present, held),
        "low_count": sum(1 for row in rows if row["is_low"]),
        "threshold": settings.LOW_ATTENDANCE_PERCENT,
    }


def _filtered(request):
    """The shared query behind both the page and its CSV."""
    sessions = _confirmed_sessions(request.user)
    form = ReportFilterForm(request.GET or None, sessions=sessions)
    return form, form.filter(sessions)


@teacher_required
def index(request):
    form, sessions = _filtered(request)
    rows = student_rows(sessions)
    return render(
        request,
        "reports/index.html",
        {
            "form": form,
            "rows": rows,
            "summary": _summary(sessions, rows),
            "query": request.GET.urlencode(),
            "sessions": sessions.select_related("section", "subject", "teacher__user")[:12],
        },
    )


@teacher_required
def export_csv(request):
    _, sessions = _filtered(request)
    rows = student_rows(sessions)

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = (
        f'attachment; filename="attendance-{timezone.localdate():%Y%m%d}.csv"'
    )
    writer = csv.writer(response)
    writer.writerow(
        ["Roll number", "Name", "Class", "Classes held", "Present", "Absent", "Percentage"]
    )
    for row in rows:
        writer.writerow(
            [
                row["roll_no"], row["name"], row["section"],
                row["held"], row["present"], row["absent"], row["percentage"],
            ]
        )
    return response


@student_required
def my_attendance(request):
    """A student's own record, class by class."""
    student = getattr(request.user, "student_profile", None)
    if student is None:
        return render(request, "reports/student.html", {"student": None})

    records = list(
        AttendanceRecord.objects.filter(
            student=student, session__status=SessionStatus.CONFIRMED
        )
        .select_related("session__subject", "session__section", "session__teacher__user")
        .order_by("-session__date", "-session__period")
    )
    held = len(records)
    present = sum(1 for record in records if record.status == AttendanceStatus.PRESENT)

    # Per subject, so a student can see which class they are behind in.
    by_subject: dict[int, dict] = {}
    for record in records:
        subject = record.session.subject
        entry = by_subject.setdefault(
            subject.pk, {"subject": subject, "held": 0, "present": 0}
        )
        entry["held"] += 1
        entry["present"] += 1 if record.status == AttendanceStatus.PRESENT else 0
    subjects = sorted(by_subject.values(), key=lambda entry: entry["subject"].code)
    for entry in subjects:
        entry["percentage"] = _percentage(entry["present"], entry["held"])
        entry["is_low"] = entry["percentage"] < settings.LOW_ATTENDANCE_PERCENT

    percentage = _percentage(present, held)
    return render(
        request,
        "reports/student.html",
        {
            "student": student,
            "records": records[:60],
            "subjects": subjects,
            "held": held,
            "present": present,
            "absent": held - present,
            "percentage": percentage,
            "is_low": percentage < settings.LOW_ATTENDANCE_PERCENT,
            "threshold": settings.LOW_ATTENDANCE_PERCENT,
        },
    )
