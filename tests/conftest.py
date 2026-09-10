import pytest


@pytest.fixture(autouse=True)
def _media_root(tmp_path, settings):
    """Keep test uploads out of the real MEDIA_ROOT."""
    settings.MEDIA_ROOT = tmp_path / "media"
    settings.MEDIA_ROOT.mkdir(parents=True, exist_ok=True)
    return settings.MEDIA_ROOT


# ---------------------------------------------------------------------------
# Shared academic fixtures
# ---------------------------------------------------------------------------
import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image


@pytest.fixture
def dept(db):
    from apps.academics.models import Department

    return Department.objects.create(code="CSE", name="Computer Science")


@pytest.fixture
def section(dept):
    from apps.academics.models import ClassSection

    return ClassSection.objects.create(department=dept, year=3, section_letter="B")


@pytest.fixture
def other_section(dept):
    from apps.academics.models import ClassSection

    return ClassSection.objects.create(department=dept, year=3, section_letter="A")


@pytest.fixture
def subject(dept):
    from apps.academics.models import Subject

    return Subject.objects.create(code="CS301", name="DBMS", department=dept)


@pytest.fixture
def other_subject(dept):
    from apps.academics.models import Subject

    return Subject.objects.create(code="CS302", name="Operating Systems", department=dept)


@pytest.fixture
def teacher(db, dept, section, subject):
    """A teacher assigned to CSE-3B / CS301 only."""
    from apps.academics.models import Teacher, TeacherAssignment
    from apps.accounts.models import Role, User

    user = User.objects.create_user(
        "t.demo", password="pw-teacher-123", role=Role.TEACHER, first_name="Anita"
    )
    record = Teacher.objects.create(user=user, department=dept, employee_id="EMP-1")
    TeacherAssignment.objects.create(teacher=record, section=section, subject=subject)
    return record


@pytest.fixture
def admin_user(db):
    from apps.accounts.models import Role, User

    return User.objects.create_user("admin1", password="pw-admin-123", role=Role.ADMIN)


def make_photo(name="room.jpg", size=(640, 480), colour=(120, 140, 160)):
    """A real JPEG upload — the intake path decodes it, so bytes must be valid."""
    buffer = io.BytesIO()
    Image.new("RGB", size, colour).save(buffer, format="JPEG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/jpeg")


# ---------------------------------------------------------------------------
# Face engine test doubles
# ---------------------------------------------------------------------------
import numpy as np

from apps.face.services import Detection, FaceEngine


def make_selfie(name="selfie.jpg", size=(600, 800), mean=130, spread=40, seed=0):
    """A textured image: flat colour would fail the blur gate before anything else."""
    rng = np.random.default_rng(seed)
    pixels = rng.integers(
        max(0, mean - spread), min(255, mean + spread), (size[1], size[0], 3), dtype=np.uint8
    )
    buffer = io.BytesIO()
    Image.fromarray(pixels, "RGB").save(buffer, format="JPEG", quality=95)
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/jpeg")


def unit(vector):
    vector = np.asarray(vector, dtype=np.float32)
    return vector / np.linalg.norm(vector)


def random_embedding(seed=0, dim=512):
    """A unit vector standing in for an ArcFace embedding."""
    return unit(np.random.default_rng(seed).normal(size=dim))


class FakeEngine(FaceEngine):
    """Returns whatever detections a test asks for, without loading a model."""

    def __init__(self, detections=None):
        self.detections = detections if detections is not None else []
        self.calls = 0

    def detect(self, image_bgr):
        self.calls += 1
        return list(self.detections)

    def preload(self):
        pass

    def returning(self, embedding=None, det_score=0.9, bbox=(10, 10, 210, 270), count=1):
        """Convenience: replace the queued detections."""
        vector = random_embedding() if embedding is None else embedding
        self.detections = [
            Detection(bbox=bbox, det_score=det_score, embedding=vector) for _ in range(count)
        ]
        return self


@pytest.fixture
def fake_engine():
    """Swap the process-wide engine for a fake, and put it back afterwards."""
    from apps.face import services

    engine = FakeEngine()
    services.set_engine(engine)
    yield engine
    services.set_engine(None)
