from __future__ import annotations

from django.shortcuts import redirect
from django.urls import resolve, reverse

# Views a user with must_change_password may still reach.
EXEMPT_URL_NAMES = {
    "accounts:force_password_change",
    "accounts:logout",
    "accounts:login",
}


class ForcePasswordChangeMiddleware:
    """Redirect users flagged ``must_change_password`` to the change form."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated and user.must_change_password:
            if not self._is_exempt(request):
                return redirect(reverse("accounts:force_password_change"))
        return self.get_response(request)

    @staticmethod
    def _is_exempt(request) -> bool:
        path = request.path
        if path.startswith(("/static/", "/media/", "/admin/")):
            return True
        try:
            match = resolve(path)
        except Exception:
            return True
        return match.view_name in EXEMPT_URL_NAMES
