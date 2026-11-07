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


def label_for(it):
    return {"id": it["id"], "category": "research_report", "topic": "ai-ml", "political": "none", "confidence": "high",
            "summary_zh": "摘要", "summary_en": "A summary."}


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
