"""Fetch the InsightFace model pack so the app can start recognising faces."""
from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.face.services import get_engine


class Command(BaseCommand):
    help = "Download and warm up the configured face model pack (default buffalo_l)."

    def handle(self, *args, **options):
        engine = get_engine()
        self.stdout.write(
            f"Preparing '{settings.FACE_MODEL_NAME}' in {settings.FACE_MODEL_ROOT} "
            f"(first run downloads ~280 MB)…"
        )
        try:
            # FaceAnalysis downloads the pack itself when the directory is absent.
            engine.preload(download=True)
        except Exception as exc:  # noqa: BLE001 — surfaced verbatim to the operator
            raise CommandError(f"Could not prepare the face models: {exc}") from exc
        self.stdout.write(self.style.SUCCESS(f"Ready: {engine.model_dir}"))
