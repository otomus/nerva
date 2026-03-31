---
title: Quick Start — TypeScript
description: Build a weather agent in 5 minutes.
---

## Install

```bash
npm install nerva
```

Requires Node.js 20+.

## Scaffold a project

```bash
npx nerva new my-agent --lang typescript
cd my-agent
```

This creates a project with `index.ts`, `nerva.yaml`, and a sample handler.

## Build a weather agent

Create `index.ts`:

```typescript
import {
  Orchestrator,
  ExecContext,
  RuleRouter,
  InProcessRuntime,
  FunctionToolManager,
  TieredMemory,
  InMemoryHotMemory,
  PassthroughResponder,
  InMemoryRegistry,
  NoopPolicyEngine,
} from "nerva";

// -- Tools -------------------------------------------------------------------

const tools = new FunctionToolManager();

tools.register({
  name: "get_weather",
  description: "Get current weather for a city",
  handler: async (args: { city: string }) => {
    // Replace with a real API call
    return `22°C and sunny in ${args.city}`;
  },
});

// -- Handler -----------------------------------------------------------------

async function weatherHandler(
  input: { message: string },
  ctx: ExecContext,
): Promise<string> {
  const result = await tools.call("get_weather", { city: "Berlin" }, ctx);
  return result.output;
}

// -- Runtime -----------------------------------------------------------------

const runtime = new InProcessRuntime();
runtime.register("weather_agent", weatherHandler);

// -- Orchestrator ------------------------------------------------------------

const orchestrator = new Orchestrator({
  router: new RuleRouter({
    rules: [
      { pattern: /weather/, handler: "weather_agent", intent: "weather" },
    ],
    defaultHandler: "weather_agent",
  }),
  runtime,
  tools,
  memory: new TieredMemory({ hot: new InMemoryHotMemory() }),
  responder: new PassthroughResponder(),
  registry: new InMemoryRegistry(),
  policy: new NoopPolicyEngine(),
});

// -- Run ---------------------------------------------------------------------

async function main() {
  const ctx = ExecContext.create({ userId: "user_1" });
  const result = await orchestrator.handle(
    "What's the weather in Berlin?",
    ctx,
  );
  console.log(result.text);
}

main();
```

## Run it

```bash
npx tsx index.ts
# Output: 22°C and sunny in Berlin
```

## Trace output

Every request produces a structured trace:

```
[req_abc] handle "What's the weather in Berlin?"  (52ms)
  +-- [req_abc.1] router.classify                   (1ms)
  |   +-- intent=weather, handler=weather_agent, confidence=1.0
  +-- [req_abc.2] runtime.invoke weather_agent       (48ms)
  |   +-- [req_abc.2.1] tools.call get_weather       (45ms)
  +-- [req_abc.3] responder.format                   (1ms)
```

## Next steps

- **Add more agents** — register additional handlers and routing rules
- **Use memory** — pass `InMemoryHotMemory` to persist conversation across turns
- **Add middleware** — inject logging, auth, or rate limiting into the pipeline
- **Switch routers** — swap `RuleRouter` for `EmbeddingRouter` when you have 10+ agents
- **Read the [Primitives Overview](/primitives/overview/)** to learn what each piece does
