#!/usr/bin/env node
/**
 * Nerva CLI entry point.
 *
 * Registers all sub-commands and runs the CLI program.
 */

import { Command } from 'commander';
import { registerNewCommand } from './commands/new.js';
import { registerGenerateCommand } from './commands/generate.js';
import { registerListCommand } from './commands/list.js';
import { registerDevCommand } from './commands/dev.js';
import { registerTestCommand } from './commands/test.js';
import { registerPluginCommand } from './commands/plugin.js';

const VERSION = '0.1.0';

/**
 * Creates and configures the root CLI program with all commands.
 *
 * @returns Configured Commander program
 */
export function createProgram(): Command {
  const program = new Command();

  program
    .name('nerva')
    .description('CLI for the Nerva agent runtime')
    .version(VERSION);

  registerNewCommand(program);
  registerGenerateCommand(program);
  registerListCommand(program);
  registerDevCommand(program);
  registerTestCommand(program);
  registerPluginCommand(program);

  return program;
}

const program = createProgram();
program.parse(process.argv);
