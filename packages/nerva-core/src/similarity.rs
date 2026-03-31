//! Vector similarity functions with SIMD-friendly loop structure.
//!
//! All functions operate on `f32` slices, which aligns with typical
//! embedding model output (384/768/1536 dimensions).

/// Computes the dot product of two vectors.
///
/// Uses a 4-wide manual unroll so the compiler can auto-vectorize
/// into SIMD instructions on supported targets.
///
/// # Panics
///
/// Panics if `a` and `b` have different lengths.
pub fn dot_product(a: &[f32], b: &[f32]) -> f32 {
    assert_eq!(a.len(), b.len(), "vectors must have equal length");

    let n = a.len();
    let chunks = n / 4;
    let remainder = n % 4;

    let mut sum0: f32 = 0.0;
    let mut sum1: f32 = 0.0;
    let mut sum2: f32 = 0.0;
    let mut sum3: f32 = 0.0;

    for i in 0..chunks {
        let base = i * 4;
        sum0 += a[base] * b[base];
        sum1 += a[base + 1] * b[base + 1];
        sum2 += a[base + 2] * b[base + 2];
        sum3 += a[base + 3] * b[base + 3];
    }

    let tail_start = chunks * 4;
    for i in 0..remainder {
        sum0 += a[tail_start + i] * b[tail_start + i];
    }

    sum0 + sum1 + sum2 + sum3
}

/// Computes the cosine similarity between two vectors.
///
/// Returns a value in `[-1.0, 1.0]`. Returns `0.0` if either vector
/// has zero magnitude (avoids division by zero).
///
/// # Panics
///
/// Panics if `a` and `b` have different lengths.
pub fn cosine_similarity(a: &[f32], b: &[f32]) -> f32 {
    assert_eq!(a.len(), b.len(), "vectors must have equal length");

    if a.is_empty() {
        return 0.0;
    }

    let mut dot = 0.0_f32;
    let mut norm_a = 0.0_f32;
    let mut norm_b = 0.0_f32;

    // Single-pass: compute dot product and both norms together
    // to maximize cache locality.
    let n = a.len();
    let chunks = n / 4;
    let remainder = n % 4;

    for i in 0..chunks {
        let base = i * 4;
        for k in 0..4 {
            let av = a[base + k];
            let bv = b[base + k];
            dot += av * bv;
            norm_a += av * av;
            norm_b += bv * bv;
        }
    }

    let tail_start = chunks * 4;
    for i in 0..remainder {
        let av = a[tail_start + i];
        let bv = b[tail_start + i];
        dot += av * bv;
        norm_a += av * av;
        norm_b += bv * bv;
    }

    let denom = norm_a.sqrt() * norm_b.sqrt();
    if denom == 0.0 {
        return 0.0;
    }

    (dot / denom).clamp(-1.0, 1.0)
}

/// Ranks candidates by cosine similarity to the query vector.
///
/// Returns the top-k results sorted by descending similarity score.
/// If `candidates` has fewer than `top_k` entries, all are returned.
///
/// # Arguments
///
/// * `query` - The query embedding vector.
/// * `candidates` - Pairs of `(id, embedding)` to rank.
/// * `top_k` - Maximum number of results to return.
pub fn cosine_rank(
    query: &[f32],
    candidates: &[(String, Vec<f32>)],
    top_k: usize,
) -> Vec<(String, f32)> {
    let mut scored: Vec<(String, f32)> = candidates
        .iter()
        .map(|(id, vec)| (id.clone(), cosine_similarity(query, vec)))
        .collect();

    // Sort descending by score. Use total_cmp for deterministic NaN handling.
    scored.sort_by(|a, b| b.1.total_cmp(&a.1));
    scored.truncate(top_k);
    scored
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn identical_vectors_have_similarity_one() {
        let v = vec![1.0, 2.0, 3.0, 4.0];
        let sim = cosine_similarity(&v, &v);
        assert!((sim - 1.0).abs() < 1e-6, "expected ~1.0, got {sim}");
    }

    #[test]
    fn orthogonal_vectors_have_similarity_zero() {
        let a = vec![1.0, 0.0];
        let b = vec![0.0, 1.0];
        let sim = cosine_similarity(&a, &b);
        assert!(sim.abs() < 1e-6, "expected ~0.0, got {sim}");
    }

    #[test]
    fn opposite_vectors_have_similarity_negative_one() {
        let a = vec![1.0, 2.0, 3.0];
        let b = vec![-1.0, -2.0, -3.0];
        let sim = cosine_similarity(&a, &b);
        assert!((sim + 1.0).abs() < 1e-6, "expected ~-1.0, got {sim}");
    }

    #[test]
    fn empty_vectors_return_zero() {
        let a: Vec<f32> = vec![];
        let sim = cosine_similarity(&a, &a);
        assert_eq!(sim, 0.0);
    }

    #[test]
    fn zero_vector_returns_zero() {
        let a = vec![0.0, 0.0, 0.0];
        let b = vec![1.0, 2.0, 3.0];
        let sim = cosine_similarity(&a, &b);
        assert_eq!(sim, 0.0);
    }

    #[test]
    #[should_panic(expected = "vectors must have equal length")]
    fn mismatched_lengths_panics() {
        cosine_similarity(&[1.0, 2.0], &[1.0]);
    }

    #[test]
    fn dot_product_basic() {
        let a = vec![1.0, 2.0, 3.0, 4.0, 5.0];
        let b = vec![5.0, 4.0, 3.0, 2.0, 1.0];
        // 5 + 8 + 9 + 8 + 5 = 35
        let dp = dot_product(&a, &b);
        assert!((dp - 35.0).abs() < 1e-6);
    }

    #[test]
    fn ranking_returns_correct_order() {
        let query = vec![1.0, 0.0];
        let candidates = vec![
            ("far".into(), vec![0.0, 1.0]),    // orthogonal
            ("close".into(), vec![1.0, 0.1]),   // nearly aligned
            ("exact".into(), vec![1.0, 0.0]),   // identical direction
        ];

        let ranked = cosine_rank(&query, &candidates, 2);
        assert_eq!(ranked.len(), 2);
        assert_eq!(ranked[0].0, "exact");
        assert_eq!(ranked[1].0, "close");
    }

    #[test]
    fn ranking_with_top_k_larger_than_candidates() {
        let query = vec![1.0];
        let candidates = vec![("a".into(), vec![1.0])];
        let ranked = cosine_rank(&query, &candidates, 10);
        assert_eq!(ranked.len(), 1);
    }

    #[test]
    fn ranking_empty_candidates() {
        let query = vec![1.0, 0.0];
        let ranked = cosine_rank(&query, &[], 5);
        assert!(ranked.is_empty());
    }

    // --- proptest ---
    use proptest::prelude::*;

    proptest! {
        #[test]
        fn cosine_always_in_range(
            a in prop::collection::vec(-100.0_f32..100.0, 1..128usize),
        ) {
            // Use the same length for b
            let b: Vec<f32> = a.iter().map(|x| x + 1.0).collect();
            let sim = cosine_similarity(&a, &b);
            prop_assert!(sim >= -1.0 - 1e-6 && sim <= 1.0 + 1e-6,
                "cosine out of range: {sim}");
        }
    }
}
