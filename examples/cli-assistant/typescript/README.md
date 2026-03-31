# CLI Assistant — TypeScript

A minimal CLI agent that demonstrates RuleRouter, FunctionToolManager, and TieredMemory.

## What it shows

- Rule-based routing (regex patterns to handler selection)
- Function tools (get_time, add)
- In-process runtime (handler functions called directly)
- Interactive CLI loop

## Run

```bash
cd examples/cli-assistant/typescript

# Install dependencies
npm install

# Run the assistant
npm start
```

## Try it

```
> what time is it?
The current time is: 2026-03-31T14:30:00.123Z

> add some numbers
Example: 2 + 3 = 5

> hello world
I heard you say: hello world

> quit
Goodbye!
```
