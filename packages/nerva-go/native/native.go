//go:build cgo && nerva_native

// Package native provides CGo bindings to the nerva-core Rust library.
//
// This file is only compiled when the "nerva_native" build tag is set
// and CGo is available. Otherwise, native_stub.go provides pure Go
// fallbacks.
package native

/*
#cgo LDFLAGS: -L${SRCDIR}/../../nerva-core-go/target/release -lnerva_core_go -lm -ldl
#include "../../nerva-core-go/nerva_core.h"
#include <stdlib.h>
*/
import "C"

import (
	"math"
	"unsafe"
)

// Available reports whether the native library is loaded.
func Available() bool {
	return true
}

// CosineSimilarity computes cosine similarity between two float32 slices
// using the Rust SIMD-optimized implementation.
//
// Returns 0.0 if lengths differ or either slice is empty.
func CosineSimilarity(a, b []float32) float64 {
	if len(a) == 0 || len(b) == 0 || len(a) != len(b) {
		return 0.0
	}
	result := C.nerva_cosine_similarity(
		(*C.float)(unsafe.Pointer(&a[0])), C.size_t(len(a)),
		(*C.float)(unsafe.Pointer(&b[0])), C.size_t(len(b)),
	)
	return float64(result)
}

// CountTokens counts the approximate number of tokens in text.
func CountTokens(text string) uint32 {
	cs := C.CString(text)
	defer C.free(unsafe.Pointer(cs))
	return uint32(C.nerva_count_tokens(cs))
}

// TruncateToTokens truncates text to at most maxTokens approximate tokens.
func TruncateToTokens(text string, maxTokens uint32) string {
	cs := C.CString(text)
	defer C.free(unsafe.Pointer(cs))
	result := C.nerva_truncate_to_tokens(cs, C.uint32_t(maxTokens))
	if result == nil {
		return ""
	}
	defer C.nerva_free_string(result)
	return C.GoString(result)
}

// ValidateSchema validates a JSON instance against a JSON schema.
//
// Returns a slice of validation error strings. An empty slice means valid.
func ValidateSchema(instance, schema string) []string {
	ci := C.CString(instance)
	defer C.free(unsafe.Pointer(ci))
	cs := C.CString(schema)
	defer C.free(unsafe.Pointer(cs))

	result := C.nerva_validate_schema(ci, cs)
	if result == nil {
		return nil
	}
	defer C.nerva_free_string(result)

	goStr := C.GoString(result)
	if goStr == "" {
		return nil
	}
	return splitLines(goStr)
}

// splitLines splits a newline-separated string into non-empty lines.
func splitLines(s string) []string {
	var lines []string
	start := 0
	for i := 0; i < len(s); i++ {
		if s[i] == '\n' {
			if i > start {
				lines = append(lines, s[start:i])
			}
			start = i + 1
		}
	}
	if start < len(s) {
		lines = append(lines, s[start:])
	}
	return lines
}

// Ensure math is used (prevents unused import when only CosineSimilarity is called).
var _ = math.Sqrt
