---
title: Migration Guide
description: Migrate from LangGraph, CrewAI, or AutoGen to Nerva.
---

## From LangGraph

### Concept mapping

| LangGraph | Nerva | Notes |
|-----------|-------|-------|
| Graph | Orchestrator | Nerva uses a pipeline, not a graph. Complex flows use delegation. |
| Node | Agent handler | A registered async function in the Runtime |
| Edge / conditional edge | Router | RuleRouter for static, EmbeddingRouter for dynamic routing |
| State | Memory (TieredMemory) | Hot tier = conversation, Warm = facts, Cold = knowledge |
| Tool | FunctionToolManager or MCPToolManager | Same concept, protocol-based |
| Checkpointer | Memory.store() | Explicit store calls instead of automatic checkpointing |

### What you keep

- Your LLM provider configuration
- Your tool implementations (wrap as Nerva tools)
- Your prompt templates

### What changes

- **No graph definition.** Routing is declarative (rules, embeddings, or LLM-based), not edges between nodes.
- **Explicit context.** ExecContext replaces LangGraph's implicit state passing.
- **No compile step.** Nerva primitives are instantiated directly.

### Code comparison

**LangGraph:**

```python
from langgraph.graph import StateGraph

graph = StateGraph(AgentState)
graph.add_node("classify", classify_intent)
graph.add_node("weather", weather_agent)
graph.add_node("calendar", calendar_agent)
graph.add_conditional_edges("classify", route_by_intent)
app = graph.compile()
result = app.invoke({"messages": [user_message]})
```

**Nerva:**

```python
from nerva import Orchestrator, ExecContext
from nerva.router.rule import RuleRouter, Rule
from nerva.runtime.inprocess import InProcessRuntime

runtime = InProcessRuntime()
runtime.register("weather", weather_handler)
runtime.register("calendar", calendar_handler)

orchestrator = Orchestrator(
    router=RuleRouter(rules=[
        Rule(pattern=r"weather|forecast", handler="weather", intent="weather"),
        Rule(pattern=r"calendar|schedule", handler="calendar", intent="calendar"),
    ]),
    runtime=runtime,
    ...
)

ctx = ExecContext.create(user_id="user_1")
result = await orchestrator.handle(user_message, ctx)
```

---

## From CrewAI

### Concept mapping

| CrewAI | Nerva | Notes |
|--------|-------|-------|
| Crew | Orchestrator | Top-level coordinator |
| Agent | Handler + RegistryEntry | Handler = code, RegistryEntry = metadata |
| Task | AgentInput | Structured input with message and args |
| Tool | FunctionToolManager | Same concept |
| Process (sequential/hierarchical) | invoke_chain / delegate | Chain for sequential, delegate for hierarchical |
| Memory | TieredMemory | Short-term = Hot, Long-term = Warm/Cold |

### What you keep

- Your agent logic (convert to handler functions)
- Your tool definitions
- Your LLM configuration

### What changes

- **No agent personas in config.** Tone/personality moves to ToneResponder.
- **Explicit routing.** CrewAI's task assignment becomes Router classification.
- **No implicit orchestration.** You compose the pipeline explicitly.

### Code comparison

**CrewAI:**

```python
from crewai import Crew, Agent, Task

researcher = Agent(role="Researcher", goal="Find information", tools=[search_tool])
writer = Agent(role="Writer", goal="Write content")

crew = Crew(
    agents=[researcher, writer],
    tasks=[
        Task(description="Research topic X", agent=researcher),
        Task(description="Write article about X", agent=writer),
    ],
    process="sequential",
)
result = crew.kickoff()
```

**Nerva:**

```python
runtime = InProcessRuntime()
runtime.register("researcher", research_handler)
runtime.register("writer", writer_handler)

# Sequential: chain handlers
result = await runtime.invoke_chain(
    ["researcher", "writer"],
    AgentInput(message="Write an article about topic X"),
    ctx,
)

# Or hierarchical: delegate from one handler to another
async def editor_handler(input: AgentInput, ctx: ExecContext) -> str:
    research = await runtime.delegate("researcher", AgentInput(message=input.message), ctx)
    article = await runtime.delegate("writer", AgentInput(message=research.output), ctx)
    return article.output
```

---

## From AutoGen

### Concept mapping

| AutoGen | Nerva | Notes |
|---------|-------|-------|
| Agent | Handler function | Stateless function, state lives in Memory |
| ConversableAgent | Handler + TieredMemory | Memory provides conversation history |
| GroupChat | Router + Runtime.delegate | Router selects speaker, delegate executes |
| Function call | FunctionToolManager | Same concept |
| UserProxyAgent | PolicyEngine (approval gate) | Approval gate pauses for human input |
| Conversation history | Memory.hot (InMemoryHotMemory) | Explicit tier instead of implicit list |

### What you keep

- Your function implementations
- Your system prompts
- Your LLM configuration

### What changes

- **No agent objects.** Agents are stateless handler functions. State lives in Memory.
- **Explicit conversation management.** Memory.hot replaces AutoGen's implicit message list.
- **No implicit back-and-forth.** Multi-turn delegation is explicit via `runtime.delegate()`.

### Code comparison

**AutoGen:**

```python
from autogen import AssistantAgent, UserProxyAgent

assistant = AssistantAgent("assistant", llm_config=llm_config)
user_proxy = UserProxyAgent("user_proxy", human_input_mode="NEVER")

user_proxy.initiate_chat(assistant, message="Analyze this data")
```

**Nerva:**

```python
runtime = InProcessRuntime()
runtime.register("assistant", assistant_handler)

# Single turn
result = await runtime.invoke(
    "assistant",
    AgentInput(message="Analyze this data"),
    ctx,
)

# Multi-turn with memory
memory = TieredMemory(hot=InMemoryHotMemory())
await memory.store(MemoryEvent(content="Analyze this data", source="user"), ctx)

context = await memory.recall("data analysis", ctx)
result = await runtime.invoke(
    "assistant",
    AgentInput(message="Analyze this data", history=context.conversation),
    ctx,
)
await memory.store(MemoryEvent(content=result.output, source="assistant"), ctx)
```

For human-in-the-loop, use the PolicyEngine approval gate:

```yaml
policies:
  approval:
    agents:
      - name: deploy_agent
        requires_approval: true
        approvers: [human_operator]
```
