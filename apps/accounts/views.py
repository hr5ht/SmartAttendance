from __future__ import annotations

from django.contrib import messages
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.shortcuts import redirect, render
from django.urls import reverse, reverse_lazy

from apps.academics.models import ClassSection
from apps.accounts.forms import ForcePasswordChangeForm, LoginForm
from apps.accounts.permissions import admin_required, student_required
from apps.attendance.models import AttendanceSession
from apps.students.models import EnrollmentResetRequest, ResetRequestStatus, Student


def home(request):
    if request.user.is_authenticated:
        return redirect("accounts:post_login")
    return redirect("accounts:login")


class LoginView(auth_views.LoginView):
    template_name = "accounts/login.html"
    authentication_form = LoginForm
    redirect_authenticated_user = True

    def get_success_url(self):
        return self.get_redirect_url() or reverse("accounts:post_login")


class LogoutView(auth_views.LogoutView):
    next_page = reverse_lazy("accounts:login")


@login_required
def post_login(request):
    """Send each role to the right home page."""
    user = request.user
    if user.must_change_password:
        return redirect("accounts:force_password_change")
    if user.is_admin_role:
        return redirect("accounts:admin_dashboard")
    if user.is_teacher_role:
        return redirect("attendance:dashboard")
    return redirect("accounts:student_dashboard")


class ForcePasswordChangeView(auth_views.PasswordChangeView):
    template_name = "accounts/password_change.html"
    form_class = ForcePasswordChangeForm
    success_url = reverse_lazy("accounts:post_login")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["forced"] = self.request.user.must_change_password
        return context

    def form_valid(self, form):
        response = super().form_valid(form)
        user = self.request.user
        user.mark_password_changed()
        user.save(update_fields=["must_change_password", "password_changed_at"])
        messages.success(self.request, "Password updated. You're all set.")
        return response


@admin_required
def admin_dashboard(request):
    students = Student.objects.select_related("section")
    sections = (
        ClassSection.objects.filter(is_active=True)
        .annotate(
            student_count=Count("students", filter=Q(students__is_active=True), distinct=True),
            enrolled_count=Count(
                "students",
                filter=Q(students__is_active=True, students__face_enrolled=True),
                distinct=True,
            ),
        )
        .order_by("name")
    )
    context = {
        "total_students": students.count(),
        "enrolled_students": students.filter(face_enrolled=True).count(),
        "flagged_students": students.filter(flagged_for_review=True).count(),
        "pending_resets": EnrollmentResetRequest.objects.filter(
            status=ResetRequestStatus.PENDING
        ).count(),
        "sections": sections,
        "recent_sessions": AttendanceSession.objects.select_related(
            "section", "subject", "teacher__user"
        )[:8],
    }
    return render(request, "accounts/dashboard_admin.html", context)


@student_required
def student_dashboard(request):
    student = getattr(request.user, "student_profile", None)
    context = {
        "student": student,
        "pending_reset": (
            student.reset_requests.filter(status=ResetRequestStatus.PENDING).first()
            if student
            else None
        ),
    }
    return render(request, "accounts/dashboard_student.html", context)
