/**
 * Tests for CLI extension commands: `nerva dev`, `nerva test`, and trace UI.
 *
 * Covers server endpoints, language detection, HTML content-type,
 * and edge cases like missing config and unknown languages.
 */

import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { mkdtemp, rm, writeFile, mkdir } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { request } from 'node:http';
import { startDevServer, type DevServerHandle } from '../src/commands/dev.js';
import { buildTestCommand, detectLanguage } from '../src/commands/test.js';
import { buildTraceHtml } from '../src/commands/trace-ui.js';
import { serializeNervaConfig, type NervaConfig } from '../src/config/nerva-yaml.js';

/** Minimal valid nerva.yaml config for testing. */
function makeConfig(overrides: Partial<NervaConfig> = {}): NervaConfig {
  return {
    name: 'test-project',
    version: '0.1.0',
    lang: 'python',
    agents: [{ name: 'greeter', path: 'agents/greeter.py' }],
    tools: [],
    middleware: [],
    routers: [],
    ...overrides,
  };
}

/**
 * Sends an HTTP request to the dev server and returns the parsed response.
 *
 * @param port - Server port
 * @param method - HTTP method
 * @param path - URL path
 * @param body - Optional request body
 * @returns Tuple of [statusCode, headers, responseBody]
 */
function httpRequest(
  port: number,
  method: string,
  path: string,
  body?: string
): Promise<{ status: number; headers: Record<string, string | string[] | undefined>; body: string }> {
  return new Promise((resolve, reject) => {
    const req = request(
      {
        hostname: 'localhost',
        port,
        path,
        method,
        headers: body ? { 'Content-Type': 'application/json' } : {},
      },
      (res) => {
        const chunks: Buffer[] = [];
        res.on('data', (chunk: Buffer) => chunks.push(chunk));
        res.on('end', () => {
          resolve({
            status: res.statusCode ?? 0,
            headers: res.headers as Record<string, string | string[] | undefined>,
            body: Buffer.concat(chunks).toString('utf-8'),
          });
        });
      }
    );
    req.on('error', reject);
    if (body) {
      req.write(body);
    }
    req.end();
  });
}

let tempDir: string;
let originalCwd: string;

beforeEach(async () => {
  tempDir = await mkdtemp(join(tmpdir(), 'nerva-ext-test-'));
  originalCwd = process.cwd();
  process.chdir(tempDir);
});

afterEach(async () => {
  process.chdir(originalCwd);
  await rm(tempDir, { recursive: true, force: true });
});

// ---------------------------------------------------------------------------
// nerva dev — server endpoints
// ---------------------------------------------------------------------------

describe('nerva dev', () => {
  let handle: DevServerHandle;
  let port: number;

  beforeEach(async () => {
    const config = makeConfig();
    await writeFile(join(tempDir, 'nerva.yaml'), serializeNervaConfig(config), 'utf-8');
    await mkdir(join(tempDir, 'agents'), { recursive: true });
    await mkdir(join(tempDir, 'tools'), { recursive: true });
    await mkdir(join(tempDir, 'middleware'), { recursive: true });

    // Use port 0 to get a random available port
    handle = await startDevServer(tempDir, 0);
    const addr = handle.server.address();
    port = typeof addr === 'object' && addr !== null ? addr.port : 0;
  });

  afterEach(async () => {
    if (handle) {
      await handle.close();
    }
  });

  it('responds to GET /health with status ok', async () => {
    const res = await httpRequest(port, 'GET', '/health');

    expect(res.status).toBe(200);
    const data = JSON.parse(res.body);
    expect(data.status).toBe('ok');
    expect(typeof data.uptime).toBe('number');
  });

  it('responds to GET /components with registered components', async () => {
    const res = await httpRequest(port, 'GET', '/components');

    expect(res.status).toBe(200);
    const data = JSON.parse(res.body);
    expect(data.agents).toHaveLength(1);
    expect(data.agents[0].name).toBe('greeter');
    expect(data.tools).toHaveLength(0);
  });

  it('responds to GET /traces with empty array initially', async () => {
    const res = await httpRequest(port, 'GET', '/traces');

    expect(res.status).toBe(200);
    const data = JSON.parse(res.body);
    expect(Array.isArray(data)).toBe(true);
    expect(data).toHaveLength(0);
  });

  it('POST /chat stores a trace and returns mock response', async () => {
    const chatRes = await httpRequest(
      port,
      'POST',
      '/chat',
      JSON.stringify({ message: 'hello world' })
    );

    expect(chatRes.status).toBe(200);
    const chatData = JSON.parse(chatRes.body);
    expect(chatData.reply).toContain('hello world');

    const tracesRes = await httpRequest(port, 'GET', '/traces');
    const traces = JSON.parse(tracesRes.body);
    expect(traces).toHaveLength(1);
    expect(traces[0].channel).toBe('system');
    expect(traces[0].message).toContain('hello world');
  });

  it('POST /chat with invalid JSON returns 400', async () => {
    const res = await httpRequest(port, 'POST', '/chat', 'not-json');

    expect(res.status).toBe(400);
    const data = JSON.parse(res.body);
    expect(data.error).toContain('Invalid JSON');
  });

  it('POST /chat with missing message field returns mock with empty', async () => {
    const res = await httpRequest(port, 'POST', '/chat', JSON.stringify({}));

    expect(res.status).toBe(200);
    const data = JSON.parse(res.body);
    expect(data.reply).toContain('Received: ');
  });

  it('returns 404 for unknown routes', async () => {
    const res = await httpRequest(port, 'GET', '/nonexistent');

    expect(res.status).toBe(404);
    const data = JSON.parse(res.body);
    expect(data.error).toBe('Not found');
  });

  it('enforces max 100 traces', async () => {
    for (let i = 0; i < 105; i++) {
      await httpRequest(
        port,
        'POST',
        '/chat',
        JSON.stringify({ message: `msg-${i}` })
      );
    }

    const res = await httpRequest(port, 'GET', '/traces');
    const traces = JSON.parse(res.body);
    expect(traces).toHaveLength(100);
    // Oldest should have been evicted — first remaining should be msg-5
    expect(traces[0].message).toContain('msg-5');
  });
});

describe('nerva dev — missing config', () => {
  it('starts without nerva.yaml and /components returns 503', async () => {
    // tempDir has no nerva.yaml
    const handle = await startDevServer(tempDir, 0);
    const addr = handle.server.address();
    const port = typeof addr === 'object' && addr !== null ? addr.port : 0;

    try {
      const healthRes = await httpRequest(port, 'GET', '/health');
      expect(healthRes.status).toBe(200);

      const compRes = await httpRequest(port, 'GET', '/components');
      expect(compRes.status).toBe(503);
      const data = JSON.parse(compRes.body);
      expect(data.error).toContain('Config not loaded');
    } finally {
      await handle.close();
    }
  });
});

// ---------------------------------------------------------------------------
// nerva test — language detection and command building
// ---------------------------------------------------------------------------

describe('nerva test', () => {
  it('detects python language from nerva.yaml', async () => {
    const config = makeConfig({ lang: 'python' });
    await writeFile(join(tempDir, 'nerva.yaml'), serializeNervaConfig(config), 'utf-8');

    const lang = await detectLanguage(tempDir);
    expect(lang).toBe('python');
  });

  it('detects typescript language from nerva.yaml', async () => {
    const config = makeConfig({ lang: 'typescript' });
    await writeFile(join(tempDir, 'nerva.yaml'), serializeNervaConfig(config), 'utf-8');

    const lang = await detectLanguage(tempDir);
    expect(lang).toBe('typescript');
  });

  it('builds correct python test command', () => {
    const { cmd, args } = buildTestCommand('python', false);
    expect(cmd).toBe('python');
    expect(args).toEqual(['-m', 'pytest']);
  });

  it('builds correct typescript test command', () => {
    const { cmd, args } = buildTestCommand('typescript', false);
    expect(cmd).toBe('npx');
    expect(args).toEqual(['vitest', 'run']);
  });

  it('appends --coverage flag for python', () => {
    const { args } = buildTestCommand('python', true);
    expect(args).toContain('--coverage');
    expect(args).toEqual(['-m', 'pytest', '--coverage']);
  });

  it('appends --coverage flag for typescript', () => {
    const { args } = buildTestCommand('typescript', true);
    expect(args).toContain('--coverage');
    expect(args).toEqual(['vitest', 'run', '--coverage']);
  });

  it('throws on unsupported language', () => {
    // @ts-expect-error — intentionally passing invalid lang
    expect(() => buildTestCommand('ruby', false)).toThrow('Unsupported language');
  });

  it('throws when nerva.yaml is missing', async () => {
    await expect(detectLanguage(tempDir)).rejects.toThrow();
  });

  it('does not mutate the runner config when adding coverage', () => {
    const first = buildTestCommand('python', true);
    const second = buildTestCommand('python', false);

    expect(first.args).toContain('--coverage');
    expect(second.args).not.toContain('--coverage');
  });
});

// ---------------------------------------------------------------------------
// Trace UI
// ---------------------------------------------------------------------------

describe('trace UI', () => {
  it('returns HTML with correct content-type from dev server', async () => {
    const config = makeConfig();
    await writeFile(join(tempDir, 'nerva.yaml'), serializeNervaConfig(config), 'utf-8');

    const handle = await startDevServer(tempDir, 0);
    const addr = handle.server.address();
    const port = typeof addr === 'object' && addr !== null ? addr.port : 0;

    try {
      const res = await httpRequest(port, 'GET', '/traces/ui');

      expect(res.status).toBe(200);
      expect(res.headers['content-type']).toContain('text/html');
      expect(res.body).toContain('<!DOCTYPE html>');
      expect(res.body).toContain('Nerva Trace UI');
    } finally {
      await handle.close();
    }
  });

  it('buildTraceHtml returns a complete HTML document', () => {
    const html = buildTraceHtml();

    expect(html).toContain('<!DOCTYPE html>');
    expect(html).toContain('<title>Nerva Trace UI</title>');
    expect(html).toContain('fetch(\'/traces\')');
    expect(html).toContain('monospace');
  });

  it('HTML includes color coding classes', () => {
    const html = buildTraceHtml();

    expect(html).toContain('channel-agent');
    expect(html).toContain('channel-tool');
    expect(html).toContain('channel-middleware');
    expect(html).toContain('channel-router');
    expect(html).toContain('channel-system');
  });

  it('HTML includes auto-refresh interval', () => {
    const html = buildTraceHtml();

    expect(html).toContain('setInterval');
    expect(html).toContain('2000');
  });

  it('HTML includes tree rendering logic', () => {
    const html = buildTraceHtml();

    // Should handle nested children for tree display
    expect(html).toContain('children');
    expect(html).toContain('renderTrace');
  });
});
