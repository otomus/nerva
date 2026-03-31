// Package registry provides a unified catalog of agents, tools, and components.
//
// Defines the Registry interface and supporting types: RegistryEntry,
// ComponentKind, HealthStatus, InvocationStats, and RegistryPatch.
package registry

import (
	"time"

	nctx "github.com/otomus/nerva/go/context"
)

// ComponentKind classifies a registered component.
type ComponentKind string

const (
	// KindAgent is an agent handler that processes user input.
	KindAgent ComponentKind = "agent"
	// KindTool is a tool invocable by agents.
	KindTool ComponentKind = "tool"
	// KindSense is a sensory input processor.
	KindSense ComponentKind = "sense"
	// KindPlugin is an extension that hooks into lifecycle events.
	KindPlugin ComponentKind = "plugin"
)

// HealthStatus is the operational health of a registered component.
type HealthStatus string

const (
	// HealthHealthy means fully operational.
	HealthHealthy HealthStatus = "healthy"
	// HealthDegraded means operational with reduced capability.
	HealthDegraded HealthStatus = "degraded"
	// HealthUnavailable means not accepting invocations.
	HealthUnavailable HealthStatus = "unavailable"
)

// DurationSmoothingFactor is the EMA weight for new duration observations.
const DurationSmoothingFactor = 0.2

// InvocationStats tracks invocation metrics for a registered component.
type InvocationStats struct {
	TotalCalls    int
	Successes     int
	Failures      int
	LastInvokedAt *time.Time
	AvgDurationMs float64
}

// RecordSuccess records a successful invocation.
func (s *InvocationStats) RecordSuccess(durationMs float64) {
	s.TotalCalls++
	s.Successes++
	now := time.Now()
	s.LastInvokedAt = &now
	s.updateAvgDuration(durationMs)
}

// RecordFailure records a failed invocation.
func (s *InvocationStats) RecordFailure(durationMs float64) {
	s.TotalCalls++
	s.Failures++
	now := time.Now()
	s.LastInvokedAt = &now
	s.updateAvgDuration(durationMs)
}

func (s *InvocationStats) updateAvgDuration(durationMs float64) {
	if s.TotalCalls <= 1 {
		s.AvgDurationMs = durationMs
		return
	}
	s.AvgDurationMs = DurationSmoothingFactor*durationMs + (1-DurationSmoothingFactor)*s.AvgDurationMs
}

// RegistryEntry is a registered component in the catalog.
type RegistryEntry struct {
	Name         string
	Kind         ComponentKind
	Description  string
	Schema       map[string]any
	Metadata     map[string]string
	Health       HealthStatus
	Stats        InvocationStats
	Enabled      bool
	Requirements []string
	Permissions  []string
}

// RegistryPatch is a partial update for a registry entry.
// Only non-nil fields are applied when passed to Registry.Update().
type RegistryPatch struct {
	Description  *string
	Metadata     *map[string]string
	Health       *HealthStatus
	Enabled      *bool
	Requirements *[]string
	Permissions  *[]string
}

// Registry is the unified catalog of agents, tools, senses, and plugins.
type Registry interface {
	// Register adds or replaces a component in the registry.
	Register(ctx *nctx.ExecContext, entry RegistryEntry) error

	// Discover lists components of a given kind visible to the caller.
	Discover(ctx *nctx.ExecContext, kind ComponentKind) ([]RegistryEntry, error)

	// Resolve looks up a single component by name.
	Resolve(ctx *nctx.ExecContext, name string) (*RegistryEntry, error)

	// Health gets the current health status of a component.
	Health(name string) (HealthStatus, error)

	// Update applies a partial update to a registered component.
	Update(name string, patch RegistryPatch) error
}
