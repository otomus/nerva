//! Node.js bindings for nerva-core via napi-rs.
//!
//! Exposes the hot-path functions (similarity, tokenizer, schema validation)
//! as synchronous N-API functions callable from JavaScript/TypeScript.

use napi_derive::napi;

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/// A candidate entry for cosine ranking.
///
/// Passed from JS as an object with `id` and `embedding` fields.
#[napi(object)]
pub struct CandidateInput {
    /// Unique identifier for this candidate.
    pub id: String,
    /// Embedding vector (must match query dimension).
    pub embedding: Vec<f64>,
}

/// A ranked result returned from cosine_rank.
#[napi(object)]
pub struct RankedResult {
    /// Candidate identifier.
    pub id: String,
    /// Cosine similarity score.
    pub score: f64,
}

// ---------------------------------------------------------------------------
// Similarity
// ---------------------------------------------------------------------------

/// Computes cosine similarity between two vectors.
///
/// Returns a value in [-1.0, 1.0]. Returns 0.0 if either vector
/// has zero magnitude or if lengths differ.
#[napi]
pub fn cosine_similarity(a: Vec<f64>, b: Vec<f64>) -> f64 {
    if a.len() != b.len() || a.is_empty() {
        return 0.0;
    }
    let a_f32: Vec<f32> = a.iter().map(|&v| v as f32).collect();
    let b_f32: Vec<f32> = b.iter().map(|&v| v as f32).collect();
    nerva_core::cosine_similarity(&a_f32, &b_f32) as f64
}

/// Ranks candidates by cosine similarity to the query vector.
///
/// Returns the top-k results sorted by descending similarity score.
#[napi]
pub fn cosine_rank(query: Vec<f64>, candidates: Vec<CandidateInput>, top_k: u32) -> Vec<RankedResult> {
    let query_f32: Vec<f32> = query.iter().map(|&v| v as f32).collect();
    let pairs: Vec<(String, Vec<f32>)> = candidates
        .into_iter()
        .map(|c| {
            let emb: Vec<f32> = c.embedding.iter().map(|&v| v as f32).collect();
            (c.id, emb)
        })
        .collect();

    nerva_core::cosine_rank(&query_f32, &pairs, top_k as usize)
        .into_iter()
        .map(|(id, score)| RankedResult {
            id,
            score: score as f64,
        })
        .collect()
}

// ---------------------------------------------------------------------------
// Tokenizer
// ---------------------------------------------------------------------------

/// Counts the approximate number of tokens in the given text.
///
/// Uses a whitespace-plus-punctuation heuristic that approximates
/// BPE token counts without a full tokenizer model.
#[napi]
pub fn count_tokens(text: String) -> u32 {
    nerva_core::count_tokens(&text)
}

/// Truncates text to at most `max_tokens` approximate tokens.
///
/// Preserves whole words — never splits mid-word.
#[napi]
pub fn truncate_to_tokens(text: String, max_tokens: u32) -> String {
    nerva_core::truncate_to_tokens(&text, max_tokens)
}

// ---------------------------------------------------------------------------
// Schema validation
// ---------------------------------------------------------------------------

/// Validates a JSON instance string against a JSON schema string.
///
/// Returns an array of validation error messages. An empty array
/// means the instance is valid. Returns a single error if either
/// input is not valid JSON.
#[napi]
pub fn validate_schema(instance: String, schema: String) -> Vec<String> {
    let instance_val: serde_json::Value = match serde_json::from_str(&instance) {
        Ok(v) => v,
        Err(e) => return vec![format!("invalid instance JSON: {}", e)],
    };
    let schema_val: serde_json::Value = match serde_json::from_str(&schema) {
        Ok(v) => v,
        Err(e) => return vec![format!("invalid schema JSON: {}", e)],
    };
    nerva_core::validate(&instance_val, &schema_val)
}
