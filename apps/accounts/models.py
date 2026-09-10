from __future__ import annotations

import secrets
import string

from django.contrib.auth.models import AbstractUser, UserManager as DjangoUserManager
from django.db import models
from django.utils import timezone

# Ambiguous glyphs are removed so printed credential slips can be typed reliably.
PASSWORD_ALPHABET = "".join(
    c for c in (string.ascii_letters + string.digits) if c not in "O0oIl1"
)


def generate_password(length: int = 10) -> str:
    """Return a random initial password from an unambiguous alphabet."""
    return "".join(secrets.choice(PASSWORD_ALPHABET) for _ in range(length))


class Role(models.TextChoices):
    ADMIN = "ADMIN", "Administrator"
    TEACHER = "TEACHER", "Teacher"
    STUDENT = "STUDENT", "Student"


class UserManager(DjangoUserManager):
    def create_superuser(self, username, email=None, password=None, **extra_fields):
        extra_fields.setdefault("role", Role.ADMIN)
        extra_fields.setdefault("must_change_password", False)
        return super().create_superuser(username, email, password, **extra_fields)


class User(AbstractUser):
    """Custom user carrying the role and the forced-password-change flag."""

    role = models.CharField(max_length=16, choices=Role.choices, default=Role.STUDENT)
    must_change_password = models.BooleanField(
        default=False,
        help_text="When true the user is redirected to the password change page.",
    )
    password_changed_at = models.DateTimeField(null=True, blank=True)
    phone = models.CharField(max_length=20, blank=True)

    objects = UserManager()

    class Meta:
        ordering = ["username"]

    def __str__(self) -> str:
        return f"{self.get_full_name() or self.username} ({self.get_role_display()})"

    # -- role helpers -----------------------------------------------------
    @property
    def is_admin_role(self) -> bool:
        return self.role == Role.ADMIN or self.is_superuser

    @property
    def is_teacher_role(self) -> bool:
        return self.role == Role.TEACHER

    @property
    def is_student_role(self) -> bool:
        return self.role == Role.STUDENT

    @property
    def display_name(self) -> str:
        return self.get_full_name() or self.username

    def set_initial_password(self, raw_password: str) -> None:
        """Set a password that the user must change on first login."""
        self.set_password(raw_password)
        self.must_change_password = True
        self.password_changed_at = None

    def mark_password_changed(self) -> None:
        self.must_change_password = False
        self.password_changed_at = timezone.now()
