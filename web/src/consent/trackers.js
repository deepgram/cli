// Heap and Google Analytics 4 for cli.deepgram.com, held until Ketch consent
// through the dgOnConsent / dgOnRevoke helpers in ./ketch.js, as on
// developers.deepgram.com (fern/assets/component.js). Both wait for
// `targeted_advertising`: the Ketch config for deepgram_marketing_site lists
// Heap's _hp2_* and GA's _ga / _ga_* cookies under that purpose. If Ketch
// never loads (blocked, offline), neither starts.
(function (w, d) {
  if (typeof w.dgOnConsent !== "function") return;
  var PURPOSES = ["targeted_advertising"];
  var GA_ID = "G-TYPC1TBCKT";

  function insertHeap() {
    if (d.getElementById("heap-script")) return;
    var script = d.createElement("script");
    script.id = "heap-script";
    script.text =
      'window.heapReadyCb=window.heapReadyCb||[],window.heap=window.heap||[],heap.load=function(e,t){window.heap.envId=e,window.heap.clientConfig=t=t||{},window.heap.clientConfig.shouldFetchServerConfig=!1;var a=document.createElement("script");a.type="text/javascript",a.async=!0,a.src="https://cdn.us.heap-api.com/config/"+e+"/heap_config.js";var r=document.getElementsByTagName("script")[0];r.parentNode.insertBefore(a,r);var n=["init","startTracking","stopTracking","track","resetIdentity","identify","getSessionId","getUserId","getIdentity","addUserProperties","addEventProperties","removeEventProperty","clearEventProperties","addAccountProperties","addAdapter","addTransformer","addTransformerFn","onReady","addPageviewProperties","removePageviewProperty","clearPageviewProperties","trackPageview"],i=function(e){return function(){var t=Array.prototype.slice.call(arguments,0);window.heapReadyCb.push({name:e,fn:function(){heap[e]&&heap[e].apply(heap,t)}})}};for(var p=0;p<n.length;p++)heap[n[p]]=i(n[p])};' +
      'heap.load("765739241");';
    d.head.appendChild(script);
  }

  function stopHeap() {
    if (w.heap && typeof w.heap.stopTracking === "function") w.heap.stopTracking();
  }

  function consentState(value) {
    return {
      ad_storage: value,
      ad_user_data: value,
      ad_personalization: value,
      analytics_storage: value,
    };
  }

  function insertGa() {
    if (d.getElementById("ga4-script")) return;
    w.dataLayer = w.dataLayer || [];
    w.gtag = function () {
      w.dataLayer.push(arguments);
    };
    // Google Consent Mode v2: queue the denied default ahead of everything else
    // so no hit can go out before the state is declared, then record the grant
    // that got us here. gtag.js reads the dataLayer in order.
    w.gtag("consent", "default", consentState("denied"));
    w.gtag("consent", "update", consentState("granted"));
    w.gtag("js", new Date());
    w.gtag("config", GA_ID);
    var script = d.createElement("script");
    script.id = "ga4-script";
    script.async = true;
    script.src = "https://www.googletagmanager.com/gtag/js?id=" + GA_ID;
    d.head.appendChild(script);
  }

  // Google's documented per-property opt-out; the page also reloads on a
  // withdrawal (dg-consent.js), so nothing restarts until consent is granted.
  function stopGa() {
    w["ga-disable-" + GA_ID] = true;
    if (typeof w.gtag === "function") w.gtag("consent", "update", consentState("denied"));
  }

  w.dgOnConsent(PURPOSES, insertHeap);
  w.dgOnConsent(PURPOSES, insertGa);
  w.dgOnRevoke(PURPOSES, stopHeap);
  w.dgOnRevoke(PURPOSES, stopGa);
})(window, document);
