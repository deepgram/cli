import * as Sentry from '@sentry/astro';

Sentry.init({
  dsn: 'https://d7a2aabbf772218e3bbe89266999af70@o206115.ingest.us.sentry.io/4510993603362816',
  environment: import.meta.env.PROD ? 'production' : 'development',
  sendDefaultPii: false,
  tracesSampleRate: 0,
  replaysSessionSampleRate: 0,
  replaysOnErrorSampleRate: 0,
});
