/**
 * Template rendering utilities.
 *
 * Simple string interpolation for scaffold templates.
 * Uses {{key}} placeholders — no external template engine required.
 */

import { readFile } from 'node:fs/promises';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

/** Variable map for template interpolation. */
export type TemplateVars = Record<string, string>;

const TEMPLATE_PATTERN = /\{\{(\w+)\}\}/g;

const CURRENT_DIR = dirname(fileURLToPath(import.meta.url));

/**
 * Replaces all {{key}} placeholders in a template string with values from vars.
 *
 * Unknown placeholders are left as-is to avoid silent data loss.
 *
 * @param template - Raw template string with {{key}} placeholders
 * @param vars - Key-value pairs to substitute
 * @returns Rendered string
 */
export function renderTemplate(template: string, vars: TemplateVars): string {
  return template.replace(TEMPLATE_PATTERN, (_match, key: string) => {
    return key in vars ? vars[key] : `{{${key}}}`;
  });
}

/**
 * Converts a kebab-case or snake_case name to PascalCase for class names.
 *
 * @param name - Input name (e.g. "my-agent" or "my_agent")
 * @returns PascalCase string (e.g. "MyAgent")
 */
export function toPascalCase(name: string): string {
  return name
    .split(/[-_]/)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1).toLowerCase())
    .join('');
}

/**
 * Loads a template file from the templates directory and renders it.
 *
 * @param lang - Project language ("python" or "typescript")
 * @param templateName - Template filename (e.g. "agent.py.tmpl")
 * @param vars - Variables to substitute into the template
 * @returns Rendered file content
 * @throws {Error} If the template file cannot be read
 */
export async function loadAndRender(
  lang: string,
  templateName: string,
  vars: TemplateVars
): Promise<string> {
  const templatePath = join(CURRENT_DIR, lang, templateName);
  const raw = await readFile(templatePath, 'utf-8');
  return renderTemplate(raw, vars);
}
