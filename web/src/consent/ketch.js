// Ketch consent manager loader. Must run before Google Tag Manager and every
// other tracking script, so it is the first `beforeInteractive` entry in
// docs.yml and GTM is loaded from ./gtm.js right after it (not from Fern's
// built-in `analytics.gtm`, which Fern always renders ahead of custom JS).
// The line below is Ketch's snippet verbatim, minus the <script> wrapper.
!function(){window.semaphore=window.semaphore||[],window.ketch=function(){window.semaphore.push(arguments)};var e=document.createElement("script");e.type="text/javascript",e.src="https://global.ketchcdn.com/web/v3/config/deepgram/deepgram_marketing_site/boot.js",e.defer=e.async=!0,document.getElementsByTagName("head")[0].appendChild(e)}();

// Consent helpers shared by gtm.js, component.js (Heap) and sentry.js (Sentry).
// One `consent` subscription keeps Ketch's latest answer. Ketch fires
// `consent` when it resolves the visitor's choice and again whenever the
// visitor changes it, including a withdrawal, and replays the current value to
// late subscribers. Purpose codes come from the deepgram_marketing_site
// property: `analytics` and `targeted_advertising` (opt-in under gdpr, where
// unconsented purposes are left out of the payload; granted by default
// elsewhere). If Ketch never loads (blocked, offline), nothing counts as
// granted: trackers fail closed.
(function (w) {
  var granted = {};
  var waiting = [];
  var revokers = [];

  function hasAll(purposes) {
    for (var i = 0; i < purposes.length; i++) {
      if (granted[purposes[i]] !== true) return false;
    }
    return true;
  }

  function run(cb) {
    // A throwing callback must not stop the others; rethrow it on its own so
    // Sentry still sees it. It is not retried: a callback that failed partway
    // could load GTM or add Replay twice if run again, which is worse than
    // leaving that one tracker off until the next page load.
    try {
      cb();
    } catch (err) {
      setTimeout(function () {
        throw err;
      });
    }
  }

  w.ketch("on", "consent", function (consent) {
    granted = (consent && consent.purposes) || {};
    var ready = [];
    var still = [];
    for (var i = 0; i < waiting.length; i++) {
      (hasAll(waiting[i].purposes) ? ready : still).push(waiting[i]);
    }
    waiting = still;
    for (var j = 0; j < ready.length; j++) run(ready[j].cb);
    for (var k = 0; k < revokers.length; k++) {
      var now = hasAll(revokers[k].purposes);
      if (revokers[k].had && !now) run(revokers[k].cb);
      revokers[k].had = now;
    }
  });

  // True only while Ketch's latest answer grants every purpose in `purposes`.
  // Check it at the moment data would leave the page, so a withdrawal stops
  // anything that forwards data per event.
  w.dgHasConsent = function (purposes) {
    return hasAll(purposes);
  };

  // Run `cb` once, the first time every purpose in `purposes` is granted, so a
  // later opt-in still runs it. To stop a tracker on withdrawal, pair it with
  // dgOnRevoke; anything that can't pause stops on the next full page load.
  w.dgOnConsent = function (purposes, cb) {
    if (hasAll(purposes)) run(cb);
    else waiting.push({ purposes: purposes, cb: cb });
  };

  // Run `cb` each time a visitor withdraws any purpose in `purposes` after
  // granting all of them, so a tracker that can pause (Heap, Sentry Replay)
  // stops collecting on the same page. A tracker stopped this way stays off
  // if the visitor grants consent again on the same page, until the next
  // page load. GTM tags rely on the container's own Ketch consent settings
  // to stop on withdrawal.
  w.dgOnRevoke = function (purposes, cb) {
    revokers.push({ purposes: purposes, cb: cb, had: hasAll(purposes) });
  };

  // Ketch's `needsConsent` verdict for this page load: true while consent is
  // missing or incomplete, false once the visitor has chosen, null until Ketch
  // says. Ketch emits it once, before `consent`, and does not replay it to late
  // subscribers. dg-consent.js subscribes after hydration, after Ketch has
  // emitted it, so it reads the verdict recorded here to decide auto-open.
  var needsConsent = null;
  w.ketch("on", "needsConsent", function (value) {
    if (typeof value === "boolean") needsConsent = value;
  });
  w.dgKetchNeedsConsent = function () {
    return needsConsent;
  };
})(window);
