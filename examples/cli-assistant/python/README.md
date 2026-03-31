# CLI Assistant — Python

A minimal CLI agent that demonstrates RuleRouter, FunctionToolManager, and TieredMemory.

## What it shows

- Rule-based routing (regex patterns to handler selection)
- Function tools (get_time, add)
- In-process runtime (handler functions called directly)
- Interactive CLI loop

## Run

```bash
cd examples/cli-assistant/python

# Install dependencies
pip install -r requirements.txt

# Run the assistant
python main.py
```

## Try it

```
> what time is it?
The current time is: 2026-03-31T14:30:00.123456

> add some numbers
Example: 2 + 3 = 5.0

> hello world
I heard you say: hello world

> quit
Goodbye!
```
