/**
 * Ambient type declaration for js-yaml.
 *
 * Provides minimal typing for the `load` function used by the
 * YAML policy engine. Install `@types/js-yaml` for full typings.
 */
declare module "js-yaml" {
  /**
   * Parse a YAML string into a JavaScript value.
   *
   * @param input - YAML string to parse.
   * @returns Parsed JavaScript value (object, array, string, number, etc.).
   */
  export function load(input: string): unknown;
}
