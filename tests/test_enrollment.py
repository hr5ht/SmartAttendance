"""Face enrollment: quality gates, duplicate and cross-student checks, wizard flow."""
import numpy as np
import pytest
from django.urls import reverse

from apps.accounts.models import Role, User
from apps.face import embeddings as emb
from apps.face.enrollment import process_capture, progress, steps
from apps.face.imaging import normalise
from apps.face.models import FaceEnrollment, PoseLabel
from apps.face.services import Detection, ModelFilesMissing
from apps.students.models import EnrollmentResetRequest, ResetRequestStatus, Student
from tests.conftest import FakeEngine, make_selfie, random_embedding, unit

ENROLL = reverse("face:enroll")
CAPTURE = reverse("face:capture")


@pytest.fixture
def student(db, section):
    user = User.objects.create_user("cse3b001", password="pw-student-123", role=Role.STUDENT)
    return Student.objects.create(
        user=user, roll_no="CSE3B001", name="Aarav Sharma", section=section, email="a@example.edu"
    )


@pytest.fixture
def classmate(db, section):
    user = User.objects.create_user("cse3b002", password="pw-student-123", role=Role.STUDENT)
    return Student.objects.create(
        user=user, roll_no="CSE3B002", name="Diya Nair", section=section
    )


def capture(student, step, engine, embedding=None, **kwargs):
    """Run one capture through the real pipeline with a scripted detection."""
    engine.returning(embedding=embedding, **kwargs)
    return process_capture(student, step, normalise(make_selfie(seed=step + 1)))


# ------------------------------------------------------------------ happy path


def test_five_good_captures_complete_enrollment(student, fake_engine):
    for step in range(5):
        result = capture(student, step, fake_engine, embedding=random_embedding(seed=step))
        assert result.ok, result.message

    student.refresh_from_db()
    assert student.face_enrolled is True
    assert student.enrollment_completed_at is not None
    assert student.face_enrollments.count() == 5
    assert [e.pose for e in student.face_enrollments.order_by("step_index")] == [
        PoseLabel.FRONT, PoseLabel.LEFT, PoseLabel.RIGHT, PoseLabel.UP, PoseLabel.ALT
    ]


def test_not_enrolled_until_every_step_is_done(student, fake_engine):
    for step in range(4):
        assert capture(student, step, fake_engine, embedding=random_embedding(seed=step)).ok
    student.refresh_from_db()
    assert student.face_enrolled is False
    assert progress(student)["next_step"]["index"] == 4

    assert capture(student, 4, fake_engine, embedding=random_embedding(seed=4)).ok
    student.refresh_from_db()
    assert student.face_enrolled is True
    assert progress(student)["complete"] is True


def test_stored_embedding_round_trips_as_float32_blob(student, fake_engine):
    vector = random_embedding(seed=7)
    assert capture(student, 0, fake_engine, embedding=vector).ok

    stored = FaceEnrollment.objects.get().get_embedding()
    assert stored.dtype == np.float32
    assert stored.shape == (512,)
    assert emb.cosine_similarity(stored, vector) == pytest.approx(1.0, abs=1e-5)


# --------------------------------------------------------------- quality gates


def test_no_face_is_rejected(student, fake_engine):
    fake_engine.detections = []
    result = process_capture(student, 0, normalise(make_selfie()))
    assert not result.ok and result.code == "NO_FACE"
    assert "No face found" in result.message
    assert not FaceEnrollment.objects.exists()


def test_two_faces_are_rejected(student, fake_engine):
    result = capture(student, 0, fake_engine, count=2)
    assert result.code == "MULTIPLE_FACES"
    assert "2 faces" in result.message
    assert not FaceEnrollment.objects.exists()


def test_low_detection_score_is_rejected(student, fake_engine, settings):
    settings.MIN_ENROLL_DET_SCORE = 0.65
    result = capture(student, 0, fake_engine, det_score=0.4)
    assert result.code == "LOW_CONFIDENCE"
    assert not FaceEnrollment.objects.exists()


def test_face_smaller_than_the_minimum_is_rejected(student, fake_engine, settings):
    settings.MIN_ENROLL_FACE_PIXELS = 120
    result = capture(student, 0, fake_engine, bbox=(10, 10, 90, 120))  # 80px wide
    assert result.code == "FACE_TOO_SMALL"
    assert "80 pixels" in result.message
    assert "120" in result.message


def test_dark_image_is_rejected(student, fake_engine):
    fake_engine.returning()
    result = process_capture(student, 0, normalise(make_selfie(mean=20, spread=15)))
    assert result.code == "TOO_DARK"


def test_blown_out_image_is_rejected(student, fake_engine):
    fake_engine.returning()
    result = process_capture(student, 0, normalise(make_selfie(mean=250, spread=4)))
    assert result.code == "TOO_BRIGHT"


def test_blurry_image_is_rejected(student, fake_engine):
    # A flat fill has almost no high-frequency detail, which is what blur looks like.
    fake_engine.returning()
    result = process_capture(student, 0, normalise(make_selfie(mean=130, spread=1)))
    assert result.code == "TOO_BLURRY"


def test_missing_model_files_give_a_clear_message(student, monkeypatch):
    from apps.face import services

    class Broken(FakeEngine):
        def detect(self, image_bgr):
            raise ModelFilesMissing("no weights on disk")

    services.set_engine(Broken())
    try:
        result = process_capture(student, 0, normalise(make_selfie()))
    finally:
        services.set_engine(None)
    assert result.code == "MODEL_MISSING"
    assert "administrator" in result.message
    assert not FaceEnrollment.objects.exists()


# --------------------------------------------------- duplicate / impostor gates


def test_resubmitting_the_same_frame_is_rejected(student, fake_engine, settings):
    settings.DUPLICATE_EMBEDDING_THRESHOLD = 0.98
    vector = random_embedding(seed=3)
    assert capture(student, 0, fake_engine, embedding=vector).ok

    result = capture(student, 1, fake_engine, embedding=vector)
    assert result.code == "DUPLICATE"
    assert result.detail["duplicate_of_step"] == 0
    assert FaceEnrollment.objects.count() == 1


def test_a_merely_similar_frame_is_accepted(student, fake_engine, settings):
    settings.DUPLICATE_EMBEDDING_THRESHOLD = 0.98
    base = random_embedding(seed=3)
    assert capture(student, 0, fake_engine, embedding=base).ok

    # Same person, different pose: high similarity but below the duplicate line.
    nudged = unit(base + 0.35 * random_embedding(seed=99))
    assert 0.90 < emb.cosine_similarity(base, nudged) < 0.98
    assert capture(student, 1, fake_engine, embedding=nudged).ok
    assert FaceEnrollment.objects.count() == 2


def test_enrolling_a_classmates_face_is_blocked_and_flagged(
    student, classmate, fake_engine, settings
):
    settings.SIMILARITY_THRESHOLD = 0.45
    classmate_face = random_embedding(seed=11)
    assert capture(classmate, 0, fake_engine, embedding=classmate_face).ok

    result = capture(student, 0, fake_engine, embedding=classmate_face)
    assert result.code == "MATCHES_OTHER_STUDENT"
    assert result.detail["other_student_id"] == classmate.pk

    student.refresh_from_db()
    assert student.flagged_for_review is True
    assert str(classmate.pk) in student.flag_reason
    assert student.face_enrollments.count() == 0


def test_a_student_in_another_section_does_not_block_enrollment(
    student, fake_engine, other_section, settings
):
    """The cross-check is scoped to the section, like the matcher's gallery."""
    outsider_user = User.objects.create_user("cse3a009", password="pw-x-123", role=Role.STUDENT)
    outsider = Student.objects.create(
        user=outsider_user, roll_no="CSE3A009", name="Vikram Rao", section=other_section
    )
    shared = random_embedding(seed=21)
    assert capture(outsider, 0, fake_engine, embedding=shared).ok

    result = capture(student, 0, fake_engine, embedding=shared)
    assert result.ok, result.message


def test_a_half_enrolled_impostor_is_still_caught(student, classmate, fake_engine):
    """The check looks at stored samples, not only fully enrolled students."""
    face = random_embedding(seed=31)
    assert capture(classmate, 0, fake_engine, embedding=face).ok
    classmate.refresh_from_db()
    assert classmate.face_enrolled is False  # only 1 of 5

    assert capture(student, 0, fake_engine, embedding=face).code == "MATCHES_OTHER_STUDENT"


# ------------------------------------------------------------------- retaking


def test_retaking_a_step_replaces_the_stored_sample(student, fake_engine):
    assert capture(student, 0, fake_engine, embedding=random_embedding(seed=1)).ok
    first = FaceEnrollment.objects.get()

    assert capture(student, 0, fake_engine, embedding=random_embedding(seed=2)).ok
    assert FaceEnrollment.objects.count() == 1
    assert FaceEnrollment.objects.get().pk != first.pk


# ----------------------------------------------------------------- HTTP layer


def test_wizard_page_renders_with_progress(client, student, fake_engine):
    client.force_login(student.user)
    response = client.get(ENROLL)
    assert response.status_code == 200
    assert response.context["progress"]["done"] == 0
    assert len(response.context["steps"]) == 5
    body = response.content.decode()
    assert "data-video" in body            # camera path
    assert 'capture="user"' in body        # file-input fallback is always present


def test_capture_endpoint_accepts_and_reports_progress(client, student, fake_engine):
    fake_engine.returning(embedding=random_embedding(seed=5))
    client.force_login(student.user)
    response = client.post(CAPTURE, {"step": 0, "image": make_selfie()})

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["progress"] == {"done": 1, "total": 5}
    assert payload["next_step"]["index"] == 1
    assert payload["complete"] is False


def test_capture_endpoint_reports_rejection_as_200_with_ok_false(client, student, fake_engine):
    fake_engine.detections = []
    client.force_login(student.user)
    response = client.post(CAPTURE, {"step": 0, "image": make_selfie()})

    assert response.status_code == 200          # a bad photo is not a server error
    assert response.json()["ok"] is False
    assert response.json()["code"] == "NO_FACE"


def test_capture_endpoint_rejects_a_non_image(client, student, fake_engine):
    from django.core.files.uploadedfile import SimpleUploadedFile

    client.force_login(student.user)
    response = client.post(
        CAPTURE,
        {"step": 0, "image": SimpleUploadedFile("x.pdf", b"%PDF-1.4", content_type="application/pdf")},
    )
    assert response.status_code == 400
    assert response.json()["code"] == "BAD_IMAGE"


def test_capture_requires_login_and_the_student_role(client, teacher, student, fake_engine):
    assert client.post(CAPTURE, {"step": 0, "image": make_selfie()}).status_code == 302

    client.force_login(teacher.user)
    assert client.post(CAPTURE, {"step": 0, "image": make_selfie()}).status_code == 403
    assert client.get(ENROLL).status_code == 403


def test_a_student_cannot_enroll_against_another_students_record(
    client, student, classmate, fake_engine
):
    """The endpoint takes no student id — it always uses the session's own profile."""
    fake_engine.returning(embedding=random_embedding(seed=8))
    client.force_login(student.user)
    client.post(CAPTURE, {"step": 0, "image": make_selfie(), "student": classmate.pk})

    assert student.face_enrollments.count() == 1
    assert classmate.face_enrollments.count() == 0


# -------------------------------------------------------------- reset requests


def test_student_can_request_a_reset_once(client, student, fake_engine):
    client.force_login(student.user)
    client.post(reverse("face:request_reset"), {"reason": "I grew a beard"})
    assert EnrollmentResetRequest.objects.count() == 1

    client.post(reverse("face:request_reset"), {"reason": "again"})
    assert EnrollmentResetRequest.objects.count() == 1  # one pending at a time


def test_reset_needs_admin_approval_before_samples_are_cleared(
    client, student, fake_engine, admin_user
):
    for step in range(5):
        assert capture(student, step, fake_engine, embedding=random_embedding(seed=step)).ok
    student.refresh_from_db()
    assert student.face_enrolled is True

    client.force_login(student.user)
    client.post(reverse("face:request_reset"), {"reason": "new glasses"})
    reset = EnrollmentResetRequest.objects.get()

    student.refresh_from_db()
    assert student.face_enrolled is True      # nothing happens on request alone
    assert student.face_enrollments.count() == 5
    client.logout()

    client.force_login(admin_user)
    client.post(reverse("students:decide_reset", args=[reset.pk]), {"decision": "approve"})

    student.refresh_from_db()
    reset.refresh_from_db()
    assert reset.status == ResetRequestStatus.APPROVED
    assert student.face_enrolled is False
    assert student.face_enrollments.count() == 0


def test_rejected_reset_leaves_the_samples_alone(client, student, fake_engine, admin_user):
    assert capture(student, 0, fake_engine, embedding=random_embedding(seed=1)).ok
    client.force_login(student.user)
    client.post(reverse("face:request_reset"), {"reason": "wrong photos"})
    reset = EnrollmentResetRequest.objects.get()
    client.logout()

    client.force_login(admin_user)
    client.post(reverse("students:decide_reset", args=[reset.pk]), {"decision": "reject"})

    reset.refresh_from_db()
    assert reset.status == ResetRequestStatus.REJECTED
    assert student.face_enrollments.count() == 1


def test_students_cannot_decide_their_own_reset(client, student, fake_engine):
    client.force_login(student.user)
    client.post(reverse("face:request_reset"), {"reason": "x"})
    reset = EnrollmentResetRequest.objects.get()

    response = client.post(reverse("students:decide_reset", args=[reset.pk]), {"decision": "approve"})
    assert response.status_code == 403
    reset.refresh_from_db()
    assert reset.status == ResetRequestStatus.PENDING
