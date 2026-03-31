/**
 * `nerva plugin` command group.
 *
 * Manages template plugins that extend `nerva generate` with custom
 * component types. Plugins are stored in `.nerva/plugins/` and expose
 * a `nerva-plugin.json` manifest.
 */

import { Command } from 'commander';
import { mkdir, readFile, readdir, rm, writeFile, cp } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { existsSync } from 'node:fs';
import { execSync } from 'node:child_process';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/** A single template entry inside a plugin manifest. */
export interface PluginTemplate {
  type: string;
  files: string[];
}

/** Schema of the `nerva-plugin.json` manifest file. */
export interface PluginManifest {
  name: string;
  version: string;
  templates: PluginTemplate[];
}

/** Summary of an installed plugin returned by `listPlugins`. */
export interface InstalledPlugin {
  name: string;
  version: string;
  types: string[];
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** Directory name where plugins are stored, relative to project root. */
const PLUGINS_DIR = '.nerva/plugins';

/** Name of the manifest file inside each plugin directory. */
const MANIFEST_FILENAME = 'nerva-plugin.json';

// ---------------------------------------------------------------------------
// Manifest parsing
// ---------------------------------------------------------------------------

/**
 * Parses and validates a raw JSON string as a PluginManifest.
 *
 * @param raw - Raw JSON string from nerva-plugin.json
 * @returns Validated manifest
 * @throws {Error} If the JSON is malformed or required fields are missing
 */
export function parsePluginManifest(raw: string): PluginManifest {
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    throw new Error('nerva-plugin.json contains invalid JSON');
  }

  if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
    throw new Error('nerva-plugin.json must be a JSON object');
  }

  const obj = parsed as Record<string, unknown>;

  if (typeof obj['name'] !== 'string' || obj['name'].trim() === '') {
    throw new Error('nerva-plugin.json: "name" must be a non-empty string');
  }

  if (typeof obj['version'] !== 'string' || obj['version'].trim() === '') {
    throw new Error('nerva-plugin.json: "version" must be a non-empty string');
  }

  if (!Array.isArray(obj['templates'])) {
    throw new Error('nerva-plugin.json: "templates" must be an array');
  }

  const templates = validateTemplates(obj['templates']);

  return {
    name: obj['name'] as string,
    version: obj['version'] as string,
    templates,
  };
}

/**
 * Validates the templates array from a plugin manifest.
 *
 * @param raw - Unvalidated templates array
 * @returns Validated PluginTemplate array
 * @throws {Error} If any entry is malformed
 */
function validateTemplates(raw: unknown[]): PluginTemplate[] {
  return raw.map((entry, index) => {
    if (typeof entry !== 'object' || entry === null || Array.isArray(entry)) {
      throw new Error(`nerva-plugin.json: templates[${index}] must be an object`);
    }

    const item = entry as Record<string, unknown>;

    if (typeof item['type'] !== 'string' || item['type'].trim() === '') {
      throw new Error(`nerva-plugin.json: templates[${index}].type must be a non-empty string`);
    }

    if (!Array.isArray(item['files']) || item['files'].length === 0) {
      throw new Error(`nerva-plugin.json: templates[${index}].files must be a non-empty array`);
    }

    const files = item['files'].map((f, fi) => {
      if (typeof f !== 'string' || f.trim() === '') {
        throw new Error(
          `nerva-plugin.json: templates[${index}].files[${fi}] must be a non-empty string`
        );
      }
      return f as string;
    });

    return { type: item['type'] as string, files };
  });
}

// ---------------------------------------------------------------------------
// Plugin directory helpers
// ---------------------------------------------------------------------------

/**
 * Returns the absolute path to the plugins directory for a project.
 *
 * @param projectDir - Absolute path to the project root
 * @returns Absolute path to `.nerva/plugins/`
 */
export function pluginsDir(projectDir: string): string {
  return join(projectDir, PLUGINS_DIR);
}

/**
 * Returns the absolute path to a specific plugin's directory.
 *
 * @param projectDir - Absolute path to the project root
 * @param name - Plugin name
 * @returns Absolute path to the plugin directory
 */
function pluginPath(projectDir: string, name: string): string {
  return join(pluginsDir(projectDir), name);
}

// ---------------------------------------------------------------------------
// Install
// ---------------------------------------------------------------------------

/**
 * Installs a plugin from a local path or npm package name.
 *
 * For local paths: copies the directory into `.nerva/plugins/<name>`.
 * For npm packages: runs `npm pack` in a temp dir, extracts, and copies.
 *
 * @param source - Local directory path or npm package name
 * @param projectDir - Absolute path to the project root
 * @returns The parsed manifest of the installed plugin
 * @throws {Error} If the source has no valid manifest or the plugin is already installed
 */
export async function installPlugin(
  source: string,
  projectDir: string
): Promise<PluginManifest> {
  const resolvedSource = resolvePluginSource(source);
  const manifest = await readManifestFrom(resolvedSource);

  const dest = pluginPath(projectDir, manifest.name);
  if (existsSync(dest)) {
    throw new Error(`Plugin "${manifest.name}" is already installed. Remove it first to reinstall.`);
  }

  await mkdir(dest, { recursive: true });
  await copyPluginFiles(resolvedSource, dest);

  return manifest;
}

/**
 * Resolves a source argument to an absolute local path.
 *
 * If the source looks like a local path (starts with `.`, `/`, or contains
 * path separators), it is resolved against cwd. Otherwise it is treated as
 * an npm package name and fetched via `npm pack`.
 *
 * @param source - CLI argument for plugin source
 * @returns Absolute path to the plugin directory
 */
function resolvePluginSource(source: string): string {
  const isLocalPath = source.startsWith('.') || source.startsWith('/') || source.includes('/');

  if (isLocalPath) {
    return resolve(source);
  }

  return fetchNpmPackage(source);
}

/**
 * Downloads an npm package using `npm pack` and extracts it to a temp directory.
 *
 * @param packageName - npm package name
 * @returns Absolute path to the extracted package directory
 * @throws {Error} If `npm pack` fails
 */
function fetchNpmPackage(packageName: string): string {
  const tmpDir = join(process.cwd(), '.nerva', '.tmp', `npm-${Date.now()}`);

  try {
    execSync(`mkdir -p "${tmpDir}" && cd "${tmpDir}" && npm pack "${packageName}" --silent 2>/dev/null`, {
      stdio: 'pipe',
    });

    // npm pack creates a tarball — extract it
    const tgzFiles = execSync(`ls "${tmpDir}"/*.tgz 2>/dev/null`, { encoding: 'utf-8' }).trim();
    if (!tgzFiles) {
      throw new Error(`Failed to download npm package "${packageName}"`);
    }

    const tgzPath = tgzFiles.split('\n')[0];
    execSync(`cd "${tmpDir}" && tar xzf "${tgzPath}" --strip-components=1`, { stdio: 'pipe' });

    return tmpDir;
  } catch (err) {
    throw new Error(
      `Failed to install npm package "${packageName}": ${err instanceof Error ? err.message : String(err)}`
    );
  }
}

/**
 * Reads and parses the manifest from a plugin source directory.
 *
 * @param sourceDir - Absolute path to the plugin source
 * @returns Parsed manifest
 * @throws {Error} If the manifest file is missing or invalid
 */
async function readManifestFrom(sourceDir: string): Promise<PluginManifest> {
  const manifestPath = join(sourceDir, MANIFEST_FILENAME);

  if (!existsSync(manifestPath)) {
    throw new Error(`No ${MANIFEST_FILENAME} found in ${sourceDir}`);
  }

  const raw = await readFile(manifestPath, 'utf-8');
  return parsePluginManifest(raw);
}

/**
 * Copies all plugin files from source to destination.
 *
 * @param sourceDir - Source directory
 * @param destDir - Destination directory
 */
async function copyPluginFiles(sourceDir: string, destDir: string): Promise<void> {
  await cp(sourceDir, destDir, { recursive: true });
}

// ---------------------------------------------------------------------------
// List
// ---------------------------------------------------------------------------

/**
 * Lists all installed plugins by reading `.nerva/plugins/`.
 *
 * @param projectDir - Absolute path to the project root
 * @returns Array of installed plugin summaries
 */
export async function listPlugins(projectDir: string): Promise<InstalledPlugin[]> {
  const dir = pluginsDir(projectDir);

  if (!existsSync(dir)) {
    return [];
  }

  const entries = await readdir(dir, { withFileTypes: true });
  const plugins: InstalledPlugin[] = [];

  for (const entry of entries) {
    if (!entry.isDirectory()) {
      continue;
    }

    const manifest = await readManifestSafe(join(dir, entry.name));
    if (manifest) {
      plugins.push({
        name: manifest.name,
        version: manifest.version,
        types: manifest.templates.map((t) => t.type),
      });
    }
  }

  return plugins;
}

/**
 * Reads a manifest from a plugin directory, returning null on failure.
 *
 * @param pluginDir - Absolute path to a plugin directory
 * @returns Parsed manifest or null if unreadable
 */
async function readManifestSafe(pluginDir: string): Promise<PluginManifest | null> {
  const manifestPath = join(pluginDir, MANIFEST_FILENAME);

  if (!existsSync(manifestPath)) {
    return null;
  }

  try {
    const raw = await readFile(manifestPath, 'utf-8');
    return parsePluginManifest(raw);
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------------------
// Remove
// ---------------------------------------------------------------------------

/**
 * Removes an installed plugin by name.
 *
 * @param name - Plugin name to remove
 * @param projectDir - Absolute path to the project root
 * @throws {Error} If the plugin is not installed
 */
export async function removePlugin(name: string, projectDir: string): Promise<void> {
  const dest = pluginPath(projectDir, name);

  if (!existsSync(dest)) {
    throw new Error(`Plugin "${name}" is not installed`);
  }

  await rm(dest, { recursive: true, force: true });
}

// ---------------------------------------------------------------------------
// Command registration
// ---------------------------------------------------------------------------

/**
 * Registers the `nerva plugin` command group with the CLI program.
 *
 * Sub-commands:
 * - `nerva plugin install <source>` — install from npm or local path
 * - `nerva plugin list` — list installed plugins
 * - `nerva plugin remove <name>` — remove an installed plugin
 *
 * @param program - The root commander program
 */
export function registerPluginCommand(program: Command): void {
  const pluginCmd = program
    .command('plugin')
    .description('Manage template plugins for nerva generate');

  pluginCmd
    .command('install')
    .description('Install a plugin from npm or a local path')
    .argument('<source>', 'npm package name or local directory path')
    .action(async (source: string) => {
      const manifest = await installPlugin(source, process.cwd());
      const types = manifest.templates.map((t) => t.type).join(', ');
      console.log(`Installed plugin "${manifest.name}" v${manifest.version}`);
      console.log(`Available types: ${types}`);
    });

  pluginCmd
    .command('list')
    .alias('ls')
    .description('List installed plugins')
    .action(async () => {
      const plugins = await listPlugins(process.cwd());

      if (plugins.length === 0) {
        console.log('No plugins installed.');
        return;
      }

      console.log(`\nInstalled plugins:\n`);
      for (const plugin of plugins) {
        console.log(`  ${plugin.name} (v${plugin.version})`);
        console.log(`    types: ${plugin.types.join(', ')}`);
      }
      console.log('');
    });

  pluginCmd
    .command('remove')
    .description('Remove an installed plugin')
    .argument('<name>', 'Plugin name to remove')
    .action(async (name: string) => {
      await removePlugin(name, process.cwd());
      console.log(`Removed plugin "${name}"`);
    });
}
