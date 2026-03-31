package registry

import (
	"sync"
	"testing"

	nctx "github.com/otomus/nerva/go/context"
)

// --- InMemoryRegistry ---

func TestNewInMemoryRegistry(t *testing.T) {
	r := NewInMemoryRegistry()
	if r == nil {
		t.Fatal("expected non-nil registry")
	}
}

func TestRegisterAndResolve(t *testing.T) {
	r := NewInMemoryRegistry()
	ctx := nctx.NewContext()

	entry := RegistryEntry{
		Name:        "agent-a",
		Kind:        KindAgent,
		Description: "test agent",
		Health:      HealthHealthy,
		Enabled:     true,
		Metadata:    map[string]string{"key": "val"},
	}
	err := r.Register(ctx, entry)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	resolved, err := r.Resolve(ctx, "agent-a")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if resolved == nil {
		t.Fatal("expected non-nil entry")
	}
	if resolved.Name != "agent-a" {
		t.Fatalf("expected 'agent-a', got %q", resolved.Name)
	}
	if resolved.Description != "test agent" {
		t.Fatalf("expected 'test agent', got %q", resolved.Description)
	}
}

func TestResolveNotFound(t *testing.T) {
	r := NewInMemoryRegistry()
	ctx := nctx.NewContext()

	resolved, err := r.Resolve(ctx, "nonexistent")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if resolved != nil {
		t.Fatal("expected nil for nonexistent entry")
	}
}

func TestRegisterOverwrites(t *testing.T) {
	r := NewInMemoryRegistry()
	ctx := nctx.NewContext()

	r.Register(ctx, RegistryEntry{Name: "a", Kind: KindAgent, Description: "v1", Health: HealthHealthy, Enabled: true})
	r.Register(ctx, RegistryEntry{Name: "a", Kind: KindAgent, Description: "v2", Health: HealthHealthy, Enabled: true})

	resolved, _ := r.Resolve(ctx, "a")
	if resolved.Description != "v2" {
		t.Fatalf("expected 'v2', got %q", resolved.Description)
	}
}

func TestResolveReturnsCopy(t *testing.T) {
	r := NewInMemoryRegistry()
	ctx := nctx.NewContext()
	r.Register(ctx, RegistryEntry{Name: "a", Kind: KindAgent, Description: "orig", Health: HealthHealthy, Enabled: true})

	resolved, _ := r.Resolve(ctx, "a")
	resolved.Description = "mutated"

	original, _ := r.Resolve(ctx, "a")
	if original.Description == "mutated" {
		t.Fatal("Resolve should return a copy")
	}
}

// --- Discover ---

func TestDiscoverByKind(t *testing.T) {
	r := NewInMemoryRegistry()
	ctx := nctx.NewContext()

	r.Register(ctx, RegistryEntry{Name: "a1", Kind: KindAgent, Health: HealthHealthy, Enabled: true})
	r.Register(ctx, RegistryEntry{Name: "t1", Kind: KindTool, Health: HealthHealthy, Enabled: true})
	r.Register(ctx, RegistryEntry{Name: "a2", Kind: KindAgent, Health: HealthHealthy, Enabled: true})

	agents, _ := r.Discover(ctx, KindAgent)
	if len(agents) != 2 {
		t.Fatalf("expected 2 agents, got %d", len(agents))
	}

	tools, _ := r.Discover(ctx, KindTool)
	if len(tools) != 1 {
		t.Fatalf("expected 1 tool, got %d", len(tools))
	}
}

func TestDiscoverSortedByName(t *testing.T) {
	r := NewInMemoryRegistry()
	ctx := nctx.NewContext()

	r.Register(ctx, RegistryEntry{Name: "zebra", Kind: KindAgent, Health: HealthHealthy, Enabled: true})
	r.Register(ctx, RegistryEntry{Name: "alpha", Kind: KindAgent, Health: HealthHealthy, Enabled: true})

	agents, _ := r.Discover(ctx, KindAgent)
	if agents[0].Name != "alpha" {
		t.Fatal("expected alphabetical sort")
	}
}

func TestDiscoverExcludesDisabled(t *testing.T) {
	r := NewInMemoryRegistry()
	ctx := nctx.NewContext()

	r.Register(ctx, RegistryEntry{Name: "disabled", Kind: KindAgent, Health: HealthHealthy, Enabled: false})
	r.Register(ctx, RegistryEntry{Name: "enabled", Kind: KindAgent, Health: HealthHealthy, Enabled: true})

	agents, _ := r.Discover(ctx, KindAgent)
	if len(agents) != 1 {
		t.Fatalf("expected 1 (disabled excluded), got %d", len(agents))
	}
}

func TestDiscoverExcludesUnavailable(t *testing.T) {
	r := NewInMemoryRegistry()
	ctx := nctx.NewContext()

	r.Register(ctx, RegistryEntry{Name: "down", Kind: KindAgent, Health: HealthUnavailable, Enabled: true})
	r.Register(ctx, RegistryEntry{Name: "up", Kind: KindAgent, Health: HealthHealthy, Enabled: true})

	agents, _ := r.Discover(ctx, KindAgent)
	if len(agents) != 1 {
		t.Fatalf("expected 1 (unavailable excluded), got %d", len(agents))
	}
}

func TestDiscoverIncludesDegraded(t *testing.T) {
	r := NewInMemoryRegistry()
	ctx := nctx.NewContext()

	r.Register(ctx, RegistryEntry{Name: "degraded", Kind: KindAgent, Health: HealthDegraded, Enabled: true})

	agents, _ := r.Discover(ctx, KindAgent)
	if len(agents) != 1 {
		t.Fatal("degraded agents should be discoverable")
	}
}

func TestDiscoverWithPermissions(t *testing.T) {
	r := NewInMemoryRegistry()

	r.Register(nil, RegistryEntry{
		Name:        "restricted",
		Kind:        KindAgent,
		Health:      HealthHealthy,
		Enabled:     true,
		Permissions: []string{"admin"},
	})
	r.Register(nil, RegistryEntry{
		Name:    "open",
		Kind:    KindAgent,
		Health:  HealthHealthy,
		Enabled: true,
	})

	// Without admin role
	ctx := nctx.NewContext()
	agents, _ := r.Discover(ctx, KindAgent)
	if len(agents) != 1 {
		t.Fatalf("expected 1 (restricted excluded), got %d", len(agents))
	}

	// With admin role
	perms := nctx.Permissions{Roles: map[string]bool{"admin": true}}
	ctxAdmin := nctx.NewContext(nctx.WithPermissions(perms))
	agents2, _ := r.Discover(ctxAdmin, KindAgent)
	if len(agents2) != 2 {
		t.Fatalf("expected 2 with admin role, got %d", len(agents2))
	}
}

func TestDiscoverEmpty(t *testing.T) {
	r := NewInMemoryRegistry()
	ctx := nctx.NewContext()

	agents, _ := r.Discover(ctx, KindAgent)
	if len(agents) != 0 {
		t.Fatalf("expected 0, got %d", len(agents))
	}
}

// --- Health ---

func TestHealthExistingComponent(t *testing.T) {
	r := NewInMemoryRegistry()
	ctx := nctx.NewContext()
	r.Register(ctx, RegistryEntry{Name: "a", Kind: KindAgent, Health: HealthDegraded, Enabled: true})

	health, err := r.Health("a")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if health != HealthDegraded {
		t.Fatalf("expected degraded, got %s", health)
	}
}

func TestHealthNotFound(t *testing.T) {
	r := NewInMemoryRegistry()
	_, err := r.Health("nonexistent")
	if err == nil {
		t.Fatal("expected error for nonexistent component")
	}
}

// --- Update ---

func TestUpdateDescription(t *testing.T) {
	r := NewInMemoryRegistry()
	ctx := nctx.NewContext()
	r.Register(ctx, RegistryEntry{Name: "a", Kind: KindAgent, Description: "old", Health: HealthHealthy, Enabled: true})

	newDesc := "new description"
	err := r.Update("a", RegistryPatch{Description: &newDesc})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	resolved, _ := r.Resolve(ctx, "a")
	if resolved.Description != "new description" {
		t.Fatalf("expected 'new description', got %q", resolved.Description)
	}
}

func TestUpdateHealth(t *testing.T) {
	r := NewInMemoryRegistry()
	ctx := nctx.NewContext()
	r.Register(ctx, RegistryEntry{Name: "a", Kind: KindAgent, Health: HealthHealthy, Enabled: true})

	newHealth := HealthDegraded
	r.Update("a", RegistryPatch{Health: &newHealth})

	health, _ := r.Health("a")
	if health != HealthDegraded {
		t.Fatalf("expected degraded, got %s", health)
	}
}

func TestUpdateEnabled(t *testing.T) {
	r := NewInMemoryRegistry()
	ctx := nctx.NewContext()
	r.Register(ctx, RegistryEntry{Name: "a", Kind: KindAgent, Health: HealthHealthy, Enabled: true})

	disabled := false
	r.Update("a", RegistryPatch{Enabled: &disabled})

	resolved, _ := r.Resolve(ctx, "a")
	if resolved.Enabled {
		t.Fatal("expected disabled")
	}
}

func TestUpdateNotFound(t *testing.T) {
	r := NewInMemoryRegistry()
	newDesc := "x"
	err := r.Update("nonexistent", RegistryPatch{Description: &newDesc})
	if err == nil {
		t.Fatal("expected error for nonexistent component")
	}
}

func TestUpdateNilFields(t *testing.T) {
	r := NewInMemoryRegistry()
	ctx := nctx.NewContext()
	r.Register(ctx, RegistryEntry{Name: "a", Kind: KindAgent, Description: "orig", Health: HealthHealthy, Enabled: true})

	// Empty patch should change nothing
	err := r.Update("a", RegistryPatch{})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	resolved, _ := r.Resolve(ctx, "a")
	if resolved.Description != "orig" {
		t.Fatal("empty patch should not change description")
	}
}

// --- InvocationStats ---

func TestInvocationStatsRecordSuccess(t *testing.T) {
	s := &InvocationStats{}
	s.RecordSuccess(100.0)

	if s.TotalCalls != 1 {
		t.Fatalf("expected 1, got %d", s.TotalCalls)
	}
	if s.Successes != 1 {
		t.Fatalf("expected 1, got %d", s.Successes)
	}
	if s.LastInvokedAt == nil {
		t.Fatal("expected non-nil LastInvokedAt")
	}
	if s.AvgDurationMs != 100.0 {
		t.Fatalf("expected 100.0, got %f", s.AvgDurationMs)
	}
}

func TestInvocationStatsRecordFailure(t *testing.T) {
	s := &InvocationStats{}
	s.RecordFailure(50.0)

	if s.TotalCalls != 1 {
		t.Fatalf("expected 1, got %d", s.TotalCalls)
	}
	if s.Failures != 1 {
		t.Fatalf("expected 1, got %d", s.Failures)
	}
}

func TestInvocationStatsEMA(t *testing.T) {
	s := &InvocationStats{}
	s.RecordSuccess(100.0)
	s.RecordSuccess(200.0)

	// EMA: 0.2*200 + 0.8*100 = 40 + 80 = 120
	if s.AvgDurationMs != 120.0 {
		t.Fatalf("expected 120.0, got %f", s.AvgDurationMs)
	}
}

// --- Concurrent access ---

func TestConcurrentRegistryAccess(t *testing.T) {
	r := NewInMemoryRegistry()
	var wg sync.WaitGroup
	count := 50

	for i := 0; i < count; i++ {
		wg.Add(3)
		go func(n int) {
			defer wg.Done()
			r.Register(nil, RegistryEntry{
				Name: "agent", Kind: KindAgent, Health: HealthHealthy, Enabled: true,
			})
		}(i)
		go func() {
			defer wg.Done()
			r.Discover(nctx.NewContext(), KindAgent)
		}()
		go func() {
			defer wg.Done()
			r.Resolve(nctx.NewContext(), "agent")
		}()
	}
	wg.Wait()
	// No panic = pass
}

// --- SqliteRegistry ---

func TestSqliteRegistryBasicCRUD(t *testing.T) {
	r, err := NewSqliteRegistry(":memory:")
	if err != nil {
		t.Fatalf("failed to create sqlite registry: %v", err)
	}
	defer r.Close()

	ctx := nctx.NewContext()

	entry := RegistryEntry{
		Name:        "sql-agent",
		Kind:        KindAgent,
		Description: "sqlite-backed agent",
		Health:      HealthHealthy,
		Enabled:     true,
		Metadata:    map[string]string{"env": "test"},
	}
	err = r.Register(ctx, entry)
	if err != nil {
		t.Fatalf("register failed: %v", err)
	}

	resolved, err := r.Resolve(ctx, "sql-agent")
	if err != nil {
		t.Fatalf("resolve failed: %v", err)
	}
	if resolved == nil {
		t.Fatal("expected non-nil entry")
	}
	if resolved.Description != "sqlite-backed agent" {
		t.Fatalf("expected description, got %q", resolved.Description)
	}
}

func TestSqliteRegistryResolveNotFound(t *testing.T) {
	r, _ := NewSqliteRegistry(":memory:")
	defer r.Close()

	resolved, err := r.Resolve(nctx.NewContext(), "missing")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if resolved != nil {
		t.Fatal("expected nil for missing entry")
	}
}

func TestSqliteRegistryDiscover(t *testing.T) {
	r, _ := NewSqliteRegistry(":memory:")
	defer r.Close()
	ctx := nctx.NewContext()

	r.Register(ctx, RegistryEntry{Name: "a1", Kind: KindAgent, Health: HealthHealthy, Enabled: true})
	r.Register(ctx, RegistryEntry{Name: "t1", Kind: KindTool, Health: HealthHealthy, Enabled: true})
	r.Register(ctx, RegistryEntry{Name: "a2", Kind: KindAgent, Health: HealthHealthy, Enabled: true})
	r.Register(ctx, RegistryEntry{Name: "disabled", Kind: KindAgent, Health: HealthHealthy, Enabled: false})
	r.Register(ctx, RegistryEntry{Name: "down", Kind: KindAgent, Health: HealthUnavailable, Enabled: true})

	agents, _ := r.Discover(ctx, KindAgent)
	if len(agents) != 2 {
		t.Fatalf("expected 2 discoverable agents, got %d", len(agents))
	}
}

func TestSqliteRegistryHealthNotFound(t *testing.T) {
	r, _ := NewSqliteRegistry(":memory:")
	defer r.Close()

	_, err := r.Health("missing")
	if err == nil {
		t.Fatal("expected error for missing component")
	}
}

func TestSqliteRegistryUpdate(t *testing.T) {
	r, _ := NewSqliteRegistry(":memory:")
	defer r.Close()
	ctx := nctx.NewContext()

	r.Register(ctx, RegistryEntry{Name: "a", Kind: KindAgent, Description: "old", Health: HealthHealthy, Enabled: true})

	newDesc := "updated"
	err := r.Update("a", RegistryPatch{Description: &newDesc})
	if err != nil {
		t.Fatalf("update failed: %v", err)
	}

	resolved, _ := r.Resolve(ctx, "a")
	if resolved.Description != "updated" {
		t.Fatalf("expected 'updated', got %q", resolved.Description)
	}
}

func TestSqliteRegistryUpdateNotFound(t *testing.T) {
	r, _ := NewSqliteRegistry(":memory:")
	defer r.Close()

	newDesc := "x"
	err := r.Update("missing", RegistryPatch{Description: &newDesc})
	if err == nil {
		t.Fatal("expected error for missing component")
	}
}

func TestSqliteRegistryRegisterOverwrites(t *testing.T) {
	r, _ := NewSqliteRegistry(":memory:")
	defer r.Close()
	ctx := nctx.NewContext()

	r.Register(ctx, RegistryEntry{Name: "a", Kind: KindAgent, Description: "v1", Health: HealthHealthy, Enabled: true})
	r.Register(ctx, RegistryEntry{Name: "a", Kind: KindAgent, Description: "v2", Health: HealthHealthy, Enabled: true})

	resolved, _ := r.Resolve(ctx, "a")
	if resolved.Description != "v2" {
		t.Fatalf("expected 'v2', got %q", resolved.Description)
	}
}
