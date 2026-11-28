// Openshelf page analytics: views and interactions, batched and sent to our own endpoint (a Cloudflare Worker).
// Privacy: no cookies, nothing stored in the browser, no IP or user agent kept by the server (it only keeps a daily
// salted hash to count unique visitors). Do Not Track and Global Privacy Control turn tracking off completely.
// Configure with window.OPENSHELF_ANALYTICS = { endpoint: "https://…/e" } before this script. Without an endpoint
// (the local demo) events are only kept in memory (window.__openshelfEvents) so they can be inspected.
(function (root) {
  var cfg = root.OPENSHELF_ANALYTICS || {};
  var nav = root.navigator || {};
  var off = nav.doNotTrack === "1" || root.doNotTrack === "1" || nav.globalPrivacyControl === true;
  var TYPES = { view: 1, open: 1, expand: 1, vote: 1, view_switch: 1, range: 1, layout: 1, filter: 1, topic: 1, search: 1,
    more: 1, lang: 1, signin: 1, submit: 1 };
  var queue = [], sent = root.__openshelfEvents = [], session = Math.random().toString(36).slice(2, 10);
  var MAX_BATCH = 50, FLUSH_MS = 10000;

  function clean(v) {
    // only short scalar fields leave the page
    var out = {};
    for (var k in v) {
      if (!Object.prototype.hasOwnProperty.call(v, k)) continue;
      var x = v[k];
      if (typeof x === "number" && isFinite(x)) out[k] = x;
      else if (typeof x === "boolean") out[k] = x;
      else if (typeof x === "string") out[k] = x.slice(0, 80);
    }
    return out;
  }
  function track(type, data) {
    if (off || !TYPES[type]) return false;
    var e = clean(data || {});
    e.type = type; e.t = Date.now(); e.s = session;
    queue.push(e);
    if (queue.length >= MAX_BATCH) flush();
    return true;
  }
  function flush() {
    if (!queue.length) return 0;
    var batch = queue.splice(0, MAX_BATCH);
    var page = { path: (root.location && root.location.pathname) || "", lang: (root.document && root.document.documentElement.lang) || "" };
    var ref = root.document && root.document.referrer ? root.document.referrer.split("/")[2] || "" : "";
    var body = JSON.stringify({ page: page, ref: ref, events: batch });
    if (cfg.endpoint) {
      var ok = false;
      try { ok = nav.sendBeacon && nav.sendBeacon(cfg.endpoint, body); } catch (err) { ok = false; }
      if (!ok && root.fetch) { try { root.fetch(cfg.endpoint, { method: "POST", body: body, keepalive: true, headers: { "Content-Type": "text/plain" } }); } catch (err) {} }
    }
    Array.prototype.push.apply(sent, batch);
    return batch.length;
  }
  if (!off && root.setInterval) root.setInterval(flush, FLUSH_MS);
  if (!off && root.addEventListener) {
    root.addEventListener("visibilitychange", function () { if (root.document.visibilityState === "hidden") flush(); });
    root.addEventListener("pagehide", flush);
  }
  root.openshelfTrack = track;
  root.openshelfFlush = flush;
  root.openshelfTrackingOff = off;
})(typeof window !== "undefined" ? window : globalThis);
