// Package tools provides tool discovery and execution within sandbox constraints.
//
// Defines the ToolManager interface, ToolSpec, ToolResult, and ToolStatus.
package tools

import (
	nctx "github.com/otomus/nerva/go/context"
)

// ToolStatus is the outcome status of a tool call.
type ToolStatus string

const (
	// ToolSuccess indicates the tool completed normally.
	ToolSuccess ToolStatus = "success"
	// ToolError indicates the tool raised an error.
	ToolError ToolStatus = "error"
	// ToolPermissionDenied indicates the caller lacks permission.
	ToolPermissionDenied ToolStatus = "permission_denied"
	// ToolNotFound indicates the tool does not exist.
	ToolNotFound ToolStatus = "not_found"
	// ToolTimeout indicates the tool exceeded its deadline.
	ToolTimeout ToolStatus = "timeout"
)

// ToolSpec describes a discoverable tool.
type ToolSpec struct {
	Name                string
	Description         string
	Parameters          map[string]any
	RequiredPermissions map[string]bool
}

// ToolResult is the result from executing a tool.
type ToolResult struct {
	Status     ToolStatus
	Output     string
	Error      string
	DurationMs float64
}

// ToolManager discovers and executes tools within sandbox constraints.
type ToolManager interface {
	// Discover returns available tools filtered by the context's permissions.
	Discover(ctx *nctx.ExecContext) ([]ToolSpec, error)

	// Call executes a tool call within sandbox constraints.
	Call(ctx *nctx.ExecContext, name string, args map[string]any) (ToolResult, error)
}
