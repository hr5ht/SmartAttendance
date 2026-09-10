"""Embedding (de)serialisation and similarity maths.

Embeddings live in SQLite as raw float32 bytes; all comparison happens here in
NumPy so that the rest of the code never touches buffers directly.
"""
from __future__ import annotations

import numpy as np

DTYPE = np.float32


def to_bytes(vector: np.ndarray) -> bytes:
    """Normalise to unit length and serialise as float32 bytes."""
    array = np.asarray(vector, dtype=DTYPE).ravel()
    return normalize(array).tobytes()


def from_bytes(blob: bytes | memoryview, dim: int | None = None) -> np.ndarray:
    array = np.frombuffer(bytes(blob), dtype=DTYPE)
    if dim is not None and array.size != dim:
        raise ValueError(f"expected {dim}-d embedding, got {array.size}")
    return array


def normalize(vector: np.ndarray) -> np.ndarray:
    """Unit-normalise a single vector; a zero vector is returned unchanged."""
    array = np.asarray(vector, dtype=DTYPE).ravel()
    norm = float(np.linalg.norm(array))
    if norm == 0.0:
        return array
    return (array / norm).astype(DTYPE)


def normalize_rows(matrix: np.ndarray) -> np.ndarray:
    """Unit-normalise each row of a 2-D matrix."""
    array = np.atleast_2d(np.asarray(matrix, dtype=DTYPE))
    norms = np.linalg.norm(array, axis=1, keepdims=True)
    norms[norms == 0.0] = 1.0
    return (array / norms).astype(DTYPE)


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity between two vectors."""
    return float(np.dot(normalize(a), normalize(b)))


def cosine_similarity_matrix(probes: np.ndarray, gallery: np.ndarray) -> np.ndarray:
    """(n_probes, n_gallery) similarity via one matrix multiply, not a loop."""
    if gallery.size == 0:
        return np.zeros((np.atleast_2d(probes).shape[0], 0), dtype=DTYPE)
    return normalize_rows(probes) @ normalize_rows(gallery).T
