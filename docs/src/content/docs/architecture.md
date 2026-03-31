---
title: Architecture
description: How Nerva's primitives fit together.
---

## Design principles

1. **Library, not server** -- Nerva runs inside your web framework. It does not own HTTP, auth, or API docs.
2. **Protocol-based** -- every primitive is a Python `Protocol` / TypeScript `interface`. No abstract base classes, no inheritance hierarchies.
3. **Composable** -- use one primitive or all eight. Replace any piece with your own implementation.
4. **ExecContext everywhere** -- a single, explicit context object flows through every method. No hidden globals, no thread-locals.
5. **Side effects at the edges** -- pure logic in the core, I/O only at the boundary.

## Request flow

```
         Message
           |
    +------v------+
    | ExecContext  |  <-- created from framework auth (user, session, permissions)
    +------+------+
           |
    +------v------+
    |   Policy     |  <-- rate limit check, budget check, approval gate
    +------+------+
           |
    +------v------+
    |    Router    |  <-- classify intent, select handler from Registry
    +------+------+
           |
    +------v------+
    |   Policy     |  <-- invoke_agent permission check
    +------+------+
           |
    +------v------+
    |   Runtime    |  <-- execute handler (subprocess / in-process / container)
    |     +- Tools |  <-- discover from Registry, sandbox, call
    |     +- Memory|  <-- read context, write results
    |     +- Policy|  <-- per-agent budget, tool call limits
    +------+------+
           |
    +------v------+
    |   Memory     |  <-- store episode, extract facts
    +------+------+
           |
    +------v------+
    |  Registry    |  <-- update invocation stats
    +------+------+
           |
    +------v------+
    |  Responder   |  <-- format for channel + tone
    +------+------+
           |
         Output
```

Policy appears three times -- before routing, before invocation, and during execution. Each checkpoint evaluates different rules (rate limit vs permission vs budget).

## ExecContext as connective tissue

ExecContext is the single object that threads through every primitive call. It carries:

- **Identity** -- request ID, trace ID, user ID, session ID
- **Permissions** -- what this user/agent can access
- **Memory scope** -- user, session, agent, or global
- **Observability** -- trace spans, structured events, token usage
- **Lifecycle** -- creation time, timeout, cooperative cancellation
- **Streaming** -- optional stream sink for real-time token delivery

Without it, you end up passing 6+ separate arguments to every function, or building a god-object in v2.

```python
ctx = ExecContext.create(
    user_id="user_123",
    session_id="session_abc",
    permissions=my_permissions,
    memory_scope="session",
    timeout_seconds=30,
)
```

Child contexts inherit the parent's trace and permissions:

```python
child_ctx = parent_ctx.child("calendar_agent")
# child_ctx.trace_id == parent_ctx.trace_id
# child_ctx.permissions == parent_ctx.permissions
# child_ctx has its own request_id and timeout
```

## Delegation model

Multi-agent is not a separate system. It is runtime recursion with shared context.

When an agent needs another agent, it calls `runtime.delegate()`:

```python
async def handle(input: AgentInput, ctx: ExecContext, runtime: AgentRuntime) -> AgentResult:
    calendar = await runtime.delegate("calendar_agent", AgentInput(message="next Tuesday"), ctx)
    flights = await tools.call("search_flights", {"to": "BER"}, ctx)
    return AgentResult(text=f"Found flights. {calendar.output}")
```

What `delegate` does:

1. Creates a child ExecContext -- inherits trace, permissions, memory scope
2. Checks permissions via Policy -- can this agent invoke that agent?
3. Invokes via the same Runtime (subprocess / in-process / container)
4. Returns structured result to the calling agent
5. Child spans appear nested under parent in traces

Depth limits prevent infinite recursion. Each agent has its own timeout and circuit breaker.

## Middleware pipeline

The Orchestrator supports middleware hooks at each stage of the pipeline:

```python
from nerva.middleware import Middleware

class LoggingMiddleware(Middleware):
    async def before_route(self, message: str, ctx: ExecContext) -> str:
        log.info("Routing", message=message, user=ctx.user_id)
        return message

    async def after_invoke(self, result: AgentResult, ctx: ExecContext) -> AgentResult:
        log.info("Invoked", handler=result.handler, status=result.status)
        return result

orchestrator = Orchestrator(
    ...,
    middleware=[LoggingMiddleware(), AuthMiddleware(), MetricsMiddleware()],
)
```

Middleware runs in order for `before_*` hooks and reverse order for `after_*` hooks, matching the standard onion model.

## Streaming architecture

Streaming is an orchestrator-level concern, not a per-primitive feature. Tokens flow through `ctx.stream`:

```python
# Server-side: orchestrator yields chunks
async for chunk in orchestrator.stream("Book me a flight", ctx):
    await websocket.send(chunk)
```

The data flow:

```
LLM Provider --> Runtime --> ctx.stream --> Responder.format_chunk() --> Client
```

1. **Runtime** pushes LLM tokens as they arrive
2. **Tools** push progress events (optional)
3. **Responder** formats each chunk for the target channel
4. **Client** receives formatted chunks in real time

Async generator handlers enable streaming from agent code:

```python
async def streaming_handler(input: AgentInput, ctx: ExecContext):
    async for token in llm.stream(input.message):
        yield token
```

## Observability

Every primitive records spans and events via ExecContext:

```
[t_abc] handle "Book me a flight to Berlin"  (1,247ms, $0.003)
  +-- [t_abc.1] router.classify                  (45ms)
  |   +-- scores: flight_agent=0.92, calendar=0.71
  +-- [t_abc.2] runtime.invoke flight_agent       (1,102ms, $0.002)
  |   +-- [t_abc.2.1] memory.recall               (23ms)
  |   +-- [t_abc.2.2] tools.call search_flights   (890ms)
  |   +-- [t_abc.2.3] delegate calendar_agent     (134ms, $0.001)
  |       +-- [t_abc.2.3.1] tools.call calendar   (98ms)
  +-- [t_abc.3] memory.store                      (12ms)
  +-- [t_abc.4] responder.format                  (88ms)
```

Export to OpenTelemetry, structured JSON logs, or any custom tracer.

## Repo structure

```
nerva/
  spec/                         # TypeSpec definitions (source of truth)
    exec_context.tsp
    router.tsp / runtime.tsp / tools.tsp / memory.tsp
    responder.tsp / registry.tsp / policy.tsp
    generated/                  # auto-generated JSON Schema

  packages/
    nerva-py/                   # Python implementation
      nerva/
        context.py              # ExecContext
        orchestrator.py         # Orchestrator
        router/                 # RuleRouter, EmbeddingRouter, LLMRouter, HybridRouter
        runtime/                # InProcessRuntime, SubprocessRuntime, ContainerRuntime
        tools/                  # FunctionToolManager, MCPToolManager, CompositeToolManager
        memory/                 # TieredMemory (hot/warm/cold)
        responder/              # PassthroughResponder, ToneResponder, MultimodalResponder
        registry/               # InMemoryRegistry, SqliteRegistry
        policy/                 # NoopPolicyEngine, YamlPolicyEngine, AdaptivePolicyEngine
        middleware/
        tracing/

    nerva-js/                   # TypeScript implementation (mirrors Python)
    nerva-cli/                  # CLI (new, generate, list, trace-ui)
```

## Framework integration

```
FastAPI / NestJS / Express   (HTTP, auth, sessions, swagger, CORS)
  +-- Nerva                  (routing, runtime, tools, memory, policy)
       +-- LLM providers, MCP servers, subprocess agents
```

Nerva ships optional contrib bridges:

- `nerva.contrib.fastapi` -- `NervaMiddleware`, `get_nerva_ctx()` dependency
- `nerva/contrib/express` -- `nervaMiddleware()`, `req.nervaCtx`
- `nerva/contrib/nestjs` -- `NervaModule`, `@NervaCtx()` decorator

These are convenience helpers -- you can always construct ExecContext manually.
