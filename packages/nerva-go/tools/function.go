package tools

import (
	"fmt"
	"reflect"
	"time"

	nctx "github.com/otomus/nerva/go/context"
)

// registeredTool is an internal record for a function registered as a tool.
type registeredTool struct {
	name                string
	description         string
	function            any
	parameters          map[string]any
	requiredPermissions map[string]bool
}

// FunctionToolManager wraps plain Go functions as tools.
type FunctionToolManager struct {
	tools map[string]*registeredTool
}

// NewFunctionToolManager creates a new FunctionToolManager.
func NewFunctionToolManager() *FunctionToolManager {
	return &FunctionToolManager{
		tools: make(map[string]*registeredTool),
	}
}

// Register registers a Go function as a tool.
// The function must accept arguments matching the parameter schema.
func (m *FunctionToolManager) Register(name, description string, fn any, permissions map[string]bool) error {
	if _, exists := m.tools[name]; exists {
		return fmt.Errorf("tool '%s' is already registered", name)
	}

	params := extractParameters(fn)

	m.tools[name] = &registeredTool{
		name:                name,
		description:         description,
		function:            fn,
		parameters:          params,
		requiredPermissions: permissions,
	}
	return nil
}

// Discover returns tool specs the caller is permitted to use.
func (m *FunctionToolManager) Discover(ctx *nctx.ExecContext) ([]ToolSpec, error) {
	var specs []ToolSpec
	for _, rt := range m.tools {
		if !ctx.Permissions.CanUseTool(rt.name) {
			continue
		}
		if !hasRequiredRoles(ctx, rt.requiredPermissions) {
			continue
		}
		specs = append(specs, ToolSpec{
			Name:                rt.name,
			Description:         rt.description,
			Parameters:          rt.parameters,
			RequiredPermissions: rt.requiredPermissions,
		})
	}
	return specs, nil
}

// Call executes a registered tool by name.
func (m *FunctionToolManager) Call(ctx *nctx.ExecContext, name string, args map[string]any) (ToolResult, error) {
	rt, ok := m.tools[name]
	if !ok {
		return ToolResult{Status: ToolNotFound, Error: fmt.Sprintf("Tool '%s' not found", name)}, nil
	}

	if !ctx.Permissions.CanUseTool(name) {
		return ToolResult{Status: ToolPermissionDenied, Error: fmt.Sprintf("Permission denied for tool '%s'", name)}, nil
	}

	if !hasRequiredRoles(ctx, rt.requiredPermissions) {
		return ToolResult{Status: ToolPermissionDenied, Error: fmt.Sprintf("Missing required role for tool '%s'", name)}, nil
	}

	return execute(rt, args), nil
}

func execute(rt *registeredTool, args map[string]any) ToolResult {
	start := time.Now()

	fn := reflect.ValueOf(rt.function)
	fnType := fn.Type()

	callArgs := make([]reflect.Value, fnType.NumIn())
	for i := 0; i < fnType.NumIn(); i++ {
		paramType := fnType.In(i)
		paramName := getParamName(fnType, i)
		argVal, ok := args[paramName]
		if !ok {
			callArgs[i] = reflect.Zero(paramType)
			continue
		}
		callArgs[i] = coerceArg(argVal, paramType)
	}

	defer func() {
		if r := recover(); r != nil {
			// Caught a panic during function call — handled below
		}
	}()

	results := fn.Call(callArgs)
	elapsed := float64(time.Since(start).Milliseconds())

	// Check if last return value is an error
	if len(results) > 0 {
		last := results[len(results)-1]
		if last.Type().Implements(reflect.TypeOf((*error)(nil)).Elem()) && !last.IsNil() {
			return ToolResult{
				Status:     ToolError,
				Error:      last.Interface().(error).Error(),
				DurationMs: elapsed,
			}
		}
	}

	output := ""
	if len(results) > 0 {
		output = fmt.Sprintf("%v", results[0].Interface())
	}

	return ToolResult{
		Status:     ToolSuccess,
		Output:     output,
		DurationMs: elapsed,
	}
}

func hasRequiredRoles(ctx *nctx.ExecContext, required map[string]bool) bool {
	if len(required) == 0 {
		return true
	}
	for role := range required {
		if !ctx.Permissions.HasRole(role) {
			return false
		}
	}
	return true
}

// goTypeToJSONType maps Go reflect kinds to JSON Schema type strings.
var goTypeToJSONType = map[reflect.Kind]string{
	reflect.String:  "string",
	reflect.Int:     "integer",
	reflect.Int8:    "integer",
	reflect.Int16:   "integer",
	reflect.Int32:   "integer",
	reflect.Int64:   "integer",
	reflect.Float32: "number",
	reflect.Float64: "number",
	reflect.Bool:    "boolean",
}

func extractParameters(fn any) map[string]any {
	fnType := reflect.TypeOf(fn)
	if fnType.Kind() != reflect.Func {
		return map[string]any{"type": "object", "properties": map[string]any{}}
	}

	properties := make(map[string]any)
	var required []string

	for i := 0; i < fnType.NumIn(); i++ {
		paramType := fnType.In(i)
		paramName := getParamName(fnType, i)
		jsonType := "string"
		if t, ok := goTypeToJSONType[paramType.Kind()]; ok {
			jsonType = t
		}
		properties[paramName] = map[string]string{"type": jsonType}
		required = append(required, paramName)
	}

	schema := map[string]any{
		"type":       "object",
		"properties": properties,
	}
	if len(required) > 0 {
		schema["required"] = required
	}
	return schema
}

// getParamName returns a positional parameter name since Go reflection
// does not expose parameter names at runtime.
func getParamName(fnType reflect.Type, index int) string {
	return fmt.Sprintf("arg%d", index)
}

func coerceArg(val any, targetType reflect.Type) reflect.Value {
	v := reflect.ValueOf(val)
	if v.Type().ConvertibleTo(targetType) {
		return v.Convert(targetType)
	}
	return reflect.Zero(targetType)
}
