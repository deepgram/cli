import type { APIRoute } from 'astro';
import { Resvg } from '@resvg/resvg-js';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

export const prerender = true;

export const GET: APIRoute = () => {
  const svgPath = join(process.cwd(), 'public', 'og-image.svg');
  const svg = readFileSync(svgPath, 'utf-8');

  const resvg = new Resvg(svg, {
    fitTo: { mode: 'width', value: 1200 },
  });

  const png = resvg.render().asPng();

  return new Response(png, {
    headers: {
      'Content-Type': 'image/png',
      'Cache-Control': 'public, max-age=31536000, immutable',
    },
  });
};
