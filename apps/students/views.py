"""Admin-side student management: enrollment, credentials, resets."""
from __future__ import annotations

import csv
import logging

from django.contrib import messages
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.academics.models import ClassSection
from apps.accounts.models import Role, User, generate_password
from apps.accounts.permissions import admin_required
from apps.students.forms import StudentForm, StudentSearchForm, username_for
from apps.students.models import EnrollmentResetRequest, ResetRequestStatus, Student

logger = logging.getLogger("apps.students")

# Generated passwords are never stored in the clear, so the only chance to hand
# them out is right after they are issued. They are held in the admin's session
# until printed/exported, then cleared.
ISSUED_KEY = "issued_credentials"


def _remember_credentials(request, student: Student, password: str) -> None:
    issued = request.session.get(ISSUED_KEY, [])
    issued = [row for row in issued if row["roll_no"] != student.roll_no]
    issued.append(
        {
            "roll_no": student.roll_no,
            "name": student.name,
            "section": student.section.name,
            "username": student.user.username,
            "password": password,
            "issued_at": timezone.now().isoformat(timespec="seconds"),
        }
    )
    request.session[ISSUED_KEY] = issued
    request.session.modified = True


@admin_required
def student_list(request):
    form = StudentSearchForm(request.GET or None)
    students = Student.objects.select_related("section", "user").order_by(
        "section__name", "roll_no"
    )

    if form.is_valid():
        query = form.cleaned_data.get("q")
        if query:
            students = students.filter(
                Q(roll_no__icontains=query)
                | Q(name__icontains=query)
                | Q(user__username__icontains=query)
                | Q(email__icontains=query)
            )
        section = form.cleaned_data.get("section")
        if section:
            students = students.filter(section=section)
        enrolled = form.cleaned_data.get("enrolled")
        if enrolled == "yes":
            students = students.filter(face_enrolled=True)
        elif enrolled == "no":
            students = students.filter(face_enrolled=False)

    return render(
        request,
        "students/list.html",
        {
            "students": students,
            "form": form,
            "sections": ClassSection.objects.filter(is_active=True),
            "issued_count": len(request.session.get(ISSUED_KEY, [])),
            "pending_resets": EnrollmentResetRequest.objects.filter(
                status=ResetRequestStatus.PENDING
            ).count(),
        },
    )


@admin_required
@transaction.atomic
def student_create(request):
    """Create the student record and generate their login."""
    form = StudentForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        student = form.save(commit=False)
        username = username_for(student.roll_no)
        password = generate_password()

        user = User(
            username=username,
            role=Role.STUDENT,
            email=student.email or "",
            first_name=student.name.split()[0],
            last_name=" ".join(student.name.split()[1:]),
        )
        user.set_initial_password(password)
        user.save()

        student.user = user
        student.save()

        _remember_credentials(request, student, password)
        logger.info("student %s created with username %s", student.roll_no, username)
        messages.success(
            request,
            f"{student.name} added. Username {username} — print the credentials "
            f"before you leave this page, the password can't be shown again.",
        )
        return redirect("students:credentials")

    return render(request, "students/form.html", {"form": form, "student": None})


@admin_required
def student_edit(request, pk):
    student = get_object_or_404(Student.objects.select_related("user"), pk=pk)
    form = StudentForm(request.POST or None, instance=student)
    if request.method == "POST" and form.is_valid():
        student = form.save()
        user = student.user
        user.email = student.email or ""
        user.first_name = student.name.split()[0]
        user.last_name = " ".join(student.name.split()[1:])
        user.save(update_fields=["email", "first_name", "last_name"])
        messages.success(request, f"Updated {student.name}.")
        return redirect("students:list")
    return render(request, "students/form.html", {"form": form, "student": student})


@admin_required
def student_detail(request, pk):
    student = get_object_or_404(
        Student.objects.select_related("section", "user").prefetch_related("face_enrollments"),
        pk=pk,
    )
    return render(
        request,
        "students/detail.html",
        {
            "student": student,
            "enrollments": student.face_enrollments.all(),
            "reset_requests": student.reset_requests.all()[:5],
        },
    )


@admin_required
def reset_password(request, pk):
    """Issue a fresh initial password; the student must change it at next login."""
    student = get_object_or_404(Student.objects.select_related("user"), pk=pk)
    if request.method != "POST":
        return redirect("students:detail", pk=pk)

    password = generate_password()
    user = student.user
    user.set_initial_password(password)
    user.save()

    _remember_credentials(request, student, password)
    logger.info("password reset for %s by %s", student.roll_no, request.user.username)
    messages.success(
        request,
        f"New password issued for {student.name}. It's on the credentials page — "
        f"print it now, it can't be shown again.",
    )
    return redirect("students:credentials")


@admin_required
def credentials(request):
    """Printable slips for the credentials issued in this session."""
    issued = request.session.get(ISSUED_KEY, [])
    if request.method == "POST" and request.POST.get("action") == "clear":
        request.session[ISSUED_KEY] = []
        messages.success(request, "Cleared the issued credentials list.")
        return redirect("students:list")
    return render(request, "students/credentials.html", {"issued": issued})


@admin_required
def credentials_csv(request):
    issued = request.session.get(ISSUED_KEY, [])
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = (
        f'attachment; filename="credentials-{timezone.localdate():%Y%m%d}.csv"'
    )
    writer = csv.writer(response)
    writer.writerow(["Roll number", "Name", "Section", "Username", "Initial password"])
    for row in issued:
        writer.writerow(
            [row["roll_no"], row["name"], row["section"], row["username"], row["password"]]
        )
    return response


@admin_required
def reset_requests(request):
    return render(
        request,
        "students/resets.html",
        {
            "requests": EnrollmentResetRequest.objects.select_related(
                "student__section"
            ).order_by("status", "-created_at"),
            "flagged": Student.objects.filter(flagged_for_review=True).select_related("section"),
        },
    )


@admin_required
@transaction.atomic
def decide_reset(request, pk):
    """Approve (clearing the samples) or reject a student's reset request."""
    reset = get_object_or_404(
        EnrollmentResetRequest.objects.select_related("student"), pk=pk
    )
    if request.method != "POST":
        return redirect("students:resets")

    if not reset.is_pending:
        messages.info(request, "That request has already been decided.")
        return redirect("students:resets")

    decision = request.POST.get("decision")
    student = reset.student

    if decision == "approve":
        removed = student.face_enrollments.count()
        student.face_enrollments.all().delete()
        student.face_enrolled = False
        student.enrollment_completed_at = None
        student.save(update_fields=["face_enrolled", "enrollment_completed_at"])
        reset.status = ResetRequestStatus.APPROVED
        logger.info(
            "reset approved for %s by %s (%s samples removed)",
            student.roll_no, request.user.username, removed,
        )
        messages.success(
            request, f"Cleared {removed} samples — {student.name} can redo their face setup."
        )
    else:
        reset.status = ResetRequestStatus.REJECTED
        messages.success(request, f"Rejected the reset request from {student.name}.")

    reset.decided_at = timezone.now()
    reset.decided_by = request.user
    reset.decision_note = request.POST.get("note", "").strip()[:500]
    reset.save()
    return redirect("students:resets")


@admin_required
def clear_flag(request, pk):
    """Dismiss the impostor flag after an administrator has looked into it."""
    student = get_object_or_404(Student, pk=pk)
    if request.method == "POST":
        student.flagged_for_review = False
        student.flag_reason = ""
        student.save(update_fields=["flagged_for_review", "flag_reason"])
        logger.info("flag cleared for %s by %s", student.roll_no, request.user.username)
        messages.success(request, f"Cleared the review flag on {student.name}.")
    return redirect("students:resets")
