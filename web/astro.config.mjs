// @ts-check
import { defineConfig } from 'astro/config';
import sitemap from '@astrojs/sitemap';
import tailwindcss from '@tailwindcss/vite';

// SITE_URL is injected by CI for each environment.
// Falls back to production URL for local builds.
const site = process.env.SITE_URL ?? 'https://cli.deepgram.com';

// https://astro.build/config
export default defineConfig({
  site,
  integrations: [
    sitemap(),
  ],
  vite: {
    plugins: [tailwindcss()]
  }
});
