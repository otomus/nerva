---
title: ExecContext
description: The connective tissue that flows through every primitive.
---

ExecContext is primitive #0 — the shared nervous system of a request. It flows through every method in every primitive, carrying identity, permissions, memory scope, observability, and lifecycle state.

## Why It Exists

Without ExecContext, you end up with one of two outcomes:

1. **Argument explosion** — every function takes 6+ separate parameters for user ID, trace ID, permissions, memory scope, timeout, and streaming
2. **God object later** — you build a request context object in v2 after the codebase becomes unmanageable

ExecContext makes this explicit from day one.

## What It Carries

### Identity

```python
ctx.request_id   # unique per request
ctx.trace_id     # groups related requests (multi-turn conversation)
ctx.user_id      # who is asking
ctx.session_id   # conversation session
```

### Permissions

```python
ctx.permissions   # what this user/agent can access
ctx.permissions.can_invoke("flight_agent")  # check agent access
ctx.permissions.can_use_tool("search_flights")  # check tool access
```

### Memory Scope

```python
ctx.memory_scope  # "user" | "session" | "agent" | "global"
```

Memory operations are automatically scoped — user A's context never leaks to user B.

### Observability

```python
ctx.spans          # OpenTelemetry-compatible trace spans
ctx.events         # structured log events
ctx.token_usage    # accumulated LLM token counts across all calls
```

### Lifecycle

```python
ctx.created_at     # when the request started
ctx.timeout_at     # when it should be killed
ctx.cancelled      # cooperative cancellation event
```

### Streaming

```python
ctx.stream         # if set, primitives push tokens here as they are produced
```

## Creating a Context

### Python

```python
from nerva import ExecContext

# Minimal
ctx = ExecContext.create(user_id="user_123")

# Full control
ctx = ExecContext.create(
    user_id="user_123",
    session_id="session_abc",
    permissions=my_permissions,
    memory_scope="session",
    timeout_seconds=30,
)
```

### TypeScript

```typescript
import { ExecContext } from "nerva";

const ctx = ExecContext.create({
  userId: "user_123",
  sessionId: "session_abc",
  permissions: myPermissions,
  memoryScope: "session",
  timeoutSeconds: 30,
});
```

## Who Uses It

Every primitive receives ExecContext and uses the parts it needs:

| Primitive | What it reads from ExecContext |
|-----------|-------------------------------|
| **Router** | `permissions` — permission-aware handler selection |
| **Runtime** | `timeout_at`, `cancelled`, `spans` — lifecycle and tracing |
| **Tools** | `permissions` — which tools this user/agent can call |
| **Memory** | `memory_scope`, `session_id`, `user_id` — scope isolation |
| **Responder** | `stream` — streaming vs batch mode |
| **Registry** | `permissions` — filtered discovery |
| **Policy** | `token_usage`, `user_id` — budget and rate limit evaluation |

## Framework Bridge

When Nerva runs inside FastAPI, NestJS, or Express, you bridge your framework's auth into ExecContext:

```python
# FastAPI — map JWT user to Nerva context
@app.post("/chat")
async def chat(req: ChatRequest, user: User = Depends(get_current_user)):
    ctx = ExecContext.create(
        user_id=user.id,
        session_id=req.session_id,
        permissions=permissions_from_user(user),
    )
    return await orchestrator.handle(req.message, ctx)
```

The contrib helpers (`nerva.contrib.fastapi`, `nerva/contrib/express`, `nerva/contrib/nestjs`) automate this bridge.
