/**
 * Build-output check: the landing page must not reference Google font hosts,
 * because a font request before consent leaks the visitor's IP. Fonts are
 * self-hosted via @fontsource. Builds the site into a temp dir (needs
 * node_modules, i.e. npm ci first) and scans the output.
 */
const test = require('node:test');
const assert = require('node:assert');
const { execFileSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');

const web = path.join(__dirname, '..');
const walk = (d) => fs.readdirSync(d, { withFileTypes: true }).flatMap((e) =>
  e.isDirectory() ? walk(path.join(d, e.name)) : [path.join(d, e.name)]);

test('built site has no Google font references and ships Inter locally', { timeout: 120000 }, () => {
  // Inside web/ (not os.tmpdir) so Astro's rename stays on one filesystem.
  const cache = path.join(web, 'node_modules', '.cache');
  fs.mkdirSync(cache, { recursive: true });
  const out = fs.mkdtempSync(path.join(cache, 'build-output-'));
  try {
    execFileSync('npx', ['astro', 'build', '--outDir', out], { cwd: web, stdio: 'pipe' });
    const all = walk(out);
    const text = all.filter((f) => /\.(html|css|js|xml|json|webmanifest)$/.test(f));
    assert.ok(text.some((f) => f.endsWith('index.html')));
    for (const f of text) {
      const body = fs.readFileSync(f, 'utf8');
      assert.ok(!/fonts\.googleapis\.com|fonts\.gstatic\.com/.test(body), `Google font host in ${path.relative(out, f)}`);
    }
    assert.ok(all.some((f) => f.endsWith('.woff2') && /inter/i.test(f)), 'Inter woff2 is bundled');
    assert.ok(all.some((f) => f.endsWith('.woff2') && /fira-code/i.test(f)), 'Fira Code woff2 is bundled');
  } finally {
    fs.rmSync(out, { recursive: true, force: true });
  }
});
