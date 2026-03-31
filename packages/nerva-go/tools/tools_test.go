package tools

import (
	"errors"
	"testing"

	nctx "github.com/otomus/nerva/go/context"
)

// --- FunctionToolManager creation ---

func TestNewFunctionToolManager(t *testing.T) {
	m := NewFunctionToolManager()
	if m == nil {
		t.Fatal("expected non-nil manager")
	}
}

// --- Register ---

func TestRegisterAndDiscover(t *testing.T) {
	m := NewFunctionToolManager()
	fn := func(s string) string { return s }

	err := m.Register("echo", "echoes input", fn, nil)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	ctx := nctx.NewContext()
	specs, err := m.Discover(ctx)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(specs) != 1 {
		t.Fatalf("expected 1 spec, got %d", len(specs))
	}
	if specs[0].Name != "echo" {
		t.Fatalf("expected 'echo', got %q", specs[0].Name)
	}
	if specs[0].Description != "echoes input" {
		t.Fatalf("expected 'echoes input', got %q", specs[0].Description)
	}
}

func TestRegisterDuplicateName(t *testing.T) {
	m := NewFunctionToolManager()
	fn := func() {}

	if err := m.Register("tool", "first", fn, nil); err != nil {
		t.Fatal(err)
	}
	err := m.Register("tool", "second", fn, nil)
	if err == nil {
		t.Fatal("expected error for duplicate name")
	}
}

// --- Discover with permissions ---

func TestDiscoverFiltersRestrictedTools(t *testing.T) {
	m := NewFunctionToolManager()
	fn := func() {}
	m.Register("public", "pub", fn, nil)
	m.Register("private", "priv", fn, nil)

	allowed := map[string]bool{"public": true}
	perms := nctx.Permissions{AllowedTools: &allowed}
	ctx := nctx.NewContext(nctx.WithPermissions(perms))

	specs, _ := m.Discover(ctx)
	if len(specs) != 1 {
		t.Fatalf("expected 1, got %d", len(specs))
	}
	if specs[0].Name != "public" {
		t.Fatalf("expected 'public', got %q", specs[0].Name)
	}
}

func TestDiscoverFiltersRequiredRoles(t *testing.T) {
	m := NewFunctionToolManager()
	fn := func() {}
	m.Register("admin-tool", "needs admin", fn, map[string]bool{"admin": true})

	// No roles
	ctx := nctx.NewContext()
	specs, _ := m.Discover(ctx)
	if len(specs) != 0 {
		t.Fatalf("expected 0 tools for user without admin role, got %d", len(specs))
	}

	// With admin role
	perms := nctx.Permissions{Roles: map[string]bool{"admin": true}}
	ctx2 := nctx.NewContext(nctx.WithPermissions(perms))
	specs2, _ := m.Discover(ctx2)
	if len(specs2) != 1 {
		t.Fatalf("expected 1 tool for admin, got %d", len(specs2))
	}
}

// --- Call ---

func TestCallSuccess(t *testing.T) {
	m := NewFunctionToolManager()
	fn := func(a string, b string) string { return a + " " + b }
	m.Register("concat", "concatenates", fn, nil)

	ctx := nctx.NewContext()
	result, err := m.Call(ctx, "concat", map[string]any{"arg0": "hello", "arg1": "world"})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if result.Status != ToolSuccess {
		t.Fatalf("expected success, got %s: %s", result.Status, result.Error)
	}
	if result.Output != "hello world" {
		t.Fatalf("expected 'hello world', got %q", result.Output)
	}
	if result.DurationMs < 0 {
		t.Fatal("expected non-negative duration")
	}
}

func TestCallReturnsError(t *testing.T) {
	m := NewFunctionToolManager()
	fn := func() error { return errors.New("boom") }
	m.Register("fail", "always fails", fn, nil)

	ctx := nctx.NewContext()
	result, err := m.Call(ctx, "fail", nil)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if result.Status != ToolError {
		t.Fatalf("expected error status, got %s", result.Status)
	}
	if result.Error != "boom" {
		t.Fatalf("expected 'boom', got %q", result.Error)
	}
}

func TestCallToolNotFound(t *testing.T) {
	m := NewFunctionToolManager()
	ctx := nctx.NewContext()

	result, err := m.Call(ctx, "nonexistent", nil)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if result.Status != ToolNotFound {
		t.Fatalf("expected not_found, got %s", result.Status)
	}
}

func TestCallPermissionDeniedByAllowlist(t *testing.T) {
	m := NewFunctionToolManager()
	fn := func() string { return "ok" }
	m.Register("secret", "secret tool", fn, nil)

	allowed := map[string]bool{"other": true}
	perms := nctx.Permissions{AllowedTools: &allowed}
	ctx := nctx.NewContext(nctx.WithPermissions(perms))

	result, _ := m.Call(ctx, "secret", nil)
	if result.Status != ToolPermissionDenied {
		t.Fatalf("expected permission_denied, got %s", result.Status)
	}
}

func TestCallPermissionDeniedByRole(t *testing.T) {
	m := NewFunctionToolManager()
	fn := func() string { return "ok" }
	m.Register("admin-fn", "admin only", fn, map[string]bool{"admin": true})

	ctx := nctx.NewContext() // no roles
	result, _ := m.Call(ctx, "admin-fn", nil)
	if result.Status != ToolPermissionDenied {
		t.Fatalf("expected permission_denied, got %s", result.Status)
	}
}

func TestCallMissingArgs(t *testing.T) {
	m := NewFunctionToolManager()
	fn := func(a string) string { return a }
	m.Register("echo", "echo", fn, nil)

	ctx := nctx.NewContext()
	// Call without providing any args — should use zero values
	result, err := m.Call(ctx, "echo", nil)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if result.Status != ToolSuccess {
		t.Fatalf("expected success, got %s: %s", result.Status, result.Error)
	}
	// zero-value string is ""
	if result.Output != "" {
		t.Fatalf("expected empty output for zero-value arg, got %q", result.Output)
	}
}

func TestCallIntegerArg(t *testing.T) {
	m := NewFunctionToolManager()
	fn := func(n int) int { return n * 2 }
	m.Register("double", "doubles", fn, nil)

	ctx := nctx.NewContext()
	// JSON numbers come as float64 in Go's map[string]any
	result, _ := m.Call(ctx, "double", map[string]any{"arg0": 5})
	if result.Status != ToolSuccess {
		t.Fatalf("expected success, got %s: %s", result.Status, result.Error)
	}
	if result.Output != "10" {
		t.Fatalf("expected '10', got %q", result.Output)
	}
}

func TestCallNoArgsFunction(t *testing.T) {
	m := NewFunctionToolManager()
	fn := func() string { return "done" }
	m.Register("noop", "no args", fn, nil)

	ctx := nctx.NewContext()
	result, _ := m.Call(ctx, "noop", nil)
	if result.Status != ToolSuccess {
		t.Fatalf("expected success, got %s", result.Status)
	}
	if result.Output != "done" {
		t.Fatalf("expected 'done', got %q", result.Output)
	}
}

// --- extractParameters ---

func TestExtractParametersFunction(t *testing.T) {
	fn := func(a string, b int) {}
	params := extractParameters(fn)

	props, ok := params["properties"].(map[string]any)
	if !ok {
		t.Fatal("expected properties map")
	}
	if len(props) != 2 {
		t.Fatalf("expected 2 properties, got %d", len(props))
	}

	arg0, ok := props["arg0"].(map[string]string)
	if !ok || arg0["type"] != "string" {
		t.Fatal("expected arg0 type string")
	}

	arg1, ok := props["arg1"].(map[string]string)
	if !ok || arg1["type"] != "integer" {
		t.Fatal("expected arg1 type integer")
	}
}

func TestExtractParametersNonFunction(t *testing.T) {
	params := extractParameters("not a function")
	if params["type"] != "object" {
		t.Fatal("expected type object for non-function")
	}
}

// --- ToolSpec fields ---

func TestToolSpecParameters(t *testing.T) {
	m := NewFunctionToolManager()
	fn := func(name string, age int, active bool) {}
	m.Register("test", "test tool", fn, nil)

	ctx := nctx.NewContext()
	specs, _ := m.Discover(ctx)
	if len(specs) != 1 {
		t.Fatalf("expected 1 spec, got %d", len(specs))
	}

	params := specs[0].Parameters
	required, ok := params["required"].([]string)
	if !ok {
		t.Fatal("expected required field")
	}
	if len(required) != 3 {
		t.Fatalf("expected 3 required params, got %d", len(required))
	}
}
