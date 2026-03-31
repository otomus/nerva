---
title: Runtime
description: Execute agent code with isolation, timeouts, and circuit breakers.
---

The Runtime executes agent handlers, manages their lifecycle, and collects structured output.

## Protocol

```python
class AgentRuntime(Protocol):
    async def invoke(self, handler: str, input: AgentInput, ctx: ExecContext) -> AgentResult:
        ...

    async def invoke_chain(self, handlers: list[str], input: AgentInput, ctx: ExecContext) -> AgentResult:
        ...

    async def delegate(self, handler: str, input: AgentInput, parent_ctx: ExecContext) -> AgentResult:
        ...
```

### Value types

```python
@dataclass(frozen=True)
class AgentInput:
    message: str
    args: dict[str, str]
    tools: list[dict[str, str]]
    history: list[dict[str, str]]

@dataclass
class AgentResult:
    status: AgentStatus  # SUCCESS | ERROR | TIMEOUT | WRONG_HANDLER | NEEDS_DATA | NEEDS_CREDENTIALS
    output: str
    data: dict[str, str]
    error: str | None
    handler: str
```

## Strategies

### InProcessRuntime

Runs handlers as async functions in the main process. Fastest, no isolation.

```python
from nerva.runtime.inprocess import InProcessRuntime, InProcessConfig
from nerva.runtime.circuit_breaker import CircuitBreakerConfig

runtime = InProcessRuntime(config=InProcessConfig(
    timeout_seconds=30.0,
    circuit_breaker=CircuitBreakerConfig(
        failure_threshold=3,
        recovery_seconds=60.0,
    ),
))

async def my_handler(input: AgentInput, ctx: ExecContext) -> str:
    return f"Processed: {input.message}"

runtime.register("my_agent", my_handler)
result = await runtime.invoke("my_agent", AgentInput(message="hello"), ctx)
```

**Streaming handlers** use async generators:

```python
async def streaming_handler(input: AgentInput, ctx: ExecContext):
    for word in input.message.split():
        yield word + " "

runtime.register("streaming_agent", streaming_handler)
# Chunks are pushed to ctx.stream as they are produced
```

### SubprocessRuntime

Runs each handler in a separate process. Process-level isolation, crash protection.

```python
from nerva.runtime.subprocess import SubprocessRuntime

runtime = SubprocessRuntime(
    handler_paths={"my_agent": "./agents/my_agent.py"},
    timeout_seconds=30.0,
)
```

**When to use:** Production, untrusted agent code, agents with conflicting dependencies.

### ContainerRuntime

Runs handlers in Docker or Firecracker containers. Maximum isolation.

```python
from nerva.runtime.container import ContainerRuntime

runtime = ContainerRuntime(
    images={"my_agent": "my-agent:latest"},
    timeout_seconds=60.0,
)
```

**When to use:** Multi-tenant systems, agents running user-supplied code.

## Circuit breaker

Every runtime strategy includes per-handler circuit breakers:

```
CLOSED (normal) --[3 consecutive failures]--> OPEN (rejecting)
                                                  |
                                           [60s recovery]
                                                  |
                                             HALF_OPEN (probe)
                                                  |
                                    success -> CLOSED | failure -> OPEN
```

Configure thresholds per handler:

```python
from nerva.runtime.circuit_breaker import CircuitBreakerConfig

config = CircuitBreakerConfig(
    failure_threshold=3,       # failures before opening
    recovery_seconds=60.0,     # wait before probe
    half_open_max_calls=1,     # probe calls in half-open
)
```

## Chaining and delegation

**Chain** runs handlers in sequence, piping each output as the next input:

```python
result = await runtime.invoke_chain(
    ["extract_intent", "lookup_data", "format_response"],
    AgentInput(message="Find my order status"),
    ctx,
)
# Stops early if any handler returns non-SUCCESS
```

**Delegate** enables agent-to-agent calls with inherited context:

```python
async def travel_handler(input: AgentInput, ctx: ExecContext) -> str:
    # Delegate to calendar agent -- creates child ExecContext automatically
    calendar = await runtime.delegate("calendar_agent", AgentInput(message="next Tuesday"), ctx)
    return f"Your next free slot: {calendar.output}"
```

Child contexts inherit trace ID, permissions, and memory scope. Depth limits prevent infinite recursion.

## Choosing a strategy

| Criteria | InProcess | Subprocess | Container |
|----------|-----------|------------|-----------|
| Latency overhead | ~0ms | ~50ms | ~200ms |
| Isolation | None | Process boundary | Full OS |
| Crash recovery | Crashes host | Isolated | Isolated |
| Dependency conflicts | Shared env | Separate env | Separate image |
| Use case | Dev, trusted agents | Production | Multi-tenant |
