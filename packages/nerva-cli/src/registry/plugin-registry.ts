/**
 * Community plugin registry client.
 *
 * Searches the npm registry for packages with the `nerva-plugin` keyword
 * and fetches package metadata. Uses `https.get` with no external dependencies.
 */

import { get } from 'node:https';
import type { IncomingMessage } from 'node:http';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/** Summary of a plugin found in the npm registry. */
export interface RegistryPluginInfo {
  name: string;
  description: string;
  version: string;
  downloads: number;
}

/** Raw npm search result shape (subset of fields we use). */
interface NpmSearchResult {
  objects: Array<{
    package: {
      name: string;
      description: string;
      version: string;
    };
    downloads?: {
      monthly?: number;
    };
  }>;
}

/** Raw npm package.json shape from the registry (subset). */
interface NpmPackageInfo {
  name: string;
  description?: string;
  'dist-tags'?: { latest?: string };
  versions?: Record<string, { description?: string }>;
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** Base URL for the npm registry API. */
const NPM_REGISTRY = 'https://registry.npmjs.org';

/** npm search API endpoint. */
const NPM_SEARCH_URL = 'https://registry.npmjs.org/-/v1/search';

/** Request timeout in milliseconds. */
const REQUEST_TIMEOUT_MS = 10_000;

// ---------------------------------------------------------------------------
// HTTP helpers
// ---------------------------------------------------------------------------

/**
 * Performs an HTTPS GET request and returns the response body as a string.
 *
 * @param url - Full URL to fetch
 * @returns Response body
 * @throws {Error} If the request fails or returns a non-2xx status
 */
function httpsGet(url: string): Promise<string> {
  return new Promise((resolve, reject) => {
    const req = get(url, { timeout: REQUEST_TIMEOUT_MS }, (res: IncomingMessage) => {
      const statusCode = res.statusCode ?? 0;

      if (statusCode < 200 || statusCode >= 300) {
        res.resume();
        reject(new Error(`HTTP ${statusCode} from ${url}`));
        return;
      }

      const chunks: Buffer[] = [];
      res.on('data', (chunk: Buffer) => chunks.push(chunk));
      res.on('end', () => resolve(Buffer.concat(chunks).toString('utf-8')));
    });

    req.on('error', (err) => reject(new Error(`Request failed: ${err.message}`)));
    req.on('timeout', () => {
      req.destroy();
      reject(new Error(`Request timed out after ${REQUEST_TIMEOUT_MS}ms`));
    });
  });
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Searches the npm registry for packages matching a query and the `nerva-plugin` keyword.
 *
 * @param query - Free-text search query
 * @returns Array of matching plugin summaries, sorted by relevance
 * @throws {Error} If the npm registry is unreachable or returns invalid data
 */
export async function searchPlugins(query: string): Promise<RegistryPluginInfo[]> {
  const encodedQuery = encodeURIComponent(`${query} keywords:nerva-plugin`);
  const url = `${NPM_SEARCH_URL}?text=${encodedQuery}&size=20`;
  const body = await httpsGet(url);

  const data = parseJson<NpmSearchResult>(body, 'npm search response');

  if (!Array.isArray(data.objects)) {
    return [];
  }

  return data.objects.map((obj) => ({
    name: obj.package.name,
    description: obj.package.description ?? '',
    version: obj.package.version,
    downloads: obj.downloads?.monthly ?? 0,
  }));
}

/**
 * Fetches detailed package info from the npm registry for a specific package.
 *
 * @param name - Exact npm package name
 * @returns Plugin info with name, description, latest version, and monthly downloads
 * @throws {Error} If the package is not found or the registry is unreachable
 */
export async function fetchPluginInfo(name: string): Promise<RegistryPluginInfo> {
  const encodedName = encodeURIComponent(name);
  const url = `${NPM_REGISTRY}/${encodedName}`;
  const body = await httpsGet(url);

  const data = parseJson<NpmPackageInfo>(body, `npm package "${name}"`);

  const latestVersion = data['dist-tags']?.latest ?? 'unknown';
  const description =
    data.description ??
    data.versions?.[latestVersion]?.description ??
    '';

  // Fetch download counts separately (different API)
  const downloads = await fetchMonthlyDownloads(name);

  return {
    name: data.name,
    description,
    version: latestVersion,
    downloads,
  };
}

/**
 * Fetches monthly download count for a package from the npm downloads API.
 *
 * Returns 0 if the request fails — download counts are non-critical metadata.
 *
 * @param name - Exact npm package name
 * @returns Monthly download count
 */
async function fetchMonthlyDownloads(name: string): Promise<number> {
  try {
    const encodedName = encodeURIComponent(name);
    const url = `https://api.npmjs.org/downloads/point/last-month/${encodedName}`;
    const body = await httpsGet(url);
    const data = parseJson<{ downloads?: number }>(body, 'npm downloads');
    return data.downloads ?? 0;
  } catch {
    // Download counts are best-effort — don't fail the whole operation
    return 0;
  }
}

/**
 * Parses a JSON string with a descriptive error on failure.
 *
 * @param raw - Raw JSON string
 * @param context - Human-readable description of what was being parsed
 * @returns Parsed value
 * @throws {Error} If the JSON is invalid
 */
function parseJson<T>(raw: string, context: string): T {
  try {
    return JSON.parse(raw) as T;
  } catch {
    throw new Error(`Invalid JSON in ${context}`);
  }
}
