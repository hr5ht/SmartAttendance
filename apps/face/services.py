"""Face detection and embedding, behind a swappable interface.

Nothing outside this module imports insightface. To swap the model, implement
``FaceEngine`` and point ``get_engine`` at it.
"""
from __future__ import annotations

import logging
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from django.conf import settings

logger = logging.getLogger("apps.face")


class FaceEngineError(RuntimeError):
    """Base class for problems the user needs to be told about."""


class ModelFilesMissing(FaceEngineError):
    """The ONNX weights aren't on disk — the app can't do anything useful."""


@dataclass(frozen=True)
class Detection:
    """One detected face: where it is, how confident, and what it looks like."""

    bbox: tuple[float, float, float, float]
    det_score: float
    embedding: np.ndarray          # 512-d, unit length
    keypoints: np.ndarray | None = None

    @property
    def width(self) -> float:
        return self.bbox[2] - self.bbox[0]

    @property
    def height(self) -> float:
        return self.bbox[3] - self.bbox[1]


class FaceEngine(ABC):
    """The only surface the rest of the app is allowed to depend on."""

    @abstractmethod
    def detect(self, image_bgr: np.ndarray) -> list[Detection]:
        """Return every face found, largest first."""

    @abstractmethod
    def preload(self) -> None:
        """Load the model now rather than on first use."""


class InsightFaceEngine(FaceEngine):
    """SCRFD detection + ArcFace embeddings (buffalo_l) on CPU via onnxruntime."""

    def __init__(self, name=None, root=None, det_size=None, providers=None):
        self.name = name or settings.FACE_MODEL_NAME
        self.root = root or settings.FACE_MODEL_ROOT
        self.det_size = det_size or settings.FACE_DET_SIZE
        self.providers = providers or settings.FACE_MODEL_PROVIDERS
        self._app = None
        self._lock = threading.Lock()

    @property
    def model_dir(self) -> Path:
        # insightface nests its downloads under <root>/models/<name>.
        return Path(self.root) / "models" / self.name

    def _ensure_loaded(self):
        """Load once, under a lock — requests are served by several threads."""
        if self._app is not None:
            return self._app
        with self._lock:
            if self._app is not None:
                return self._app
            if not self.model_dir.is_dir() or not any(self.model_dir.glob("*.onnx")):
                raise ModelFilesMissing(
                    f"Face recognition models are not installed. Expected "
                    f"'{self.name}' in {self.model_dir}. Run "
                    f"'python manage.py download_face_models' and try again."
                )
            import insightface  # imported lazily so a missing model can't break startup

            app = insightface.app.FaceAnalysis(
                name=self.name,
                root=self.root,
                providers=list(self.providers),
                # Age/gender and dense landmarks cost time we don't need.
                allowed_modules=["detection", "recognition"],
            )
            app.prepare(ctx_id=-1, det_size=(self.det_size, self.det_size))
            logger.info(
                "face engine ready: %s det_size=%s providers=%s",
                self.name, self.det_size, self.providers,
            )
            self._app = app
        return self._app

    def preload(self) -> None:
        self._ensure_loaded()

    def detect(self, image_bgr: np.ndarray) -> list[Detection]:
        app = self._ensure_loaded()
        faces = app.get(image_bgr)
        detections = [
            Detection(
                bbox=tuple(float(v) for v in face.bbox),
                det_score=float(face.det_score),
                # normed_embedding is already unit length; copy it off the model's buffer.
                embedding=np.asarray(face.normed_embedding, dtype=np.float32).copy(),
                keypoints=(
                    np.asarray(face.kps, dtype=np.float32).copy()
                    if getattr(face, "kps", None) is not None else None
                ),
            )
            for face in faces
        ]
        detections.sort(key=lambda d: d.width * d.height, reverse=True)
        return detections


_engine: FaceEngine | None = None
_engine_lock = threading.Lock()


def get_engine() -> FaceEngine:
    """Process-wide engine. Loading the ONNX graphs takes seconds, so reuse it."""
    global _engine
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                _engine = InsightFaceEngine()
    return _engine


def set_engine(engine: FaceEngine | None) -> None:
    """Swap the engine — used by tests to avoid loading the real model."""
    global _engine
    _engine = engine
