# Nerva -- CLAUDE.md

## What this project is

Nerva is composable agent primitives -- the "React for AI agents." It is a library layer that sits between raw LLM SDKs (Anthropic, OpenAI) and full-featured agent frameworks (LangChain, CrewAI). Protocol-based: every primitive is a Python Protocol / TypeScript interface. Use one primitive or all eight. Replace any piece. No lock-in.

The 8 primitives every agent orchestrator needs:

| # | Primitive | Purpose |
|---|-----------|---------|
| 0 | ExecContext | Connective tissue -- passed to every method |
| 1 | Router | Intent classification and agent selection |
| 2 | Runtime | Agent execution lifecycle |
| 3 | Tools | Tool discovery, invocation, sandboxing |
| 4 | Memory | Short/long-term storage and retrieval |
| 5 | Responder | Output formatting and delivery |
| 6 | Registry | Agent/nerve registration and discovery |
| 7 | Policy | Permissions, rate limits, safety guards |

## Architecture

```
spec/ (TypeSpec -> JSON Schema -- source of truth)
  | generates
  v
packages/nerva-py/   packages/nerva-js/
  nerva/               src/
    context.py           context.ts        <- ExecContext (#0)
    orchestrator.py      orchestrator.ts   <- Wires all primitives
    router/              router/           <- #1 Intent Router
    runtime/             runtime/          <- #2 Agent Runtime
    tools/               tools/            <- #3 Tool Layer
    memory/              memory/           <- #4 Memory
    responder/           responder/        <- #5 Responder
    registry/            registry/         <- #6 Registry
    policy/              policy/           <- #7 Policy
    middleware/           middleware/
    tracing/             tracing/
```

Monorepo structure:
- `spec/` -- TypeSpec definitions, the single source of truth for all types and schemas
- `packages/nerva-py` -- Python implementation
- `packages/nerva-js` -- TypeScript implementation
- `packages/nerva-cli` -- CLI tooling

## Running tests

```bash
# Python
cd packages/nerva-py && python -m pytest tests/ -q

# Node.js
cd packages/nerva-js && npm test

# CLI
cd packages/nerva-cli && npm test
```

Never skip or ignore failing tests -- fix the root cause.

## Key conventions

- Every primitive is a Protocol (Python) / interface (TypeScript) -- never abstract base class
- ExecContext is the connective tissue -- passed to every method, carries request-scoped state
- Implementations are strategies: HybridRouter, SubprocessRuntime, MCPToolManager, etc.
- TypeSpec in `spec/` is the source of truth -- both languages validate against generated JSON Schema
- No `Any` in Python, no `any` in TypeScript -- ever
- All exported functions, classes, and types must have docstrings (Python) / JSDoc (TypeScript)
- Functions do one thing, max ~20-30 lines
- Max 3 parameters -- use options objects / dataclasses beyond that
- Guard clauses over deep nesting (max 3 levels)
- Named constants over magic numbers and strings
- Inject dependencies, don't hardcode them
- No dead code -- VCS is the history
- Prefer immutability: return new values, don't mutate inputs
- Pure functions at the core, side effects at the edges

## Testing standards

Tests must try to BREAK the code, not confirm it works. Every test file must include:
- Edge cases: None/null, empty strings, empty collections
- Wrong types: pass an int where a str is expected
- Boundary values: extremely long strings, deeply nested structures, unicode, special characters
- Malformed input: truncated JSON, partial data, duplicate keys, unexpected extra fields
- Concurrency/ordering: if the code depends on state, test out-of-order calls

When a test file has an invariant that must hold for every test, use `@pytest.fixture(autouse=True)` with a yield pattern (Python) or `beforeEach`/`afterEach` (TypeScript) -- never copy-paste the same assertion into every test.

## Mandatory compliance

All code in this repo MUST follow:
- `~/.claude/rules/clean-code.md` -- applies to ALL code written or modified
- `~/.claude/rules/testing.md` -- applies to ALL tests written or modified
- `~/.claude/rules/investigation-first.md` -- applies to ALL bug fixes

These are hard requirements, not suggestions. Every function, every test, every edit must comply. No exceptions.

## First consumer

Arqitect (sentient-server) will import nerva as a pip dependency. Approximately 3,000 lines of infrastructure code disappear from Arqitect when the migration is complete. The primitives extracted into Nerva currently live as tightly coupled modules inside `arqitect/brain/`, `arqitect/inference/`, `arqitect/memory/`, and `arqitect/mcp/`.
