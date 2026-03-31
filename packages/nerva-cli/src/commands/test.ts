/**
 * `nerva test` command.
 *
 * Detects the project language from nerva.yaml and runs the appropriate
 * test runner (pytest for Python, vitest for TypeScript).
 */

import { Command } from 'commander';
import { spawn } from 'node:child_process';
import { readNervaConfig, type ProjectLang } from '../config/nerva-yaml.js';

/** Test runner command and arguments for each supported language. */
const RUNNER_CONFIG: Record<ProjectLang, { cmd: string; args: string[] }> = {
  python: { cmd: 'python', args: ['-m', 'pytest'] },
  typescript: { cmd: 'npx', args: ['vitest', 'run'] },
};

/** Options accepted by the test command. */
interface TestCommandOptions {
  coverage: boolean;
}

/**
 * Builds the full argument list for the test runner, including optional coverage flag.
 *
 * @param lang - Project language
 * @param coverage - Whether to enable coverage reporting
 * @returns Tuple of [command, args]
 */
export function buildTestCommand(
  lang: ProjectLang,
  coverage: boolean
): { cmd: string; args: string[] } {
  const config = RUNNER_CONFIG[lang];
  if (!config) {
    throw new Error(`Unsupported language for testing: "${lang}"`);
  }

  const args = [...config.args];
  if (coverage) {
    args.push('--coverage');
  }

  return { cmd: config.cmd, args };
}

/**
 * Detects the project language from nerva.yaml in the given directory.
 *
 * @param projectDir - Absolute path to project root
 * @returns Detected project language
 * @throws {Error} If nerva.yaml is missing or invalid
 */
export async function detectLanguage(projectDir: string): Promise<ProjectLang> {
  const config = await readNervaConfig(projectDir);
  return config.lang;
}

/**
 * Runs the test command as a child process and returns its exit code.
 *
 * @param projectDir - Working directory for the test runner
 * @param cmd - Command to execute
 * @param args - Arguments to pass
 * @returns Exit code from the test runner (0 = success)
 */
export function runTestProcess(
  projectDir: string,
  cmd: string,
  args: string[]
): Promise<number> {
  return new Promise((resolve) => {
    const child = spawn(cmd, args, {
      cwd: projectDir,
      stdio: 'inherit',
      shell: true,
    });

    child.on('close', (code) => {
      resolve(code ?? 1);
    });

    child.on('error', (err) => {
      console.error(`[nerva test] Failed to start test runner: ${err.message}`);
      resolve(1);
    });
  });
}

/**
 * Executes the test command: detects language, runs the appropriate test runner,
 * and prints a trace summary.
 *
 * @param projectDir - Absolute path to project root
 * @param coverage - Whether to enable coverage
 * @returns Exit code from the test runner
 */
export async function executeTest(
  projectDir: string,
  coverage: boolean
): Promise<number> {
  const lang = await detectLanguage(projectDir);
  const { cmd, args } = buildTestCommand(lang, coverage);

  console.log(`[nerva test] Detected language: ${lang}`);
  console.log(`[nerva test] Running: ${cmd} ${args.join(' ')}`);
  console.log('');

  const startTime = Date.now();
  const exitCode = await runTestProcess(projectDir, cmd, args);
  const elapsed = ((Date.now() - startTime) / 1000).toFixed(2);

  console.log('');
  console.log('  Nerva Test Summary');
  console.log('  ==================');
  console.log(`  Language: ${lang}`);
  console.log(`  Runner:   ${cmd} ${args.join(' ')}`);
  console.log(`  Duration: ${elapsed}s`);
  console.log(`  Exit:     ${exitCode === 0 ? 'PASS' : 'FAIL'} (code ${exitCode})`);
  console.log('');

  return exitCode;
}

/**
 * Registers the `nerva test` command with the CLI program.
 *
 * @param program - The root commander program
 */
export function registerTestCommand(program: Command): void {
  program
    .command('test')
    .description('Run tests using the language-appropriate test runner')
    .option('--coverage', 'Enable coverage reporting', false)
    .action(async (options: TestCommandOptions) => {
      const projectDir = process.cwd();
      const exitCode = await executeTest(projectDir, options.coverage);
      process.exitCode = exitCode;
    });
}
