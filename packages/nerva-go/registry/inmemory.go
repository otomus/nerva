package registry

import (
	"fmt"
	"sort"
	"sync"

	nctx "github.com/otomus/nerva/go/context"
)

// InMemoryRegistry is a registry backed by a plain map. No persistence.
// Suitable for tests and single-process deployments.
type InMemoryRegistry struct {
	mu      sync.RWMutex
	entries map[string]*RegistryEntry
}

// NewInMemoryRegistry creates a new in-memory registry.
func NewInMemoryRegistry() *InMemoryRegistry {
	return &InMemoryRegistry{
		entries: make(map[string]*RegistryEntry),
	}
}

// Register adds or replaces a component in the registry.
func (r *InMemoryRegistry) Register(_ *nctx.ExecContext, entry RegistryEntry) error {
	r.mu.Lock()
	defer r.mu.Unlock()
	e := entry // copy
	r.entries[entry.Name] = &e
	return nil
}

// Discover lists components of a given kind visible to the caller.
func (r *InMemoryRegistry) Discover(ctx *nctx.ExecContext, kind ComponentKind) ([]RegistryEntry, error) {
	r.mu.RLock()
	defer r.mu.RUnlock()

	var results []RegistryEntry
	for _, entry := range r.entries {
		if !matchesDiscoveryCriteria(entry, kind, ctx) {
			continue
		}
		results = append(results, *entry)
	}

	sort.Slice(results, func(i, j int) bool {
		return results[i].Name < results[j].Name
	})

	return results, nil
}

// Resolve looks up a single component by name.
func (r *InMemoryRegistry) Resolve(_ *nctx.ExecContext, name string) (*RegistryEntry, error) {
	r.mu.RLock()
	defer r.mu.RUnlock()
	entry, ok := r.entries[name]
	if !ok {
		return nil, nil
	}
	e := *entry // copy
	return &e, nil
}

// Health gets the current health status of a component.
func (r *InMemoryRegistry) Health(name string) (HealthStatus, error) {
	r.mu.RLock()
	defer r.mu.RUnlock()
	entry, ok := r.entries[name]
	if !ok {
		return "", fmt.Errorf("component not found: %q", name)
	}
	return entry.Health, nil
}

// Update applies a partial update to a registered component.
func (r *InMemoryRegistry) Update(name string, patch RegistryPatch) error {
	r.mu.Lock()
	defer r.mu.Unlock()
	entry, ok := r.entries[name]
	if !ok {
		return fmt.Errorf("component not found: %q", name)
	}
	applyPatch(entry, patch)
	return nil
}

func matchesDiscoveryCriteria(entry *RegistryEntry, kind ComponentKind, ctx *nctx.ExecContext) bool {
	if entry.Kind != kind {
		return false
	}
	if !entry.Enabled {
		return false
	}
	if entry.Health == HealthUnavailable {
		return false
	}
	if len(entry.Permissions) > 0 && !hasRequiredPermission(entry, ctx) {
		return false
	}
	return true
}

func hasRequiredPermission(entry *RegistryEntry, ctx *nctx.ExecContext) bool {
	for _, perm := range entry.Permissions {
		if ctx.Permissions.HasRole(perm) {
			return true
		}
	}
	return false
}

func applyPatch(entry *RegistryEntry, patch RegistryPatch) {
	if patch.Description != nil {
		entry.Description = *patch.Description
	}
	if patch.Metadata != nil {
		entry.Metadata = *patch.Metadata
	}
	if patch.Health != nil {
		entry.Health = *patch.Health
	}
	if patch.Enabled != nil {
		entry.Enabled = *patch.Enabled
	}
	if patch.Requirements != nil {
		entry.Requirements = *patch.Requirements
	}
	if patch.Permissions != nil {
		entry.Permissions = *patch.Permissions
	}
}
