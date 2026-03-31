/*
 * nerva_core.h — C header for the nerva-core FFI functions.
 *
 * Used by CGo to call into the Rust static library.
 * All returned strings must be freed with nerva_free_string().
 */

#ifndef NERVA_CORE_H
#define NERVA_CORE_H

#include <stdint.h>
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

/**
 * Frees a string previously returned by a nerva_* function.
 * Safe to call with NULL.
 */
void nerva_free_string(char *s);

/**
 * Computes cosine similarity between two float arrays.
 *
 * Returns 0.0 if lengths differ, either pointer is NULL, or length is 0.
 */
float nerva_cosine_similarity(
    const float *a_ptr, size_t a_len,
    const float *b_ptr, size_t b_len
);

/**
 * Counts the approximate number of tokens in a UTF-8 string.
 * Returns 0 for NULL input.
 */
uint32_t nerva_count_tokens(const char *text);

/**
 * Truncates text to at most max_tokens approximate tokens.
 *
 * Returns an owned string that must be freed with nerva_free_string().
 * Returns NULL for NULL input.
 */
char *nerva_truncate_to_tokens(const char *text, uint32_t max_tokens);

/**
 * Validates a JSON instance string against a JSON schema string.
 *
 * Returns a newline-separated string of validation errors, or an
 * empty string if valid. Must be freed with nerva_free_string().
 * Returns NULL for NULL inputs.
 */
char *nerva_validate_schema(const char *instance, const char *schema);

#ifdef __cplusplus
}
#endif

#endif /* NERVA_CORE_H */
