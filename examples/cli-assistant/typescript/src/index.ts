/**
 * CLI assistant example — demonstrates RuleRouter, FunctionToolManager, and TieredMemory.
 */

import {
  Orchestrator,
  ExecContext,
  RuleRouter,
  FunctionToolManager,
  TieredMemory,
  InMemoryRegistry,
  NoopPolicyEngine,
} from "nerva";
import { createInterface } from "readline";

const SESSION_ID = "cli_session_1";
const USER_ID = "cli_user";

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
  name: "add",
  description: "Adds two numbers together",
  handler: async (args: Record<string, unknown>) => {
    const a = Number(args.a ?? 0);
    const b = Number(args.b ?? 0);
    return String(a + b);
  },
});

// ---------------------------------------------------------------------------
// Handlers
// ---------------------------------------------------------------------------

async function timeHandler(_input: string, ctx: ExecContext): Promise<string> {
  const result = await tools.call("get_time", {}, ctx);
  return `The current time is: ${result.output}`;
}

async function mathHandler(_input: string, ctx: ExecContext): Promise<string> {
  const result = await tools.call("add", { a: 2, b: 3 }, ctx);
  return `Example: 2 + 3 = ${result.output}`;
}

async function generalHandler(input: string, _ctx: ExecContext): Promise<string> {
  return `I heard you say: ${input}`;
}

const handlers: Record<string, (input: string, ctx: ExecContext) => Promise<string>> = {
  time: timeHandler,
  math: mathHandler,
  general: generalHandler,
};

// ---------------------------------------------------------------------------
// Router + Runtime wiring
// ---------------------------------------------------------------------------

const router = new RuleRouter({
  rules: [
    { pattern: /(?:time|clock|date|now)/i, handler: "time", description: "Time queries" },
    { pattern: /(?:add|sum|plus|math|\d+\s*\+)/i, handler: "math", description: "Math queries" },
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
// CLI loop
// ---------------------------------------------------------------------------

async function main(): Promise<void> {
  console.log("Nerva CLI Assistant (type 'quit' to exit)");
  console.log("-".repeat(40));

  const rl = createInterface({ input: process.stdin, output: process.stdout });

  const prompt = (): Promise<string> =>
    new Promise((resolve) => rl.question("\n> ", resolve));

  // eslint-disable-next-line no-constant-condition
  while (true) {
    const input = await prompt();
    const trimmed = input.trim().toLowerCase();

    if (["quit", "exit", "q"].includes(trimmed)) {
      console.log("Goodbye!");
      rl.close();
      break;
    }

    if (!input.trim()) continue;

    const ctx = ExecContext.create({ userId: USER_ID, sessionId: SESSION_ID });
    const result = await orchestrator.handle(input, ctx);
    console.log(`\n${result.text}`);
  }
}

main();
