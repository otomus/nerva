/**
 * `nerva generate <type> <name>` command (alias: `nerva g`).
 *
 * Generates a new component (agent, tool, middleware, or router),
 * creates its test file, and registers it in nerva.yaml.
 */

import { Command } from 'commander';
import { mkdir, writeFile } from 'node:fs/promises';
import { join, dirname } from 'node:path';
import {
  readNervaConfig,
  writeNervaConfig,
  pluralizeType,
  type ComponentType,
  type NervaConfig,
  type ComponentEntry,
} from '../config/nerva-yaml.js';
import { loadAndRender, toPascalCase, type TemplateVars } from '../templates/render.js';

/** Valid component types the generate command accepts. */
const VALID_TYPES: ReadonlySet<string> = new Set(['agent', 'tool', 'middleware', 'router']);

/** File extension for each language. */
const EXTENSIONS: Record<string, string> = {
  python: '.py',
  typescript: '.ts',
};

/** Source directory prefix for each language. */
const SOURCE_PREFIX: Record<string, string> = {
  python: '',
  typescript: 'src/',
};

/**
 * Resolves the directory where a component file should be created.
 *
 * @param lang - Project language
 * @param type - Component type
 * @returns Relative directory path from project root
 */
function resolveComponentDir(lang: string, type: ComponentType): string {
  const prefix = SOURCE_PREFIX[lang] ?? '';
  return `${prefix}${pluralizeType(type)}`;
}

/**
 * Resolves the directory where a test file should be created.
 *
 * @returns Relative test directory path from project root
 */
function resolveTestDir(): string {
  return 'tests';
}

/**
 * Builds the template filename for a given component type and language.
 *
 * @param type - Component type
 * @param lang - Project language
 * @returns Template filename (e.g. "agent.py.tmpl")
 */
function templateFileName(type: ComponentType, lang: string): string {
  const ext = EXTENSIONS[lang] ?? '.py';
  return `${type}${ext}.tmpl`;
}

/**
 * Builds the test template filename for a given language.
 *
 * @param lang - Project language
 * @returns Test template filename
 */
function testTemplateFileName(lang: string): string {
  const ext = EXTENSIONS[lang] ?? '.py';
  return `test${ext}.tmpl`;
}

/**
 * Builds template variables from component name and type.
 *
 * @param name - Component name (kebab-case)
 * @param type - Component type
 * @returns Template variable map
 */
function buildTemplateVars(name: string, type: ComponentType): TemplateVars {
  return {
    name,
    class_name: toPascalCase(name),
    type,
    description: `A ${type} named ${name}`,
  };
}

/**
 * Registers a new component in the nerva.yaml configuration.
 *
 * @param config - Current config
 * @param type - Component type to register
 * @param entry - The component entry to add
 * @returns Updated config with the new component
 */
function addComponentToConfig(
  config: NervaConfig,
  type: ComponentType,
  entry: ComponentEntry
): NervaConfig {
  const key = pluralizeType(type) as keyof NervaConfig;
  const existing = config[key] as ComponentEntry[];
  const isDuplicate = existing.some((e) => e.name === entry.name);

  if (isDuplicate) {
    return config;
  }

  return {
    ...config,
    [key]: [...existing, entry],
  };
}

/**
 * Registers the `nerva generate` command (and `g` alias) with the CLI program.
 *
 * @param program - The root commander program
 */
export function registerGenerateCommand(program: Command): void {
  const cmd = program
    .command('generate')
    .alias('g')
    .description('Generate a new component (agent, tool, middleware, router)')
    .argument('<type>', 'Component type: agent, tool, middleware, router')
    .argument('<name>', 'Component name (kebab-case)')
    .action(async (type: string, name: string) => {
      await executeGenerate(type, name);
    });
}

/**
 * Executes the component generation logic.
 *
 * Creates the component file, its test file, and updates nerva.yaml.
 *
 * @param rawType - Component type string from CLI
 * @param name - Component name
 * @throws {Error} If type is invalid or nerva.yaml cannot be read
 */
export async function executeGenerate(rawType: string, name: string): Promise<void> {
  if (!VALID_TYPES.has(rawType)) {
    throw new Error(
      `Unknown component type: "${rawType}". Valid types: ${[...VALID_TYPES].join(', ')}`
    );
  }

  const type = rawType as ComponentType;
  const projectDir = process.cwd();
  const config = await readNervaConfig(projectDir);
  const lang = config.lang;
  const ext = EXTENSIONS[lang];
  const vars = buildTemplateVars(name, type);

  // Generate component file
  const componentDir = resolveComponentDir(lang, type);
  const componentPath = join(componentDir, `${name}${ext}`);
  const componentContent = await loadAndRender(lang, templateFileName(type, lang), vars);
  await ensureDirForFile(join(projectDir, componentPath));
  await writeFile(join(projectDir, componentPath), componentContent, 'utf-8');

  // Generate test file
  const testDir = resolveTestDir();
  const testFileName = lang === 'python' ? `test_${name}${ext}` : `${name}.test${ext}`;
  const testPath = join(testDir, testFileName);
  const testContent = await loadAndRender(lang, testTemplateFileName(lang), vars);
  await ensureDirForFile(join(projectDir, testPath));
  await writeFile(join(projectDir, testPath), testContent, 'utf-8');

  // Update nerva.yaml
  const entry: ComponentEntry = { name, path: componentPath };
  const updatedConfig = addComponentToConfig(config, type, entry);
  await writeNervaConfig(projectDir, updatedConfig);

  console.log(`Generated ${type}: ${componentPath}`);
  console.log(`Generated test: ${testPath}`);
  console.log(`Updated nerva.yaml`);
}

/**
 * Ensures the parent directory of a file path exists.
 *
 * @param filePath - Absolute file path
 */
async function ensureDirForFile(filePath: string): Promise<void> {
  await mkdir(dirname(filePath), { recursive: true });
}
