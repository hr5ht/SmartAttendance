"""Image intake and quality metrics.

Anything a browser uploads passes through here before it is stored or fed to
the detector: validated, rotated upright, downscaled, stripped of EXIF and
re-encoded. Decoding happens once — callers get the pixels back as a BGR array
so the CV step doesn't re-open the file.
"""
from __future__ import annotations

import io
from dataclasses import dataclass

import cv2
import numpy as np
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from PIL import Image, ImageOps, UnidentifiedImageError


@dataclass
class NormalisedImage:
    """A re-encoded JPEG plus its pixels, ready for an ImageField or the detector."""

    file: ContentFile
    array: np.ndarray  # BGR uint8, what OpenCV and InsightFace expect
    width: int
    height: int


def validate_upload(uploaded) -> None:
    """Reject anything that isn't a plausible image before we decode it."""
    if uploaded.size > settings.MAX_UPLOAD_SIZE_BYTES:
        raise ValidationError(
            f"“{uploaded.name}” is {uploaded.size / 1024 / 1024:.1f} MB. "
            f"The limit is {settings.MAX_UPLOAD_SIZE_MB} MB — take the photo at a "
            f"lower resolution or compress it first."
        )
    content_type = (getattr(uploaded, "content_type", "") or "").lower()
    if content_type not in settings.ALLOWED_IMAGE_CONTENT_TYPES:
        allowed = ", ".join(
            t.split("/")[-1].upper() for t in settings.ALLOWED_IMAGE_CONTENT_TYPES
        )
        raise ValidationError(
            f"“{uploaded.name}” is not a supported image ({content_type or 'unknown type'}). "
            f"Use {allowed}."
        )


def normalise(uploaded) -> NormalisedImage:
    """Validate, rotate upright, downscale, strip EXIF and re-encode as JPEG."""
    validate_upload(uploaded)

    uploaded.seek(0)
    try:
        image = Image.open(uploaded)
        image.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise ValidationError(
            f"“{uploaded.name}” could not be read as an image. It may be corrupt — "
            f"try taking the photo again."
        ) from exc

    # exif_transpose applies the orientation tag; saving a fresh copy drops EXIF.
    image = ImageOps.exif_transpose(image)
    if image.mode != "RGB":
        image = image.convert("RGB")

    limit = settings.MAX_IMAGE_DIMENSION
    if max(image.size) > limit:
        image.thumbnail((limit, limit), Image.LANCZOS)

    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=settings.REENCODE_JPEG_QUALITY, optimize=True)
    buffer.seek(0)

    return NormalisedImage(
        # upload_to replaces this name with a UUID.
        file=ContentFile(buffer.read(), name="upload.jpg"),
        array=cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR),
        width=image.width,
        height=image.height,
    )


# ---------------------------------------------------------------------------
# Quality metrics — used to reject unusable enrollment captures.
# ---------------------------------------------------------------------------

def laplacian_variance(image_bgr: np.ndarray) -> float:
    """Focus measure: low variance means the crop is blurred or out of focus."""
    if image_bgr.size == 0:
        return 0.0
    grey = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(grey, cv2.CV_64F).var())


def mean_brightness(image_bgr: np.ndarray) -> float:
    """Mean luma, 0–255. Catches photos taken in the dark or blown out."""
    if image_bgr.size == 0:
        return 0.0
    return float(cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY).mean())


def crop(image_bgr: np.ndarray, bbox, pad: float = 0.0) -> np.ndarray:
    """Clamped crop of a bounding box, optionally padded by a fraction of its size."""
    height, width = image_bgr.shape[:2]
    x1, y1, x2, y2 = bbox
    if pad:
        dx, dy = (x2 - x1) * pad, (y2 - y1) * pad
        x1, y1, x2, y2 = x1 - dx, y1 - dy, x2 + dx, y2 + dy
    x1 = max(0, int(x1)); y1 = max(0, int(y1))
    x2 = min(width, int(x2)); y2 = min(height, int(y2))
    if x2 <= x1 or y2 <= y1:
        return np.zeros((0, 0, 3), dtype=np.uint8)
    return image_bgr[y1:y2, x1:x2]
