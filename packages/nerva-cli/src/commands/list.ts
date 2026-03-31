/**
 * `nerva list` command.
 *
 * Reads nerva.yaml and prints all registered components in a table.
 */

import { Command } from 'commander';
import { readNervaConfig, type ComponentEntry, type ComponentType } from '../config/nerva-yaml.js';

/** Section labels for display. */
const SECTION_LABELS: Record<ComponentType, string> = {
  agent: 'Agents',
  tool: 'Tools',
  middleware: 'Middleware',
  router: 'Routers',
};

/** All component types in display order. */
const DISPLAY_ORDER: readonly ComponentType[] = ['agent', 'tool', 'middleware', 'router'];

/** Minimum column widths for the table. */
const MIN_NAME_WIDTH = 20;
const MIN_PATH_WIDTH = 40;

/**
 * Formats a list of component entries as padded table rows.
 *
 * @param entries - Components to format
 * @param nameWidth - Column width for the name field
 * @param pathWidth - Column width for the path field
 * @returns Formatted row strings
 */
function formatEntries(
  entries: ComponentEntry[],
  nameWidth: number,
  pathWidth: number
): string[] {
  if (entries.length === 0) {
    return ['  (none)'];
  }

  return entries.map((entry) => {
    const name = entry.name.padEnd(nameWidth);
    const path = entry.path.padEnd(pathWidth);
    const desc = entry.description ?? '';
    return `  ${name}  ${path}  ${desc}`;
  });
}

/**
 * Computes the widest name and path across all entries.
 *
 * @param sections - Map of component type to entries
 * @returns Tuple of [nameWidth, pathWidth]
 */
function computeColumnWidths(
  sections: Record<string, ComponentEntry[]>
): [number, number] {
  let maxName = MIN_NAME_WIDTH;
  let maxPath = MIN_PATH_WIDTH;

  for (const entries of Object.values(sections)) {
    for (const entry of entries) {
      maxName = Math.max(maxName, entry.name.length);
      maxPath = Math.max(maxPath, entry.path.length);
    }
  }

  return [maxName, maxPath];
}

/**
 * Registers the `nerva list` command with the CLI program.
 *
 * @param program - The root commander program
 */
export function registerListCommand(program: Command): void {
  program
    .command('list')
    .alias('ls')
    .description('List all registered components')
    .action(async () => {
      await executeList();
    });
}

/**
 * Executes the list logic: reads nerva.yaml and prints component tables.
 *
 * @throws {Error} If nerva.yaml cannot be read
 */
export async function executeList(): Promise<void> {
  const projectDir = process.cwd();
  const config = await readNervaConfig(projectDir);

  const sections: Record<string, ComponentEntry[]> = {
    agent: config.agents,
    tool: config.tools,
    middleware: config.middleware,
    router: config.routers,
  };

  console.log(`\nProject: ${config.name} (${config.lang})\n`);

  const [nameWidth, pathWidth] = computeColumnWidths(sections);

  for (const type of DISPLAY_ORDER) {
    const label = SECTION_LABELS[type];
    const entries = sections[type];
    console.log(`${label}:`);
    const rows = formatEntries(entries, nameWidth, pathWidth);
    for (const row of rows) {
      console.log(row);
    }
    console.log('');
  }
}
