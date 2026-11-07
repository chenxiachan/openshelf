# Incremental fetchers for the daily pipeline. All requests share one Throttle (<= 6 workers, <= 90 requests/min).
#   hn_stories(since_ts)        Hacker News stories via Algolia, sliced so no query hits the 1000-result cap
#   rogue_posts(since_date)     Rogue Scholar records published on or after a date
#   feed_posts(feeds, since, cache)   academic homepage feeds with conditional GET (ETag / Last-Modified)
#   page(row)                   fetch one page: rule features, description, excerpt, outbound links
import concurrent.futures as cf, json, os, re, sys, time, urllib.error, urllib.request
from email.utils import parsedate_to_datetime
from urllib.parse import urlencode

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "crawl"))
from common import CTX, EXCLUDE, HTML_HEADERS, UA, Throttle, dom, feat, get_json, main_text, meta, out_links, parse_feed, strip  # noqa: E402

ROOT = os.path.join(HERE, "..")
T = Throttle(os.path.join(ROOT, "data", "ctl"), min(float(os.environ.get("RPM", "90")), 90.0))
WORKERS = min(int(os.environ.get("WORKERS", "6")), 6)


def hn_stories(since_ts, until_ts=None):
    """Every story created in [since_ts, until_ts). Returns (stories, requests)."""
    until_ts = until_ts or int(time.time())
    hits, stack, n = {}, [(int(since_ts), int(until_ts))], 0
    while stack:
        a, b = stack.pop()
        d = get_json("https://hn.algolia.com/api/v1/search_by_date?" + urlencode(
            {"tags": "story", "hitsPerPage": 1000, "numericFilters": f"created_at_i>={a},created_at_i<{b}"}), T)
        n += 1
        if not d or "hits" not in d:
            raise RuntimeError(f"HN Algolia query failed for {a}..{b}")
        if (d["nbHits"] > 1000 or len(d["hits"]) >= 1000) and b - a > 60:
            m = (a + b) // 2
            stack += [(a, m), (m, b)]
            continue
        for h in d["hits"]:
            hits[h["objectID"]] = {k: h.get(k) for k in ("objectID", "title", "url", "points", "num_comments", "created_at_i", "author")}
    return sorted(hits.values(), key=lambda x: x["created_at_i"]), n


def rogue_posts(since_date, until_date=None):
    until_date = until_date or time.strftime("%Y-%m-%d", time.gmtime())
    out, page = [], 1
    while True:
        d = get_json("https://rogue-scholar.org/api/records?" + urlencode(
            {"q": f"metadata.publication_date:[{since_date} TO {until_date}]", "size": 100, "page": page, "sort": "newest"}), T)
        if not d or "hits" not in d:
            raise RuntimeError("Rogue Scholar query failed")
        for x in d["hits"]["hits"]:
            m, c = x["metadata"], x.get("custom_fields", {})
            ch = c.get("rs:content_html", "")
            urls = [i["identifier"] for i in m.get("identifiers", []) if i.get("scheme") == "url"]
            if not urls:
                continue
            out.append({"id": "rs:" + x["id"], "source": "rs", "url": urls[0], "domain": dom(urls[0]), "title": m.get("title") or "",
                        "date": (m.get("publication_date") or "")[:10], "doi": x.get("pids", {}).get("doi", {}).get("identifier"),
                        "site": c.get("journal:journal", {}).get("title") or "", "author": ", ".join(p["person_or_org"].get("name", "") for p in m.get("creators", []))[:200],
                        "description": strip(m.get("description", ""))[:600], "excerpt": " ".join(strip(ch).split()[:320]),
                        "links": sorted(set(re.findall(r'href=["\'](https?://[^"\']+)', ch)))[:200]})
        if len(d["hits"]["hits"]) < 100 or page * 100 >= d["hits"]["total"]:
            break
        page += 1
    return out


def _date(s):
    s = (s or "").strip()
    try:
        return parsedate_to_datetime(s).strftime("%Y-%m-%d")      # RSS pubDate
    except Exception:
        m = re.match(r"(\d{4}-\d{2}-\d{2})", s)                  # Atom published / updated
        return m.group(1) if m else ""


def _feed(f, cache):
    T.wait()
    h = {"User-Agent": UA, "Accept": "application/atom+xml, application/rss+xml, application/xml;q=0.9, */*;q=0.5"}
    c = cache.get(f["feed"]) or {}
    if c.get("etag"):
        h["If-None-Match"] = c["etag"]
    if c.get("modified"):
        h["If-Modified-Since"] = c["modified"]
    try:
        with urllib.request.urlopen(urllib.request.Request(f["feed"], headers=h), timeout=20, context=CTX) as r:
            xml = r.read(3_000_000).decode("utf-8", "ignore")
            new_cache = {"etag": r.headers.get("ETag"), "modified": r.headers.get("Last-Modified")}
        return "ok", parse_feed(xml, f["feed"]), new_cache
    except urllib.error.HTTPError as e:
        return ("not_modified" if e.code == 304 else f"http{e.code}"), [], c
    except Exception as e:
        return type(e).__name__, [], c


def feed_posts(feeds, since_date, cache, template_titles):
    """New posts from all feeds since a date. Returns (posts, status counts, updated cache)."""
    posts, status, new_cache = [], {}, dict(cache)
    with cf.ThreadPoolExecutor(WORKERS) as ex:
        for f, (st, entries, c) in zip(feeds, ex.map(lambda f: _feed(f, cache), feeds)):
            status[st] = status.get(st, 0) + 1
            new_cache[f["feed"]] = {k: v for k, v in c.items() if v}
            for e in entries:
                d = _date(e.get("published"))
                if not d or d < since_date or not e.get("url") or (e.get("title") or "").strip().lower() in template_titles:
                    continue
                posts.append({"id": "fork:" + e["url"], "source": "fork", "url": e["url"], "domain": dom(e["url"]), "title": e.get("title", ""),
                              "date": d, "site": f["site"], "author": "", "description": e.get("summary", ""), "excerpt": e.get("excerpt", ""),
                              "links": e.get("links", [])})
    return posts, status, new_cache


def eligible(story):
    u = story.get("url") or ""
    return bool(u) and dom(u) not in EXCLUDE and not u.lower().split("?")[0].endswith(".pdf")


def page(row, wanted=frozenset()):
    """Fetch one HN story page. Never raises: errors come back in the record.
    For links pointing at a listed post (keys in `wanted`), the text around the link is kept in out["ctx"]."""
    out = {k: row.get(k) for k in ("objectID", "url", "points", "num_comments", "created_at_i", "author")}
    out["hn_title"] = row.get("title")
    T.wait()
    try:
        with urllib.request.urlopen(urllib.request.Request(row["url"], headers=HTML_HEADERS), timeout=20, context=CTX) as r:
            final, ct = r.geturl(), r.headers.get("Content-Type", "")
            out["final"] = final
            if "html" not in ct:
                out["skip"] = "not_html"
                return out
            t = r.read(3_000_000).decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        out["error"] = f"http{e.code}"
        return out
    except Exception as e:
        out["error"] = type(e).__name__
        return out
    out["f"] = feat(t)
    out["description"] = meta(t, "description") or meta(t, "og:description")
    out["og_site"] = meta(t, "og:site_name")
    out["page_author"] = meta(t, "author") or meta(t, "citation_author")
    out["excerpt"] = " ".join(main_text(t).split()[:320])
    out["links"] = out_links(t, final)
    if wanted and any(l[0] in wanted for l in out["links"]):
        from context import contexts
        out["ctx"] = contexts(t, final, {l[0] for l in out["links"] if l[0] in wanted})
    return out


def pages(rows, wanted=frozenset()):
    with cf.ThreadPoolExecutor(WORKERS) as ex:
        return list(ex.map(lambda r: page(r, wanted), rows))
