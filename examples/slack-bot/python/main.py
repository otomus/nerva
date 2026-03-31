"""Slack bot example — demonstrates FunctionToolManager, TieredMemory, and tool use with Nerva.

This is a skeleton. You need a Slack App token to run it for real.
Replace the mock Slack client with slack_bolt for production use.
"""

import asyncio
import os
from datetime import datetime

from nerva import ExecContext, Orchestrator
from nerva.memory import TieredMemory
from nerva.policy import NoopPolicyEngine
from nerva.registry import InMemoryRegistry
from nerva.responder import PassthroughResponder
from nerva.router import Rule, RuleRouter
from nerva.tools import FunctionToolManager

SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "xoxb-mock-token")

# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

tools = FunctionToolManager()


@tools.register(name="get_time", description="Returns the current date and time")
async def get_time(_args: dict, _ctx: ExecContext) -> str:
    """Return the current ISO-formatted timestamp."""
    return datetime.now().isoformat()


@tools.register(name="lookup_user", description="Looks up a user's profile by Slack user ID")
async def lookup_user(args: dict, _ctx: ExecContext) -> str:
    """Look up a Slack user profile. In production, call the Slack API."""
    user_id = args.get("user_id", "unknown")
    # Mock response — replace with real Slack API call
    return f"User {user_id}: name=Jane Doe, role=Engineer, timezone=UTC"


@tools.register(name="search_docs", description="Searches internal documentation")
async def search_docs(args: dict, _ctx: ExecContext) -> str:
    """Search internal docs. Replace with real search backend."""
    query = args.get("query", "")
    # Mock response
    return f"Found 3 results for '{query}': [Getting Started, API Reference, Troubleshooting]"


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


async def time_handler(input_text: str, ctx: ExecContext) -> str:
    """Handle time-related queries."""
    result = await tools.call("get_time", {}, ctx)
    return f"The current time is {result.output}"


async def help_handler(input_text: str, ctx: ExecContext) -> str:
    """Handle documentation/help queries."""
    result = await tools.call("search_docs", {"query": input_text}, ctx)
    return f"Here's what I found:\n{result.output}"


async def general_handler(input_text: str, _ctx: ExecContext) -> str:
    """Catch-all handler."""
    return f"I'm not sure how to help with that. Try asking about time or documentation."


HANDLERS = {
    "time": time_handler,
    "help": help_handler,
    "general": general_handler,
}

# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

router = RuleRouter(rules=[
    Rule(pattern=r"(?i)(time|clock|date|now)", handler="time", description="Time queries"),
    Rule(pattern=r"(?i)(help|doc|how|guide|search)", handler="help", description="Help/docs queries"),
    Rule(pattern=r".*", handler="general", description="General catch-all"),
])

memory = TieredMemory()


class SimpleRuntime:
    """Minimal in-process runtime for the Slack bot."""

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
# Mock Slack event handler
# ---------------------------------------------------------------------------


async def handle_slack_message(event: dict) -> str:
    """
    Handle a Slack message event.

    In production, this would be called by slack_bolt's event handler:
        @app.event("message")
        def handle_message(event, say):
            response = asyncio.run(handle_slack_message(event))
            say(response)
    """
    user_id = event.get("user", "unknown")
    text = event.get("text", "")
    channel = event.get("channel", "general")

    ctx = ExecContext.create(
        user_id=user_id,
        session_id=f"slack_{channel}",
    )

    result = await orchestrator.handle(text, ctx)
    return result.text


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------


async def main() -> None:
    """Run a demo simulating Slack messages."""
    print("Nerva Slack Bot Demo")
    print("=" * 40)

    test_messages = [
        {"user": "U123", "text": "what time is it?", "channel": "C001"},
        {"user": "U456", "text": "how do I set up the API?", "channel": "C001"},
        {"user": "U123", "text": "tell me a joke", "channel": "C002"},
    ]

    for msg in test_messages:
        print(f"\n[#{msg['channel']}] <{msg['user']}> {msg['text']}")
        response = await handle_slack_message(msg)
        print(f"  Bot: {response}")


if __name__ == "__main__":
    asyncio.run(main())
