package runtime

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strings"
	"time"

	nctx "github.com/otomus/nerva/go/context"
)

const (
	// DefaultTimeoutSeconds is the maximum wall-clock seconds a handler may run.
	DefaultTimeoutSeconds = 30.0
	// MaxOutputBytes is the maximum stdout bytes collected before truncation.
	MaxOutputBytes = 1_048_576
	// WrongHandlerExitCode signals the handler cannot handle this input.
	WrongHandlerExitCode = 2
)

var jsonExtractPattern = regexp.MustCompile(`\{[^{}]*\}|\{.*\}`)

// SubprocessConfig configures the subprocess runtime.
type SubprocessConfig struct {
	TimeoutSeconds float64
	CircuitBreaker *CircuitBreakerConfig
	MaxOutputBytes int
	HandlerDir     string
}

// DefaultSubprocessConfig returns a config with default values.
func DefaultSubprocessConfig() SubprocessConfig {
	return SubprocessConfig{
		TimeoutSeconds: DefaultTimeoutSeconds,
		MaxOutputBytes: MaxOutputBytes,
		HandlerDir:     ".",
	}
}

// SubprocessRuntime executes handlers as child processes with lifecycle management.
type SubprocessRuntime struct {
	config   SubprocessConfig
	breakers map[string]*CircuitBreaker
}

// NewSubprocessRuntime creates a new subprocess runtime.
func NewSubprocessRuntime(config *SubprocessConfig) *SubprocessRuntime {
	cfg := DefaultSubprocessConfig()
	if config != nil {
		cfg = *config
	}
	return &SubprocessRuntime{
		config:   cfg,
		breakers: make(map[string]*CircuitBreaker),
	}
}

// Invoke runs a single handler as a subprocess.
func (sr *SubprocessRuntime) Invoke(ctx *nctx.ExecContext, handler string, input AgentInput) (AgentResult, error) {
	breaker := sr.getBreaker(handler)
	if !breaker.IsAllowed() {
		return sr.circuitOpenResult(handler), nil
	}

	ctx.AddEvent("subprocess.start", map[string]string{"handler": handler})
	startedAt := time.Now()

	stdout, exitCode := sr.spawnProcess(ctx, handler, input)

	elapsed := time.Since(startedAt).Seconds()
	ctx.AddEvent("subprocess.end", map[string]string{
		"handler":         handler,
		"returncode":      fmt.Sprintf("%d", exitCode),
		"elapsed_seconds": fmt.Sprintf("%.3f", elapsed),
	})

	errorKind := sr.classifyError(exitCode)
	result := sr.buildResult(handler, stdout, exitCode, errorKind)
	sr.recordOnBreaker(breaker, result.Status)

	return result, nil
}

// InvokeChain runs handlers in sequence, piping each output as the next input's message.
func (sr *SubprocessRuntime) InvokeChain(ctx *nctx.ExecContext, handlers []string, input AgentInput) (AgentResult, error) {
	if len(handlers) == 0 {
		return AgentResult{}, fmt.Errorf("handlers list must not be empty")
	}

	currentInput := input
	var result AgentResult

	for _, handlerName := range handlers {
		var err error
		result, err = sr.Invoke(ctx, handlerName, currentInput)
		if err != nil {
			return result, err
		}
		if result.Status != StatusSuccess {
			return result, nil
		}
		currentInput = AgentInput{
			Message: result.Output,
			Args:    currentInput.Args,
			Tools:   currentInput.Tools,
			History: currentInput.History,
		}
	}

	return result, nil
}

// Delegate invokes a handler in a child context.
func (sr *SubprocessRuntime) Delegate(ctx *nctx.ExecContext, handler string, input AgentInput) (AgentResult, error) {
	childCtx := ctx.Child(handler)
	return sr.Invoke(childCtx, handler, input)
}

func (sr *SubprocessRuntime) getBreaker(handler string) *CircuitBreaker {
	if b, ok := sr.breakers[handler]; ok {
		return b
	}
	b := NewCircuitBreaker(sr.config.CircuitBreaker)
	sr.breakers[handler] = b
	return b
}

func (sr *SubprocessRuntime) circuitOpenResult(handler string) AgentResult {
	return AgentResult{
		Status:  StatusError,
		Handler: handler,
		Error:   fmt.Sprintf("circuit open for handler '%s'", handler),
	}
}

func (sr *SubprocessRuntime) recordOnBreaker(breaker *CircuitBreaker, status AgentStatus) {
	if status == StatusSuccess {
		breaker.RecordSuccess()
	} else {
		breaker.RecordFailure()
	}
}

func (sr *SubprocessRuntime) spawnProcess(ctx *nctx.ExecContext, handler string, input AgentInput) (string, int) {
	commandPath := sr.resolveHandlerPath(handler)
	timeout := time.Duration(sr.config.TimeoutSeconds * float64(time.Second))
	cmdCtx, cancel := context.WithTimeout(ctx.Context(), timeout)
	defer cancel()

	inputJSON, _ := json.Marshal(input)

	cmd := exec.CommandContext(cmdCtx, commandPath)
	cmd.Stdin = strings.NewReader(string(inputJSON))

	output, err := cmd.Output()
	if err != nil {
		if cmdCtx.Err() == context.DeadlineExceeded {
			ctx.AddEvent("subprocess.timeout", map[string]string{"handler": handler})
			return "", -1
		}
		if exitErr, ok := err.(*exec.ExitError); ok {
			return string(output), exitErr.ExitCode()
		}
		return "", -1
	}

	return string(output), 0
}

func (sr *SubprocessRuntime) resolveHandlerPath(handler string) string {
	candidate := filepath.Join(sr.config.HandlerDir, handler)
	if info, err := os.Stat(candidate); err == nil && !info.IsDir() {
		abs, _ := filepath.Abs(candidate)
		return abs
	}
	return handler
}

func (sr *SubprocessRuntime) classifyError(exitCode int) *string {
	if exitCode == 0 {
		return nil
	}
	var kind string
	switch exitCode {
	case -1:
		kind = "retryable"
	case WrongHandlerExitCode:
		kind = "wrong_handler"
	default:
		kind = "fatal"
	}
	return &kind
}

func (sr *SubprocessRuntime) buildResult(handler, output string, exitCode int, errorKind *string) AgentResult {
	if errorKind == nil {
		return sr.buildSuccessResult(handler, output)
	}
	switch *errorKind {
	case "retryable":
		return AgentResult{
			Status:  StatusTimeout,
			Handler: handler,
			Error:   fmt.Sprintf("handler '%s' timed out after %.0fs", handler, sr.config.TimeoutSeconds),
		}
	case "wrong_handler":
		data := sr.extractJSON(output)
		reason := output
		if r, ok := data["error"]; ok {
			reason = r
		}
		if strings.TrimSpace(reason) == "" {
			reason = "handler declined the input"
		}
		return AgentResult{
			Status:  StatusWrongHandler,
			Handler: handler,
			Error:   reason,
			Data:    data,
		}
	default:
		data := sr.extractJSON(output)
		errorMsg := fmt.Sprintf("handler '%s' exited with code %d", handler, exitCode)
		if e, ok := data["error"]; ok {
			errorMsg = e
		}
		return AgentResult{
			Status:  StatusError,
			Output:  output,
			Data:    data,
			Handler: handler,
			Error:   errorMsg,
		}
	}
}

func (sr *SubprocessRuntime) buildSuccessResult(handler, output string) AgentResult {
	data := sr.extractJSON(output)
	responseText := output
	if v, ok := data["output"]; ok {
		responseText = v
		delete(data, "output")
	} else if v, ok := data["response"]; ok {
		responseText = v
		delete(data, "response")
	}
	return AgentResult{
		Status:  StatusSuccess,
		Output:  responseText,
		Data:    data,
		Handler: handler,
	}
}

func (sr *SubprocessRuntime) extractJSON(raw string) map[string]string {
	trimmed := strings.TrimSpace(raw)
	if trimmed == "" {
		return map[string]string{}
	}

	result := tryParseJSON(trimmed)
	if result != nil {
		return result
	}

	matches := jsonExtractPattern.FindAllString(trimmed, -1)
	for _, match := range matches {
		parsed := tryParseJSON(match)
		if parsed != nil {
			return parsed
		}
	}

	return map[string]string{}
}

func tryParseJSON(text string) map[string]string {
	var raw map[string]any
	if err := json.Unmarshal([]byte(text), &raw); err != nil {
		return nil
	}
	result := make(map[string]string)
	for k, v := range raw {
		result[k] = fmt.Sprintf("%v", v)
	}
	return result
}
