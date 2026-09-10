from __future__ import annotations

import logging

from django.conf import settings
from django.contrib import messages
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST

from apps.academics.models import ClassSection, Subject, TimetableSlot
from apps.accounts.permissions import teacher_required
from apps.attendance.forms import AddImagesForm, SessionDetailsForm
from apps.attendance.models import (
    AttendanceSession,
    AttendanceStatus,
    ImageStatus,
    SessionImage,
    SessionStatus,
)
from apps.attendance.emails import send_session_summary
from apps.attendance.overrides import OverrideError, assign_face, confirm, set_record_status
from apps.attendance.pipeline import PipelineError, process_session
from apps.face.imaging import normalise

logger = logging.getLogger("apps.attendance")


def _requested_date(request):
    """The day the dashboard is showing — ?date=YYYY-MM-DD, else today."""
    raw = request.GET.get("date") or request.POST.get("date")
    return (parse_date(raw) if raw else None) or timezone.localdate()


def _day_context(teacher, day):
    """Timetable and sessions for one teacher on one day."""
    slots = (
        TimetableSlot.objects.filter(teacher=teacher, weekday=day.weekday())
        .select_related("section", "subject")
        .order_by("period")
    )
    sessions = (
        AttendanceSession.objects.filter(teacher=teacher, date=day)
        .select_related("section", "subject")
        .prefetch_related("images")
        .order_by("period")
    )
    # Mark which timetable slots already have attendance taken.
    taken = {(s.section_id, s.subject_id, s.period): s for s in sessions}
    for slot in slots:
        slot.session = taken.get((slot.section_id, slot.subject_id, slot.period))
    return slots, sessions


@teacher_required
def teacher_dashboard(request):
    teacher = getattr(request.user, "teacher_profile", None)
    day = _requested_date(request)

    if teacher is None:
        return render(request, "attendance/dashboard.html", {"teacher": None, "day": day})

    form = SessionDetailsForm(teacher=teacher, initial={"date": day})

    if request.method == "POST":
        form = SessionDetailsForm(
            request.POST, request.FILES, teacher=teacher
        )
        if form.is_valid():
            session = _create_session(request, teacher, form)
            # Straight to the session page, which starts processing on arrival.
            return redirect("attendance:session_detail", pk=session.pk)
        messages.error(request, "Check the highlighted fields and try again.")
        day = form.data.get("date") and parse_date(form.data["date"]) or day

    slots, sessions = _day_context(teacher, day)
    return render(
        request,
        "attendance/dashboard.html",
        {
            "teacher": teacher,
            "form": form,
            "day": day,
            "is_today": day == timezone.localdate(),
            "slots": slots,
            "sessions": sessions,
            "assignment_map": _assignment_map(teacher),
            "restricted": settings.RESTRICT_TEACHER_TO_ASSIGNMENTS,
            "min_images": settings.MIN_SESSION_IMAGES,
            "max_images": settings.MAX_SESSION_IMAGES,
        },
    )


def _assignment_map(teacher):
    """Rows the browser uses to narrow section/subject as the branch changes."""
    if settings.RESTRICT_TEACHER_TO_ASSIGNMENTS:
        return [
            {
                "department": assignment.section.department_id,
                "year": assignment.section.year,
                "section": assignment.section_id,
                "subject": assignment.subject_id,
            }
            for assignment in teacher.assignments.select_related("section")
        ]

    # Open policy: every active section, paired with its own branch's subjects.
    # A subject is tied to a branch in the data model but not to a year, so all
    # of the branch's subjects are offered for each of its sections.
    subjects_by_department: dict[int, list[int]] = {}
    for subject_id, department_id in Subject.objects.values_list("id", "department_id"):
        subjects_by_department.setdefault(department_id, []).append(subject_id)

    return [
        {
            "department": section.department_id,
            "year": section.year,
            "section": section.id,
            "subject": subject_id,
        }
        for section in ClassSection.objects.filter(is_active=True)
        for subject_id in subjects_by_department.get(section.department_id, [])
    ]


@transaction.atomic
def _create_session(request, teacher, form):
    data = form.cleaned_data
    session = AttendanceSession.objects.create(
        section=data["section"],
        subject=data["subject"],
        teacher=teacher,
        period=data["period"],
        date=data["date"],
        status=SessionStatus.DRAFT,
    )
    stored = _store_images(session, data["images"])
    logger.info(
        "session %s created by %s: %s images stored for %s/%s period %s on %s",
        session.pk, request.user.username, stored, session.section.name,
        session.subject.code, session.period, session.date,
    )
    messages.success(
        request,
        f"Draft saved for {session.section.name} · {session.subject.code} · "
        f"period {session.period} with {stored} photo{'s' if stored != 1 else ''}.",
    )
    return session


def _store_images(session, files) -> int:
    """Re-encode each upload and attach it to the session."""
    start_order = session.images.count()
    for index, uploaded in enumerate(files):
        normalised = normalise(uploaded)
        SessionImage.objects.create(
            session=session,
            image=normalised.file,
            width=normalised.width,
            height=normalised.height,
            order=start_order + index,
        )
    return len(files)


def _visible_session(request, pk) -> AttendanceSession:
    """Permission lives in the queryset — a URL edit can't reach another class."""
    return get_object_or_404(
        AttendanceSession.objects.visible_to(request.user).select_related(
            "section", "subject", "teacher__user"
        ),
        pk=pk,
    )


@teacher_required
def session_detail(request, pk):
    """Results for one session, and where processing is kicked off."""
    session = _visible_session(request, pk)
    images = list(session.images.all())
    records = list(
        session.records.select_related("student", "matched_face").order_by(
            "student__roll_no"
        )
    )
    unknown_faces = [
        face
        for image in images
        for face in image.faces.filter(matched_student__isnull=True)
    ]

    return render(
        request,
        "attendance/session_detail.html",
        {
            "session": session,
            "images": images,
            "records": records,
            "present_records": [r for r in records if r.status == AttendanceStatus.PRESENT],
            "absent_records": [r for r in records if r.status == AttendanceStatus.ABSENT],
            "unknown_faces": unknown_faces,
            "unenrolled": session.unenrolled_roster(),
            "roster": session.roster(),
            "counts": session.counts(),
            "needs_processing": any(i.status == ImageStatus.PENDING for i in images),
            "processed": session.last_processed_at is not None,
        },
    )


@teacher_required
@require_POST
def process_session_view(request, pk):
    """Run the pipeline. Blocking, and answers in JSON for the progress panel."""
    session = _visible_session(request, pk)
    if session.is_confirmed:
        return JsonResponse(
            {"ok": False, "code": "CONFIRMED",
             "message": "This session is already confirmed and can't be reprocessed."},
            status=400,
        )

    try:
        report = process_session(session)
    except PipelineError as exc:
        logger.warning("session %s could not be processed: %s", session.pk, exc.message)
        return JsonResponse({"ok": False, "code": exc.code, "message": exc.message})

    counts = session.counts()
    return JsonResponse(
        {
            "ok": True,
            "message": (
                f"Found {report.faces_detected} faces and recognised "
                f"{report.faces_matched} of them."
            ),
            "report": {
                "images": report.images,
                "faces_detected": report.faces_detected,
                "faces_matched": report.faces_matched,
                "faces_unknown": report.faces_unknown,
                "skipped_too_small": report.skipped_too_small,
                "present": counts["present"],
                "absent": counts["absent"],
                "elapsed_seconds": round(report.elapsed_seconds, 2),
                "failed_images": report.failed_images,
            },
        }
    )


@teacher_required
def session_status(request, pk):
    """Per-image progress, polled while the processing request is in flight."""
    session = _visible_session(request, pk)
    images = list(session.images.all())
    done = sum(1 for image in images if image.status in (ImageStatus.DONE, ImageStatus.FAILED))
    current = next(
        (index + 1 for index, image in enumerate(images)
         if image.status == ImageStatus.PROCESSING),
        min(done + 1, len(images)) or 0,
    )
    return JsonResponse(
        {
            "total": len(images),
            "done": done,
            "current": current,
            "finished": done == len(images) and bool(images),
            "images": [
                {
                    "order": image.order + 1,
                    "status": image.status,
                    "faces_detected": image.faces_detected,
                    "faces_matched": image.faces_matched,
                    "error": image.error_message,
                }
                for image in images
            ],
        }
    )


@teacher_required
def add_images(request, pk):
    """Attach more photos to a draft the teacher already started."""
    session = get_object_or_404(
        AttendanceSession.objects.visible_to(request.user), pk=pk
    )
    if not session.is_draft:
        messages.error(request, "This session is confirmed — its photos can't be changed.")
        return redirect(f"{reverse('attendance:dashboard')}?date={session.date:%Y-%m-%d}")

    if request.method == "POST":
        form = AddImagesForm(request.POST, request.FILES, session=session)
        if form.is_valid():
            stored = _store_images(session, form.cleaned_data["images"])
            logger.info("session %s: %s more images added", session.pk, stored)
            messages.success(
                request,
                f"Added {stored} photo{'s' if stored != 1 else ''} — reprocessing the session.",
            )
        else:
            for error in form.errors.get("images", []):
                messages.error(request, error)
    return redirect("attendance:session_detail", pk=session.pk)


@teacher_required
@require_POST
def override_record(request, pk):
    """Mark one student present or absent by hand."""
    session = _visible_session(request, pk)
    try:
        record = set_record_status(
            session,
            int(request.POST.get("student", 0)),
            request.POST.get("status", ""),
            request.user,
            request.POST.get("reason", ""),
        )
    except (OverrideError, ValueError) as exc:
        messages.error(request, str(exc) or "That change could not be applied.")
    else:
        messages.success(
            request,
            f"{record.student.name} marked {record.get_status_display().lower()} by hand.",
        )
    return redirect("attendance:session_detail", pk=session.pk)


@teacher_required
@require_POST
def assign_unknown_face(request, pk):
    """Put a name to a face the matcher could not place."""
    session = _visible_session(request, pk)
    try:
        face = assign_face(
            session,
            int(request.POST.get("face", 0)),
            int(request.POST.get("student", 0)),
            request.user,
        )
    except (OverrideError, ValueError) as exc:
        messages.error(request, str(exc) or "That face could not be assigned.")
    else:
        messages.success(
            request, f"Face assigned to {face.matched_student.name}, now marked present."
        )
    return redirect("attendance:session_detail", pk=session.pk)


@teacher_required
@require_POST
def confirm_session(request, pk):
    """Commit the register and send the attendance notice."""
    session = _visible_session(request, pk)
    try:
        confirm(session, request.user)
    except OverrideError as exc:
        messages.error(request, str(exc))
        return redirect("attendance:session_detail", pk=session.pk)

    counts = session.counts()
    if send_session_summary(session):
        messages.success(
            request,
            f"Attendance confirmed — {counts['present']} present, {counts['absent']} "
            f"absent. The notice has been emailed.",
        )
    else:
        messages.warning(
            request,
            f"Attendance confirmed — {counts['present']} present, {counts['absent']} "
            f"absent. The notice could not be emailed: {session.email_error}",
        )
    return redirect("attendance:session_detail", pk=session.pk)


@teacher_required
def delete_session(request, pk):
    """Discard a draft — used when a teacher picks the wrong class."""
    session = get_object_or_404(
        AttendanceSession.objects.visible_to(request.user), pk=pk
    )
    day = session.date
    if request.method != "POST":
        return redirect(f"{reverse('attendance:dashboard')}?date={day:%Y-%m-%d}")
    if session.is_confirmed:
        messages.error(request, "Confirmed attendance can't be deleted.")
    else:
        label = f"{session.section.name} · {session.subject.code} · period {session.period}"
        session.delete()
        logger.info("draft session %s discarded by %s", pk, request.user.username)
        messages.success(request, f"Discarded the draft for {label}.")
    return redirect(f"{reverse('attendance:dashboard')}?date={day:%Y-%m-%d}")
