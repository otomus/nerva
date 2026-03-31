//! Python bindings for nerva-core via PyO3.
//!
//! Exposes the performance-critical functions from `nerva-core` as a native
//! Python module.  Install via `maturin develop` or `pip install nerva-core`.

use pyo3::prelude::*;

/// Compute cosine similarity between two float vectors.
///
/// Returns a value in [-1.0, 1.0].  Returns 0.0 if either vector has
/// zero magnitude.
///
/// Raises:
///     ValueError: If the vectors have different lengths.
#[pyfunction]
fn cosine_similarity(a: Vec<f32>, b: Vec<f32>) -> PyResult<f32> {
    if a.len() != b.len() {
        return Err(pyo3::exceptions::PyValueError::new_err(format!(
            "vectors must have equal length: {} != {}",
            a.len(),
            b.len()
        )));
    }
    Ok(nerva_core_rs::cosine_similarity(&a, &b))
}

/// Rank candidates by cosine similarity to a query vector.
///
/// Returns up to `top_k` results sorted by descending similarity score.
///
/// Args:
///     query: The query embedding vector.
///     candidates: List of `(id, embedding)` pairs.
///     top_k: Maximum number of results to return.
///
/// Returns:
///     List of `(id, score)` pairs, best first.
#[pyfunction]
fn cosine_rank(
    query: Vec<f32>,
    candidates: Vec<(String, Vec<f32>)>,
    top_k: usize,
) -> Vec<(String, f32)> {
    nerva_core_rs::cosine_rank(&query, &candidates, top_k)
}

/// Count the approximate number of tokens in a text string.
///
/// Uses a whitespace-and-punctuation heuristic that approximates
/// sub-word tokenizers without requiring a model vocabulary.
///
/// Args:
///     text: The input text.
///
/// Returns:
///     Approximate token count.
#[pyfunction]
fn count_tokens(text: &str) -> u32 {
    nerva_core_rs::count_tokens(text)
}

/// Truncate text to fit within a maximum token budget.
///
/// Splits on whitespace-and-punctuation boundaries and rejoins,
/// stopping before exceeding `max_tokens`.
///
/// Args:
///     text: The input text.
///     max_tokens: Maximum number of tokens to keep.
///
/// Returns:
///     Truncated text that fits within the token budget.
#[pyfunction]
fn truncate_to_tokens(text: &str, max_tokens: u32) -> String {
    nerva_core_rs::truncate_to_tokens(text, max_tokens)
}

/// Validate a JSON instance against a JSON schema.
///
/// Returns a list of validation error messages.  An empty list means
/// the instance is valid.
///
/// Args:
///     instance: JSON string of the value to validate.
///     schema: JSON string of the JSON Schema.
///
/// Returns:
///     List of validation error strings (empty if valid).
///
/// Raises:
///     ValueError: If either string is not valid JSON.
#[pyfunction]
fn validate_schema(instance: &str, schema: &str) -> PyResult<Vec<String>> {
    let inst: serde_json::Value = serde_json::from_str(instance).map_err(|e| {
        pyo3::exceptions::PyValueError::new_err(format!("invalid instance JSON: {e}"))
    })?;
    let sch: serde_json::Value = serde_json::from_str(schema).map_err(|e| {
        pyo3::exceptions::PyValueError::new_err(format!("invalid schema JSON: {e}"))
    })?;
    Ok(nerva_core_rs::validate(&inst, &sch))
}

/// Native nerva-core module for Python.
///
/// Provides high-performance implementations of similarity search,
/// token counting, and JSON schema validation.
#[pymodule]
fn nerva_core(_py: Python, m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(cosine_similarity, m)?)?;
    m.add_function(wrap_pyfunction!(cosine_rank, m)?)?;
    m.add_function(wrap_pyfunction!(count_tokens, m)?)?;
    m.add_function(wrap_pyfunction!(truncate_to_tokens, m)?)?;
    m.add_function(wrap_pyfunction!(validate_schema, m)?)?;
    Ok(())
}
