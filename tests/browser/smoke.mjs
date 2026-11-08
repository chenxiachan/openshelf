// Browser smoke test of the built site (site/public) in headless Chrome: every control on the front page is clicked,
// the About and privacy pages load, nothing throws, nothing overflows at phone width, and every interaction is
// handed to the statistics script. Hermetic: only the local server is reachable, the Worker is faked, beacons are caught.
// Run: python3 site/build_openshelf.py && (cd tests/browser && npm ci) && node tests/browser/smoke.mjs
// Chrome: CHROME_PATH, else the usual install locations (GitHub's Ubuntu runners have Google Chrome preinstalled).
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright-core";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const PUB = path.join(ROOT, "site", "public");
const CHROME = [process.env.CHROME_PATH, "/usr/bin/google-chrome", "/usr/bin/google-chrome-stable", "/usr/bin/chromium", "/usr/bin/chromium-browser",
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"].find((p) => p && existsSync(p));
assert.ok(existsSync(path.join(PUB, "index.html")), "build the site first: python3 site/build_openshelf.py");
assert.ok(CHROME, "no Chrome found; set CHROME_PATH");

const PORT = 8700 + Math.floor(Math.random() * 200);
const BASE = `http://127.0.0.1:${PORT}`;
const server = spawn("python3", ["-m", "http.server", String(PORT), "--bind", "127.0.0.1", "--directory", PUB], { stdio: "ignore" });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const passed = [];
const ok = (name) => { passed.push(name); console.log("  ok  " + name); };

async function waitForServer() {
  for (let i = 0; i < 50; i++) {
    try { if ((await fetch(BASE + "/")).ok) return; } catch {}
    await sleep(100);
  }
  throw new Error("local server did not start");
}

async function newPage(ctx, errors) {
  const page = await ctx.newPage();
  page.on("pageerror", (e) => errors.push(`${page.url()}: ${e.message}`));
  page.on("console", (m) => { if (m.type() === "error" && !/net::ERR_FAILED|Failed to load resource/.test(m.text())) errors.push(`${page.url()}: console: ${m.text()}`); });
  return page;
}

const visibleRows = (page) => page.$$eval("a[data-id]", (els) => els.filter((a) => a.offsetParent).length);
const topIds = (page) => page.$$eval("a[data-id]", (els) => els.filter((a) => a.offsetParent).slice(0, 20).map((a) => a.dataset.id));
const inViews = (page, text) => page.locator("#views button, #views a", { hasText: new RegExp(`^\\s*${text}\\s*$`) }).first();
const flag = (page, re) => page.locator("#flags .flag, #topics .flag", { hasText: re }).first();

async function noOverflow(page, name) {
  const [sw, iw] = await page.evaluate(() => [document.documentElement.scrollWidth, window.innerWidth]);
  assert.ok(sw <= iw + 1, `${name}: page scrolls sideways at ${iw}px (content ${sw}px wide)`);
}

async function main() {
  await waitForServer();
  const browser = await chromium.launch({ executablePath: CHROME, headless: true });
  const errors = [], worker = [];
  try {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    await ctx.route("**/*", (route) => {
      const u = new URL(route.request().url());
      if (u.hostname === "127.0.0.1") return route.continue();
      if (u.hostname.endsWith(".workers.dev")) {               // the statistics and votes Worker, faked
        worker.push({ path: u.pathname, method: route.request().method(), body: route.request().postData() || "" });
        if (u.pathname === "/votes") return route.fulfill({ status: 200, contentType: "application/json", headers: { "access-control-allow-origin": "*" }, body: '{"items":{}}' });
        return route.fulfill({ status: 204, headers: { "access-control-allow-origin": "*" } });
      }
      return route.abort();                                    // nothing else may load: the site has no third-party resources
    });
    await ctx.addInitScript(() => {
      window.__beacons = [];
      Object.defineProperty(navigator, "sendBeacon", { value: (url, body) => { window.__beacons.push({ url: String(url), body: String(body) }); return true; } });
    });

    // ---------- front page, English ----------
    const page = await newPage(ctx, errors);
    await page.goto(BASE + "/");
    await page.waitForSelector("a[data-id]");
    const all = await visibleRows(page);
    assert.ok(all > 10, `front page lists only ${all} posts`);
    ok(`front page renders (${all} posts)`);

    await inViews(page, "trending").click();
    assert.equal(await page.evaluate(() => location.hash), "#trending");
    const week = await topIds(page);
    assert.ok(week.length > 0, "trending is empty");
    await inViews(page, "month").click();
    assert.ok((await visibleRows(page)) > 0, "trending month is empty");
    await inViews(page, "week").click();
    assert.deepEqual(await topIds(page), week, "back to week shows a different list");
    ok("trending view and week/month range");

    await inViews(page, "threads").click();
    assert.equal(await page.evaluate(() => location.hash), "#threads");
    assert.ok((await visibleRows(page)) > 0, "threads view is empty");
    const [newest, firstDay] = await page.evaluate(() => [
      window.SHELF.items.flatMap((r) => r.resp.map((x) => x.date)).sort().pop(),
      document.querySelector("#log .sep b").textContent]);
    assert.ok(newest && firstDay.endsWith(newest), `threads should start with the day of the newest response (${newest}), not ${firstDay}`);
    await inViews(page, "latest").click();
    assert.equal(await visibleRows(page), all, "latest does not come back to the full list");
    ok("threads view and back to latest");

    await inViews(page, "summary").click();
    const sums = await page.$$eval(".sm", (els) => els.filter((x) => x.offsetParent).length);
    assert.ok(sums > 0, "summary layout shows no summaries");
    await inViews(page, "compact").click();
    assert.equal(await page.$$eval(".sm", (els) => els.filter((x) => x.offsetParent).length), 0, "compact layout still shows summaries");
    ok(`compact/summary layout (${sums} summaries)`);

    await flag(page, /^\s*research\s*$/).click();
    const research = await visibleRows(page);
    assert.ok(research > 0 && research < all, `research filter: ${research} of ${all}`);
    await flag(page, /^\s*research\s*$/).click();
    assert.equal(await visibleRows(page), all, "research filter does not switch off");
    ok(`type filter (${research} research posts)`);

    const topicName = await page.$$eval("#topics .flag", (els) => {
      const t = els.map((b) => b.textContent.trim()).filter((s) => !/^all/.test(s));
      return (t.find((s) => /^math/.test(s)) || t[0]).replace(/\d+$/, "");
    });
    await flag(page, new RegExp(`^\\s*${topicName}\\s*\\d*\\s*$`)).click();
    const topicRows = await visibleRows(page);
    assert.ok(topicRows > 0 && topicRows < all, `topic ${topicName}: ${topicRows} of ${all}`);
    await flag(page, /^\s*all topics/).click();
    assert.equal(await visibleRows(page), all, "all topics does not restore the list");
    ok(`topic chip "${topicName}" (${topicRows} posts)`);

    const word = await page.$eval("a[data-id]", (a) => (a.textContent.match(/[A-Za-z]{5,}/) || ["research"])[0]);
    await page.fill("#q", word);
    await sleep(1600);                                         // search is applied after 120 ms and recorded after a pause
    const found = await visibleRows(page);
    assert.ok(found > 0 && found <= all, `search "${word}": ${found} posts`);
    await page.fill("#q", "");
    await sleep(300);
    assert.equal(await visibleRows(page), all, "clearing the search does not restore the list");
    ok(`search ("${word}": ${found} posts)`);

    await page.click("#pager");
    const more = await visibleRows(page);
    assert.ok(more > all, `pager adds nothing (${all} -> ${more})`);
    ok(`pager (${all} -> ${more} posts)`);

    const box = page.locator(".vote").first();
    if (await box.count()) {
      await box.locator("button").first().click();
      assert.equal((await box.locator(".n").textContent()).trim(), "1", "upvote does not show 1");
      await box.locator("button").first().click();
      assert.equal((await box.locator(".n").textContent()).trim(), "0", "taking the vote back does not show 0");
      await sleep(300);
      const votes = worker.filter((w) => w.path === "/vote").map((w) => JSON.parse(w.body));
      assert.deepEqual(votes.map((v) => v.dir), [1, 0], "vote requests sent: " + JSON.stringify(votes));
      assert.match(votes[0].voter, /^[a-f0-9]{16,32}$/);
      ok("anonymous vote and taking it back");
    }

    await page.evaluate(() => { const a = document.querySelector("a[data-id]"); a.addEventListener("click", (e) => e.preventDefault(), { once: true }); });
    await page.locator("a[data-id]").first().click();
    ok("opening a post is recorded (checked below)");

    await page.click("#lang");
    assert.match(await page.textContent("#views"), /最新/, "language switch does not show Chinese");
    await page.click("#lang");
    assert.match(await page.textContent("#views"), /latest/, "language switch does not come back to English");
    ok("language switch");

    await page.fill("#sub-url", "https://twitter.com/someone");
    await page.click("#sub-btn");
    assert.ok(await page.isVisible("#sub-out"), "submitting a non-personal site shows no message");
    ok("submission form refuses a non-personal site");

    await page.evaluate(() => window.openshelfFlush && window.openshelfFlush());
    const beacons = await page.evaluate(() => window.__beacons);
    const types = new Set(beacons.flatMap((b) => JSON.parse(b.body).events.map((e) => e.type)));
    if (await page.evaluate(() => !!(window.OPENSHELF_ANALYTICS || {}).endpoint)) {
      for (const t of ["view", "view_switch", "range", "layout", "filter", "topic", "search", "more", "open", "lang", "submit"].concat(await box.count() ? ["vote"] : [])) {
        assert.ok(types.has(t), `the "${t}" interaction was not handed to the statistics script (got: ${[...types].join(", ")})`);
      }
      ok(`statistics receive every interaction (${types.size} types)`);
    }

    // ---------- Chinese front page, About, privacy ----------
    for (const p of ["/zh/", "/about/", "/zh/about/", "/privacy/", "/zh/privacy/"]) {
      const pg = await newPage(ctx, errors);
      await pg.goto(BASE + p);
      assert.ok(await pg.isVisible("h1"), `${p}: no visible h1`);
      if (p === "/zh/") {
        await pg.waitForSelector("a[data-id]");
        await inViews(pg, "最火").click();
        assert.ok((await visibleRows(pg)) > 0, "/zh/ trending is empty");
      } else {
        assert.ok(await pg.locator('a[href$="privacy/"]').count(), `${p}: no privacy link`);
      }
      await pg.close();
      ok(`${p} loads`);
    }

    // ---------- phone width ----------
    const phone = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true });
    await phone.route("**/*", (route) => (new URL(route.request().url()).hostname === "127.0.0.1" ? route.continue() : route.fulfill({ status: 204 })));
    for (const p of ["/", "/zh/", "/about/", "/privacy/"]) {
      const pg = await newPage(phone, errors);
      await pg.goto(BASE + p);
      if (p.endsWith("/") && !p.includes("about") && !p.includes("privacy")) await pg.waitForSelector("a[data-id]");
      await noOverflow(pg, p);
      await pg.close();
    }
    ok("no sideways scrolling at 390px");

    assert.deepEqual(errors, [], "page errors:\n" + errors.join("\n"));
    ok("no script errors on any page");
    console.log(`browser smoke test passed: ${passed.length} checks`);
  } finally {
    await browser.close();
    server.kill();
  }
}

main().catch((e) => { console.error("browser smoke test FAILED:", e.message); server.kill(); process.exit(1); });
