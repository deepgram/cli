/**
 * Consent gate for src/consent/ketch.js + src/consent/trackers.js.
 * Runs the real scripts in node:vm with a fake Ketch (same approach as
 * deepgram-docs tools/tests/consent-gate.test.cjs) and checks Heap and GA4
 * start only on targeted_advertising, never without Ketch, and stop on withdrawal.
 * Run: npm test (from web/)
 */
const test = require('node:test');
const assert = require('node:assert');
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const web = path.join(__dirname, '..');
const read = (p) => fs.readFileSync(path.join(web, p), 'utf8');
const ketchJs = read('src/consent/ketch.js');
const trackersJs = read('src/consent/trackers.js');
const GA_ID = 'G-TYPC1TBCKT';

function browser({ ketchBoots = true, loadKetch = true } = {}) {
  const timers = [];
  const appended = [];
  const consentLs = [];
  let dlAtGaScript = null; // dataLayer contents at the moment gtag.js is appended
  const head = {
    appendChild: (el) => {
      if (el.id === 'ga4-script') dlAtGaScript = JSON.parse(JSON.stringify(Array.from(window.dataLayer || []).map((a) => Array.from(a))));
      appended.push(el);
      return el;
    },
  };
  const document = {
    head,
    createElement: (tag) => ({ tagName: String(tag).toUpperCase() }),
    getElementById: (id) => appended.find((el) => el.id === id) || null,
    getElementsByTagName: (tag) => (tag === 'head' ? [head] : tag === 'script' ? [{ parentNode: head }] : []),
  };
  // Fake scheduling: record timeouts, intervals, idle callbacks and load-event listeners
  // so settle() can fire them all, whatever the delay.
  const intervals = new Map();
  const listeners = [];
  let nextId = 1;
  const addListener = (type, fn) => { if (typeof fn === 'function') listeners.push({ type, fn }); };
  const removeListener = (type, fn) => {
    const i = listeners.findIndex((l) => l.type === type && l.fn === fn);
    if (i >= 0) listeners.splice(i, 1);
  };
  document.addEventListener = addListener;
  document.removeEventListener = removeListener;
  const window = {
    document,
    setTimeout: (fn) => (timers.push(fn), nextId++),
    setInterval: (fn) => { const id = nextId++; intervals.set(id, fn); return id; },
    clearInterval: (id) => intervals.delete(id),
    requestIdleCallback: (fn) => (timers.push(() => fn({ didTimeout: true, timeRemaining: () => 0 })), nextId++),
    addEventListener: addListener,
    removeEventListener: removeListener,
  };
  window.window = window;
  const ctx = vm.createContext(window);
  const run = (src, name) => vm.runInContext(src, ctx, { filename: name });
  if (loadKetch) run(ketchJs, 'ketch.js');
  if (loadKetch && ketchBoots) {
    const queued = (window.semaphore || []).slice();
    window.ketch = (m, n, fn) => { if (m === 'on' && n === 'consent') consentLs.push(fn); };
    queued.forEach((a) => window.ketch.apply(null, Array.from(a)));
  }
  run(trackersJs, 'trackers.js');
  const ids = () => appended.map((el) => el.id).filter(Boolean);
  // What the page would fetch: GA's src, or Heap's inline loader once it runs.
  const urls = () => appended.map((el) => el.src || '').filter(Boolean);
  // Alternate until nothing is left: each pass fires pending timeouts and idle callbacks,
  // ticks every interval, dispatches load/DOMContentLoaded to listeners, then yields to the
  // event loop so promise-scheduled work gets queued for the next pass. Stops after a few quiet
  // passes (no timers, no intervals), capped at 1000 passes so a poll that gives up late is caught.
  const settle = async () => {
    let quiet = 0;
    for (let pass = 0; pass < 1000 && quiet < 3; pass++) {
      const due = timers.splice(0);
      const events = listeners.filter((l) => l.type === 'load' || l.type === 'DOMContentLoaded');
      events.forEach((l) => removeListener(l.type, l.fn));
      due.forEach((fn) => fn());
      Array.from(intervals.values()).forEach((fn) => fn());
      events.forEach((l) => l.fn({ type: l.type }));
      await new Promise((r) => setImmediate(r));
      quiet = due.length || events.length || intervals.size ? 0 : quiet + 1;
    }
  };
  // JSON round-trip: objects built inside the vm have a different realm's prototypes.
  const dataLayer = () => JSON.parse(JSON.stringify(Array.from(window.dataLayer || []).map((a) => Array.from(a))));
  return { window, appended, ids, urls, settle, dataLayer, dlAtGaScript: () => dlAtGaScript, fire: (purposes) => consentLs.forEach((fn) => fn({ purposes })) };
}

test('before consent: Heap and GA4 do not load', async () => {
  const b = browser();
  await b.settle();
  assert.deepStrictEqual(b.ids(), []);
  assert.strictEqual(b.window.dataLayer, undefined);
  assert.strictEqual(b.window.heap, undefined);
});

test('Ketch unavailable (blocked or offline): Heap and GA4 do not load, even after timers fire', async () => {
  const b = browser({ ketchBoots: false });
  assert.deepStrictEqual(b.ids(), []);
  await b.settle();
  assert.deepStrictEqual(b.ids(), []);
  assert.strictEqual(b.window.dataLayer, undefined);
});

test('consent helpers missing (ketch.js did not run): Heap and GA4 do not load', async () => {
  const b = browser({ loadKetch: false });
  assert.strictEqual(b.window.dgOnConsent, undefined);
  await b.settle();
  assert.deepStrictEqual(b.ids(), []);
  assert.strictEqual(b.window.dataLayer, undefined);
});

test('after rejection: Heap and GA4 do not load', async () => {
  const b = browser();
  b.fire({ essential_services: true });
  await b.settle();
  assert.deepStrictEqual(b.ids(), []);
});

test('analytics alone does not start Heap or GA4', async () => {
  const b = browser();
  b.fire({ essential_services: true, analytics: true });
  await b.settle();
  assert.deepStrictEqual(b.ids(), []);
});

test('grant targeted_advertising: Heap and GA4 both load without a reload', () => {
  const b = browser();
  b.fire({ essential_services: true });
  b.fire({ essential_services: true, targeted_advertising: true });
  b.fire({ essential_services: true, targeted_advertising: true, analytics: true });
  assert.deepStrictEqual(b.ids().sort(), ['ga4-script', 'heap-script']);
  assert.ok(b.urls().some((u) => u === `https://www.googletagmanager.com/gtag/js?id=${GA_ID}`));
  const cfg = Array.from(b.window.dataLayer).map((a) => Array.from(a));
  assert.ok(cfg.some((a) => a[0] === 'config' && a[1] === GA_ID));
});

test('withdrawal: GA4 is disabled and Heap stopTracking is called', () => {
  const b = browser();
  b.fire({ targeted_advertising: true });
  let stopped = 0;
  b.window.heap = { stopTracking: () => stopped++ };
  b.fire({ essential_services: true });
  assert.strictEqual(stopped, 1);
  assert.strictEqual(b.window[`ga-disable-${GA_ID}`], true);
});

const DENIED = { ad_storage: 'denied', ad_user_data: 'denied', ad_personalization: 'denied', analytics_storage: 'denied' };
const GRANTED = { ad_storage: 'granted', ad_user_data: 'granted', ad_personalization: 'granted', analytics_storage: 'granted' };
const consentCalls = (dl) => dl.filter((a) => a[0] === 'consent').map((a) => [a[1], a[2]]);

test('Consent Mode: denied default is queued before config and before gtag.js is appended', () => {
  const b = browser();
  b.fire({ targeted_advertising: true });
  const dl = b.dataLayer();
  const iDefault = dl.findIndex((a) => a[0] === 'consent' && a[1] === 'default');
  const iConfig = dl.findIndex((a) => a[0] === 'config' && a[1] === GA_ID);
  assert.ok(iDefault >= 0, 'consent default is set');
  assert.ok(iConfig > iDefault, 'config comes after consent default');
  assert.deepStrictEqual(dl[iDefault][2], DENIED);
  const atScript = b.dlAtGaScript();
  assert.ok(atScript, 'gtag.js was appended');
  assert.ok(atScript.some((a) => a[0] === 'consent' && a[1] === 'default'), 'default was already in the dataLayer when gtag.js was appended');
});

test('Consent Mode: grant sends update granted, withdrawal sends update denied', () => {
  const b = browser();
  b.fire({ targeted_advertising: true });
  assert.deepStrictEqual(consentCalls(b.dataLayer()), [['default', DENIED], ['update', GRANTED]]);
  b.fire({ essential_services: true });
  assert.deepStrictEqual(consentCalls(b.dataLayer()), [['default', DENIED], ['update', GRANTED], ['update', DENIED]]);
});

test('Consent Mode: nothing is pushed to the dataLayer without consent', async () => {
  const b = browser();
  b.fire({ essential_services: true });
  await b.settle();
  assert.strictEqual(b.window.dataLayer, undefined);
});

test('vendored files match SOURCE.json hashes', () => {
  const src = JSON.parse(read('src/consent/SOURCE.json'));
  const sha = (p) => crypto.createHash('sha256').update(fs.readFileSync(path.join(web, p))).digest('hex');
  assert.strictEqual(sha('src/consent/ketch.js'), src.sha256['ketch.js']);
  assert.strictEqual(sha('public/consent/dg-consent.js'), src.sha256['dg-consent.js']);
});
