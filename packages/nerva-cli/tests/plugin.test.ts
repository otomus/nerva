/**
 * Tests for the plugin management system.
 *
 * Covers manifest parsing, install/list/remove lifecycle,
 * directory structure, and edge cases for malformed input.
 */

import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { mkdtemp, rm, readFile, writeFile, mkdir, readdir } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { existsSync } from 'node:fs';
import {
  parsePluginManifest,
  installPlugin,
  listPlugins,
  removePlugin,
  pluginsDir,
  type PluginManifest,
} from '../src/commands/plugin.js';

let tempDir: string;
let originalCwd: string;

beforeEach(async () => {
  tempDir = await mkdtemp(join(tmpdir(), 'nerva-plugin-test-'));
  originalCwd = process.cwd();
  process.chdir(tempDir);
});

afterEach(async () => {
  process.chdir(originalCwd);
  await rm(tempDir, { recursive: true, force: true });
});

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Creates a valid plugin source directory with a manifest and template files. */
async function createPluginSource(
  dir: string,
  manifest: PluginManifest
): Promise<string> {
  const pluginDir = join(dir, `source-${manifest.name}`);
  await mkdir(pluginDir, { recursive: true });
  await writeFile(
    join(pluginDir, 'nerva-plugin.json'),
    JSON.stringify(manifest, null, 2),
    'utf-8'
  );

  // Create template files referenced in the manifest
  for (const tmpl of manifest.templates) {
    for (const file of tmpl.files) {
      await writeFile(
        join(pluginDir, file),
        `// Template: {{name}} ({{type}})`,
        'utf-8'
      );
    }
  }

  return pluginDir;
}

/** Returns a minimal valid manifest for testing. */
function validManifest(overrides: Partial<PluginManifest> = {}): PluginManifest {
  return {
    name: 'test-plugin',
    version: '1.0.0',
    templates: [
      { type: 'workflow', files: ['workflow.py.tmpl'] },
    ],
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// Manifest parsing
// ---------------------------------------------------------------------------

describe('parsePluginManifest', () => {
  it('parses a valid manifest', () => {
    const raw = JSON.stringify(validManifest());
    const result = parsePluginManifest(raw);

    expect(result.name).toBe('test-plugin');
    expect(result.version).toBe('1.0.0');
    expect(result.templates).toHaveLength(1);
    expect(result.templates[0].type).toBe('workflow');
    expect(result.templates[0].files).toEqual(['workflow.py.tmpl']);
  });

  it('parses manifest with multiple templates', () => {
    const manifest = validManifest({
      templates: [
        { type: 'workflow', files: ['workflow.py.tmpl', 'test_workflow.py.tmpl'] },
        { type: 'pipeline', files: ['pipeline.py.tmpl'] },
      ],
    });

    const result = parsePluginManifest(JSON.stringify(manifest));
    expect(result.templates).toHaveLength(2);
    expect(result.templates[1].type).toBe('pipeline');
  });

  it('throws on invalid JSON', () => {
    expect(() => parsePluginManifest('{')).toThrow('invalid JSON');
  });

  it('throws on empty string', () => {
    expect(() => parsePluginManifest('')).toThrow('invalid JSON');
  });

  it('throws on JSON array instead of object', () => {
    expect(() => parsePluginManifest('[]')).toThrow('must be a JSON object');
  });

  it('throws on JSON null', () => {
    expect(() => parsePluginManifest('null')).toThrow('must be a JSON object');
  });

  it('throws on missing name', () => {
    const raw = JSON.stringify({ version: '1.0.0', templates: [] });
    expect(() => parsePluginManifest(raw)).toThrow('"name" must be a non-empty string');
  });

  it('throws on empty name', () => {
    const raw = JSON.stringify({ name: '', version: '1.0.0', templates: [] });
    expect(() => parsePluginManifest(raw)).toThrow('"name" must be a non-empty string');
  });

  it('throws on whitespace-only name', () => {
    const raw = JSON.stringify({ name: '   ', version: '1.0.0', templates: [] });
    expect(() => parsePluginManifest(raw)).toThrow('"name" must be a non-empty string');
  });

  it('throws on numeric name', () => {
    const raw = JSON.stringify({ name: 42, version: '1.0.0', templates: [] });
    expect(() => parsePluginManifest(raw)).toThrow('"name" must be a non-empty string');
  });

  it('throws on missing version', () => {
    const raw = JSON.stringify({ name: 'test', templates: [] });
    expect(() => parsePluginManifest(raw)).toThrow('"version" must be a non-empty string');
  });

  it('throws on empty version', () => {
    const raw = JSON.stringify({ name: 'test', version: '', templates: [] });
    expect(() => parsePluginManifest(raw)).toThrow('"version" must be a non-empty string');
  });

  it('throws on missing templates', () => {
    const raw = JSON.stringify({ name: 'test', version: '1.0.0' });
    expect(() => parsePluginManifest(raw)).toThrow('"templates" must be an array');
  });

  it('throws on templates as string', () => {
    const raw = JSON.stringify({ name: 'test', version: '1.0.0', templates: 'bad' });
    expect(() => parsePluginManifest(raw)).toThrow('"templates" must be an array');
  });

  it('throws on template entry that is not an object', () => {
    const raw = JSON.stringify({ name: 'test', version: '1.0.0', templates: ['bad'] });
    expect(() => parsePluginManifest(raw)).toThrow('templates[0] must be an object');
  });

  it('throws on template entry that is null', () => {
    const raw = JSON.stringify({ name: 'test', version: '1.0.0', templates: [null] });
    expect(() => parsePluginManifest(raw)).toThrow('templates[0] must be an object');
  });

  it('throws on template with missing type', () => {
    const raw = JSON.stringify({
      name: 'test',
      version: '1.0.0',
      templates: [{ files: ['a.tmpl'] }],
    });
    expect(() => parsePluginManifest(raw)).toThrow('templates[0].type must be a non-empty string');
  });

  it('throws on template with empty type', () => {
    const raw = JSON.stringify({
      name: 'test',
      version: '1.0.0',
      templates: [{ type: '', files: ['a.tmpl'] }],
    });
    expect(() => parsePluginManifest(raw)).toThrow('templates[0].type must be a non-empty string');
  });

  it('throws on template with missing files', () => {
    const raw = JSON.stringify({
      name: 'test',
      version: '1.0.0',
      templates: [{ type: 'workflow' }],
    });
    expect(() => parsePluginManifest(raw)).toThrow('templates[0].files must be a non-empty array');
  });

  it('throws on template with empty files array', () => {
    const raw = JSON.stringify({
      name: 'test',
      version: '1.0.0',
      templates: [{ type: 'workflow', files: [] }],
    });
    expect(() => parsePluginManifest(raw)).toThrow('templates[0].files must be a non-empty array');
  });

  it('throws on template with non-string file entry', () => {
    const raw = JSON.stringify({
      name: 'test',
      version: '1.0.0',
      templates: [{ type: 'workflow', files: [123] }],
    });
    expect(() => parsePluginManifest(raw)).toThrow('templates[0].files[0] must be a non-empty string');
  });

  it('throws on template with empty string file entry', () => {
    const raw = JSON.stringify({
      name: 'test',
      version: '1.0.0',
      templates: [{ type: 'workflow', files: ['valid.tmpl', ''] }],
    });
    expect(() => parsePluginManifest(raw)).toThrow('templates[0].files[1] must be a non-empty string');
  });

  it('preserves extra fields in templates without error', () => {
    const raw = JSON.stringify({
      name: 'test',
      version: '1.0.0',
      templates: [{ type: 'workflow', files: ['a.tmpl'], description: 'extra field' }],
    });
    const result = parsePluginManifest(raw);
    expect(result.templates[0].type).toBe('workflow');
  });

  it('handles unicode in name and version', () => {
    const raw = JSON.stringify({
      name: 'nerva-plugin-',
      version: '1.0.0-beta',
      templates: [{ type: 'custom', files: ['c.tmpl'] }],
    });
    const result = parsePluginManifest(raw);
    expect(result.name).toBe('nerva-plugin-');
  });
});

// ---------------------------------------------------------------------------
// Install
// ---------------------------------------------------------------------------

describe('installPlugin', () => {
  it('creates correct directory structure in .nerva/plugins/', async () => {
    const manifest = validManifest();
    const sourceDir = await createPluginSource(tempDir, manifest);

    await installPlugin(sourceDir, tempDir);

    const installed = join(pluginsDir(tempDir), 'test-plugin');
    expect(existsSync(installed)).toBe(true);
    expect(existsSync(join(installed, 'nerva-plugin.json'))).toBe(true);
    expect(existsSync(join(installed, 'workflow.py.tmpl'))).toBe(true);
  });

  it('returns the parsed manifest', async () => {
    const manifest = validManifest({ name: 'returned-manifest' });
    const sourceDir = await createPluginSource(tempDir, manifest);

    const result = await installPlugin(sourceDir, tempDir);

    expect(result.name).toBe('returned-manifest');
    expect(result.version).toBe('1.0.0');
    expect(result.templates).toHaveLength(1);
  });

  it('copies template files into the plugin directory', async () => {
    const manifest = validManifest({
      templates: [
        { type: 'workflow', files: ['workflow.py.tmpl', 'test_workflow.py.tmpl'] },
      ],
    });
    const sourceDir = await createPluginSource(tempDir, manifest);

    await installPlugin(sourceDir, tempDir);

    const installed = join(pluginsDir(tempDir), 'test-plugin');
    const content = await readFile(join(installed, 'workflow.py.tmpl'), 'utf-8');
    expect(content).toContain('{{name}}');
  });

  it('throws when plugin is already installed', async () => {
    const manifest = validManifest();
    const sourceDir = await createPluginSource(tempDir, manifest);

    await installPlugin(sourceDir, tempDir);

    await expect(installPlugin(sourceDir, tempDir)).rejects.toThrow('already installed');
  });

  it('throws when source has no manifest', async () => {
    const emptyDir = join(tempDir, 'empty-source');
    await mkdir(emptyDir, { recursive: true });

    await expect(installPlugin(emptyDir, tempDir)).rejects.toThrow('No nerva-plugin.json');
  });

  it('throws when manifest in source is invalid', async () => {
    const badDir = join(tempDir, 'bad-source');
    await mkdir(badDir, { recursive: true });
    await writeFile(join(badDir, 'nerva-plugin.json'), '{invalid json', 'utf-8');

    await expect(installPlugin(badDir, tempDir)).rejects.toThrow('invalid JSON');
  });

  it('creates .nerva/plugins/ directory if it does not exist', async () => {
    const manifest = validManifest();
    const sourceDir = await createPluginSource(tempDir, manifest);

    expect(existsSync(pluginsDir(tempDir))).toBe(false);

    await installPlugin(sourceDir, tempDir);

    expect(existsSync(pluginsDir(tempDir))).toBe(true);
  });

  it('handles plugin names with special characters', async () => {
    const manifest = validManifest({ name: '@scope/my-plugin' });
    const sourceDir = await createPluginSource(tempDir, manifest);

    const result = await installPlugin(sourceDir, tempDir);
    expect(result.name).toBe('@scope/my-plugin');
  });
});

// ---------------------------------------------------------------------------
// List
// ---------------------------------------------------------------------------

describe('listPlugins', () => {
  it('returns empty array when no plugins are installed', async () => {
    const result = await listPlugins(tempDir);
    expect(result).toEqual([]);
  });

  it('returns empty array when .nerva/plugins/ does not exist', async () => {
    const result = await listPlugins(tempDir);
    expect(result).toEqual([]);
  });

  it('lists a single installed plugin', async () => {
    const manifest = validManifest();
    const sourceDir = await createPluginSource(tempDir, manifest);
    await installPlugin(sourceDir, tempDir);

    const result = await listPlugins(tempDir);

    expect(result).toHaveLength(1);
    expect(result[0].name).toBe('test-plugin');
    expect(result[0].version).toBe('1.0.0');
    expect(result[0].types).toEqual(['workflow']);
  });

  it('lists multiple installed plugins', async () => {
    const manifest1 = validManifest({ name: 'plugin-one' });
    const manifest2 = validManifest({
      name: 'plugin-two',
      version: '2.0.0',
      templates: [{ type: 'pipeline', files: ['pipeline.py.tmpl'] }],
    });

    const source1 = await createPluginSource(tempDir, manifest1);
    const source2 = await createPluginSource(tempDir, manifest2);

    await installPlugin(source1, tempDir);
    await installPlugin(source2, tempDir);

    const result = await listPlugins(tempDir);

    expect(result).toHaveLength(2);
    const names = result.map((p) => p.name).sort();
    expect(names).toEqual(['plugin-one', 'plugin-two']);
  });

  it('includes all template types in the types array', async () => {
    const manifest = validManifest({
      templates: [
        { type: 'workflow', files: ['workflow.py.tmpl'] },
        { type: 'pipeline', files: ['pipeline.py.tmpl'] },
        { type: 'handler', files: ['handler.py.tmpl'] },
      ],
    });
    const sourceDir = await createPluginSource(tempDir, manifest);
    await installPlugin(sourceDir, tempDir);

    const result = await listPlugins(tempDir);

    expect(result[0].types).toEqual(['workflow', 'pipeline', 'handler']);
  });

  it('skips directories without a valid manifest', async () => {
    // Install a valid plugin
    const manifest = validManifest();
    const sourceDir = await createPluginSource(tempDir, manifest);
    await installPlugin(sourceDir, tempDir);

    // Create a rogue directory with no manifest
    const rogueDir = join(pluginsDir(tempDir), 'broken-plugin');
    await mkdir(rogueDir, { recursive: true });
    await writeFile(join(rogueDir, 'random.txt'), 'not a plugin', 'utf-8');

    const result = await listPlugins(tempDir);

    expect(result).toHaveLength(1);
    expect(result[0].name).toBe('test-plugin');
  });

  it('skips directories with malformed manifest JSON', async () => {
    const manifest = validManifest();
    const sourceDir = await createPluginSource(tempDir, manifest);
    await installPlugin(sourceDir, tempDir);

    // Create a plugin dir with bad JSON
    const badDir = join(pluginsDir(tempDir), 'bad-json');
    await mkdir(badDir, { recursive: true });
    await writeFile(join(badDir, 'nerva-plugin.json'), 'not json at all', 'utf-8');

    const result = await listPlugins(tempDir);

    expect(result).toHaveLength(1);
  });

  it('ignores files (non-directories) in the plugins folder', async () => {
    await mkdir(pluginsDir(tempDir), { recursive: true });
    await writeFile(join(pluginsDir(tempDir), 'stray-file.txt'), 'oops', 'utf-8');

    const result = await listPlugins(tempDir);
    expect(result).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// Remove
// ---------------------------------------------------------------------------

describe('removePlugin', () => {
  it('removes an installed plugin', async () => {
    const manifest = validManifest();
    const sourceDir = await createPluginSource(tempDir, manifest);
    await installPlugin(sourceDir, tempDir);

    const pluginDir = join(pluginsDir(tempDir), 'test-plugin');
    expect(existsSync(pluginDir)).toBe(true);

    await removePlugin('test-plugin', tempDir);

    expect(existsSync(pluginDir)).toBe(false);
  });

  it('throws when plugin is not installed', async () => {
    await expect(removePlugin('nonexistent', tempDir)).rejects.toThrow('not installed');
  });

  it('allows reinstall after remove', async () => {
    const manifest = validManifest();
    const sourceDir = await createPluginSource(tempDir, manifest);

    await installPlugin(sourceDir, tempDir);
    await removePlugin('test-plugin', tempDir);
    const result = await installPlugin(sourceDir, tempDir);

    expect(result.name).toBe('test-plugin');
    expect(existsSync(join(pluginsDir(tempDir), 'test-plugin'))).toBe(true);
  });

  it('removes all files inside the plugin directory', async () => {
    const manifest = validManifest({
      templates: [
        { type: 'workflow', files: ['a.tmpl', 'b.tmpl'] },
      ],
    });
    const sourceDir = await createPluginSource(tempDir, manifest);
    await installPlugin(sourceDir, tempDir);

    await removePlugin('test-plugin', tempDir);

    const pluginDir = join(pluginsDir(tempDir), 'test-plugin');
    expect(existsSync(pluginDir)).toBe(false);
  });

  it('does not affect other installed plugins', async () => {
    const manifest1 = validManifest({ name: 'keep-me' });
    const manifest2 = validManifest({ name: 'remove-me' });

    const source1 = await createPluginSource(tempDir, manifest1);
    const source2 = await createPluginSource(tempDir, manifest2);

    await installPlugin(source1, tempDir);
    await installPlugin(source2, tempDir);

    await removePlugin('remove-me', tempDir);

    const remaining = await listPlugins(tempDir);
    expect(remaining).toHaveLength(1);
    expect(remaining[0].name).toBe('keep-me');
  });
});

// ---------------------------------------------------------------------------
// pluginsDir
// ---------------------------------------------------------------------------

describe('pluginsDir', () => {
  it('returns the correct path', () => {
    const result = pluginsDir('/my/project');
    expect(result).toBe('/my/project/.nerva/plugins');
  });

  it('handles trailing slashes in project dir', () => {
    const result = pluginsDir('/my/project/');
    expect(result).toContain('.nerva/plugins');
  });
});
