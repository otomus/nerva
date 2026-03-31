"""CLI assistant example — demonstrates RuleRouter, FunctionToolManager, and TieredMemory."""

import asyncio
from datetime import datetime

from nerva import ExecContext, Orchestrator
from nerva.memory import TieredMemory
from nerva.policy import NoopPolicyEngine
from nerva.registry import InMemoryRegistry
from nerva.responder import PassthroughResponder
from nerva.router import Rule, RuleRouter
from nerva.tools import FunctionToolManager

SESSION_ID = "cli_session_1"
USER_ID = "cli_user"


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

tools = FunctionToolManager()


@tools.register(name="get_time", description="Returns the current date and time")
async def get_time(_args: dict, _ctx: ExecContext) -> str:
    """Return the current ISO-formatted timestamp."""
    return datetime.now().isoformat()


@tools.register(name="add", description="Adds two numbers together")
async def add(args: dict, _ctx: ExecContext) -> str:
    """Add two numbers from args['a'] and args['b']."""
    a = float(args.get("a", 0))
    b = float(args.get("b", 0))
    return str(a + b)


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


async def time_handler(input_text: str, ctx: ExecContext) -> str:
    """Handle time-related queries by calling the get_time tool."""
    result = await tools.call("get_time", {}, ctx)
    return f"The current time is: {result.output}"


async def math_handler(input_text: str, ctx: ExecContext) -> str:
    """Handle math-related queries by calling the add tool."""
    result = await tools.call("add", {"a": 2, "b": 3}, ctx)
    return f"Example: 2 + 3 = {result.output}"


async def general_handler(input_text: str, _ctx: ExecContext) -> str:
    """Catch-all handler for unmatched messages."""
    return f"I heard you say: {input_text}"


# ---------------------------------------------------------------------------
# Router + Runtime wiring
# ---------------------------------------------------------------------------

HANDLERS = {
    "time": time_handler,
    "math": math_handler,
    "general": general_handler,
}

router = RuleRouter(rules=[
    Rule(pattern=r"(?i)(time|clock|date|now)", handler="time", description="Time queries"),
    Rule(pattern=r"(?i)(add|sum|plus|math|\d+\s*\+)", handler="math", description="Math queries"),
    Rule(pattern=r".*", handler="general", description="General catch-all"),
])

memory = TieredMemory()


class SimpleRuntime:
    """Minimal in-process runtime that dispatches to handler functions."""

    async def invoke(self, handler: str, input_data: dict, ctx: ExecContext) -> dict:
        """Invoke a handler by name."""
        fn = HANDLERS.get(handler, general_handler)
        text = await fn(input_data.get("query", ""), ctx)
        return {"text": text, "status": "success"}


orchestrator = Orchestrator(
    router=router,
    runtime=SimpleRuntime(),
    tools=tools,
    memory=memory,
    responder=PassthroughResponder(),
    registry=InMemoryRegistry(),
    policy=NoopPolicyEngine(),
)


# ---------------------------------------------------------------------------
# CLI loop
# ---------------------------------------------------------------------------


async def main() -> None:
    """Run the interactive CLI loop."""
    print("Nerva CLI Assistant (type 'quit' to exit)")
    print("-" * 40)

    while True:
        try:
            user_input = input("\n> ")
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break

        if user_input.strip().lower() in ("quit", "exit", "q"):
            print("Goodbye!")
            break

        if not user_input.strip():
            continue

        ctx = ExecContext.create(user_id=USER_ID, session_id=SESSION_ID)
        result = await orchestrator.handle(user_input, ctx)
        print(f"\n{result.text}")


if __name__ == "__main__":
    asyncio.run(main())
