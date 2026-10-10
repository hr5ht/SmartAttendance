"""Role gates. Views use these; querysets do the object-level filtering."""
from __future__ import annotations

from functools import wraps

from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied


def _role_required(predicate):
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            user = request.user
            if not user.is_authenticated:
                return redirect_to_login(request.get_full_path())
            if not predicate(user):
                raise PermissionDenied("Your role does not have access to this page.")
            return view_func(request, *args, **kwargs)

        return wrapper

    return decorator


admin_required = _role_required(lambda u: u.is_admin_role)
teacher_required = _role_required(lambda u: u.is_teacher_role or u.is_admin_role)
student_required = _role_required(lambda u: u.is_student_role)

