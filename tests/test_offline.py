# Offline tests: no network, no API keys. Run with  python3 -m unittest discover -s tests -v
# They cover what the daily automation depends on: every module compiles, the site builds with all pages and links,
# opt-outs are removed everywhere, the privacy guard holds, the LLM client falls back and validates, and a whole daily
# run works end to end on fake sources.
import copy, glob, json, os, py_compile, subprocess, sys, tempfile, time, unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
for d in ("pipeline", "crawl", "site"):
    sys.path.insert(0, os.path.join(ROOT, d))
os.environ.setdefault("OPENROUTER_API_KEY", "test-key-not-real")
for k in ("GITHUB_TOKEN", "ALERT_GITHUB_REPO", "NTFY_TOPIC", "HEARTBEAT_URL"):   # alerts must stay local in tests
    os.environ.pop(k, None)
PUB = os.path.join(ROOT, "site", "public")


def read(*parts):
    with open(os.path.join(*parts), encoding="utf-8") as fh:
        return fh.read()


class Compile(unittest.TestCase):
    def test_all_modules_compile(self):
        files = [f for d in ("pipeline", "crawl", "site", "design", "eval") for f in glob.glob(os.path.join(ROOT, d, "*.py"))]
        self.assertGreater(len(files), 10)
        for f in files:
            py_compile.compile(f, doraise=True)


class Site(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        subprocess.run([sys.executable, os.path.join(ROOT, "site", "build_openshelf.py")], check=True, capture_output=True)

    def test_pages_exist(self):
        for p in ("index.html", "zh/index.html", "about/index.html", "zh/about/index.html", "privacy/index.html", "zh/privacy/index.html",
                  "status.json", "llms.txt", "shelf-data.js", "feed.xml", "zh/feed.xml", "sitemap.xml", "404.html"):
            self.assertTrue(os.path.exists(os.path.join(PUB, p)), p)

    def test_no_unfilled_placeholders_or_broken_paths(self):
        for f in glob.glob(os.path.join(PUB, "**", "*.html"), recursive=True):
            t = read(f)
            self.assertNotIn("{{", t, f)
            self.assertNotIn("../../site/", t, f)

    def test_privacy_linked_everywhere(self):
        for p in ("index.html", "zh/index.html"):
            self.assertIn('id="privacy" href="privacy/"', read(PUB, p), p)
        for p in ("about/index.html", "zh/about/index.html"):
            self.assertRegex(read(PUB, p), r'href="[./]*(zh/)?privacy/"', p)

    def test_no_name_declared_twice_in_page_scripts(self):
        """A var and a function with the same name: the var wins and every call throws (the old T bug)."""
        import re
        for f in ("site/stream.html", "site/about.html", "design/demo-c/index.html"):
            t = read(ROOT, f)
            funcs = set(re.findall(r"\bfunction\s+([A-Za-z_$][\w$]*)\s*\(", t))
            vars_ = set(re.findall(r"\bvar\s+([A-Za-z_$][\w$]*)\s*=", t))
            self.assertFalse(funcs & vars_, f"{f}: declared both as var and function: {funcs & vars_}")

    def test_status_json(self):
        st = json.loads(read(PUB, "status.json"))
        self.assertGreater(st["posts"], 100)
        self.assertTrue(st["generated"] and st["built_at"].endswith("Z"))


class OptOut(unittest.TestCase):
    def test_purge_everywhere(self):
        import optout
        D = json.loads(read(ROOT, "site", "issues", "live.json"))
        c = D["chains"][0]
        lk = next(l for r in D["reports"] for l in r.get("linkers", []))
        with tempfile.TemporaryDirectory() as tmp:
            f = os.path.join(tmp, "optout.json")
            with open(f, "w") as fh:
                json.dump({"domains": [lk, "www." + c["original"]["domain"]], "urls": [D["reports"][-1]["url"]]}, fh)
            b = optout.blocker(f)
            E = copy.deepcopy(D)
            self.assertGreater(optout.purge(E, b), 0)
            self.assertTrue(b("https://sub." + c["original"]["domain"] + "/x"))
            self.assertFalse(b("https://example.org/"))
            self.assertFalse(any(b(r["url"]) for r in E["reports"]))
            self.assertFalse(any(b(l) for r in E["reports"] for l in r.get("linkers", [])))
            self.assertFalse(any(b(x["url"]) for c in E["chains"] for x in [c["original"]] + c["responses"]))
            self.assertEqual(optout.purge(copy.deepcopy(D), optout.blocker(os.path.join(tmp, "missing.json"))), 0)


class PrivacyGuard(unittest.TestCase):
    def test_feature_without_section_refuses_to_build(self):
        import build as B
        saved_cfg, saved_priv = B.CFG, B.PRIV
        try:
            B.PRIV = copy.deepcopy(saved_priv)
            for lang in ("en", "zh"):
                B.PRIV[lang]["sections"] = [x for x in B.PRIV[lang]["sections"] if not x.get("when")]
            for cfg in ({"api": "https://example.invalid"}, {"features": {"login": True}}, {"features": {"votes": True}}):
                B.CFG = dict(saved_cfg, **cfg)
                with self.assertRaises(AssertionError):
                    B.page_privacy(B.apply_editorial(B.ISSUE), "en")
        finally:
            B.CFG, B.PRIV = saved_cfg, saved_priv

    def test_sections_follow_the_features(self):
        import build as B
        saved = B.CFG
        try:
            B.CFG = dict(saved, api="", analytics_endpoint="", features={})
            off = B.page_privacy(B.apply_editorial(B.ISSUE), "en")
            B.CFG = dict(saved, api="https://example.invalid", features={"votes": True})
            on = B.page_privacy(B.apply_editorial(B.ISSUE), "en")
        finally:
            B.CFG = saved
        self.assertIn("No cookies, no tracking", off)
        self.assertNotIn("Usage statistics", off)
        self.assertNotIn("No cookies, no tracking", on)
        self.assertIn("Usage statistics", on)
        self.assertIn("<h2>Votes</h2>", on)


class FakeRun:
    def __init__(self):
        self.counts, self.events, self.cost = {}, [], 0.0

    def count(self, k, n=1):
        self.counts[k] = self.counts.get(k, 0) + n

    def event(self, *a, **k):
        self.events.append((a, k))

    def add_cost(self, x):
        self.cost += x or 0

    def check(self, name, ok, detail=""):
        self.events.append((("check", name, bool(ok)), {}))


def label_for(it):
    return {"id": it["id"], "category": "research_report", "topic": "ai-ml", "political": "none", "confidence": "high",
            "summary_zh": "摘要", "summary_en": "A summary."}


class Homepages(unittest.TestCase):
    """Only repositories pushed since their feed was last read are read; new homepages join the feed list."""
    def setUp(self):
        import homepages as H
        self.H, self.tmp = H, tempfile.TemporaryDirectory()
        self.saved = (H.FEEDS, H.TITLES, H.FORKS, H.PER_RUN, H.list_forks, H.S.feed)
        H.FEEDS, H.TITLES, H.FORKS = (os.path.join(self.tmp.name, f) for f in ("feeds.json", "titles.json", "forks.json"))
        with open(H.FEEDS, "w") as fh:
            json.dump({"feeds": [{"feed": "https://known.example/feed.xml", "site": "known.example", "template": "alfolio", "repo": "k/k.github.io"}]}, fh)
        with open(H.TITLES, "w") as fh:
            json.dump({"titles": ["a post with math"]}, fh)
        self.now = int(time.time())
        today = time.strftime("%Y-%m-%d", time.gmtime(self.now))
        self.pushed = {"k/k.github.io": "2026-11-01T00:00:00Z", "new/new.github.io": "2026-11-02T00:00:00Z", "plain/plain.github.io": "2026-11-03T00:00:00Z",
                       "gone/gone.github.io": "2026-11-04T00:00:00Z", **{f"s{i}/s{i}.github.io": "2026-10-01T00:00:00Z" for i in range(5)}}
        H.list_forks = lambda token: {r: ("alfolio", p) for r, p in self.pushed.items()}
        self.reads = []

        def feed(f, cache):
            self.reads.append(f["feed"])
            post = lambda title, slug: {"title": title, "url": f["feed"].replace("feed.xml", slug), "published": today}
            if "gone" in f["feed"]:
                return "http404", [], {}, ""
            if "known" in f["feed"]:
                return "ok", [post("Known result", "a/")], {}, f["feed"]
            if "new.github.io" in f["feed"]:   # redirects to its own domain
                return "ok", [post("A new result", "b/"), post("A post with math", "c/")], {}, "https://new-person.example/feed.xml"
            return "ok", [post("Welcome to my blog", "d/")], {}, f["feed"]      # plain and s0..s4: only the template's sample post
        H.S.feed = feed

    def tearDown(self):
        H = self.H
        H.FEEDS, H.TITLES, H.FORKS, H.PER_RUN, H.list_forks, H.S.feed = self.saved
        self.tmp.cleanup()

    def collect(self, state, now=None):
        run = FakeRun()
        posts, status, save = self.H.collect(run, state, now or self.now, lambda u: False, "token")
        save()
        return run, posts

    def test_pushed_repositories_only(self):
        H, state = self.H, {}
        run, posts = self.collect(state)
        self.assertEqual(run.counts["feeds_read"], 9)
        self.assertEqual(sorted(p["title"] for p in posts), ["A new result", "Known result"])
        self.assertIn("welcome to my blog", json.loads(read(H.TITLES))["titles"], "a title on 5 sites is a template sample")
        sites = {f["site"]: f for f in json.loads(read(H.FEEDS))["feeds"]}
        self.assertEqual(set(sites), {"known.example", "new-person.example"}, "only homepages with a real post join the feed list")
        self.assertEqual(sites["new-person.example"]["feed"], "https://new-person.example/feed.xml")
        self.assertTrue(all(v[1] == v[2] for v in json.loads(read(H.FORKS)).values()), "every fork is marked as read at its push")

        self.reads.clear()
        run, posts = self.collect(state)                       # same day: no new list, nothing due
        self.assertEqual((run.counts["feeds_read"], self.reads), (0, []))

        self.pushed["new/new.github.io"] = "2026-11-09T08:00:00Z"
        run, posts = self.collect(state, self.now + 86400)     # next day: only the repository that was pushed
        self.assertEqual(self.reads, ["https://new-person.example/feed.xml"])

    def test_first_read_takes_the_newest_few(self):
        H, state = self.H, {}
        def flood(f, cache):
            days = [time.strftime("%Y-%m-%d", time.gmtime(self.now - i * 86400)) for i in range(10)]
            return "ok", [{"title": f"Note {d}", "url": f"https://daily.example/{d}/", "published": d} for d in days], {}, f["feed"]
        H.S.feed = flood
        iso = lambda ts: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))
        self.pushed = {"d/d.github.io": iso(self.now - 86400)}
        run, posts = self.collect(state)
        mine = [p for p in posts if p["site"] == "d.github.io"]
        self.assertEqual(len(mine), H.PER_SITE, "a site that posts every day gives only its newest posts")
        self.assertEqual(mine[0]["date"], time.strftime("%Y-%m-%d", time.gmtime(self.now)), "newest first")

        old = time.strftime("%Y-%m-%d", time.gmtime(self.now - 20 * 86400))      # in the 30-day window, but long before the last read
        H.S.feed = lambda f, cache: ("ok", [{"title": "Old note", "url": "https://daily.example/old/", "published": old}], {}, f["feed"])
        self.pushed = {"d/d.github.io": iso(self.now)}
        run, posts = self.collect(state, self.now + 86400)
        self.assertEqual([p for p in posts if p["site"] == "d.github.io"], [], "a later read does not go back before the previous read")

    def test_per_run_cap_carries_over(self):
        H, state = self.H, {}
        H.PER_RUN = 4
        run, _ = self.collect(state)
        self.assertEqual((run.counts["feeds_read"], run.counts["forks_left_for_next_run"]), (4, 5))
        self.assertEqual(self.reads[0], "https://gone.github.io/feed.xml", "newest push first")
        run, _ = self.collect(state)
        self.assertEqual((run.counts["feeds_read"], run.counts["forks_left_for_next_run"]), (4, 1))


class LLMClient(unittest.TestCase):
    def setUp(self):
        import llm
        self.llm, self._sleep = llm, llm.time.sleep
        llm.time.sleep = lambda s: None

    def tearDown(self):
        self.llm.time.sleep = self._sleep

    def test_fallback_and_validation(self):
        llm = self.llm
        run = FakeRun()
        c = llm.Client(run, chain=["a", "b"])
        calls = []

        def fake(model, items):
            calls.append(model)
            if model == "a":
                raise llm.LLMError("down")
            out = [label_for(it) for it in items]
            if len(items) > 1:
                out[0]["category"] = "not-a-category"          # invalid: must be retried, not accepted
            return out
        c._call = fake
        items = [{"id": str(i)} for i in range(12)]
        labels, left = c.label(items, batch=10)
        self.assertEqual(len(labels), 12)
        self.assertEqual(left, [])
        self.assertIn("a", c.tripped)                          # circuit breaker after 3 failures
        self.assertTrue(all(v["model"] == "b" and v["category"] in llm.CATS for v in labels.values()))
        self.assertEqual(run.counts.get("llm_items_by_fallback"), 12)

    def test_parse(self):
        self.assertEqual(self.llm.parse('```json\n{"items": [{"id": "1"}]}\n```'), [{"id": "1"}])
        self.assertEqual(self.llm.parse("no json here"), [])


class DailyDryRun(unittest.TestCase):
    def test_daily_end_to_end_on_fake_sources(self):
        import daily, sources as S
        now = int(time.time())
        stories = [{"objectID": str(i), "title": "t", "url": f"https://site{i}.example/p", "points": 1, "num_comments": 0,
                    "created_at_i": now - 100, "author": "a"} for i in range(1001)]
        saved = (S.hn_stories, S.pages, S.rogue_posts, daily.DRY, daily.NO_FEEDS, daily.optout.blocker)
        try:
            S.hn_stories, S.pages, S.rogue_posts = (lambda a, b: (stories, 1)), (lambda rows, wanted: []), (lambda since: [])
            daily.DRY, daily.NO_FEEDS = True, True

            def blocker():
                b = lambda s: "site7.example" in (s or "")
                b.empty = False
                return b
            daily.optout.blocker = blocker
            daily.main()
        finally:
            S.hn_stories, S.pages, S.rogue_posts, daily.DRY, daily.NO_FEEDS, daily.optout.blocker = saved
        last = json.loads(read(ROOT, "data", "logs", "runs.jsonl").splitlines()[-1])
        self.assertEqual(last["status"], "ok")
        self.assertEqual(last["counts"]["hn_new_urls_to_fetch"], 1000)     # one of 1001 is opted out


if __name__ == "__main__":
    unittest.main()
