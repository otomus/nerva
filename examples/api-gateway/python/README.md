# API Gateway — Python (FastAPI)

A FastAPI application that exposes Nerva as a REST API with auth, streaming SSE, and Swagger docs.

## What it shows

- FastAPI + Nerva integration pattern (Nerva is invisible to API consumers)
- API key auth bridged into ExecContext
- POST `/chat` — standard request/response
- GET `/chat/stream` — Server-Sent Events streaming
- GET `/health` — health check
- Auto-generated Swagger docs at `/docs`

## Run

```bash
cd examples/api-gateway/python

# Install dependencies
pip install -r requirements.txt

# Start the server
uvicorn main:app --reload --port 8000
```

## Try it

```bash
# Standard chat
curl -X POST http://localhost:8000/chat \
  -H "Authorization: Bearer key_alice" \
  -H "Content-Type: application/json" \
  -d '{"message": "what time is it?"}'

# Streaming
curl -N http://localhost:8000/chat/stream?q=hello \
  -H "Authorization: Bearer key_alice"

# Swagger docs
open http://localhost:8000/docs
```

## API Keys (mock)

| Key | User | Role |
|-----|------|------|
| `key_alice` | alice | admin |
| `key_bob` | bob | user |

Replace the mock auth with real JWT/OAuth for production.
