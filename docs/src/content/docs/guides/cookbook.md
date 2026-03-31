---
title: Cookbook
description: Common patterns for building agent systems with Nerva.
---

## Fan-out pattern

Delegate to multiple agents in parallel, merge results.

```python
import asyncio
from nerva.runtime import AgentInput

async def research_handler(input: AgentInput, ctx: ExecContext) -> str:
    # Fan out to three sources in parallel
    tasks = [
        runtime.delegate("web_search", AgentInput(message=input.message), ctx),
        runtime.delegate("database_lookup", AgentInput(message=input.message), ctx),
        runtime.delegate("knowledge_base", AgentInput(message=input.message), ctx),
    ]
    results = await asyncio.gather(*tasks)

    # Merge results -- filter out failures
    sources = [r.output for r in results if r.status == AgentStatus.SUCCESS]
    return "\n\n".join(sources)
```

Each delegate call creates a child ExecContext, so traces show parallel execution clearly.

## Retry pattern

Use circuit breaker config with InProcessRuntime for automatic failure tracking. For manual retry with backoff:

```python
from nerva.runtime import AgentStatus

MAX_RETRIES = 3
BACKOFF_SECONDS = [1, 2, 4]

async def retry_handler(input: AgentInput, ctx: ExecContext) -> str:
    for attempt in range(MAX_RETRIES):
        result = await runtime.delegate("flaky_service", input, ctx)
        if result.status == AgentStatus.SUCCESS:
            return result.output
        if attempt < MAX_RETRIES - 1:
            await asyncio.sleep(BACKOFF_SECONDS[attempt])

    return f"Failed after {MAX_RETRIES} attempts: {result.error}"
```

The circuit breaker handles the common case automatically:

```python
from nerva.runtime.inprocess import InProcessRuntime, InProcessConfig
from nerva.runtime.circuit_breaker import CircuitBreakerConfig

runtime = InProcessRuntime(config=InProcessConfig(
    circuit_breaker=CircuitBreakerConfig(
        failure_threshold=3,       # open after 3 failures
        recovery_seconds=60.0,     # wait 60s before retry probe
        half_open_max_calls=1,     # allow 1 test call
    ),
))
```

## Caching pattern

Check memory before making an expensive tool call.

```python
async def cached_search_handler(input: AgentInput, ctx: ExecContext) -> str:
    # Check warm memory for cached results
    context = await memory.recall(input.message, ctx)
    if context.facts:
        return f"From cache: {context.facts[0]}"

    # Cache miss -- call the tool
    result = await tools.call("expensive_search", {"query": input.message}, ctx)

    # Store in warm tier for future recall
    await memory.store(
        MemoryEvent(
            content=result.output,
            tier=MemoryTier.WARM,
            source="expensive_search",
            tags=frozenset({"cache", "search"}),
        ),
        ctx,
    )

    return result.output
```

## Rate limiting

Use the YamlPolicyEngine for per-user rate limiting:

```yaml
# nerva.yaml
policies:
  rate_limit:
    per_user:
      max_requests_per_minute: 30
      on_exceed: reject
```

For custom rate limiting logic, evaluate policy before each action:

```python
from nerva.policy import PolicyAction

action = PolicyAction(
    kind="invoke_agent",
    subject=ctx.user_id,
    target="expensive_agent",
)

decision = await policy.evaluate(action, ctx)
if not decision.allowed:
    return f"Rate limited: {decision.reason}"

result = await runtime.invoke("expensive_agent", input, ctx)
await policy.record(action, decision, ctx)
```

## Streaming with FastAPI

End-to-end SSE streaming from agent to browser:

```python
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from nerva import Orchestrator, ExecContext

app = FastAPI()

@app.post("/chat/stream")
async def stream_chat(request: ChatRequest):
    ctx = ExecContext.create(user_id=request.user_id)

    async def event_generator():
        async for chunk in orchestrator.stream(request.message, ctx):
            yield f"data: {chunk}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
```

The streaming handler on the agent side:

```python
async def streaming_agent(input: AgentInput, ctx: ExecContext):
    async for token in llm.stream(
        messages=[{"role": "user", "content": input.message}],
    ):
        yield token
```

## Multi-language agents

Python orchestrator delegates to a Node.js agent via SubprocessRuntime:

```python
from nerva.runtime.subprocess import SubprocessRuntime

runtime = SubprocessRuntime(
    handler_paths={
        "python_agent": "./agents/python_agent.py",
        "node_agent": "./agents/node_agent.js",
    },
    timeout_seconds=30.0,
)

# Both agents use the same protocol -- JSON in, JSON out over stdio
result = await runtime.invoke("node_agent", AgentInput(message="hello"), ctx)
```

The Node.js agent reads from stdin and writes to stdout:

```javascript
// agents/node_agent.js
import { readInput, writeOutput } from "nerva/subprocess";

const input = await readInput();
const result = {
  status: "success",
  output: `Node.js processed: ${input.message}`,
};
writeOutput(result);
```

## Budget enforcement

Set per-agent cost limits to prevent runaway spending:

```yaml
# nerva.yaml
policies:
  budget:
    per_agent:
      max_tokens_per_hour: 100000
      max_cost_per_day_usd: 5.00
      on_exceed: pause
```

Override at the agent level with the decorator:

```python
from nerva.policy.decorator import agent

@agent(name="expensive_agent", policy={
    "max_cost_usd": 0.50,      # per-invocation limit
    "max_tool_calls": 3,        # cap tool usage
})
class ExpensiveAgent:
    async def handle(self, input, ctx):
        ...
```

The policy engine tracks token usage from `ctx.token_usage` and denies requests when limits are reached. The `on_exceed` strategy controls behavior:

- `block` -- deny immediately
- `pause` -- deny and signal the orchestrator to queue the request
- `warn` -- allow but emit a warning event
- `degrade` -- allow with reduced capability (e.g., use a cheaper model)
