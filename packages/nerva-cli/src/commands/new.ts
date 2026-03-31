/**
 * `nerva new <name>` command.
 *
 * Scaffolds a new Nerva project directory with the correct structure
 * for either Python or TypeScript.
 */

import { Command } from 'commander';
import { mkdir, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { serializeNervaConfig, type NervaConfig, type ProjectLang } from '../config/nerva-yaml.js';

/** Directories created for a Python project. */
const PYTHON_DIRS = ['agents', 'tools', 'memory', 'middleware', 'tests'] as const;

/** Directories created for a TypeScript project. */
const TS_DIRS = ['src/agents', 'src/tools', 'src/memory', 'src/middleware', 'tests'] as const;

/** Options passed to the `new` command. */
interface NewCommandOptions {
  lang: ProjectLang;
}

/**
 * Creates a directory and all parents, silently succeeding if it already exists.
 *
 * @param dirPath - Absolute path to create
 */
async function ensureDir(dirPath: string): Promise<void> {
  await mkdir(dirPath, { recursive: true });
}

/**
 * Builds a default NervaConfig for a new project.
 *
 * @param name - Project name
 * @param lang - Project language
 * @returns Default configuration
 */
function buildDefaultConfig(name: string, lang: ProjectLang): NervaConfig {
  return {
    name,
    version: '0.1.0',
    lang,
    agents: [],
    tools: [],
    middleware: [],
    routers: [],
  };
}

/**
 * Scaffolds the Python-specific files in the project directory.
 *
 * @param projectDir - Absolute path to the project root
 * @param name - Project name
 */
async function scaffoldPython(projectDir: string, name: string): Promise<void> {
  for (const dir of PYTHON_DIRS) {
    await ensureDir(join(projectDir, dir));
  }

  await writeFile(
    join(projectDir, 'main.py'),
    `"""${name} — entry point."""\n\nfrom nerva import Runtime\n\n\ndef main() -> None:\n    """Start the Nerva runtime."""\n    runtime = Runtime.from_config("nerva.yaml")\n    runtime.start()\n\n\nif __name__ == "__main__":\n    main()\n`,
    'utf-8'
  );

  await writeFile(
    join(projectDir, 'requirements.txt'),
    'nerva>=0.1.0\n',
    'utf-8'
  );
}

/**
 * Scaffolds the TypeScript-specific files in the project directory.
 *
 * @param projectDir - Absolute path to the project root
 * @param name - Project name
 */
async function scaffoldTypeScript(projectDir: string, name: string): Promise<void> {
  for (const dir of TS_DIRS) {
    await ensureDir(join(projectDir, dir));
  }

  await writeFile(
    join(projectDir, 'src/index.ts'),
    `/**\n * ${name} — entry point.\n */\n\nimport { Runtime } from 'nerva';\n\n/** Start the Nerva runtime. */\nasync function main(): Promise<void> {\n  const runtime = await Runtime.fromConfig('nerva.yaml');\n  await runtime.start();\n}\n\nmain();\n`,
    'utf-8'
  );

  await writeFile(
    join(projectDir, 'package.json'),
    JSON.stringify(
      {
        name,
        version: '0.1.0',
        type: 'module',
        scripts: {
          build: 'tsc',
          start: 'node dist/index.js',
          test: 'vitest run',
        },
        dependencies: {
          nerva: '^0.1.0',
        },
        devDependencies: {
          typescript: '^5.4.0',
          vitest: '^1.6.0',
          '@types/node': '^20.14.0',
        },
      },
      null,
      2
    ) + '\n',
    'utf-8'
  );

  await writeFile(
    join(projectDir, 'tsconfig.json'),
    JSON.stringify(
      {
        compilerOptions: {
          strict: true,
          target: 'ES2022',
          module: 'ES2022',
          moduleResolution: 'node16',
          outDir: 'dist',
          rootDir: 'src',
          declaration: true,
          esModuleInterop: true,
          skipLibCheck: true,
        },
        include: ['src/**/*.ts'],
        exclude: ['node_modules', 'dist'],
      },
      null,
      2
    ) + '\n',
    'utf-8'
  );
}

/**
 * Registers the `nerva new` command with the CLI program.
 *
 * @param program - The root commander program
 */
export function registerNewCommand(program: Command): void {
  program
    .command('new')
    .description('Create a new Nerva project')
    .argument('<name>', 'Project name')
    .option('-l, --lang <language>', 'Project language (python or typescript)', 'python')
    .action(async (name: string, options: NewCommandOptions) => {
      await executeNew(name, options.lang);
    });
}

/**
 * Executes the project scaffolding logic.
 *
 * @param name - Project name (used as directory name)
 * @param lang - Target language
 * @throws {Error} If the language is unsupported
 */
export async function executeNew(name: string, lang: ProjectLang): Promise<void> {
  if (lang !== 'python' && lang !== 'typescript') {
    throw new Error(`Unsupported language: "${lang}". Use "python" or "typescript".`);
  }

  const projectDir = join(process.cwd(), name);
  await ensureDir(projectDir);

  // Write nerva.yaml
  const config = buildDefaultConfig(name, lang);
  await writeFile(join(projectDir, 'nerva.yaml'), serializeNervaConfig(config), 'utf-8');

  // Scaffold language-specific files
  if (lang === 'python') {
    await scaffoldPython(projectDir, name);
  } else {
    await scaffoldTypeScript(projectDir, name);
  }

  console.log(`Created project "${name}" with ${lang} template at ./${name}`);
}
