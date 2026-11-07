// End-to-end test without Cloudflare: design/analytics.js in a fake browser -> Worker ingest -> SQLite (same SQL as D1) -> /agg.
// Run: node worker/test/e2e.test.mjs
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { DatabaseSync } from "node:sqlite";
import vm from "node:vm";
import { fileURLToPath } from "node:url";
import worker, { ingest } from "../src/index.js";

const here = (p) => fileURLToPath(new URL(p, import.meta.url));

// --- a D1-shaped wrapper around node:sqlite
function d1() {
  const db = new DatabaseSync(":memory:");
  db.exec(readFileSync(here("../schema.sql"), "utf8"));
  const wrap = (sql, args = []) => ({
    bind: (...a) => wrap(sql, a),
    run: () => db.prepare(sql).run(...args),
    all: () => ({ results: db.prepare(sql).all(...args) }),
    first: () => db.prepare(sql).get(...args),
  });
  return { raw: db, prepare: (sql) => wrap(sql), batch: (stmts) => stmts.map((s) => s.run()) };
}

// --- load analytics.js in a fake browser
function browser({ dnt = false } = {}) {
  const beacons = [];
  const win = {
    OPENSHELF_ANALYTICS: { endpoint: "https://events.example/e" },
    navigator: { doNotTrack: dnt ? "1" : "0", sendBeacon: (url, body) => { beacons.push({ url, body }); return true; } },
    document: { documentElement: { lang: "zh-CN" }, referrer: "https://news.ycombinator.com/item?id=1", visibilityState: "visible" },
    location: { pathname: "/" },
    setInterval: () => 0,
    addEventListener: () => {},
  };
  win.window = win;
  vm.runInNewContext(readFileSync(here("../../design/analytics.js"), "utf8"), win);
  return { win, beacons };
}

const env = { DB: d1(), SALT: "test-salt", PIPELINE_TOKEN: "tok" };
const req = (body, ua = "Mozilla/5.0 (Macintosh) Safari", ip = "203.0.113.7") =>
  new Request("https://events.example/e", { method: "POST", body, headers: { "user-agent": ua, "cf-connecting-ip": ip } });

// 1. client: valid events queue, unknown types are refused, long strings are cut, flush sends one beacon
const a = browser();
assert.equal(a.win.openshelfTrack("view", { view: "latest" }), true);
assert.equal(a.win.openshelfTrack("open", { id: "3c51f6a", rank: 1, view: "trending" }), true);
assert.equal(a.win.openshelfTrack("search", { q: "x".repeat(500), results: 3 }), true);
assert.equal(a.win.openshelfTrack("keystroke", { k: "a" }), false, "unknown event type must be refused");
assert.equal(a.win.openshelfFlush(), 3);
assert.equal(a.beacons.length, 1);
const sent = JSON.parse(a.beacons[0].body);
assert.equal(sent.ref, "news.ycombinator.com", "only the referring host leaves the page");
assert.equal(sent.events[2].q.length, 80, "strings are cut to 80 characters");

// 2. Do Not Track turns everything off
const b = browser({ dnt: true });
assert.equal(b.win.openshelfTrack("view", {}), false);
assert.equal(b.win.openshelfFlush(), 0);

// 3. Worker stores the batch without IP or user agent; bots are dropped
assert.equal((await ingest(req(a.beacons[0].body), env)).status, 204);
assert.equal((await ingest(req(a.beacons[0].body, "Googlebot/2.1"), env)).status, 204);
const rows = env.DB.raw.prepare("SELECT * FROM events").all();
assert.equal(rows.length, 3, "bot batch must not be stored");
const dump = JSON.stringify(rows);
assert.ok(!dump.includes("203.0.113.7") && !dump.includes("Safari"), "no IP or user agent in storage");
assert.equal(rows[1].item, "3c51f6a");

// 4. a second visitor, then the aggregate endpoint counts views and unique visitors per post
const c = browser();
c.win.openshelfTrack("view", {});
c.win.openshelfTrack("open", { id: "3c51f6a", rank: 1 });
c.win.openshelfFlush();
await ingest(req(c.beacons[0].body, "Mozilla/5.0 (Windows) Firefox", "198.51.100.9"), env);
const day = new Date().toISOString().slice(0, 10);
const unauth = await worker.fetch(new Request(`https://events.example/agg?day=${day}`), env);
assert.equal(unauth.status, 401, "/agg needs the pipeline token");
const agg = await (await worker.fetch(new Request(`https://events.example/agg?day=${day}`, { headers: { authorization: "Bearer tok" } }), env)).json();
assert.deepEqual(agg.totals, { views: 2, visitors: 2 });
const opens = agg.items.find((x) => x.item === "3c51f6a" && x.type === "open");
assert.equal(opens.n, 2);
assert.equal(opens.visitors, 2);
assert.equal(agg.searches[0].q.length, 80);

// 5. bad input is refused, not stored
assert.equal((await ingest(req("not json"), env)).status, 400);
assert.equal((await ingest(req("x".repeat(40000)), env)).status, 413);

// 6. retention job deletes old rows only
env.DB.raw.prepare("INSERT INTO events (day, ts, visitor, type) VALUES ('2020-01-01', 0, 'old', 'view')").run();
await worker.scheduled({}, env);
assert.equal(env.DB.raw.prepare("SELECT COUNT(*) AS n FROM events WHERE visitor = 'old'").get().n, 0);
assert.equal(env.DB.raw.prepare("SELECT COUNT(*) AS n FROM events").get().n, 5);

// 7. anonymous votes: one per post and browser, can be changed or taken back, abuse limits per network, nothing identifying stored
const vreq = (body, ip = "203.0.113.7", ua = "Mozilla/5.0 (Macintosh) Safari") =>
  new Request("https://events.example/vote", { method: "POST", body: JSON.stringify(body), headers: { "user-agent": ua, "cf-connecting-ip": ip } });
const tallyNow = async () => (await (await worker.fetch(new Request("https://events.example/votes"), env)).json()).items;
const v1 = "a".repeat(16), v2 = "b".repeat(16);
assert.equal((await worker.fetch(vreq({ id: "3c51f6a", dir: 1, voter: v1 }), env)).status, 204);
assert.deepEqual((await tallyNow())["3c51f6a"], [1, 0]);
await worker.fetch(vreq({ id: "3c51f6a", dir: -1, voter: v1 }), env);
assert.deepEqual((await tallyNow())["3c51f6a"], [0, 1], "changing a vote replaces it");
await worker.fetch(vreq({ id: "3c51f6a", dir: 1, voter: v2 }, "198.51.100.9"), env);
assert.deepEqual((await tallyNow())["3c51f6a"], [1, 1]);
await worker.fetch(vreq({ id: "3c51f6a", dir: 0, voter: v1 }), env);
assert.deepEqual((await tallyNow())["3c51f6a"], [1, 0], "dir 0 takes the vote back");
assert.equal((await worker.fetch(vreq({ id: "3c51f6a", dir: 2, voter: v1 }), env)).status, 400);
assert.equal((await worker.fetch(vreq({ id: "3c51f6a", dir: 1, voter: "not-hex" }), env)).status, 400);
assert.equal((await worker.fetch(vreq({ id: "bad id!", dir: 1, voter: v1 }), env)).status, 400);
await worker.fetch(vreq({ id: "3c51f6a", dir: 1, voter: "c".repeat(16) }, "203.0.113.7", "Googlebot/2.1"), env);
assert.deepEqual((await tallyNow())["3c51f6a"], [1, 0], "bots do not vote");
for (let i = 0; i < 8; i++) await worker.fetch(vreq({ id: "x1", dir: 1, voter: String(i).repeat(16) }, "192.0.2.1"), env);
assert.deepEqual((await tallyNow())["x1"], [5, 0], "at most 5 browsers per network and post per day");
const vdump = JSON.stringify(env.DB.raw.prepare("SELECT * FROM votes").all());
assert.ok(!vdump.includes("203.0.113.7") && !vdump.includes(v2) && !vdump.includes("Safari"), "no IP, raw voter id or user agent stored");
const vres = await worker.fetch(new Request("https://events.example/votes"), env);
assert.equal(vres.headers.get("access-control-allow-origin"), "*");
assert.match(vres.headers.get("cache-control"), /max-age=60/);
env.DB.raw.prepare("INSERT INTO votes (item, voter, dir, net, day, ts) VALUES ('old', 'v', 1, 'nethash', '2020-01-01', 0)").run();
await worker.scheduled({}, env);
assert.equal(env.DB.raw.prepare("SELECT net FROM votes WHERE item = 'old'").get().net, "", "network hash is blanked after a day");
assert.equal(env.DB.raw.prepare("SELECT COUNT(*) AS n FROM votes WHERE item = 'old'").get().n, 1, "the vote itself is kept");

console.log("all 7 analytics and vote tests passed", JSON.stringify(agg.totals));

// 8. a write that fails once is retried, not lost
{
  const flaky = { ...env, DB: { ...env.DB, prepare: env.DB.prepare, batch: (() => { let n = 0; return (stmts) => { if (n++ === 0) throw new Error("D1 busy"); return env.DB.batch(stmts); }; })() } };
  const before = env.DB.raw.prepare("SELECT COUNT(*) AS n FROM events").get().n;
  const d = browser(); d.win.openshelfTrack("view", {}); d.win.openshelfFlush();
  assert.equal((await ingest(req(d.beacons[0].body), flaky)).status, 204);
  assert.equal(env.DB.raw.prepare("SELECT COUNT(*) AS n FROM events").get().n, before + 1, "the retried batch is stored");
  console.log("retry test passed");
}
