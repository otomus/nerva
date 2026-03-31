<p align="center">
  <h1 align="center">Nerva</h1>
  <p align="center"><strong>Composable Agent Primitives</strong></p>
  <p align="center">Build your agent system — not the plumbing around it.</p>
</p>

<p align="center">
  <a href="https://github.com/otomus/nerva/actions"><img src="https://img.shields.io/github/actions/workflow/status/otomus/nerva/ci.yml?branch=main" alt="CI"></a>
  <a href="https://pypi.org/project/nerva/"><img src="https://img.shields.io/pypi/v/nerva" alt="PyPI"></a>
  <a href="https://www.npmjs.com/package/nerva"><img src="https://img.shields.io/npm/v/nerva" alt="npm"></a>
  <a href="https://github.com/otomus/nerva/blob/main/LICENSE"><img src="https://img.shields.io/github/license/otomus/nerva" alt="License"></a>
</p>

---

## What is Nerva?

Nerva is a **library** — not a server, not a framework. It provides 8 composable primitives that every agent orchestrator needs: routing, execution, tools, memory, response formatting, registry, and policy enforcement. Use one primitive or all eight. Replace any piece with your own implementation. Nerva runs inside your existing web framework (FastAPI, NestJS, Express) and stays invisible to your API consumers.

## The 8 Primitives

| # | Primitive | What it does |
|---|-----------|-------------|
| 0 | **ExecContext** | Carries request identity, permissions, memory scope, and tracing through the entire call chain |
| 1 | **Router** | Classifies intent and selects the right agent/handler (embedding, LLM, rule-based, or hybrid) |
| 2 | **Runtime** | Executes agent code with isolation, timeouts, circuit breakers, and streaming |
| 3 | **Tools** | Discovers, sandboxes, and invokes external tools (MCP servers, plain functions) |
| 4 | **Memory** | Tiered context storage — hot (session), warm (episodes/facts), cold (vector search) |
| 5 | **Responder** | Formats agent output for the target channel and tone |
| 6 | **Registry** | Unified catalog of agents, tools, and plugins — register, discover, health-check |
| 7 | **Policy** | Declarative safety, permissions, rate limits, cost budgets, and approval gates |

Each primitive is a **Protocol** (Python) / **interface** (TypeScript) with a default implementation. Swap any piece without touching the rest.

## The Gap

**SDKs are too thin.** Pydantic AI, LiteLLM, and similar libraries give you LLM calls and not much else. You still build routing, memory, tool management, and lifecycle from scratch every time.

**Frameworks own your architecture.** LangGraph, CrewAI, and AutoGen give you everything — but on their terms. Swap a component and you fight the framework.

**Nerva is the middle ground.** Opinionated primitives, zero opinions on how you compose them. No base classes to inherit, no lifecycle you did not ask for, no magic graph runtime.

## Quick Start — Python

```bash
pip install nerva
```

```python
import asyncio
from nerva import Orchestrator, ExecContext
from nerva.router import RuleRouter, Rule
from nerva.runtime import InProcessRuntime
from nerva.tools import FunctionToolManager
from nerva.memory import TieredMemory
from nerva.responder import PassthroughResponder
from nerva.registry import InMemoryRegistry
from nerva.policy import NoopPolicyEngine

# Define a simple handler
async def greet_handler(input_text: str, ctx: ExecContext) -> str:
    return f"Hello! You said: {input_text}"

# Wire primitives together
orchestrator = Orchestrator(
    router=RuleRouter(rules=[
        Rule(pattern=r".*", handler="greet", description="Catch-all greeter"),
    ]),
    runtime=InProcessRuntime(handlers={"greet": greet_handler}),
    tools=FunctionToolManager(),
    memory=TieredMemory(),
    responder=PassthroughResponder(),
    registry=InMemoryRegistry(),
    policy=NoopPolicyEngine(),
)

async def main():
    ctx = ExecContext.create(user_id="user_1")
    result = await orchestrator.handle("What's the weather?", ctx)
    print(result.text)

asyncio.run(main())
```

## Quick Start — TypeScript

```bash
npm install nerva
```

```typescript
import {
  Orchestrator,
  ExecContext,
  RuleRouter,
  FunctionToolManager,
  TieredMemory,
  InMemoryRegistry,
  NoopPolicyEngine,
} from "nerva";

// Define a simple handler
async function greetHandler(input: string, ctx: ExecContext): Promise<string> {
  return `Hello! You said: ${input}`;
}

// Wire primitives together
const orchestrator = new Orchestrator({
  router: new RuleRouter({
    rules: [{ pattern: /.*/, handler: "greet", description: "Catch-all greeter" }],
  }),
  runtime: { invoke: async (handler, input, ctx) => ({ text: await greetHandler(input.query, ctx), status: "success" }) },
  tools: new FunctionToolManager(),
  memory: new TieredMemory(),
  responder: { format: async (output, channel, ctx) => ({ text: output.text }) },
  registry: new InMemoryRegistry(),
  policy: new NoopPolicyEngine(),
});

async function main() {
  const ctx = ExecContext.create({ userId: "user_1" });
  const result = await orchestrator.handle("What's the weather?", ctx);
  console.log(result.text);
}

main();
```

## CLI

Scaffold new projects and generate components:

```bash
# Create a new agent project
npx nerva new my-agent --lang python
npx nerva new my-agent --lang typescript

# Generate components inside a project
npx nerva generate agent weather
npx nerva generate tool search
npx nerva generate router custom
npx nerva generate middleware logging
```

## Framework Integration

Nerva is a library layer — like React, not Next.js. It runs **inside** your web framework.

```
FastAPI / NestJS / Express  (HTTP, auth, sessions, swagger, CORS)
  └── Nerva                 (agent orchestration: routing, runtime, tools, memory, policy)
       └── LLM providers, MCP servers, subprocess agents
```

### FastAPI

```python
from fastapi import FastAPI, Depends
from nerva import Orchestrator, ExecContext
from nerva.contrib.fastapi import get_nerva_ctx

app = FastAPI(title="My Agent API")
orchestrator = build_orchestrator()

@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, ctx: ExecContext = Depends(get_nerva_ctx)):
    response = await orchestrator.handle(req.message, ctx)
    return ChatResponse(text=response.text, tokens=ctx.token_usage.total)
```

### NestJS

```typescript
import { Controller, Post, Body, UseGuards } from '@nestjs/common';
import { Orchestrator } from 'nerva';
import { NervaCtx } from 'nerva/contrib/nestjs';

@Controller('chat')
export class ChatController {
  constructor(private orchestrator: Orchestrator) {}

  @Post()
  @UseGuards(JwtAuthGuard)
  async chat(@Body() dto: ChatDto, @NervaCtx() ctx: ExecContext) {
    return this.orchestrator.handle(dto.message, ctx);
  }
}
```

### Express

```typescript
import express from 'express';
import { Orchestrator } from 'nerva';
import { nervaMiddleware } from 'nerva/contrib/express';

const app = express();
app.use(nervaMiddleware(config));

app.post('/chat', async (req, res) => {
  const response = await orchestrator.handle(req.body.message, req.nervaCtx);
  res.json(response);
});
```

## Comparison

| | Composable | Router | Tools | Memory | Policy | Streaming | Server ownership |
|---|---|---|---|---|---|---|---|
| **Nerva** | Yes — use any piece | Embedding + LLM + rules | MCP + functions | Hot/warm/cold tiers | Declarative YAML | Built-in | **Your framework** |
| LangGraph | No — full graph | Graph edges | LangChain tools | Checkpointer | None | LangServe | **LangServe** |
| CrewAI | No — full crew | Role-based | CrewAI tools | Short-term only | None | No | **You build it** |
| AutoGen | No — conversation | Speaker selection | Function calling | Chat history | None | No | **You build it** |
| Pydantic AI | Partial | None | Function tools | None | None | result.stream() | **Your framework** |

## Packages

| Package | Description | |
|---------|-------------|---|
| [`nerva-py`](./packages/nerva-py) | Python implementation (3.11+) | [![PyPI](https://img.shields.io/pypi/v/nerva)](https://pypi.org/project/nerva/) |
| [`nerva-js`](./packages/nerva-js) | TypeScript implementation (Node 20+) | [![npm](https://img.shields.io/npm/v/nerva)](https://www.npmjs.com/package/nerva) |
| [`nerva-cli`](./packages/nerva-cli) | CLI for scaffolding and code generation | [![npm](https://img.shields.io/npm/v/nerva-cli)](https://www.npmjs.com/package/nerva-cli) |

## Links

- [Documentation](https://nerva.dev)
- [Examples](./examples/)
- [TypeSpec Schema](./spec/)
- [Contributing](./CONTRIBUTING.md)
- [GitHub](https://github.com/otomus/nerva)

## License

MIT
