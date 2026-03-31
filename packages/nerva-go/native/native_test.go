package native

import (
	"math"
	"testing"
)

// ---------------------------------------------------------------------------
// Availability
// ---------------------------------------------------------------------------

func TestAvailableReturnsBoolean(t *testing.T) {
	// Should not panic regardless of build tags.
	_ = Available()
}

// ---------------------------------------------------------------------------
// CosineSimilarity
// ---------------------------------------------------------------------------

func TestCosineSimilarityIdenticalVectors(t *testing.T) {
	v := []float32{1.0, 2.0, 3.0, 4.0}
	sim := CosineSimilarity(v, v)
	if math.Abs(sim-1.0) > 1e-5 {
		t.Errorf("expected ~1.0, got %f", sim)
	}
}

func TestCosineSimilarityOrthogonal(t *testing.T) {
	a := []float32{1.0, 0.0}
	b := []float32{0.0, 1.0}
	sim := CosineSimilarity(a, b)
	if math.Abs(sim) > 1e-5 {
		t.Errorf("expected ~0.0, got %f", sim)
	}
}

func TestCosineSimilarityOpposite(t *testing.T) {
	a := []float32{1.0, 2.0, 3.0}
	b := []float32{-1.0, -2.0, -3.0}
	sim := CosineSimilarity(a, b)
	if math.Abs(sim+1.0) > 1e-5 {
		t.Errorf("expected ~-1.0, got %f", sim)
	}
}

func TestCosineSimilarityEmptyVectors(t *testing.T) {
	sim := CosineSimilarity([]float32{}, []float32{})
	if sim != 0.0 {
		t.Errorf("expected 0.0, got %f", sim)
	}
}

func TestCosineSimilarityMismatchedLengths(t *testing.T) {
	a := []float32{1.0, 2.0}
	b := []float32{1.0}
	sim := CosineSimilarity(a, b)
	if sim != 0.0 {
		t.Errorf("expected 0.0 for mismatched lengths, got %f", sim)
	}
}

func TestCosineSimilarityZeroVector(t *testing.T) {
	a := []float32{0.0, 0.0, 0.0}
	b := []float32{1.0, 2.0, 3.0}
	sim := CosineSimilarity(a, b)
	if sim != 0.0 {
		t.Errorf("expected 0.0 for zero vector, got %f", sim)
	}
}

func TestCosineSimilarityNilSlices(t *testing.T) {
	sim := CosineSimilarity(nil, nil)
	if sim != 0.0 {
		t.Errorf("expected 0.0 for nil slices, got %f", sim)
	}
}

// ---------------------------------------------------------------------------
// CountTokens
// ---------------------------------------------------------------------------

func TestCountTokensEmpty(t *testing.T) {
	if n := CountTokens(""); n != 0 {
		t.Errorf("expected 0, got %d", n)
	}
}

func TestCountTokensSingleWord(t *testing.T) {
	if n := CountTokens("hello"); n != 1 {
		t.Errorf("expected 1, got %d", n)
	}
}

func TestCountTokensSimpleSentence(t *testing.T) {
	if n := CountTokens("hello world"); n != 2 {
		t.Errorf("expected 2, got %d", n)
	}
}

func TestCountTokensWithPunctuation(t *testing.T) {
	// "hello, world!" = hello + , + world + ! = 4
	if n := CountTokens("hello, world!"); n != 4 {
		t.Errorf("expected 4, got %d", n)
	}
}

func TestCountTokensWhitespaceOnly(t *testing.T) {
	if n := CountTokens("   \t\n  "); n != 0 {
		t.Errorf("expected 0, got %d", n)
	}
}

// ---------------------------------------------------------------------------
// TruncateToTokens
// ---------------------------------------------------------------------------

func TestTruncateToTokensWithinBudget(t *testing.T) {
	result := TruncateToTokens("hello world", 10)
	if result != "hello world" {
		t.Errorf("expected 'hello world', got %q", result)
	}
}

func TestTruncateToTokensZero(t *testing.T) {
	result := TruncateToTokens("hello world", 0)
	if result != "" {
		t.Errorf("expected empty string, got %q", result)
	}
}

func TestTruncateToTokensCuts(t *testing.T) {
	result := TruncateToTokens("one two three four five", 3)
	if result != "one two three" {
		t.Errorf("expected 'one two three', got %q", result)
	}
}

// ---------------------------------------------------------------------------
// ValidateSchema
// ---------------------------------------------------------------------------

func TestValidateSchemaValid(t *testing.T) {
	instance := `{"name": "alice"}`
	schema := `{"type": "object", "required": ["name"], "properties": {"name": {"type": "string"}}}`
	errors := ValidateSchema(instance, schema)
	if len(errors) != 0 {
		t.Errorf("expected no errors, got %v", errors)
	}
}

func TestValidateSchemaMissingRequired(t *testing.T) {
	instance := `{}`
	schema := `{"type": "object", "required": ["name"]}`
	errors := ValidateSchema(instance, schema)
	if len(errors) != 1 {
		t.Errorf("expected 1 error, got %d: %v", len(errors), errors)
	}
}

func TestValidateSchemaWrongType(t *testing.T) {
	instance := `42`
	schema := `{"type": "string"}`
	errors := ValidateSchema(instance, schema)
	if len(errors) != 1 {
		t.Errorf("expected 1 error, got %d: %v", len(errors), errors)
	}
}

func TestValidateSchemaInvalidJSON(t *testing.T) {
	errors := ValidateSchema("{bad", `{"type": "object"}`)
	if len(errors) != 1 {
		t.Errorf("expected 1 error for invalid JSON, got %d: %v", len(errors), errors)
	}
}

func TestValidateSchemaInvalidSchemaJSON(t *testing.T) {
	errors := ValidateSchema(`{}`, `{bad`)
	if len(errors) != 1 {
		t.Errorf("expected 1 error for invalid schema JSON, got %d: %v", len(errors), errors)
	}
}

// ---------------------------------------------------------------------------
// Consistency: native and fallback should match (if both compilable)
// ---------------------------------------------------------------------------

func TestResultsAreConsistentAcrossImplementations(t *testing.T) {
	// This test validates the pure Go fallback behavior regardless of
	// whether native is available. When running with CGo + nerva_native,
	// it validates the native implementation instead.
	a := []float32{1.0, 0.5, 0.0}
	b := []float32{0.5, 1.0, 0.0}
	sim := CosineSimilarity(a, b)

	// The exact value should be ~0.8 for these vectors.
	if sim < 0.7 || sim > 0.9 {
		t.Errorf("unexpected similarity %f for known vectors", sim)
	}

	tokens := CountTokens("hello, world!")
	if tokens != 4 {
		t.Errorf("token count mismatch: expected 4, got %d", tokens)
	}
}
