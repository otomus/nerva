/**
 * Express API gateway example — demonstrates Nerva with auth, streaming SSE, and REST endpoints.
 */

import express, { Request, Response, NextFunction } from "express";
import {
  Orchestrator,
  ExecContext,
  RuleRouter,
  FunctionToolManager,
  TieredMemory,
  InMemoryRegistry,
  NoopPolicyEngine,
} from "nerva";

const PORT = Number(process.env.PORT ?? 8000);

// ---------------------------------------------------------------------------
// Auth (mock — replace with real JWT/OAuth)
// ---------------------------------------------------------------------------

interface AuthUser {
  userId: string;
  role: string;
}

const API_KEYS: Record<string, AuthUser> = {
  key_alice: { userId: "alice", role: "admin" },
  key_bob: { userId: "bob", role: "user" },
};

function authMiddleware(req: Request, res: Response, next: NextFunction): void {
  const auth = req.headers.authorization ?? "";
  if (!auth.startsWith("Bearer ")) {
    res.status(401).json({ error: "Missing Authorization header" });
    return;
  }

  const token = auth.slice("Bearer ".length);
  const user = API_KEYS[token];
  if (!user) {
    res.status(403).json({ error: "Invalid API key" });
    return;
  }

  (req as Request & { user: AuthUser }).user = user;
  next();
}

// ---------------------------------------------------------------------------
// Tools
// ---------------------------------------------------------------------------

const tools = new FunctionToolManager();

tools.register({
  name: "get_time",
  description: "Returns the current date and time",
  handler: async () => new Date().toISOString(),
});

tools.register({
  name: "echo",
  description: "Echoes back the input message",
  handler: async (args: Record<string, unknown>) => String(args.message ?? ""),
});

// ---------------------------------------------------------------------------
// Handlers
// ---------------------------------------------------------------------------

async function timeHandler(_input: string, ctx: ExecContext): Promise<string> {
  const result = await tools.call("get_time", {}, ctx);
  return `The current time is ${result.output}`;
}

async function generalHandler(input: string, _ctx: ExecContext): Promise<string> {
  return `You said: ${input}`;
}

const handlers: Record<string, (input: string, ctx: ExecContext) => Promise<string>> = {
  time: timeHandler,
  general: generalHandler,
};

// ---------------------------------------------------------------------------
// Orchestrator
// ---------------------------------------------------------------------------

const router = new RuleRouter({
  rules: [
    { pattern: /(?:time|clock|date|now)/i, handler: "time", description: "Time queries" },
    { pattern: /.*/, handler: "general", description: "General catch-all" },
  ],
});

const orchestrator = new Orchestrator({
  router,
  runtime: {
    invoke: async (handler: string, input: { query: string }, ctx: ExecContext) => {
      const fn = handlers[handler] ?? generalHandler;
      const text = await fn(input.query, ctx);
      return { text, status: "success" as const };
    },
  },
  tools,
  memory: new TieredMemory(),
  responder: { format: async (output) => ({ text: output.text }) },
  registry: new InMemoryRegistry(),
  policy: new NoopPolicyEngine(),
});

// ---------------------------------------------------------------------------
// Express app
// ---------------------------------------------------------------------------

const app = express();
app.use(express.json());

// POST /chat — standard request/response
app.post("/chat", authMiddleware, async (req: Request, res: Response) => {
  const user = (req as Request & { user: AuthUser }).user;
  const { message, sessionId } = req.body as { message: string; sessionId?: string };

  if (!message) {
    res.status(400).json({ error: "message is required" });
    return;
  }

  const ctx = ExecContext.create({ userId: user.userId, sessionId });
  const result = await orchestrator.handle(message, ctx);

  res.json({
    text: result.text,
    userId: user.userId,
    sessionId: sessionId ?? null,
  });
});

// GET /chat/stream — Server-Sent Events
app.get("/chat/stream", authMiddleware, async (req: Request, res: Response) => {
  const user = (req as Request & { user: AuthUser }).user;
  const q = req.query.q as string | undefined;

  if (!q) {
    res.status(400).json({ error: "q query parameter is required" });
    return;
  }

  res.setHeader("Content-Type", "text/event-stream");
  res.setHeader("Cache-Control", "no-cache");
  res.setHeader("Connection", "keep-alive");

  const ctx = ExecContext.create({ userId: user.userId });
  const result = await orchestrator.handle(q, ctx);

  // Simulate word-by-word streaming
  for (const word of result.text.split(" ")) {
    res.write(`data: ${word}\n\n`);
  }
  res.write("data: [DONE]\n\n");
  res.end();
});

// GET /health — health check
app.get("/health", (_req: Request, res: Response) => {
  res.json({ status: "ok" });
});

app.listen(PORT, () => {
  console.log(`Nerva API Gateway running on http://localhost:${PORT}`);
  console.log(`Swagger: not included — use a tool like swagger-jsdoc for production`);
});
