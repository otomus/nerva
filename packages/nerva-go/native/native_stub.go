//go:build !cgo || !nerva_native

// Package native provides pure Go fallbacks when the Rust native library
// is not available.
//
// This file compiles when the "nerva_native" build tag is absent or
// CGo is disabled. All functions produce identical results to the
// native implementations, just without SIMD optimization.
package native

import (
	"encoding/json"
	"fmt"
	"math"
	"strings"
	"unicode"
)

// Available reports whether the native library is loaded.
func Available() bool {
	return false
}

// CosineSimilarity computes cosine similarity between two float32 slices.
//
// Returns a value in [-1.0, 1.0]. Returns 0.0 if lengths differ,
// either slice is empty, or either vector has zero magnitude.
func CosineSimilarity(a, b []float32) float64 {
	if len(a) != len(b) || len(a) == 0 {
		return 0.0
	}

	var dot, normA, normB float64
	for i := range a {
		ai := float64(a[i])
		bi := float64(b[i])
		dot += ai * bi
		normA += ai * ai
		normB += bi * bi
	}

	denom := math.Sqrt(normA) * math.Sqrt(normB)
	if denom == 0 {
		return 0.0
	}

	result := dot / denom
	return math.Max(-1.0, math.Min(1.0, result))
}

// isSplitter returns true if the rune is a token-boundary character.
func isSplitter(r rune) bool {
	switch r {
	case ' ', '\t', '\n', '\r', '.', ',', ';', ':', '!', '?',
		'(', ')', '[', ']', '{', '}', '"', '\'',
		'/', '\\', '-', '_', '@', '#', '$', '%', '&', '*',
		'+', '=', '<', '>', '|', '~', '`', '^':
		return true
	}
	return false
}

// CountTokens counts the approximate number of tokens in text.
//
// Splits on whitespace and punctuation boundaries, producing a
// rough BPE-style count.
func CountTokens(text string) uint32 {
	var count uint32
	inWord := false

	for _, ch := range text {
		if isSplitter(ch) {
			if inWord {
				count++
				inWord = false
			}
			if !unicode.IsSpace(ch) {
				count++
			}
		} else {
			inWord = true
		}
	}

	if inWord {
		count++
	}
	return count
}

// TruncateToTokens truncates text to at most maxTokens approximate tokens.
//
// Preserves whole words — never splits mid-character.
func TruncateToTokens(text string, maxTokens uint32) string {
	if maxTokens == 0 {
		return ""
	}

	var tokens uint32
	var lastEnd int
	inWord := false
	wordStart := 0

	for i, ch := range text {
		if isSplitter(ch) {
			if inWord {
				tokens++
				if tokens > maxTokens {
					return strings.TrimRight(text[:wordStart], " \t\n\r")
				}
				lastEnd = i
				inWord = false
			}
			if !unicode.IsSpace(ch) {
				tokens++
				if tokens > maxTokens {
					return strings.TrimRight(text[:i], " \t\n\r")
				}
				lastEnd = i + len(string(ch))
			}
		} else {
			if !inWord {
				wordStart = i
				inWord = true
			}
		}
	}

	if inWord {
		tokens++
		if tokens > maxTokens {
			return strings.TrimRight(text[:wordStart], " \t\n\r")
		}
		lastEnd = len(text)
	}

	return text[:lastEnd]
}

// ValidateSchema validates a JSON instance against a JSON schema.
//
// Pure Go implementation that checks type, required, enum, properties,
// and items constraints. Returns a slice of error strings.
func ValidateSchema(instance, schema string) []string {
	var instVal interface{}
	if err := json.Unmarshal([]byte(instance), &instVal); err != nil {
		return []string{fmt.Sprintf("invalid instance JSON: %v", err)}
	}
	var schemaVal map[string]interface{}
	if err := json.Unmarshal([]byte(schema), &schemaVal); err != nil {
		return []string{fmt.Sprintf("invalid schema JSON: %v", err)}
	}

	var errors []string
	validateNode(instVal, schemaVal, "$", &errors)
	return errors
}

// validateNode recursively validates a value against its schema definition.
func validateNode(instance interface{}, schema map[string]interface{}, path string, errors *[]string) {
	if typeVal, ok := schema["type"].(string); ok {
		if !typeMatches(instance, typeVal) {
			*errors = append(*errors, fmt.Sprintf("%s: expected type '%s', got '%s'", path, typeVal, jsonTypeName(instance)))
			return
		}
	}

	validateEnum(instance, schema, path, errors)
	validateRequired(instance, schema, path, errors)
	validateProperties(instance, schema, path, errors)
	validateItems(instance, schema, path, errors)
}

// typeMatches checks if the instance matches the expected JSON Schema type.
func typeMatches(value interface{}, expected string) bool {
	switch expected {
	case "object":
		_, ok := value.(map[string]interface{})
		return ok
	case "array":
		_, ok := value.([]interface{})
		return ok
	case "string":
		_, ok := value.(string)
		return ok
	case "number":
		_, ok := value.(float64)
		return ok
	case "integer":
		f, ok := value.(float64)
		return ok && f == math.Floor(f)
	case "boolean":
		_, ok := value.(bool)
		return ok
	case "null":
		return value == nil
	}
	return true
}

// validateEnum checks enum constraints.
func validateEnum(instance interface{}, schema map[string]interface{}, path string, errors *[]string) {
	enumVal, ok := schema["enum"].([]interface{})
	if !ok {
		return
	}
	instanceJSON, _ := json.Marshal(instance)
	for _, allowed := range enumVal {
		allowedJSON, _ := json.Marshal(allowed)
		if string(instanceJSON) == string(allowedJSON) {
			return
		}
	}
	*errors = append(*errors, fmt.Sprintf("%s: value not in enum", path))
}

// validateRequired checks required fields on object instances.
func validateRequired(instance interface{}, schema map[string]interface{}, path string, errors *[]string) {
	obj, isObj := instance.(map[string]interface{})
	required, hasReq := schema["required"].([]interface{})
	if !isObj || !hasReq {
		return
	}
	for _, req := range required {
		fieldName, ok := req.(string)
		if !ok {
			continue
		}
		if _, exists := obj[fieldName]; !exists {
			*errors = append(*errors, fmt.Sprintf("%s: missing required field '%s'", path, fieldName))
		}
	}
}

// validateProperties validates object properties against sub-schemas.
func validateProperties(instance interface{}, schema map[string]interface{}, path string, errors *[]string) {
	obj, isObj := instance.(map[string]interface{})
	props, hasProps := schema["properties"].(map[string]interface{})
	if !isObj || !hasProps {
		return
	}
	for key, subSchema := range props {
		value, exists := obj[key]
		if !exists {
			continue
		}
		sub, ok := subSchema.(map[string]interface{})
		if !ok {
			continue
		}
		childPath := path + "." + key
		validateNode(value, sub, childPath, errors)
	}
}

// validateItems validates array items against the items sub-schema.
func validateItems(instance interface{}, schema map[string]interface{}, path string, errors *[]string) {
	arr, isArr := instance.([]interface{})
	itemsSchema, hasItems := schema["items"].(map[string]interface{})
	if !isArr || !hasItems {
		return
	}
	for i, item := range arr {
		childPath := fmt.Sprintf("%s[%d]", path, i)
		validateNode(item, itemsSchema, childPath, errors)
	}
}

// jsonTypeName returns a human-readable type name for a JSON value.
func jsonTypeName(value interface{}) string {
	switch value.(type) {
	case nil:
		return "null"
	case bool:
		return "boolean"
	case float64:
		return "number"
	case string:
		return "string"
	case []interface{}:
		return "array"
	case map[string]interface{}:
		return "object"
	default:
		return "unknown"
	}
}
