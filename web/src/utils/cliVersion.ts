import { readFileSync } from 'node:fs';
import { join } from 'node:path';

// Read the CLI version from the root pyproject.toml at build time, so pages
// never carry a hand-edited version. process.cwd() is web/ during `npm run build`.
const toml = readFileSync(join(process.cwd(), '..', 'pyproject.toml'), 'utf-8');

const match = toml.match(/^version = "([^"]+)"/m);
if (!match) {
  throw new Error('Could not read the CLI version from the root pyproject.toml');
}

export const cliVersion = match[1];
