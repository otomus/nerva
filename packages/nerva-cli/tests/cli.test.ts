/**
 * CLI integration tests.
 *
 * Tests the core commands: `nerva new`, `nerva generate`, and the
 * nerva.yaml parser/serializer.
 */

import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { mkdtemp, rm, readFile, mkdir, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { existsSync } from 'node:fs';
import { executeNew } from '../src/commands/new.js';
import { executeGenerate } from '../src/commands/generate.js';
import {
  parseNervaConfig,
  serializeNervaConfig,
  readNervaConfig,
  type NervaConfig,
} from '../src/config/nerva-yaml.js';
import { renderTemplate, toPascalCase } from '../src/templates/render.js';

let tempDir: string;
let originalCwd: string;

beforeEach(async () => {
  tempDir = await mkdtemp(join(tmpdir(), 'nerva-test-'));
  originalCwd = process.cwd();
  process.chdir(tempDir);
});

afterEach(async () => {
  process.chdir(originalCwd);
  await rm(tempDir, { recursive: true, force: true });
});

// ---------------------------------------------------------------------------
// nerva new
// ---------------------------------------------------------------------------

describe('nerva new', () => {
  it('creates correct Python project structure', async () => {
    await executeNew('my-project', 'python');

    const projectDir = join(tempDir, 'my-project');
    expect(existsSync(join(projectDir, 'nerva.yaml'))).toBe(true);
    expect(existsSync(join(projectDir, 'main.py'))).toBe(true);
    expect(existsSync(join(projectDir, 'requirements.txt'))).toBe(true);
    expect(existsSync(join(projectDir, 'agents'))).toBe(true);
    expect(existsSync(join(projectDir, 'tools'))).toBe(true);
    expect(existsSync(join(projectDir, 'memory'))).toBe(true);
    expect(existsSync(join(projectDir, 'middleware'))).toBe(true);
    expect(existsSync(join(projectDir, 'tests'))).toBe(true);

    const yaml = await readFile(join(projectDir, 'nerva.yaml'), 'utf-8');
    expect(yaml).toContain('name: my-project');
    expect(yaml).toContain('lang: python');
  });

  it('creates correct TypeScript project structure', async () => {
    await executeNew('ts-project', 'typescript');

    const projectDir = join(tempDir, 'ts-project');
    expect(existsSync(join(projectDir, 'nerva.yaml'))).toBe(true);
    expect(existsSync(join(projectDir, 'src/index.ts'))).toBe(true);
    expect(existsSync(join(projectDir, 'package.json'))).toBe(true);
    expect(existsSync(join(projectDir, 'tsconfig.json'))).toBe(true);
    expect(existsSync(join(projectDir, 'src/agents'))).toBe(true);
    expect(existsSync(join(projectDir, 'src/tools'))).toBe(true);
    expect(existsSync(join(projectDir, 'tests'))).toBe(true);

    const yaml = await readFile(join(projectDir, 'nerva.yaml'), 'utf-8');
    expect(yaml).toContain('lang: typescript');
  });

  it('throws on unsupported language', async () => {
    // @ts-expect-error — intentionally passing invalid lang to test error path
    await expect(executeNew('bad', 'rust')).rejects.toThrow('Unsupported language');
  });

  it('nerva.yaml has correct default version', async () => {
    await executeNew('versioned', 'python');
    const yaml = await readFile(join(tempDir, 'versioned', 'nerva.yaml'), 'utf-8');
    expect(yaml).toContain('version: 0.1.0');
  });
});

// ---------------------------------------------------------------------------
// nerva generate
// ---------------------------------------------------------------------------

describe('nerva generate', () => {
  beforeEach(async () => {
    // Create a Python project to generate into
    await executeNew('gen-project', 'python');
    process.chdir(join(tempDir, 'gen-project'));
  });

  it('creates an agent file and test file', async () => {
    await executeGenerate('agent', 'greeter');

    const agentPath = join(process.cwd(), 'agents', 'greeter.py');
    const testPath = join(process.cwd(), 'tests', 'test_greeter.py');

    expect(existsSync(agentPath)).toBe(true);
    expect(existsSync(testPath)).toBe(true);

    const agentContent = await readFile(agentPath, 'utf-8');
    expect(agentContent).toContain('class Greeter');
    expect(agentContent).toContain('greeter');
  });

  it('creates a tool file and test file', async () => {
    await executeGenerate('tool', 'web-search');

    const toolPath = join(process.cwd(), 'tools', 'web-search.py');
    expect(existsSync(toolPath)).toBe(true);

    const toolContent = await readFile(toolPath, 'utf-8');
    expect(toolContent).toContain('class WebSearch');
  });

  it('creates a middleware file', async () => {
    await executeGenerate('middleware', 'rate-limiter');

    const mwPath = join(process.cwd(), 'middleware', 'rate-limiter.py');
    expect(existsSync(mwPath)).toBe(true);

    const content = await readFile(mwPath, 'utf-8');
    expect(content).toContain('class RateLimiter');
  });

  it('creates a router file', async () => {
    await executeGenerate('router', 'intent-router');

    const routerPath = join(process.cwd(), 'routers', 'intent-router.py');
    expect(existsSync(routerPath)).toBe(true);
  });

  it('registers agent in nerva.yaml', async () => {
    await executeGenerate('agent', 'greeter');

    const config = await readNervaConfig(process.cwd());
    expect(config.agents).toHaveLength(1);
    expect(config.agents[0].name).toBe('greeter');
    expect(config.agents[0].path).toBe('agents/greeter.py');
  });

  it('does not duplicate entries on repeated generate', async () => {
    await executeGenerate('agent', 'greeter');
    await executeGenerate('agent', 'greeter');

    const config = await readNervaConfig(process.cwd());
    expect(config.agents).toHaveLength(1);
  });

  it('throws on unknown component type', async () => {
    await expect(executeGenerate('database', 'foo')).rejects.toThrow('Unknown component type');
  });

  it('generates TypeScript components correctly', async () => {
    // Create a TS project instead
    process.chdir(tempDir);
    await executeNew('ts-gen', 'typescript');
    process.chdir(join(tempDir, 'ts-gen'));

    await executeGenerate('agent', 'my-agent');

    const agentPath = join(process.cwd(), 'src/agents', 'my-agent.ts');
    expect(existsSync(agentPath)).toBe(true);

    const content = await readFile(agentPath, 'utf-8');
    expect(content).toContain('class MyAgent');

    const testPath = join(process.cwd(), 'tests', 'my-agent.test.ts');
    expect(existsSync(testPath)).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// nerva.yaml parser
// ---------------------------------------------------------------------------

describe('nerva.yaml parser', () => {
  it('parses a valid config', () => {
    const yaml = [
      'name: test-project',
      'version: 1.0.0',
      'lang: python',
      '',
      'agents:',
      '  - name: greeter',
      '    path: agents/greeter.py',
      '    description: Greets users',
      '',
      'tools:',
      '',
      'middleware:',
      '',
      'routers:',
    ].join('\n');

    const config = parseNervaConfig(yaml);
    expect(config.name).toBe('test-project');
    expect(config.version).toBe('1.0.0');
    expect(config.lang).toBe('python');
    expect(config.agents).toHaveLength(1);
    expect(config.agents[0].name).toBe('greeter');
    expect(config.agents[0].description).toBe('Greets users');
    expect(config.tools).toHaveLength(0);
  });

  it('throws on missing name', () => {
    const yaml = 'version: 1.0.0\nlang: python\n';
    expect(() => parseNervaConfig(yaml)).toThrow('missing or invalid required field "name"');
  });

  it('throws on missing version', () => {
    const yaml = 'name: test\nlang: python\n';
    expect(() => parseNervaConfig(yaml)).toThrow('missing or invalid required field "version"');
  });

  it('throws on missing lang', () => {
    const yaml = 'name: test\nversion: 1.0.0\n';
    expect(() => parseNervaConfig(yaml)).toThrow('missing or invalid required field "lang"');
  });

  it('throws on invalid lang', () => {
    const yaml = 'name: test\nversion: 1.0.0\nlang: ruby\n';
    expect(() => parseNervaConfig(yaml)).toThrow('lang must be "python" or "typescript"');
  });

  it('handles empty string input', () => {
    expect(() => parseNervaConfig('')).toThrow('missing or invalid required field');
  });

  it('ignores comment lines', () => {
    const yaml = '# comment\nname: test\nversion: 1.0.0\nlang: python\n';
    const config = parseNervaConfig(yaml);
    expect(config.name).toBe('test');
  });

  it('round-trips through serialize and parse', () => {
    const original: NervaConfig = {
      name: 'roundtrip',
      version: '2.0.0',
      lang: 'typescript',
      agents: [{ name: 'a1', path: 'src/agents/a1.ts' }],
      tools: [{ name: 't1', path: 'src/tools/t1.ts', description: 'A tool' }],
      middleware: [],
      routers: [],
    };

    const serialized = serializeNervaConfig(original);
    const parsed = parseNervaConfig(serialized);

    expect(parsed.name).toBe(original.name);
    expect(parsed.version).toBe(original.version);
    expect(parsed.lang).toBe(original.lang);
    expect(parsed.agents).toHaveLength(1);
    expect(parsed.agents[0].name).toBe('a1');
    expect(parsed.tools).toHaveLength(1);
    expect(parsed.tools[0].description).toBe('A tool');
  });

  it('parses multiple agents', () => {
    const yaml = [
      'name: multi',
      'version: 1.0.0',
      'lang: python',
      'agents:',
      '  - name: a1',
      '    path: agents/a1.py',
      '  - name: a2',
      '    path: agents/a2.py',
      'tools:',
      'middleware:',
      'routers:',
    ].join('\n');

    const config = parseNervaConfig(yaml);
    expect(config.agents).toHaveLength(2);
    expect(config.agents[1].name).toBe('a2');
  });
});

// ---------------------------------------------------------------------------
// Template rendering
// ---------------------------------------------------------------------------

describe('template rendering', () => {
  it('replaces known placeholders', () => {
    const result = renderTemplate('Hello {{name}}, class {{class_name}}', {
      name: 'greeter',
      class_name: 'Greeter',
    });
    expect(result).toBe('Hello greeter, class Greeter');
  });

  it('leaves unknown placeholders intact', () => {
    const result = renderTemplate('{{known}} and {{unknown}}', { known: 'yes' });
    expect(result).toBe('yes and {{unknown}}');
  });

  it('handles empty vars', () => {
    const result = renderTemplate('{{a}} {{b}}', {});
    expect(result).toBe('{{a}} {{b}}');
  });

  it('handles empty template', () => {
    const result = renderTemplate('', { name: 'test' });
    expect(result).toBe('');
  });
});

// ---------------------------------------------------------------------------
// toPascalCase
// ---------------------------------------------------------------------------

describe('toPascalCase', () => {
  it('converts kebab-case', () => {
    expect(toPascalCase('my-agent')).toBe('MyAgent');
  });

  it('converts snake_case', () => {
    expect(toPascalCase('my_agent')).toBe('MyAgent');
  });

  it('handles single word', () => {
    expect(toPascalCase('greeter')).toBe('Greeter');
  });

  it('handles multiple segments', () => {
    expect(toPascalCase('a-b-c-d')).toBe('ABCD');
  });

  it('handles already PascalCase (no separators)', () => {
    expect(toPascalCase('Greeter')).toBe('Greeter');
  });

  it('handles empty string', () => {
    expect(toPascalCase('')).toBe('');
  });
});
