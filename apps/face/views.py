from __future__ import annotations

import logging

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.permissions import student_required
from apps.face.enrollment import process_capture, progress, steps
from apps.face.imaging import normalise
from apps.students.models import EnrollmentResetRequest, ResetRequestStatus

logger = logging.getLogger("apps.face")


def _student(request):
    return getattr(request.user, "student_profile", None)


@student_required
def enroll(request):
    """The guided capture wizard. Works with the camera or a file picker."""
    student = _student(request)
    if student is None:
        return render(request, "face/enroll.html", {"student": None})

    state = progress(student)
    return render(
        request,
        "face/enroll.html",
        {
            "student": student,
            "steps": steps(),
            "progress": state,
            "min_face_pixels": settings.MIN_ENROLL_FACE_PIXELS,
            "pending_reset": student.reset_requests.filter(
                status=ResetRequestStatus.PENDING
            ).first(),
        },
    )


@student_required
@require_POST
def capture(request):
    """Receive one JPEG, run the quality gates, answer in JSON."""
    student = _student(request)
    if student is None:
        return JsonResponse(
            {"ok": False, "code": "NO_PROFILE",
             "message": "Your account has no student record. Ask an administrator."},
            status=400,
        )

    try:
        step_index = int(request.POST.get("step", ""))
    except ValueError:
        return JsonResponse(
            {"ok": False, "code": "BAD_STEP", "message": "Missing capture step."}, status=400
        )

    uploaded = request.FILES.get("image")
    if uploaded is None:
        return JsonResponse(
            {"ok": False, "code": "NO_IMAGE",
             "message": "No photo reached the server. Check your connection and try again."},
            status=400,
        )

    try:
        normalised = normalise(uploaded)
    except ValidationError as exc:
        return JsonResponse(
            {"ok": False, "code": "BAD_IMAGE", "message": " ".join(exc.messages)}, status=400
        )

    result = process_capture(student, step_index, normalised)
    student.refresh_from_db()
    state = progress(student)

    payload = {
        "ok": result.ok,
        "code": result.code,
        "message": result.message,
        "progress": {"done": state["done"], "total": state["total"]},
        "completed_steps": state["completed_steps"],
        "complete": state["complete"],
        "flagged": student.flagged_for_review,
        "detail": result.detail,
    }
    if state["next_step"]:
        payload["next_step"] = {
            "index": state["next_step"]["index"],
            "title": state["next_step"]["title"],
            "hint": state["next_step"]["hint"],
        }
    if result.ok and state["complete"]:
        payload["message"] = "All done — your face setup is complete."
    # A rejected capture is a normal outcome, not a server error: 200 with ok=false.
    return JsonResponse(payload)


@student_required
@require_POST
def request_reset(request):
    """Ask an administrator to clear the stored samples so they can start over."""
    student = _student(request)
    if student is None:
        return redirect("accounts:student_dashboard")

    if student.reset_requests.filter(status=ResetRequestStatus.PENDING).exists():
        messages.info(request, "You already have a reset request waiting for approval.")
    else:
        EnrollmentResetRequest.objects.create(
            student=student, reason=request.POST.get("reason", "").strip()[:500]
        )
        logger.info("reset requested by %s", student.roll_no)
        messages.success(
            request,
            "Reset requested. An administrator will review it — you'll be able to "
            "redo your face setup once they approve.",
        )
    return redirect("accounts:student_dashboard")
