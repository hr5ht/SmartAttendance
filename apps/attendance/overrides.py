"""Teacher corrections to a processed session, and confirming the result.

What the pipeline writes is a proposal. These operations are how a teacher
changes it before committing. Everything a teacher touches is marked MANUAL so
that re-running the pipeline leaves it alone (see ``pipeline._write_records``),
and nothing here will touch a confirmed session.
"""
from __future__ import annotations

import logging

from django.db import transaction
from django.utils import timezone

from apps.attendance.models import (
    AttendanceRecord,
    AttendanceStatus,
    DetectedFace,
    RecordSource,
    SessionStatus,
)
from apps.students.models import Student

logger = logging.getLogger("apps.attendance")


class OverrideError(Exception):
    """A correction that cannot be applied. The message is shown to the teacher."""


def _require_draft(session) -> None:
    if session.is_confirmed:
        raise OverrideError(
            "This session is confirmed, so its register can no longer be changed."
        )


def _roster_student(session, student_id: int) -> Student:
    student = session.roster().filter(pk=student_id).first()
    if student is None:
        raise OverrideError("That student is not on this section's roster.")
    return student


@transaction.atomic
def set_record_status(session, student_id: int, status: str, user, reason: str = ""):
    """Mark one student present or absent by hand.

    The automatic confidence and the face it came from are dropped: once a
    teacher has decided, a recognition score would misrepresent why the row
    says what it says.
    """
    _require_draft(session)
    if status not in AttendanceStatus.values:
        raise OverrideError("That isn't a valid attendance status.")
    student = _roster_student(session, student_id)

    record, _ = AttendanceRecord.objects.get_or_create(
        session=session, student=student, defaults={"status": AttendanceStatus.ABSENT}
    )
    record.status = status
    record.source = RecordSource.MANUAL
    record.confidence = None
    record.matched_face = None
    record.overridden_by = user
    record.override_reason = (reason or "").strip()[:200]
    record.save(
        update_fields=[
            "status", "source", "confidence", "matched_face",
            "overridden_by", "override_reason", "updated_at",
        ]
    )
    logger.info(
        "session %s: %s set %s to %s by hand",
        session.pk, getattr(user, "username", "?"), student.roll_no, status,
    )
    return record


@transaction.atomic
def assign_face(session, face_id: int, student_id: int, user):
    """Put a name to an unrecognised face and mark that student present.

    The face keeps its embedding and score for the audit trail; only the
    identity changes, and the resulting record is MANUAL like any other
    teacher decision.
    """
    _require_draft(session)
    face = DetectedFace.objects.filter(
        pk=face_id, session_image__session=session
    ).select_related("session_image").first()
    if face is None:
        raise OverrideError("That face is not part of this session.")
    student = _roster_student(session, student_id)

    face.matched_student = student
    face.assigned_manually = True
    face.reject_reason = ""
    face.save(update_fields=["matched_student", "assigned_manually", "reject_reason"])

    record, _ = AttendanceRecord.objects.get_or_create(
        session=session, student=student, defaults={"status": AttendanceStatus.ABSENT}
    )
    record.status = AttendanceStatus.PRESENT
    record.source = RecordSource.MANUAL
    record.confidence = None
    record.matched_face = face
    record.overridden_by = user
    record.override_reason = f"Assigned face #{face.pk} by hand"
    record.save(
        update_fields=[
            "status", "source", "confidence", "matched_face",
            "overridden_by", "override_reason", "updated_at",
        ]
    )
    logger.info(
        "session %s: %s assigned face %s to %s",
        session.pk, getattr(user, "username", "?"), face.pk, student.roll_no,
    )
    return face


@transaction.atomic
def confirm(session, user):
    """Commit the register. After this the session is read-only and reportable."""
    if session.is_confirmed:
        raise OverrideError("This session has already been confirmed.")
    if not session.records.exists():
        raise OverrideError(
            "There is nothing to confirm yet — process the photos first so the "
            "roster has a result for every student."
        )

    session.status = SessionStatus.CONFIRMED
    session.confirmed_at = timezone.now()
    session.save(update_fields=["status", "confirmed_at"])
    counts = session.counts()
    logger.info(
        "session %s confirmed by %s: present=%s absent=%s manual=%s",
        session.pk, getattr(user, "username", "?"),
        counts["present"], counts["absent"], counts["manual"],
    )
    return session
