"""Upload path helpers — every stored file gets a UUID name under MEDIA_ROOT."""
from __future__ import annotations

import uuid
from pathlib import Path


def _uuid_name(filename: str, default_suffix: str = ".jpg") -> str:
    suffix = Path(filename or "").suffix.lower()
    if suffix not in {".jpg", ".jpeg", ".png", ".webp"}:
        suffix = default_suffix
    return f"{uuid.uuid4().hex}{suffix}"


def enrollment_image_path(instance, filename: str) -> str:
    return f"enrollments/{instance.student_id}/{_uuid_name(filename)}"


def session_image_path(instance, filename: str) -> str:
    return f"sessions/{instance.session_id}/{_uuid_name(filename)}"


def face_crop_path(instance, filename: str) -> str:
    return f"crops/{instance.session_image.session_id}/{_uuid_name(filename)}"
