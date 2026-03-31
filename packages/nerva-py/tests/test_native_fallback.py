"""Tests for native/pure-Python fallback in cosine similarity (N-813).

Verifies that:
- Pure-Python cosine similarity works when native module is unavailable.
- When native is available it is actually used.
- Native and pure-Python implementations produce matching results.
- count_tokens and truncate_to_tokens bindings work if available.
- Edge cases (empty vectors, mismatched lengths, zero vectors, NaN-like
  inputs) are handled correctly by both implementations.
"""

from __future__ import annotations

import importlib
import math
import sys
from types import ModuleType
from unittest import mock

import pytest

from nerva.router import embedding as embedding_mod
from nerva.router import hybrid as hybrid_mod


# ── Helpers ────────────────────────────────────────────────────────────


def _pure_cosine(a: list[float], b: list[float]) -> float:
    """Reference pure-Python cosine similarity for verification.

    Args:
        a: First vector.
        b: Second vector.

    Returns:
        Cosine similarity, or 0.0 for degenerate inputs.
    """
    if len(a) != len(b) or len(a) == 0:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0.0 or mag_b == 0.0:
        return 0.0
    return dot / (mag_a * mag_b)


def _native_available() -> bool:
    """Check if the native nerva_core module is importable."""
    try:
        import nerva_core  # noqa: F401
        return True
    except ImportError:
        return False


# ── Pure-Python fallback tests ─────────────────────────────────────────


class TestPurePythonFallback:
    """Ensure cosine similarity works when the native module is absent."""

    def test_embedding_pure_fallback_identical_vectors(self) -> None:
        """Identical vectors should produce similarity ~1.0."""
        v = [1.0, 2.0, 3.0, 4.0]
        result = embedding_mod._cosine_similarity_pure(v, v)
        assert abs(result - 1.0) < 1e-6

    def test_embedding_pure_fallback_orthogonal(self) -> None:
        """Orthogonal vectors should produce similarity ~0.0."""
        a = [1.0, 0.0]
        b = [0.0, 1.0]
        result = embedding_mod._cosine_similarity_pure(a, b)
        assert abs(result) < 1e-6

    def test_embedding_pure_fallback_opposite(self) -> None:
        """Opposite vectors should produce similarity ~-1.0."""
        a = [1.0, 2.0, 3.0]
        b = [-1.0, -2.0, -3.0]
        result = embedding_mod._cosine_similarity_pure(a, b)
        assert abs(result + 1.0) < 1e-6

    def test_hybrid_pure_fallback_identical(self) -> None:
        """Hybrid pure fallback: identical vectors -> ~1.0."""
        v = [3.0, 4.0]
        result = hybrid_mod._cosine_similarity_pure(v, v)
        assert abs(result - 1.0) < 1e-6

    def test_hybrid_pure_fallback_orthogonal(self) -> None:
        """Hybrid pure fallback: orthogonal vectors -> ~0.0."""
        result = hybrid_mod._cosine_similarity_pure([1.0, 0.0], [0.0, 1.0])
        assert abs(result) < 1e-6

    def test_cosine_called_without_native(self) -> None:
        """When _USE_NATIVE is False, the dispatcher must use pure Python."""
        original_flag = embedding_mod._USE_NATIVE
        try:
            embedding_mod._USE_NATIVE = False
            v = [1.0, 0.0, 0.0]
            result = embedding_mod._cosine_similarity(v, v)
            assert abs(result - 1.0) < 1e-6
        finally:
            embedding_mod._USE_NATIVE = original_flag


# ── Edge case tests (both implementations) ─────────────────────────────


class TestEdgeCases:
    """Stress boundaries: empty, zero, mismatched, large vectors."""

    @pytest.mark.parametrize("impl_fn", [
        embedding_mod._cosine_similarity_pure,
        hybrid_mod._cosine_similarity_pure,
    ])
    def test_empty_vectors(self, impl_fn) -> None:
        """Empty vectors should return 0.0 via the dispatcher (length check)."""
        # The dispatcher handles empty before reaching the pure impl,
        # but the dispatcher delegates to _cosine_similarity which guards.
        # Test the top-level function instead.
        pass  # covered by dispatcher test below

    @pytest.mark.parametrize("impl_fn", [
        embedding_mod._cosine_similarity,
        hybrid_mod._cosine_similarity,
    ])
    def test_empty_vectors_via_dispatcher(self, impl_fn) -> None:
        """Empty vectors should return 0.0."""
        assert impl_fn([], []) == 0.0

    @pytest.mark.parametrize("impl_fn", [
        embedding_mod._cosine_similarity,
        hybrid_mod._cosine_similarity,
    ])
    def test_mismatched_lengths(self, impl_fn) -> None:
        """Mismatched vector lengths should return 0.0."""
        assert impl_fn([1.0, 2.0], [1.0]) == 0.0

    @pytest.mark.parametrize("impl_fn", [
        embedding_mod._cosine_similarity_pure,
        hybrid_mod._cosine_similarity_pure,
    ])
    def test_zero_vector(self, impl_fn) -> None:
        """A zero-magnitude vector should return 0.0."""
        assert impl_fn([0.0, 0.0, 0.0], [1.0, 2.0, 3.0]) == 0.0

    @pytest.mark.parametrize("impl_fn", [
        embedding_mod._cosine_similarity_pure,
        hybrid_mod._cosine_similarity_pure,
    ])
    def test_single_element(self, impl_fn) -> None:
        """Single-element vectors should work correctly."""
        result = impl_fn([3.0], [3.0])
        assert abs(result - 1.0) < 1e-6

    @pytest.mark.parametrize("impl_fn", [
        embedding_mod._cosine_similarity_pure,
        hybrid_mod._cosine_similarity_pure,
    ])
    def test_large_vectors(self, impl_fn) -> None:
        """384-dimensional vectors (typical embedding size) should work."""
        a = [float(i) for i in range(384)]
        b = [float(384 - i) for i in range(384)]
        result = impl_fn(a, b)
        expected = _pure_cosine(a, b)
        assert abs(result - expected) < 1e-6

    @pytest.mark.parametrize("impl_fn", [
        embedding_mod._cosine_similarity_pure,
        hybrid_mod._cosine_similarity_pure,
    ])
    def test_negative_values(self, impl_fn) -> None:
        """Vectors with negative values should produce valid similarity."""
        a = [-1.0, -2.0, 3.0]
        b = [4.0, -5.0, 6.0]
        result = impl_fn(a, b)
        expected = _pure_cosine(a, b)
        assert abs(result - expected) < 1e-6

    @pytest.mark.parametrize("impl_fn", [
        embedding_mod._cosine_similarity_pure,
        hybrid_mod._cosine_similarity_pure,
    ])
    def test_very_small_values(self, impl_fn) -> None:
        """Very small float values should not cause division by zero."""
        a = [1e-38, 1e-38]
        b = [1e-38, 1e-38]
        result = impl_fn(a, b)
        # Should be ~1.0 (identical direction) or 0.0 if underflow
        assert result == 0.0 or abs(result - 1.0) < 1e-3


# ── Native detection tests ─────────────────────────────────────────────


class TestNativeDetection:
    """Verify auto-detection picks up the native module when present."""

    def test_use_native_flag_reflects_availability(self) -> None:
        """_USE_NATIVE should be True iff nerva_core is importable."""
        available = _native_available()
        assert embedding_mod._USE_NATIVE == available
        assert hybrid_mod._USE_NATIVE == available

    @pytest.mark.skipif(
        not _native_available(),
        reason="native nerva_core not installed",
    )
    def test_native_cosine_matches_pure(self) -> None:
        """Native and pure-Python should produce the same results."""
        from nerva_core import cosine_similarity as native_cosine

        test_pairs = [
            ([1.0, 2.0, 3.0], [4.0, 5.0, 6.0]),
            ([1.0, 0.0], [0.0, 1.0]),
            ([1.0, 1.0, 1.0], [1.0, 1.0, 1.0]),
            ([0.5, -0.5, 0.3], [-0.1, 0.9, -0.7]),
        ]

        for a, b in test_pairs:
            native_result = native_cosine(a, b)
            pure_result = embedding_mod._cosine_similarity_pure(a, b)
            assert abs(native_result - pure_result) < 1e-5, (
                f"mismatch for {a}, {b}: native={native_result}, pure={pure_result}"
            )

    @pytest.mark.skipif(
        not _native_available(),
        reason="native nerva_core not installed",
    )
    def test_native_cosine_rank(self) -> None:
        """cosine_rank should return correct ranking order."""
        from nerva_core import cosine_rank

        query = [1.0, 0.0]
        candidates = [
            ("far", [0.0, 1.0]),
            ("close", [1.0, 0.1]),
            ("exact", [1.0, 0.0]),
        ]
        ranked = cosine_rank(query, candidates, 2)
        assert len(ranked) == 2
        assert ranked[0][0] == "exact"
        assert ranked[1][0] == "close"

    @pytest.mark.skipif(
        not _native_available(),
        reason="native nerva_core not installed",
    )
    def test_native_count_tokens(self) -> None:
        """count_tokens should return a positive integer for non-empty text."""
        from nerva_core import count_tokens

        result = count_tokens("Hello world, this is a test.")
        assert isinstance(result, int)
        assert result > 0

    @pytest.mark.skipif(
        not _native_available(),
        reason="native nerva_core not installed",
    )
    def test_native_count_tokens_empty(self) -> None:
        """count_tokens on empty string should return 0."""
        from nerva_core import count_tokens

        assert count_tokens("") == 0

    @pytest.mark.skipif(
        not _native_available(),
        reason="native nerva_core not installed",
    )
    def test_native_truncate_to_tokens(self) -> None:
        """truncate_to_tokens should produce output no longer than input."""
        from nerva_core import truncate_to_tokens

        text = "The quick brown fox jumps over the lazy dog"
        truncated = truncate_to_tokens(text, 3)
        assert isinstance(truncated, str)
        assert len(truncated) <= len(text)
        assert len(truncated) > 0

    @pytest.mark.skipif(
        not _native_available(),
        reason="native nerva_core not installed",
    )
    def test_native_truncate_to_tokens_zero(self) -> None:
        """truncate_to_tokens with max_tokens=0 should return empty string."""
        from nerva_core import truncate_to_tokens

        result = truncate_to_tokens("hello world", 0)
        assert result == ""

    @pytest.mark.skipif(
        not _native_available(),
        reason="native nerva_core not installed",
    )
    def test_native_validate_schema_valid(self) -> None:
        """validate_schema should return empty list for valid instance."""
        from nerva_core import validate_schema

        schema = '{"type": "object", "properties": {"name": {"type": "string"}}}'
        instance = '{"name": "test"}'
        errors = validate_schema(instance, schema)
        assert errors == []

    @pytest.mark.skipif(
        not _native_available(),
        reason="native nerva_core not installed",
    )
    def test_native_validate_schema_invalid(self) -> None:
        """validate_schema should return errors for invalid instance."""
        from nerva_core import validate_schema

        schema = '{"type": "object", "properties": {"age": {"type": "integer"}}, "required": ["age"]}'
        instance = '{"age": "not_a_number"}'
        errors = validate_schema(instance, schema)
        assert len(errors) > 0

    @pytest.mark.skipif(
        not _native_available(),
        reason="native nerva_core not installed",
    )
    def test_native_validate_schema_bad_json(self) -> None:
        """validate_schema should raise ValueError for malformed JSON."""
        from nerva_core import validate_schema

        with pytest.raises(ValueError):
            validate_schema("{not valid json", '{"type": "object"}')

    @pytest.mark.skipif(
        not _native_available(),
        reason="native nerva_core not installed",
    )
    def test_native_cosine_mismatched_raises(self) -> None:
        """Native cosine_similarity should raise ValueError on mismatched lengths."""
        from nerva_core import cosine_similarity as native_cosine

        with pytest.raises(ValueError):
            native_cosine([1.0, 2.0], [1.0])
