# API Gateway — TypeScript (Express)

An Express application that exposes Nerva as a REST API with auth, streaming SSE, and health checks.

## What it shows

- Express + Nerva integration pattern (Nerva is invisible to API consumers)
- API key auth bridged into ExecContext
- POST `/chat` — standard request/response
- GET `/chat/stream` — Server-Sent Events streaming
- GET `/health` — health check

## Run

```bash
cd examples/api-gateway/typescript

# Install dependencies
npm install

# Start the server
npm start

# Or with hot reload
npm run dev
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

# Health check
curl http://localhost:8000/health
```

## API Keys (mock)

| Key | User | Role |
|-----|------|------|
| `key_alice` | alice | admin |
| `key_bob` | bob | user |

Replace the mock auth middleware with passport.js or similar for production.
