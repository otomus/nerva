/**
 * Minimal Express app demonstrating Nerva integration.
 *
 * Run with:
 *   npx tsx src/index.ts
 */

import express from "express";
import {
  nervaMiddleware,
  sseHandler,
  NERVA_CTX_KEY,
} from "nerva/contrib/express";
import { ExecContext } from "nerva";
import type { Orchestrator } from "nerva";

// In a real app, build this from your router, runtime, and responder.
const orchestrator = null as unknown as Orchestrator;

const app = express();
app.use(express.json());
app.use(nervaMiddleware());

/** Handle a chat message through the Nerva pipeline. */
app.post("/chat", async (req, res) => {
  const ctx = req[NERVA_CTX_KEY] as ExecContext;
  const { message } = req.body as { message: string };

  const response = await orchestrator.handle(message, ctx);
  res.json({ text: response.text });
});

/** Stream a chat response as Server-Sent Events. */
app.post(
  "/chat/stream",
  sseHandler(orchestrator, {
    getMessage: (req) => (req as Record<string, { message: string }>)["body"]!.message,
    getCtx: (req) => req[NERVA_CTX_KEY] as ExecContext,
  }) as express.RequestHandler,
);

const PORT = 3000;
app.listen(PORT, () => {
  console.log(`Nerva Express agent listening on port ${PORT}`);
});
