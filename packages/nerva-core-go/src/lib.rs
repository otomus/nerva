//! C FFI bindings for nerva-core, intended for CGo consumption.
//!
//! All functions use C-compatible types. Strings are passed as
//! null-terminated `*const c_char` and returned as owned pointers
//! that must be freed with `nerva_free_string`.

use std::ffi::{CStr, CString};
use std::os::raw::c_char;
use std::ptr;
use std::slice;

// ---------------------------------------------------------------------------
// String helpers
// ---------------------------------------------------------------------------

/// Frees a string previously returned by a nerva_* function.
///
/// # Safety
///
/// `s` must be a pointer returned by a nerva_* function, or null.
#[no_mangle]
pub unsafe extern "C" fn nerva_free_string(s: *mut c_char) {
    if !s.is_null() {
        drop(CString::from_raw(s));
    }
}

/// Converts a C string pointer to a Rust &str, returning None for null/invalid UTF-8.
unsafe fn cstr_to_str<'a>(s: *const c_char) -> Option<&'a str> {
    if s.is_null() {
        return None;
    }
    CStr::from_ptr(s).to_str().ok()
}

/// Converts a Rust String to an owned C string pointer.
/// Returns null on allocation failure.
fn string_to_c(s: String) -> *mut c_char {
    CString::new(s).map(|c| c.into_raw()).unwrap_or(ptr::null_mut())
}

// ---------------------------------------------------------------------------
// Similarity
// ---------------------------------------------------------------------------

/// Computes cosine similarity between two f32 arrays.
///
/// Returns 0.0 if lengths differ or either is empty.
///
/// # Safety
///
/// `a_ptr` must point to `a_len` contiguous f32 values.
/// `b_ptr` must point to `b_len` contiguous f32 values.
#[no_mangle]
pub unsafe extern "C" fn nerva_cosine_similarity(
    a_ptr: *const f32,
    a_len: usize,
    b_ptr: *const f32,
    b_len: usize,
) -> f32 {
    if a_ptr.is_null() || b_ptr.is_null() || a_len != b_len || a_len == 0 {
        return 0.0;
    }
    let a = slice::from_raw_parts(a_ptr, a_len);
    let b = slice::from_raw_parts(b_ptr, b_len);
    nerva_core::cosine_similarity(a, b)
}

// ---------------------------------------------------------------------------
// Tokenizer
// ---------------------------------------------------------------------------

/// Counts the approximate number of tokens in a string.
///
/// # Safety
///
/// `text` must be a valid null-terminated UTF-8 string or null.
/// Returns 0 for null input.
#[no_mangle]
pub unsafe extern "C" fn nerva_count_tokens(text: *const c_char) -> u32 {
    match cstr_to_str(text) {
        Some(s) => nerva_core::count_tokens(s),
        None => 0,
    }
}

/// Truncates text to at most `max_tokens` approximate tokens.
///
/// Returns an owned string that must be freed with `nerva_free_string`.
///
/// # Safety
///
/// `text` must be a valid null-terminated UTF-8 string or null.
/// Returns null for null input.
#[no_mangle]
pub unsafe extern "C" fn nerva_truncate_to_tokens(
    text: *const c_char,
    max_tokens: u32,
) -> *mut c_char {
    match cstr_to_str(text) {
        Some(s) => string_to_c(nerva_core::truncate_to_tokens(s, max_tokens)),
        None => ptr::null_mut(),
    }
}

// ---------------------------------------------------------------------------
// Schema validation
// ---------------------------------------------------------------------------

/// Validates a JSON instance string against a JSON schema string.
///
/// Returns a newline-separated string of validation errors, or an
/// empty string if valid. Must be freed with `nerva_free_string`.
///
/// # Safety
///
/// Both `instance` and `schema` must be valid null-terminated UTF-8
/// strings or null. Returns null for null inputs.
#[no_mangle]
pub unsafe extern "C" fn nerva_validate_schema(
    instance: *const c_char,
    schema: *const c_char,
) -> *mut c_char {
    let inst_str = match cstr_to_str(instance) {
        Some(s) => s,
        None => return ptr::null_mut(),
    };
    let schema_str = match cstr_to_str(schema) {
        Some(s) => s,
        None => return ptr::null_mut(),
    };

    let inst_val: serde_json::Value = match serde_json::from_str(inst_str) {
        Ok(v) => v,
        Err(e) => return string_to_c(format!("invalid instance JSON: {}", e)),
    };
    let schema_val: serde_json::Value = match serde_json::from_str(schema_str) {
        Ok(v) => v,
        Err(e) => return string_to_c(format!("invalid schema JSON: {}", e)),
    };

    let errors = nerva_core::validate(&inst_val, &schema_val);
    string_to_c(errors.join("\n"))
}
