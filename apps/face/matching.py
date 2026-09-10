"""Match detected faces against a gallery of enrolled students.

Pure NumPy and plain data — no Django models, no image handling, no engine. The
caller decides which students form the gallery; this module only does the maths,
so it can be exercised with synthetic embeddings.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from apps.face import embeddings as emb


class RejectReason:
    """Why a face ended up UNKNOWN. Stored on DetectedFace.reject_reason."""

    BELOW_THRESHOLD = "BELOW_THRESHOLD"
    AMBIGUOUS = "AMBIGUOUS"
    COLLISION = "COLLISION"
    EMPTY_GALLERY = "EMPTY_GALLERY"


@dataclass
class MatchResult:
    """The outcome for one probe face."""

    student_id: int | None
    similarity: float | None
    margin: float | None
    runner_up_id: int | None = None
    reject_reason: str = ""

    @property
    def matched(self) -> bool:
        return self.student_id is not None


def build_gallery(enrollments) -> tuple[list[int], np.ndarray]:
    """Stack enrollment embeddings into one matrix.

    A student with five samples contributes five rows; ``ids[i]`` says which
    student row ``i`` belongs to. Returns an empty matrix when there is nothing
    to match against, which the caller must treat as a hard stop.
    """
    ids: list[int] = []
    vectors: list[np.ndarray] = []
    for enrollment in enrollments:
        ids.append(enrollment.student_id)
        vectors.append(emb.from_bytes(enrollment.embedding))

    if not vectors:
        return [], np.zeros((0, 0), dtype=np.float32)
    return ids, np.stack(vectors)


def match_probes(
    probes: np.ndarray,
    gallery_ids: list[int],
    gallery: np.ndarray,
    threshold: float,
    margin_threshold: float,
) -> list[MatchResult]:
    """Score every probe against the whole gallery in one matrix multiply.

    A probe is accepted only when its best student scores at least ``threshold``
    AND beats the runner-up *student* by more than ``margin_threshold``. The
    margin is measured between students, not between rows: two samples of the
    same person are not rivals.
    """
    probes = np.atleast_2d(np.asarray(probes, dtype=np.float32))
    if probes.size == 0:
        return []
    if gallery.size == 0 or not gallery_ids:
        return [
            MatchResult(None, None, None, reject_reason=RejectReason.EMPTY_GALLERY)
            for _ in range(probes.shape[0])
        ]

    # (n_probes, n_samples) — the single matmul the whole matcher rests on.
    scores = emb.cosine_similarity_matrix(probes, gallery)

    # Collapse samples down to one score per student: their best-matching sample.
    unique_ids = sorted(set(gallery_ids))
    id_index = {student_id: position for position, student_id in enumerate(unique_ids)}
    columns = np.array([id_index[student_id] for student_id in gallery_ids])

    per_student = np.full((scores.shape[0], len(unique_ids)), -np.inf, dtype=np.float32)
    for column in range(len(unique_ids)):
        members = scores[:, columns == column]
        if members.size:
            per_student[:, column] = members.max(axis=1)

    results: list[MatchResult] = []
    for row in per_student:
        order = np.argsort(row)[::-1]
        best_index = int(order[0])
        best = float(row[best_index])
        best_id = unique_ids[best_index]

        if len(unique_ids) > 1:
            runner_index = int(order[1])
            runner_up = float(row[runner_index])
            runner_up_id = unique_ids[runner_index]
        else:
            # Nobody to be confused with, so the margin is the score itself.
            runner_up, runner_up_id = 0.0, None

        margin = best - runner_up

        if best < threshold:
            results.append(
                MatchResult(None, best, margin, runner_up_id, RejectReason.BELOW_THRESHOLD)
            )
        elif margin <= margin_threshold:
            results.append(
                MatchResult(None, best, margin, runner_up_id, RejectReason.AMBIGUOUS)
            )
        else:
            results.append(MatchResult(best_id, best, margin, runner_up_id))
    return results


def resolve_collisions(results: list[MatchResult]) -> list[MatchResult]:
    """One student per face within a single image.

    Two faces in the same photo cannot both be the same person. The higher
    similarity keeps the identity; the other becomes UNKNOWN. Returns a new list
    and leaves the input alone.
    """
    best_for_student: dict[int, int] = {}
    for index, result in enumerate(results):
        if not result.matched:
            continue
        held = best_for_student.get(result.student_id)
        if held is None or (results[index].similarity or 0) > (results[held].similarity or 0):
            best_for_student[result.student_id] = index

    resolved: list[MatchResult] = []
    for index, result in enumerate(results):
        if result.matched and best_for_student.get(result.student_id) != index:
            resolved.append(
                MatchResult(
                    None,
                    result.similarity,
                    result.margin,
                    result.student_id,  # who it lost to, useful when reviewing
                    RejectReason.COLLISION,
                )
            )
        else:
            resolved.append(result)
    return resolved
