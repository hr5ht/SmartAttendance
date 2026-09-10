from __future__ import annotations

from django.conf import settings


def navigation(request):
    """Expose role flags and branding to every template."""
    user = getattr(request, "user", None)
    authenticated = bool(user and user.is_authenticated)
    return {
        "institution_name": settings.INSTITUTION_NAME,
        "is_admin_role": authenticated and user.is_admin_role,
        "is_teacher_role": authenticated and user.is_teacher_role,
        "is_student_role": authenticated and user.is_student_role,
    }
