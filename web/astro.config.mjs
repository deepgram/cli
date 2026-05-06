// @ts-check
import { defineConfig } from 'astro/config';
import sitemap from '@astrojs/sitemap';
import sentry from '@sentry/astro';
import tailwindcss from '@tailwindcss/vite';

// SITE_URL is injected by CI for each environment.
// Falls back to production URL for local builds.
const site = process.env.SITE_URL ?? 'https://cli.deepgram.com';

// https://astro.build/config
export default defineConfig({
  site,
  integrations: [
    sitemap(),
    sentry({
      // Sentry init runs from sentry.client.config.ts so we can gate it
      // on the dg_telemetry localStorage flag for opt-out support.
      autoInstrumentation: { requestHandler: false },
      sourceMapsUploadOptions: {
        project: 'dx-cli',
        org: 'deepgram',
        authToken: process.env.SENTRY_AUTH_TOKEN,
      },
    }),
  ],
  vite: {
    plugins: [tailwindcss()]
  }
});
