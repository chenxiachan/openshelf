// Openshelf events Worker (Cloudflare Workers + D1).
//   POST /e     page views and interactions from design/analytics.js (batched, at most 50 events per request)
//   GET  /agg   per-day aggregates for the daily pipeline (needs "Authorization: Bearer <PIPELINE_TOKEN>")
//   POST /vote  {id, dir: 1 | -1 | 0, voter}: one anonymous vote per post and browser (0 takes it back)
//   GET  /votes {items: {post id: [up, down]}}, cached for a minute
//   cron        deletes raw events older than RETENTION_DAYS (default 90), and starts the daily GitHub workflow
//               (GitHub's own schedule runs hours late or not at all; Cloudflare's cron is on time). Needs the secret
//               GH_DISPATCH_TOKEN (fine-grained, this repository only, Actions read and write) and the var GH_REPO.
// Privacy: IP addresses and user agents are never stored. A visitor is a hash of (secret salt, day, IP, user agent),
// so the same person cannot be linked across days. Bots are dropped. Votes: see vote() and worker/schema.sql.
const TYPES = new Set(["view", "open", "expand", "vote", "view_switch", "range", "layout", "filter", "topic", "search", "more", "lang", "signin", "submit"]);
const BOT = /bot|crawl|spider|slurp|preview|monitor|headless|python-requests|curl|wget|httpclient|okhttp/i;
const MAX_BODY = 32 * 1024;
const MAX_EVENTS = 50;
const VOTES_PER_NET_DAY = 300;        // vote changes from one network per day
const VOTERS_PER_ITEM_NET_DAY = 5;    // browsers behind one network that may vote on the same post per day (shared office or campus networks)

function cors(res, env) {
  const h = new Headers(res.headers);
  h.set("Access-Control-Allow-Origin", env.ALLOW_ORIGIN || "*");
  h.set("Access-Control-Allow-Methods", "POST, GET, OPTIONS");
  h.set("Access-Control-Allow-Headers", "Content-Type");
  return new Response(res.body, { status: res.status, headers: h });
}

async function sha(text) {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("").slice(0, 16);
}

// D1 sometimes fails a write for a moment (a beacon cannot retry), so writes are tried three times
async function retry(what, fn) {
  for (let i = 0; ; i++) {
    try { return await fn(); } catch (e) {
      if (i >= 2) { console.error(`${what} failed`, String(e)); throw e; }
      await new Promise((r) => setTimeout(r, 150 * (i + 1)));
    }
  }
}
function str(v, n) {
  return typeof v === "string" ? v.slice(0, n) : null;
}

export async function ingest(req, env) {
  const ua = req.headers.get("user-agent") || "";
  if (!ua || BOT.test(ua)) return new Response(null, { status: 204 });
  const raw = await req.text();
  if (raw.length > MAX_BODY) return new Response("too large", { status: 413 });
  let body;
  try { body = JSON.parse(raw); } catch { return new Response("bad json", { status: 400 }); }
  const events = Array.isArray(body.events) ? body.events.slice(0, MAX_EVENTS) : [];
  if (!events.length) return new Response(null, { status: 204 });
  const now = new Date();
  const day = now.toISOString().slice(0, 10);
  const ip = req.headers.get("cf-connecting-ip") || "";
  const visitor = await sha(`${env.SALT || "dev-salt"}|${day}|${ip}|${ua}`);
  const country = (req.cf && req.cf.country) || null;
  const page = body.page || {};
  const ref = str(body.ref, 80);
  const stmt = env.DB.prepare(
    "INSERT INTO events (day, ts, visitor, session, type, item, rank, view, lang, path, ref, country, data) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)");
  const rows = [];
  for (const e of events) {
    if (!e || !TYPES.has(e.type)) continue;
    const item = typeof e.id === "string" && /^[\w-]{1,40}$/.test(e.id) ? e.id : null;
    const extra = {};
    for (const k of ["to", "days", "compact", "key", "value", "on", "topic", "q", "results", "dir", "via", "host", "accepted_by_form"]) {
      if (k in e && (typeof e[k] !== "string" || e[k].length <= 80)) extra[k] = e[k];
    }
    rows.push(stmt.bind(day, Math.min(Number(e.t) || now.getTime(), now.getTime()), visitor, str(e.s, 12), e.type, item,
      Number.isFinite(e.rank) ? e.rank : null, str(e.view, 12), str(page.lang, 8), str(page.path, 80), ref, country, JSON.stringify(extra)));
  }
  if (rows.length) {
    try { await retry("ingest", () => env.DB.batch(rows)); } catch { return new Response("storage unavailable", { status: 503 }); }
  }
  return new Response(null, { status: 204 });
}

export async function vote(req, env) {
  const ua = req.headers.get("user-agent") || "";
  if (!ua || BOT.test(ua)) return new Response(null, { status: 204 });
  const raw = await req.text();
  if (raw.length > 1024) return new Response("too large", { status: 413 });
  let b;
  try { b = JSON.parse(raw); } catch { return new Response("bad json", { status: 400 }); }
  if (!b || typeof b.id !== "string" || !/^[\w-]{1,40}$/.test(b.id) || ![1, -1, 0].includes(b.dir)
      || typeof b.voter !== "string" || !/^[a-f0-9]{16,32}$/.test(b.voter)) return new Response("bad vote", { status: 400 });
  const now = new Date(), day = now.toISOString().slice(0, 10);
  const voter = await sha(`${env.SALT || "dev-salt"}|voter|${b.voter}`);
  const net = await sha(`${env.SALT || "dev-salt"}|${day}|${req.headers.get("cf-connecting-ip") || ""}`);
  const used = await env.DB.prepare("SELECT COUNT(*) AS n FROM votes WHERE net = ? AND day = ?").bind(net, day).first();
  if ((used && used.n) >= VOTES_PER_NET_DAY) return new Response("too many votes today", { status: 429 });
  if (b.dir === 0) {
    try { await retry("unvote", () => env.DB.prepare("DELETE FROM votes WHERE item = ? AND voter = ?").bind(b.id, voter).run()); }
    catch { return new Response("storage unavailable", { status: 503 }); }
    return new Response(null, { status: 204 });
  }
  const others = await env.DB.prepare("SELECT COUNT(*) AS n FROM votes WHERE item = ? AND net = ? AND day = ? AND voter != ?").bind(b.id, net, day, voter).first();
  if ((others && others.n) >= VOTERS_PER_ITEM_NET_DAY) return new Response(null, { status: 204 });   // quietly not counted
  try {
    await retry("vote", () => env.DB.prepare("INSERT INTO votes (item, voter, dir, net, day, ts) VALUES (?, ?, ?, ?, ?, ?) " +
      "ON CONFLICT (item, voter) DO UPDATE SET dir = excluded.dir, net = excluded.net, day = excluded.day, ts = excluded.ts")
      .bind(b.id, voter, b.dir, net, day, now.getTime()).run());
  } catch { return new Response("storage unavailable", { status: 503 }); }
  return new Response(null, { status: 204 });
}
export async function tally(env) {
  const r = await env.DB.prepare("SELECT item, SUM(dir = 1) AS up, SUM(dir = -1) AS down FROM votes GROUP BY item").all();
  const items = {};
  for (const x of r.results || []) items[x.item] = [x.up, x.down];
  return new Response(JSON.stringify({ items }), { headers: { "Content-Type": "application/json", "Cache-Control": "public, max-age=60" } });
}
export async function aggregate(req, env, url) {
  if (!env.PIPELINE_TOKEN || req.headers.get("authorization") !== `Bearer ${env.PIPELINE_TOKEN}`) return new Response("unauthorized", { status: 401 });
  const day = url.searchParams.get("day") || new Date(Date.now() - 86400000).toISOString().slice(0, 10);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) return new Response("bad day", { status: 400 });
  const totals = await env.DB.prepare(
    "SELECT COUNT(*) AS views, COUNT(DISTINCT visitor) AS visitors FROM events WHERE day = ? AND type = 'view'").bind(day).first();
  const items = await env.DB.prepare(
    "SELECT item, type, COUNT(*) AS n, COUNT(DISTINCT visitor) AS visitors FROM events WHERE day = ? AND item IS NOT NULL GROUP BY item, type").bind(day).all();
  const searches = await env.DB.prepare(
    "SELECT json_extract(data, '$.q') AS q, COUNT(*) AS n FROM events WHERE day = ? AND type = 'search' GROUP BY q ORDER BY n DESC LIMIT 50").bind(day).all();
  return Response.json({ day, totals, items: items.results || [], searches: searches.results || [] });
}

// start .github/workflows/daily.yml; a run started twice is harmless (each run only takes what is new)
export async function dispatch(env, fetchImpl = fetch) {
  if (!env.GH_DISPATCH_TOKEN || !env.GH_REPO) return "skipped: GH_DISPATCH_TOKEN or GH_REPO not set";
  const url = `https://api.github.com/repos/${env.GH_REPO}/actions/workflows/${env.GH_WORKFLOW || "daily.yml"}/dispatches`;
  let last = "";
  for (let i = 0; i < 3; i++) {
    try {
      const r = await fetchImpl(url, { method: "POST", body: JSON.stringify({ ref: env.GH_REF || "main" }),
        headers: { authorization: `Bearer ${env.GH_DISPATCH_TOKEN}`, accept: "application/vnd.github+json", "user-agent": "openshelf-worker", "x-github-api-version": "2022-11-28" } });
      if (r.status === 204) return "started";
      last = `HTTP ${r.status}: ${(await r.text()).slice(0, 200)}`;
      if (r.status === 401 || r.status === 403 || r.status === 404) break;   // the token expired or lost its permission: retrying will not help
    } catch (e) { last = String(e); }
    await new Promise((res) => setTimeout(res, 2000 * (i + 1)));
  }
  throw new Error(`could not start the daily workflow: ${last}`);   // shows up as a failed cron in the Worker's logs
}

export default {
  async fetch(req, env) {
    const url = new URL(req.url);
    if (req.method === "OPTIONS") return cors(new Response(null, { status: 204 }), env);
    if (url.pathname === "/e" && req.method === "POST") return cors(await ingest(req, env), env);
    if (url.pathname === "/agg" && req.method === "GET") return aggregate(req, env, url);
    if (url.pathname === "/vote" && req.method === "POST") return cors(await vote(req, env), env);
    if (url.pathname === "/votes" && req.method === "GET") return cors(await tally(env), env);
    return new Response("not found", { status: 404 });
  },
  async scheduled(_event, env) {
    const days = Number(env.RETENTION_DAYS || 90);
    await env.DB.prepare("DELETE FROM events WHERE day < date('now', ?)").bind(`-${days} day`).run();
    await env.DB.prepare("UPDATE votes SET net = '' WHERE net != '' AND day < date('now', '-1 day')").run();   // abuse limits only need today
    console.log("daily workflow:", await dispatch(env));
  },
};
