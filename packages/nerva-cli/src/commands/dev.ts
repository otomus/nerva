/**
 * `nerva dev` command.
 *
 * Starts a development server with hot reload, watching agent/tool/middleware
 * directories for changes and serving diagnostic endpoints.
 */

import { Command } from 'commander';
import { createServer, type IncomingMessage, type ServerResponse, type Server } from 'node:http';
import { watch, type FSWatcher } from 'node:fs';
import { join } from 'node:path';
import { stat } from 'node:fs/promises';
import { readNervaConfig, type NervaConfig } from '../config/nerva-yaml.js';
import { buildTraceHtml } from './trace-ui.js';

/** Default port for the dev server. */
const DEFAULT_PORT = 3000;

/** Maximum number of trace entries kept in memory. */
const MAX_TRACES = 100;

/** Directories watched for file changes, relative to project root. */
const WATCHED_DIRS = ['agents', 'tools', 'middleware'] as const;

/** A single trace event stored in memory. */
export interface TraceEvent {
  timestamp: string;
  channel: string;
  message: string;
  children?: TraceEvent[];
}

/** Options accepted by the dev command. */
interface DevCommandOptions {
  port: string;
}

/** Runtime state for the dev server, exposed for testability. */
export interface DevServerHandle {
  server: Server;
  watchers: FSWatcher[];
  traces: TraceEvent[];
  config: NervaConfig | null;
  close: () => Promise<void>;
}

/**
 * Reads the request body as a UTF-8 string.
 *
 * @param req - Incoming HTTP request
 * @returns Resolved body string
 */
function readBody(req: IncomingMessage): Promise<string> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = [];
    req.on('data', (chunk: Buffer) => chunks.push(chunk));
    req.on('end', () => resolve(Buffer.concat(chunks).toString('utf-8')));
    req.on('error', reject);
  });
}

/**
 * Sends a JSON response with the given status code.
 *
 * @param res - Server response object
 * @param statusCode - HTTP status code
 * @param body - Object to serialize as JSON
 */
function sendJson(res: ServerResponse, statusCode: number, body: unknown): void {
  const payload = JSON.stringify(body);
  res.writeHead(statusCode, {
    'Content-Type': 'application/json',
    'Content-Length': Buffer.byteLength(payload),
  });
  res.end(payload);
}

/**
 * Sends an HTML response with the given status code.
 *
 * @param res - Server response object
 * @param statusCode - HTTP status code
 * @param html - HTML string to send
 */
function sendHtml(res: ServerResponse, statusCode: number, html: string): void {
  res.writeHead(statusCode, {
    'Content-Type': 'text/html; charset=utf-8',
    'Content-Length': Buffer.byteLength(html),
  });
  res.end(html);
}

/**
 * Adds a trace event to the in-memory store, evicting the oldest if at capacity.
 *
 * @param traces - Mutable trace array
 * @param event - Event to add
 */
function addTrace(traces: TraceEvent[], event: TraceEvent): void {
  traces.push(event);
  if (traces.length > MAX_TRACES) {
    traces.shift();
  }
}

/**
 * Handles an incoming HTTP request by routing to the appropriate endpoint.
 *
 * @param req - Incoming request
 * @param res - Server response
 * @param handle - Dev server runtime state
 */
async function handleRequest(
  req: IncomingMessage,
  res: ServerResponse,
  handle: DevServerHandle
): Promise<void> {
  const url = req.url ?? '/';
  const method = req.method ?? 'GET';

  if (method === 'GET' && url === '/health') {
    sendJson(res, 200, { status: 'ok', uptime: process.uptime() });
    return;
  }

  if (method === 'GET' && url === '/components') {
    if (!handle.config) {
      sendJson(res, 503, { error: 'Config not loaded' });
      return;
    }
    sendJson(res, 200, {
      agents: handle.config.agents,
      tools: handle.config.tools,
      middleware: handle.config.middleware,
      routers: handle.config.routers,
    });
    return;
  }

  if (method === 'GET' && url === '/traces') {
    sendJson(res, 200, handle.traces);
    return;
  }

  if (method === 'GET' && url === '/traces/ui') {
    sendHtml(res, 200, buildTraceHtml());
    return;
  }

  if (method === 'POST' && url === '/chat') {
    const body = await readBody(req);
    let message = '';
    try {
      const parsed = JSON.parse(body) as { message?: string };
      message = parsed.message ?? '';
    } catch {
      sendJson(res, 400, { error: 'Invalid JSON body' });
      return;
    }

    addTrace(handle.traces, {
      timestamp: new Date().toISOString(),
      channel: 'system',
      message: `chat: ${message}`,
    });

    sendJson(res, 200, {
      reply: `[mock] Received: ${message}`,
      timestamp: new Date().toISOString(),
    });
    return;
  }

  sendJson(res, 404, { error: 'Not found' });
}

/**
 * Attempts to reload nerva.yaml and logs what changed.
 *
 * @param projectDir - Project root directory
 * @param handle - Dev server runtime state
 * @param changedPath - The file path that triggered the reload
 */
async function reloadConfig(
  projectDir: string,
  handle: DevServerHandle,
  changedPath: string
): Promise<void> {
  try {
    handle.config = await readNervaConfig(projectDir);
    console.log(`[nerva dev] Reloaded config (changed: ${changedPath})`);
  } catch (err: unknown) {
    const message = err instanceof Error ? err.message : String(err);
    console.error(`[nerva dev] Failed to reload config: ${message}`);
  }
}

/**
 * Sets up file watchers on the standard component directories.
 *
 * @param projectDir - Project root directory
 * @param handle - Dev server runtime state
 * @returns Array of active FSWatcher instances
 */
async function setupWatchers(
  projectDir: string,
  handle: DevServerHandle
): Promise<FSWatcher[]> {
  const watchers: FSWatcher[] = [];

  for (const dir of WATCHED_DIRS) {
    const dirPath = join(projectDir, dir);
    const exists = await stat(dirPath).then(() => true).catch(() => false);

    if (!exists) {
      continue;
    }

    const watcher = watch(dirPath, { recursive: true }, (_eventType, filename) => {
      const changed = filename ? join(dir, filename) : dir;
      console.log(`[nerva dev] Change detected: ${changed}`);
      void reloadConfig(projectDir, handle, changed);
    });

    watchers.push(watcher);
  }

  return watchers;
}

/**
 * Starts the dev server and file watchers.
 *
 * Exported for testing — callers should call `handle.close()` to shut down.
 *
 * @param projectDir - Absolute path to project root
 * @param port - Port number to listen on
 * @returns DevServerHandle with server, watchers, and close method
 */
export async function startDevServer(
  projectDir: string,
  port: number
): Promise<DevServerHandle> {
  let config: NervaConfig | null = null;

  try {
    config = await readNervaConfig(projectDir);
  } catch {
    console.warn('[nerva dev] No nerva.yaml found — starting without config');
  }

  const handle: DevServerHandle = {
    server: null as unknown as Server,
    watchers: [],
    traces: [],
    config,
    close: async () => {
      for (const w of handle.watchers) {
        w.close();
      }
      await new Promise<void>((resolve, reject) => {
        handle.server.close((err) => (err ? reject(err) : resolve()));
      });
    },
  };

  const server = createServer((req, res) => {
    handleRequest(req, res, handle).catch((err: unknown) => {
      const message = err instanceof Error ? err.message : String(err);
      console.error(`[nerva dev] Request error: ${message}`);
      if (!res.headersSent) {
        sendJson(res, 500, { error: 'Internal server error' });
      }
    });
  });

  handle.server = server;
  handle.watchers = await setupWatchers(projectDir, handle);

  await new Promise<void>((resolve) => {
    server.listen(port, () => resolve());
  });

  return handle;
}

/**
 * Prints the startup banner showing URL and watched directories.
 *
 * @param port - Port the server is listening on
 * @param watchedDirs - Directories being watched for changes
 */
function printBanner(port: number, watchedDirs: readonly string[]): void {
  console.log('');
  console.log('  Nerva Dev Server');
  console.log('  ================');
  console.log(`  URL:      http://localhost:${port}`);
  console.log(`  Health:   http://localhost:${port}/health`);
  console.log(`  Traces:   http://localhost:${port}/traces/ui`);
  console.log(`  Watching: ${watchedDirs.join(', ')}`);
  console.log('');
}

/**
 * Registers the `nerva dev` command with the CLI program.
 *
 * @param program - The root commander program
 */
export function registerDevCommand(program: Command): void {
  program
    .command('dev')
    .description('Start development server with hot reload')
    .option('-p, --port <port>', 'Port to listen on', String(DEFAULT_PORT))
    .action(async (options: DevCommandOptions) => {
      const port = parseInt(options.port, 10);
      if (isNaN(port) || port < 0 || port > 65535) {
        throw new Error(`Invalid port: "${options.port}"`);
      }

      const projectDir = process.cwd();
      await startDevServer(projectDir, port);
      printBanner(port, WATCHED_DIRS);
    });
}
