# Slack Bot — Python

A Slack bot skeleton that demonstrates tool use and memory with Nerva.

## What it shows

- FunctionToolManager with three example tools (get_time, lookup_user, search_docs)
- TieredMemory for conversation history per channel
- Rule-based routing for intent classification
- How to bridge Slack events into Nerva's ExecContext

## Setup

1. Create a Slack App at https://api.slack.com/apps
2. Add bot scopes: `chat:write`, `app_mentions:read`, `channels:history`
3. Install the app to your workspace
4. Copy the Bot User OAuth Token

## Run

```bash
cd examples/slack-bot/python

# Install dependencies
pip install -r requirements.txt

# Set your Slack token
export SLACK_BOT_TOKEN=xoxb-your-token-here

# Run the demo (simulated messages, no real Slack connection)
python main.py
```

## Production

To connect to real Slack, replace the demo `main()` with slack_bolt:

```python
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

app = App(token=os.environ["SLACK_BOT_TOKEN"])

@app.event("message")
def on_message(event, say):
    response = asyncio.run(handle_slack_message(event))
    say(response)

SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"]).start()
```
