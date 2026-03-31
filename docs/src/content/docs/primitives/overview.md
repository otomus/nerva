---
title: Primitives Overview
description: The 8 composable primitives that every agent orchestrator needs.
---

Nerva provides 8 primitives. Each is a **Protocol** (Python) or **interface** (TypeScript) with one or more default implementations. Use one, use all, replace any.

## The Primitives

### 0. ExecContext — The Connective Tissue

Every method in every primitive receives an `ExecContext`. It carries:

- **Identity** — request ID, trace ID, user ID, session ID
- **Permissions** — what this user/agent can access
- **Memory scope** — user, session, agent, or global
- **Observability** — trace spans, structured events, token usage
- **Lifecycle** — creation time, timeout, cooperative cancellation
- **Streaming** — optional stream sink for real-time token delivery

Without it, you end up passing 6 separate arguments to every function, or building a god-object later. Every project that starts without this adds it in v2.

### 1. Router — Intent Classification

Takes a raw message and returns a structured intent with confidence score and matched handler.

**Provided strategies:**

| Strategy | How it works | Trade-off |
|----------|-------------|-----------|
| `RuleRouter` | Regex/keyword matching | Fast, deterministic, no LLM cost |
| `EmbeddingRouter` | Cosine similarity against handler descriptions | Fast, no LLM call, needs embedding model |
| `LLMRouter` | Structured output from LLM with handler catalog | Accurate, slower, costs tokens |
| `HybridRouter` | Embedding pre-filter then LLM re-rank | Best accuracy, moderate cost |

### 2. Runtime — Agent Execution

Executes agent code in isolation, manages lifecycle, collects structured output.

**Provided strategies:**

| Strategy | Isolation | Use case |
|----------|-----------|----------|
| `InProcessRuntime` | None — runs in main process | Development, simple agents |
| `SubprocessRuntime` | Process boundary | Production, untrusted agents |
| `ContainerRuntime` | Docker/Firecracker | Maximum isolation |

Built-in: timeout, circuit breaker, structured output parsing, error classification, streaming.

### 3. Tools — Discovery and Invocation

Discovers tools, sandboxes their execution, injects them into agent context.

**Provided strategies:**

| Strategy | Source | Use case |
|----------|--------|----------|
| `FunctionToolManager` | Plain functions | Simple tools, testing |
| `MCPToolManager` | MCP protocol servers | Production, sandboxed tools |
| `CompositeToolManager` | Multiple sources combined | Mixed environments |

Built-in: permission model, sandboxing, connection pooling, schema validation, result size limits.

### 4. Memory — Tiered Context Storage

Agents read from and write to tiered memory with automatic lifecycle management.

**Tiers:**

| Tier | Scope | Storage |
|------|-------|---------|
| **Hot** | Current session — conversation + working state | In-memory / Redis |
| **Warm** | Recent episodes and extracted facts | Key-value store |
| **Cold** | Long-term searchable knowledge | Vector DB |

Built-in: episode extraction, fact deduplication, tier promotion/demotion, token budget fitting, scope isolation.

### 5. Responder — Output Formatting

Takes raw agent output and formats it for the target channel.

**Provided strategies:**

| Strategy | Behavior |
|----------|----------|
| `PassthroughResponder` | Returns raw output (APIs, programmatic consumers) |
| `ToneResponder` | Rewrites output with personality/tone |
| `MultimodalResponder` | Attaches media, cards, buttons based on channel capabilities |

### 6. Registry — Component Catalog

Unified catalog of everything in the system. Every other primitive queries it instead of implementing its own discovery.

**Provided backends:**

| Backend | Persistence | Use case |
|---------|-------------|----------|
| `InMemoryRegistry` | None | Testing, simple deployments |
| `SqliteRegistry` | Disk | Single-node production |
| `RedisRegistry` | Distributed | Multi-node clusters |

### 7. Policy — Governance

Layered rules that govern execution. Declarative defaults in config, per-agent overrides via decorators, runtime adaptation.

**Provided strategies:**

| Strategy | Behavior |
|----------|----------|
| `NoopPolicyEngine` | Allow everything (development/testing) |
| `YamlPolicyEngine` | Load policies from `nerva.yaml`, evaluate at runtime |
| `AdaptivePolicyEngine` | Extends YAML engine with runtime condition monitoring |

**Policy covers:** rate limits, cost budgets, approval gates, safety screening, execution depth limits, circuit breakers.

## Composition

The Orchestrator wires all 8 primitives together and threads ExecContext through the pipeline:

```
Message -> ExecContext -> Policy -> Router -> Runtime (Tools + Memory) -> Responder -> Output
```

But you do not need the Orchestrator. Use any primitive standalone, compose a subset, or build your own orchestration logic.
