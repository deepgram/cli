import type { APIRoute } from 'astro';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { cliVersion } from '../utils/cliVersion';

export const prerender = true;

// llms-full.txt is authored in src/data/ and served from here so its
// version line comes from pyproject.toml instead of going stale every release.
const TOKEN = '{{CLI_VERSION}}';

export const GET: APIRoute = () => {
  const template = readFileSync(join(process.cwd(), 'src', 'data', 'llms-full.txt'), 'utf-8');
  if (!template.includes(TOKEN)) {
    throw new Error(`src/data/llms-full.txt is missing the ${TOKEN} placeholder`);
  }

  return new Response(template.replaceAll(TOKEN, cliVersion), {
    headers: { 'Content-Type': 'text/plain; charset=utf-8' },
  });
};
