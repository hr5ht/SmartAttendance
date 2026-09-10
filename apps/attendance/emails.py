"""The attendance notice sent when a teacher confirms a session.

One plain-text summary per confirmed session, addressed to the teacher who
took it plus whatever ATTENDANCE_EMAIL_CC lists. Delivery problems are
recorded on the session and never raised: the register is already saved by the
time this runs, so a broken SMTP host must not undo a confirmation.
"""
from __future__ import annotations

import logging

from django.conf import settings
from django.core.mail import EmailMessage
from django.utils import timezone

from apps.attendance.models import AttendanceStatus, RecordSource

logger = logging.getLogger("apps.attendance")


def recipients(session) -> list[str]:
    """The teacher who took the class, then the standing CC list."""
    addresses = []
    teacher_email = (session.teacher.email or "").strip()
    if teacher_email:
        addresses.append(teacher_email)
    for address in settings.ATTENDANCE_EMAIL_CC:
        if address not in addresses:
            addresses.append(address)
    return addresses


def _roll(records) -> str:
    lines = []
    for record in records:
        mark = " (by hand)" if record.source == RecordSource.MANUAL else ""
        lines.append(f"  {record.student.roll_no:<12} {record.student.name}{mark}")
    return "\n".join(lines) if lines else "  (nobody)"


def build_body(session) -> str:
    """The register, as text. Absent students are listed first — that is the
    list anybody reading this actually acts on."""
    records = list(session.records.select_related("student").order_by("student__roll_no"))
    present = [r for r in records if r.status == AttendanceStatus.PRESENT]
    absent = [r for r in records if r.status == AttendanceStatus.ABSENT]
    counts = session.counts()
    unenrolled = list(session.unenrolled_roster())

    parts = [
        f"{session.section.name} · {session.subject.name} ({session.subject.code})",
        f"Period {session.period} · {session.date:%A, %d %B %Y}",
        f"Taken by {session.teacher.name}",
        "",
        f"Present: {counts['present']} of {counts['total']} ({counts['percentage']}%)",
        f"Absent:  {counts['absent']}",
        "",
        f"ABSENT ({len(absent)})",
        _roll(absent),
        "",
        f"PRESENT ({len(present)})",
        _roll(present),
    ]

    if counts["manual"]:
        parts += [
            "",
            f"{counts['manual']} record(s) were set by the teacher rather than by "
            f"face recognition; those are marked “by hand” above.",
        ]

    if unenrolled:
        parts += [
            "",
            f"{len(unenrolled)} student(s) have not finished face setup and cannot be "
            f"recognised automatically: "
            + ", ".join(student.roll_no for student in unenrolled),
        ]

    parts += [
        "",
        "—",
        f"{settings.INSTITUTION_NAME} · confirmed "
        f"{timezone.localtime(session.confirmed_at):%d %b %Y at %H:%M}"
        if session.confirmed_at
        else f"{settings.INSTITUTION_NAME}",
        "This is an automated message. Replies are not monitored.",
    ]
    return "\n".join(parts)


def send_session_summary(session) -> bool:
    """Send the notice and record the outcome on the session.

    Returns whether it went out. ``email_sent`` and ``email_error`` are always
    written, so the admin list can show which sessions still need chasing.
    """
    addresses = recipients(session)
    if not addresses:
        session.email_sent = False
        session.email_error = (
            "No address to send to — the teacher has no email address on their "
            "account and ATTENDANCE_EMAIL_CC is empty."
        )
        session.save(update_fields=["email_sent", "email_error"])
        logger.warning("session %s confirmed but has no email recipients", session.pk)
        return False

    message = EmailMessage(
        subject=session.subject_line,
        body=build_body(session),
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=addresses,
    )
    try:
        message.send(fail_silently=False)
    except Exception as exc:  # any SMTP/socket failure — the register is safe
        session.email_sent = False
        session.email_error = f"{type(exc).__name__}: {exc}"
        session.save(update_fields=["email_sent", "email_error"])
        logger.exception("attendance notice for session %s could not be sent", session.pk)
        return False

    session.email_sent = True
    session.email_error = ""
    session.save(update_fields=["email_sent", "email_error"])
    logger.info("attendance notice for session %s sent to %s", session.pk, ", ".join(addresses))
    return True
