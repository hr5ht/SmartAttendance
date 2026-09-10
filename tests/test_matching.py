"""The matcher, exercised with synthetic embeddings — no DB, no model, no images."""
import numpy as np
import pytest

from apps.face.matching import (
    MatchResult,
    RejectReason,
    match_probes,
    resolve_collisions,
)
from tests.conftest import random_embedding, unit


def blend(a, b, weight):
    """A vector `weight` of the way from a towards b, unit length."""
    return unit((1 - weight) * a + weight * b)


@pytest.fixture
def people():
    """Three well-separated identities."""
    return {student_id: random_embedding(seed=student_id) for student_id in (1, 2, 3)}


def gallery_from(people, samples_per_student=1):
    ids, rows = [], []
    for student_id, vector in people.items():
        for sample in range(samples_per_student):
            ids.append(student_id)
            # Slight per-sample variation, as five real poses would give.
            rows.append(unit(vector + 0.05 * sample * random_embedding(seed=100 + sample)))
    return ids, np.stack(rows)


# ------------------------------------------------------------------ threshold


def test_a_clear_match_is_accepted(people):
    ids, gallery = gallery_from(people)
    [result] = match_probes(people[2][None, :], ids, gallery, 0.45, 0.05)

    assert result.student_id == 2
    assert result.similarity == pytest.approx(1.0, abs=1e-5)
    assert result.matched


def test_a_stranger_is_unknown(people):
    ids, gallery = gallery_from(people)
    stranger = random_embedding(seed=999)
    [result] = match_probes(stranger[None, :], ids, gallery, 0.45, 0.05)

    assert result.student_id is None
    assert result.reject_reason == RejectReason.BELOW_THRESHOLD


def test_threshold_is_inclusive_at_the_boundary(people):
    """>= threshold is accepted, just below is not."""
    ids, gallery = gallery_from(people)
    probe = people[1]

    at_boundary = float(np.dot(probe, gallery[0]))
    [accepted] = match_probes(probe[None, :], ids, gallery, at_boundary, 0.05)
    assert accepted.student_id == 1

    [rejected] = match_probes(probe[None, :], ids, gallery, at_boundary + 1e-4, 0.05)
    assert rejected.student_id is None
    assert rejected.reject_reason == RejectReason.BELOW_THRESHOLD


# --------------------------------------------------------------------- margin


def test_a_face_between_two_students_is_ambiguous(people):
    """Over the threshold but too close to the runner-up: refuse to guess."""
    ids, gallery = gallery_from(people)
    ambiguous = blend(people[1], people[2], 0.5)

    [result] = match_probes(ambiguous[None, :], ids, gallery, 0.45, 0.05)
    assert result.student_id is None
    assert result.reject_reason == RejectReason.AMBIGUOUS
    assert result.similarity >= 0.45
    assert result.margin <= 0.05
    assert result.runner_up_id in (1, 2)


def test_margin_relaxed_enough_lets_the_same_face_through(people):
    ids, gallery = gallery_from(people)
    ambiguous = blend(people[1], people[2], 0.5)

    [result] = match_probes(ambiguous[None, :], ids, gallery, 0.45, 0.0)
    assert result.matched


def test_extra_samples_of_the_same_student_never_count_as_a_rival(people):
    """The margin is between students, not between gallery rows."""
    ids, gallery = gallery_from(people, samples_per_student=5)
    [result] = match_probes(people[3][None, :], ids, gallery, 0.45, 0.05)

    assert result.student_id == 3
    assert result.runner_up_id != 3
    assert result.margin > 0.05


def test_a_single_student_gallery_still_applies_the_threshold(people):
    ids, gallery = gallery_from({1: people[1]})

    [matched] = match_probes(people[1][None, :], ids, gallery, 0.45, 0.05)
    assert matched.student_id == 1

    [stranger] = match_probes(random_embedding(seed=555)[None, :], ids, gallery, 0.45, 0.05)
    assert stranger.student_id is None


# ---------------------------------------------------------------------- misc


def test_an_empty_gallery_matches_nobody(people):
    results = match_probes(
        np.stack([people[1], people[2]]), [], np.zeros((0, 0), dtype=np.float32), 0.45, 0.05
    )
    assert len(results) == 2
    assert all(r.student_id is None for r in results)
    assert all(r.reject_reason == RejectReason.EMPTY_GALLERY for r in results)


def test_no_probes_gives_no_results(people):
    ids, gallery = gallery_from(people)
    assert match_probes(np.zeros((0, 512), dtype=np.float32), ids, gallery, 0.45, 0.05) == []


def test_every_probe_is_scored_in_one_call(people):
    ids, gallery = gallery_from(people, samples_per_student=3)
    probes = np.stack([people[1], people[3], random_embedding(seed=777)])

    results = match_probes(probes, ids, gallery, 0.45, 0.05)
    assert [r.student_id for r in results] == [1, 3, None]


# ---------------------------------------------------------- collision handling


def test_two_faces_claiming_one_student_keep_the_stronger():
    results = [
        MatchResult(student_id=7, similarity=0.62, margin=0.2),
        MatchResult(student_id=7, similarity=0.81, margin=0.3),
        MatchResult(student_id=9, similarity=0.70, margin=0.2),
    ]
    resolved = resolve_collisions(results)

    assert resolved[0].student_id is None
    assert resolved[0].reject_reason == RejectReason.COLLISION
    assert resolved[0].runner_up_id == 7      # records who it lost to
    assert resolved[1].student_id == 7        # the higher score keeps it
    assert resolved[2].student_id == 9        # unrelated match untouched


def test_collision_resolution_leaves_unknowns_alone():
    results = [
        MatchResult(None, 0.30, 0.01, reject_reason=RejectReason.BELOW_THRESHOLD),
        MatchResult(student_id=4, similarity=0.9, margin=0.4),
    ]
    resolved = resolve_collisions(results)
    assert resolved[0].reject_reason == RejectReason.BELOW_THRESHOLD
    assert resolved[1].student_id == 4


def test_collision_resolution_does_not_mutate_its_input():
    results = [
        MatchResult(student_id=5, similarity=0.5, margin=0.2),
        MatchResult(student_id=5, similarity=0.9, margin=0.3),
    ]
    resolve_collisions(results)
    assert results[0].student_id == 5 and results[1].student_id == 5
