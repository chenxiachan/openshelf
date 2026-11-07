# One-off backfill of the current three sources into gitpost/data, sharing one global throttle.
#   A  HN stories (Algolia) for the probe window up to now
#   B  Rogue Scholar posts in the window (refresh)
#   C  template forks whose /feed.xml failed: ask GitHub Pages API for the real URL (custom domains), retry feed
#   D  every eligible HN story page: rule features + outbound links + excerpt (resumable, sharded)
# Usage: GH_TOKEN=... python3 backfill.py [stages, default ABCD]      env WORKERS (6), RPM (90)
import json, os, random, sys, time, urllib.request, urllib.error, concurrent.futures as cf
from urllib.parse import urlencode
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import *  # noqa

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
DATA = os.path.join(ROOT, "data")
PROBE_HN = os.path.join(ROOT, "..", "probe_hn_2026-11-06", "data")
PROBE_SUP = os.path.join(ROOT, "..", "probe_supply_2026-11-06", "data")
WORKERS = min(int(os.environ.get("WORKERS", "6")), 6)
T = Throttle(os.path.join(DATA, "ctl"), min(float(os.environ.get("RPM", "90")), 90.0))
START_I = 1791377752          # first story of the probe window (2026-10-07)
RS_START, RS_END = "2026-10-07", time.strftime("%Y-%m-%d", time.gmtime())
t0 = time.time()


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def stage_a():
    out = os.path.join(DATA, "hn", "stories.jsonl")
    end = int(time.time())
    hits, stack, n = {}, [(START_I, end)], 0
    while stack:
        a, b = stack.pop()
        d = get_json("https://hn.algolia.com/api/v1/search_by_date?" + urlencode(
            {"tags": "story", "hitsPerPage": 1000, "numericFilters": f"created_at_i>={a},created_at_i<{b}"}), T)
        n += 1
        if not d or "hits" not in d:
            log("A: failed slice", a, b)
            continue
        if (d["nbHits"] > 1000 or len(d["hits"]) >= 1000) and b - a > 60:
            m = (a + b) // 2
            stack += [(a, m), (m, b)]
            continue
        for h in d["hits"]:
            hits[h["objectID"]] = {k: h.get(k) for k in ("objectID", "title", "url", "points", "num_comments", "created_at_i", "author")}
    with open(out, "w") as f:
        for h in sorted(hits.values(), key=lambda x: x["created_at_i"]):
            f.write(json.dumps(h, ensure_ascii=False) + "\n")
    log("A: HN requests", n, "stories", len(hits))


def stage_b():
    out = os.path.join(DATA, "rogue", "posts.jsonl")
    n, page = 0, 1
    with open(out, "w") as f:
        while True:
            d = get_json("https://rogue-scholar.org/api/records?" + urlencode(
                {"q": f"metadata.publication_date:[{RS_START} TO {RS_END}]", "size": 100, "page": page, "sort": "newest"}), T)
            if not d or "hits" not in d:
                break
            hits = d["hits"]["hits"]
            if page == 1:
                log("B: Rogue Scholar total in window", d["hits"]["total"])
            for x in hits:
                m = x["metadata"]
                c = x.get("custom_fields", {})
                ch = c.get("rs:content_html", "")
                urls = [i["identifier"] for i in m.get("identifiers", []) if i.get("scheme") == "url"]
                f.write(json.dumps({
                    "id": x["id"], "doi": x.get("pids", {}).get("doi", {}).get("identifier"), "title": m.get("title"),
                    "date": m.get("publication_date"), "blog": c.get("journal:journal", {}).get("title"),
                    "url": urls[0] if urls else None, "language": [l.get("id") for l in m.get("languages", [])],
                    "authors": [p["person_or_org"].get("name") for p in m.get("creators", [])],
                    "orcid": sum(1 for p in m.get("creators", []) for i in p["person_or_org"].get("identifiers", []) if i.get("scheme") == "orcid"),
                    "subjects": [s.get("subject") for s in m.get("subjects", [])], "description": strip(m.get("description", ""))[:600],
                    "excerpt": " ".join(strip(ch).split()[:320]),
                    "links": sorted(set(re.findall(r'href=["\'](https?://[^"\']+)', ch)))[:200],
                    "communities": x.get("parent", {}).get("communities", {}).get("ids", []),
                }, ensure_ascii=False) + "\n")
                n += 1
            if len(hits) < 100 or page * 100 >= d["hits"]["total"]:
                break
            page += 1
    log("B: Rogue Scholar saved", n)


def fetch_text(url, maxbytes=4_000_000):
    T.wait()
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=20, context=CTX) as r:
        return r.geturl(), r.read(maxbytes).decode("utf-8", "ignore")


def stage_c():
    out = os.path.join(DATA, "forks", "feeds_fallback.jsonl")
    tok = os.environ.get("GH_TOKEN")
    GH = {"Accept": "application/vnd.github+json"}
    if tok:
        GH["Authorization"] = f"Bearer {tok}"
    fails = [json.loads(l) for l in open(os.path.join(PROBE_SUP, "feeds.jsonl"))]
    fails = [s for s in fails if s.get("status") != "ok"]
    log("C: sites whose feed failed", len(fails), "token" if tok else "no token")
    stats = {}
    with open(out, "w") as f:
        for i, s in enumerate(fails):
            r = {k: s.get(k) for k in ("repo", "template", "base", "pushed_at", "created_at", "homepage")}
            d = get_json(f"https://api.github.com/repos/{s['repo']}/pages", T, GH, tries=2)
            if not d or "__http" in d:
                r["status"] = "no_pages" if d else "api_error"
            else:
                r["html_url"], r["cname"], r["pages_status"] = d.get("html_url"), d.get("cname"), d.get("status")
                base = (d.get("html_url") or s["base"]).rstrip("/") + "/"
                r["status"] = "feed_missing"
                for path in ("feed.xml", "atom.xml"):
                    try:
                        final, xml = fetch_text(base + path)
                        if "<entry" in xml or "<item" in xml or "<feed" in xml or "<rss" in xml:
                            r.update(final=final, entries=parse_feed(xml, final.rsplit("/", 1)[0] + "/"), status="ok")
                            break
                    except urllib.error.HTTPError as e:
                        r["last_http"] = e.code
                    except Exception as e:
                        r["last_err"] = type(e).__name__
            stats[r["status"]] = stats.get(r["status"], 0) + 1
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
            if i % 50 == 0:
                log("C:", i, stats)
    log("C: done", stats)


TRANSIENT = {"URLError", "TimeoutError", "timeout", "ConnectionResetError", "RemoteDisconnected", "IncompleteRead", "SSLError", "ConnectionRefusedError", "OSError"}


def page(row):
    try:
        return _page(row)
    except Exception as e:
        return {"objectID": row["objectID"], "url": row["url"], "error": "Crash:" + type(e).__name__}


def _page(row):
    T.wait()
    out = {k: row.get(k) for k in ("objectID", "url", "points", "num_comments", "created_at_i", "author")}
    out["hn_title"] = row.get("title")
    out["fetched_at"] = int(time.time())
    try:
        with urllib.request.urlopen(urllib.request.Request(row["url"], headers=HTML_HEADERS), timeout=20, context=CTX) as r:
            out["status"], final, ct = r.status, r.geturl(), r.headers.get("Content-Type", "")
            out["final"], out["ctype"] = final, ct.split(";")[0][:40]
            if "html" not in ct:
                out["skip"] = 1
                return out
            t = r.read(3_000_000).decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        out["error"], out["status"] = "HTTPError", e.code
        return out
    except Exception as e:
        out["error"] = type(e).__name__
        return out
    out["f"] = feat(t)
    out["description"] = meta(t, "description") or meta(t, "og:description")
    out["og_site"] = meta(t, "og:site_name")
    out["page_author"] = meta(t, "author") or meta(t, "citation_author")
    out["pub_date"] = meta(t, "citation_publication_date") or meta(t, "article:published_time") or meta(t, "citation_date")
    out["excerpt"] = " ".join(main_text(t).split()[:320])
    out["links"] = out_links(t, final)
    return out


def stage_d():
    d = os.path.join(DATA, "hn", "pages")
    rows = [json.loads(l) for l in open(os.path.join(DATA, "hn", "stories.jsonl"))]
    todo = [r for r in rows if r.get("url") and dom(r["url"]) not in EXCLUDE and not r["url"].lower().split("?")[0].endswith(".pdf")]
    done = {}
    for x in read_shards(d, "pages"):
        if "final" in x or x.get("error") == "HTTPError":
            done[x["objectID"]] = 1
    old = set()
    for l in open(os.path.join(PROBE_HN, "features.jsonl")):
        old.add(json.loads(l)["objectID"])
    for l in open(os.path.join(PROBE_HN, "refetch.jsonl")):
        x = json.loads(l)
        if "final" in x:
            old.add(x["objectID"])
    todo = [r for r in todo if r["objectID"] not in done]
    random.Random(7).shuffle(todo)                       # spread hosts apart
    todo.sort(key=lambda r: r["objectID"] in old)        # never-fetched pages first
    log("D: eligible", len(todo) + len(done), "already done", len(done), "to fetch", len(todo),
        "never fetched before", sum(r["objectID"] not in old for r in todo), "workers", WORKERS, "rpm", T.rpm)
    W = ShardWriter(d, "pages", 2000)
    errs, t1 = {}, time.time()
    retry = []
    try:
        for attempt in (0, 1):
            batch = todo if attempt == 0 else retry
            if attempt == 1:
                if not retry:
                    break
                log("D: retry pass for transient errors", len(retry))
                time.sleep(60)
                retry, errs = [], {}
            byid = {r["objectID"]: r for r in batch}
            with cf.ThreadPoolExecutor(WORKERS) as ex:
                for i, res in enumerate(ex.map(page, batch), 1):
                    W.write(res)
                    e = res.get("error")
                    if e:
                        errs[e] = errs.get(e, 0) + 1
                        if attempt == 0 and (e in TRANSIENT or e.startswith("Crash")):
                            retry.append(byid[res["objectID"]])
                    if i % 250 == 0:
                        el = time.time() - t1
                        log(f"D{attempt}: {i}/{len(batch)}  {60 * i / el:.0f}/min  eta {(len(batch) - i) / max(i / el, 1e-6) / 60:.0f} min  errors {dict(sorted(errs.items(), key=lambda x: -x[1])[:6])}")
            t1 = time.time()
    finally:
        W.close()
    log("D: done, errors in last pass", errs)


if __name__ == "__main__":
    stages = sys.argv[1] if len(sys.argv) > 1 else "ABCD"
    log("backfill start, stages", stages, "rpm cap", T.rpm, "workers", WORKERS)
    for s in stages:
        globals()["stage_" + s.lower()]()
        log(f"stage {s} finished, elapsed {(time.time() - t0) / 60:.1f} min")
    log("ALL DONE")
