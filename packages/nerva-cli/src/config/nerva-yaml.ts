/**
 * nerva.yaml configuration parser and validator.
 *
 * Handles reading, writing, and validating the project-level nerva.yaml
 * that declares agents, tools, middleware, and routers.
 */

import { readFile, writeFile } from 'node:fs/promises';
import { join } from 'node:path';

/** A single registered component (agent, tool, middleware, or router). */
export interface ComponentEntry {
  name: string;
  path: string;
  description?: string;
}

/** Supported project languages. */
export type ProjectLang = 'python' | 'typescript';

/** Top-level nerva.yaml schema. */
export interface NervaConfig {
  name: string;
  version: string;
  lang: ProjectLang;
  agents: ComponentEntry[];
  tools: ComponentEntry[];
  middleware: ComponentEntry[];
  routers: ComponentEntry[];
  policy?: Record<string, unknown>;
}

/** Component types that can be registered in nerva.yaml. */
export type ComponentType = 'agent' | 'tool' | 'middleware' | 'router';

/** Keys used in nerva.yaml for each component type's list section. */
export type ConfigListKey = 'agents' | 'tools' | 'middleware' | 'routers';

const VALID_LANGS: ReadonlySet<string> = new Set(['python', 'typescript']);
const REQUIRED_FIELDS: readonly string[] = ['name', 'version', 'lang'];
const COMPONENT_LISTS: readonly ComponentType[] = ['agent', 'tool', 'middleware', 'router'];

/** Maps singular component type to the nerva.yaml section key. */
const TYPE_TO_KEY: Record<ComponentType, ConfigListKey> = {
  agent: 'agents',
  tool: 'tools',
  middleware: 'middleware',
  router: 'routers',
};

/**
 * Returns the config key used in nerva.yaml for a given component type.
 *
 * @param type - The singular component type
 * @returns The yaml section key (e.g. 'agent' -> 'agents', 'middleware' -> 'middleware')
 */
export function pluralizeType(type: ComponentType): ConfigListKey {
  return TYPE_TO_KEY[type];
}

/**
 * Parses raw YAML-like text into a NervaConfig object.
 *
 * Uses a simple line-based parser for the flat nerva.yaml format.
 * This avoids pulling in a full YAML library for a predictable schema.
 *
 * @param content - Raw file content
 * @returns Parsed configuration
 * @throws {Error} If required fields are missing or lang is invalid
 */
export function parseNervaConfig(content: string): NervaConfig {
  const lines = content.split('\n');
  const config: Record<string, unknown> = {};
  const listSections: Record<string, ComponentEntry[]> = {
    agents: [],
    tools: [],
    middleware: [],
    routers: [],
  };

  let currentSection: string | null = null;
  let currentEntry: Partial<ComponentEntry> | null = null;

  for (const rawLine of lines) {
    const line = rawLine.trimEnd();

    if (line.trim() === '' || line.trim().startsWith('#')) {
      continue;
    }

    // Top-level section header (e.g. "agents:")
    const sectionMatch = line.match(/^(\w+):$/);
    if (sectionMatch) {
      flushEntry(currentSection, currentEntry, listSections);
      currentEntry = null;
      const key = sectionMatch[1];
      if (key in listSections) {
        currentSection = key;
      } else {
        currentSection = null;
      }
      continue;
    }

    // Top-level key: value pair (e.g. "name: my-project")
    const kvMatch = line.match(/^(\w+):\s+(.+)$/);
    if (kvMatch && !currentSection) {
      config[kvMatch[1]] = kvMatch[2].trim();
      continue;
    }

    // List item start (e.g. "  - name: my-agent")
    const listItemMatch = line.match(/^\s+-\s+(\w+):\s+(.+)$/);
    if (listItemMatch && currentSection) {
      flushEntry(currentSection, currentEntry, listSections);
      currentEntry = { [listItemMatch[1]]: listItemMatch[2].trim() };
      continue;
    }

    // Continuation of list item (e.g. "    path: agents/my-agent.py")
    const continuationMatch = line.match(/^\s+(\w+):\s+(.+)$/);
    if (continuationMatch && currentSection && currentEntry) {
      currentEntry[continuationMatch[1] as keyof ComponentEntry] = continuationMatch[2].trim();
      continue;
    }
  }

  flushEntry(currentSection, currentEntry, listSections);

  return validateConfig({
    ...config,
    ...listSections,
  });
}

/**
 * Flushes a partially-built component entry into the appropriate list.
 *
 * @param section - Current yaml section name
 * @param entry - Partial entry to flush
 * @param lists - Map of section name to component entries
 */
function flushEntry(
  section: string | null,
  entry: Partial<ComponentEntry> | null,
  lists: Record<string, ComponentEntry[]>
): void {
  if (!section || !entry || !entry.name || !entry.path) {
    return;
  }
  lists[section].push({ name: entry.name, path: entry.path, description: entry.description });
}

/**
 * Validates a raw parsed object against the NervaConfig schema.
 *
 * @param raw - Unvalidated config object
 * @returns Validated NervaConfig
 * @throws {Error} If validation fails
 */
function validateConfig(raw: Record<string, unknown>): NervaConfig {
  for (const field of REQUIRED_FIELDS) {
    if (!raw[field] || typeof raw[field] !== 'string') {
      throw new Error(`nerva.yaml: missing or invalid required field "${field}"`);
    }
  }

  const lang = raw['lang'] as string;
  if (!VALID_LANGS.has(lang)) {
    throw new Error(`nerva.yaml: lang must be "python" or "typescript", got "${lang}"`);
  }

  return {
    name: raw['name'] as string,
    version: raw['version'] as string,
    lang: lang as ProjectLang,
    agents: (raw['agents'] as ComponentEntry[]) ?? [],
    tools: (raw['tools'] as ComponentEntry[]) ?? [],
    middleware: (raw['middleware'] as ComponentEntry[]) ?? [],
    routers: (raw['routers'] as ComponentEntry[]) ?? [],
    policy: (raw['policy'] as Record<string, unknown>) ?? undefined,
  };
}

/**
 * Reads and parses nerva.yaml from the given project directory.
 *
 * @param projectDir - Absolute path to project root
 * @returns Parsed NervaConfig
 * @throws {Error} If the file cannot be read or parsed
 */
export async function readNervaConfig(projectDir: string): Promise<NervaConfig> {
  const filePath = join(projectDir, 'nerva.yaml');
  const content = await readFile(filePath, 'utf-8');
  return parseNervaConfig(content);
}

/**
 * Serializes a NervaConfig to YAML-formatted text.
 *
 * @param config - The configuration to serialize
 * @returns YAML string
 */
export function serializeNervaConfig(config: NervaConfig): string {
  const lines: string[] = [];

  lines.push(`name: ${config.name}`);
  lines.push(`version: ${config.version}`);
  lines.push(`lang: ${config.lang}`);

  for (const type of COMPONENT_LISTS) {
    const plural = pluralizeType(type);
    const entries = config[plural as keyof NervaConfig] as ComponentEntry[];
    lines.push('');
    lines.push(`${plural}:`);
    for (const entry of entries) {
      lines.push(`  - name: ${entry.name}`);
      lines.push(`    path: ${entry.path}`);
      if (entry.description) {
        lines.push(`    description: ${entry.description}`);
      }
    }
  }

  lines.push('');
  return lines.join('\n');
}

/**
 * Writes a NervaConfig back to nerva.yaml in the given directory.
 *
 * @param projectDir - Absolute path to project root
 * @param config - Configuration to write
 */
export async function writeNervaConfig(projectDir: string, config: NervaConfig): Promise<void> {
  const filePath = join(projectDir, 'nerva.yaml');
  const content = serializeNervaConfig(config);
  await writeFile(filePath, content, 'utf-8');
}
