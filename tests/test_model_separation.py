"""Does the embedding model actually tell two people apart?

Everything else in the suite tests our code around the model. This tests the
model itself, so that changing FACE_MODEL_NAME or the thresholds can't quietly
destroy the one property the whole system rests on.

Scope note: the six faces are genuinely different people, so the impostor side
is a real measurement. The same-person side uses re-crops of one photo, which
tests crop invariance only — not the same person on a different day.
"""
import itertools
from pathlib import Path

import cv2
import numpy as np
import pytest
from django.conf import settings

from apps.face import embeddings as emb
from apps.face.matching import RejectReason, match_probes
from apps.face.services import InsightFaceEngine, set_engine

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def engine():
    real = InsightFaceEngine()
    try:
        real.preload()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"face models unavailable: {exc}")
    set_engine(real)
    yield real
    set_engine(None)


@pytest.fixture(scope="module")
def crowd(engine):
    """Six different people and their embeddings, from the sample group photo."""
    import insightface

    root = Path(insightface.__file__).parent / "data" / "images"
    image = cv2.imread(str(root / "t1.jpg"))
    faces = engine.detect(image)
    assert len(faces) >= 4, "sample photo should contain several distinct people"
    return image, faces, np.stack([face.embedding for face in faces])


def test_different_people_score_far_below_the_threshold(crowd):
    _, faces, embeddings = crowd
    pairs = [
        emb.cosine_similarity(embeddings[i], embeddings[j])
        for i, j in itertools.combinations(range(len(faces)), 2)
    ]
    worst = max(pairs)

    # Unrelated identities should be close to orthogonal, not merely "under 0.45".
    assert worst < 0.35, f"two different people scored {worst:.3f} — too close for comfort"
    assert worst < settings.SIMILARITY_THRESHOLD, (
        f"the best impostor pair scores {worst:.3f}, at or above the "
        f"{settings.SIMILARITY_THRESHOLD} threshold"
    )


def test_the_same_face_survives_recropping(crowd, engine):
    """Reframing a face must not change who it is."""
    image, faces, _ = crowd

    def recrop(bbox, pad, width):
        x1, y1, x2, y2 = bbox
        w, h = x2 - x1, y2 - y1
        x1 = max(0, int(x1 - w * pad)); y1 = max(0, int(y1 - h * pad))
        x2 = min(image.shape[1], int(x2 + w * pad)); y2 = min(image.shape[0], int(y2 + h * pad))
        patch = image[y1:y2, x1:x2]
        scale = width / patch.shape[1]
        return cv2.resize(patch, (width, int(patch.shape[0] * scale)), interpolation=cv2.INTER_CUBIC)

    worst = 1.0
    for face in faces[:3]:
        vectors = []
        for pad, width in [(0.85, 380), (1.10, 430), (0.80, 370)]:
            found = engine.detect(recrop(face.bbox, pad, width))
            if found:
                vectors.append(found[0].embedding)
        assert len(vectors) >= 2
        for a, b in itertools.combinations(vectors, 2):
            worst = min(worst, emb.cosine_similarity(a, b))

    assert worst > settings.SIMILARITY_THRESHOLD, (
        f"the same face scored {worst:.3f} against itself after recropping"
    )


def test_there_is_a_wide_gap_between_same_and_different(crowd, engine):
    """The threshold should sit in empty space, not on top of the distribution."""
    image, faces, embeddings = crowd

    impostor = max(
        emb.cosine_similarity(embeddings[i], embeddings[j])
        for i, j in itertools.combinations(range(len(faces)), 2)
    )
    assert settings.SIMILARITY_THRESHOLD - impostor > 0.15, (
        f"only {settings.SIMILARITY_THRESHOLD - impostor:.3f} of headroom between the "
        f"best impostor pair and the threshold"
    )


def test_the_matcher_maps_every_face_to_its_own_identity(crowd):
    """Through the real matcher: no swaps, no confident wrong answers."""
    _, faces, embeddings = crowd
    ids = list(range(len(faces)))

    results = match_probes(
        embeddings, ids, embeddings,
        settings.SIMILARITY_THRESHOLD, settings.MARGIN_THRESHOLD,
    )
    assert [r.student_id for r in results] == ids
    assert all(r.margin > settings.MARGIN_THRESHOLD for r in results)


def test_someone_not_in_the_gallery_comes_back_unknown(crowd):
    """A person who was never enrolled must not be assigned to a classmate."""
    _, faces, embeddings = crowd

    # Everyone except person 0 is enrolled; person 0 walks into the photo.
    result = match_probes(
        embeddings[0][None, :], list(range(1, len(faces))), embeddings[1:],
        settings.SIMILARITY_THRESHOLD, settings.MARGIN_THRESHOLD,
    )[0]

    assert result.student_id is None, (
        f"an unenrolled person was matched to student {result.student_id} "
        f"at {result.similarity:.3f}"
    )
    assert result.reject_reason == RejectReason.BELOW_THRESHOLD


def test_an_outsider_from_another_photo_is_not_matched(crowd, engine):
    """A face from an entirely different source image, not just a different crop."""
    import insightface

    root = Path(insightface.__file__).parent / "data" / "images"
    outsider = cv2.imread(str(root / "Tom_Hanks_54745.png"))
    # Pre-aligned 112px crop: pad it so the detector has margin to work with.
    padded = cv2.copyMakeBorder(
        cv2.resize(outsider, (300, 300), interpolation=cv2.INTER_CUBIC),
        150, 150, 150, 150, cv2.BORDER_REPLICATE,
    )
    found = engine.detect(padded)
    if not found:
        pytest.skip("outsider sample did not detect at this scale")

    _, faces, embeddings = crowd
    result = match_probes(
        found[0].embedding[None, :], list(range(len(faces))), embeddings,
        settings.SIMILARITY_THRESHOLD, settings.MARGIN_THRESHOLD,
    )[0]

    assert result.student_id is None, (
        f"a stranger matched student {result.student_id} at {result.similarity:.3f}"
    )
