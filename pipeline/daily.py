# The daily pipeline: collect, check, link, and update site/issues/live.json. The workflow builds and deploys afterwards.
#   1. HN stories of the last 14 days: new ones become candidates, older ones refresh the points of listed posts
#   2. fetch new candidate pages (rule features, excerpt, links, and the text around links to listed posts)
#   3. Rogue Scholar posts every day; academic homepage feeds once a week (conditional GET)
#   4. rule filter for HN pages, then LLM labels with the fallback chain; a second model must confirm
#      every research/explainer label
#   5. links to listed posts: count independent linking sites, and let the LLM judge which links are responses
#   6. recompute counts, save the dataset and state; anything left unlabelled waits in state/queue.json for tomorrow
# Sites and posts in site/optout.json are skipped everywhere and deleted from the dataset first (pipeline/optout.py).
# Usage: python3 pipeline/daily.py [--dry-run] [--no-feeds]   (--dry-run: no LLM calls, nothing saved; --no-feeds: skip the weekly feed poll)
import json, os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "crawl"))
from runlog import Run  # noqa: E402
import sources as S  # noqa: E402
import optout  # noqa: E402
from common import dom, norm  # noqa: E402
from candidates import classify as rule_class  # noqa: E402
from pairs import reg  # noqa: E402

LIVE = os.path.join(ROOT, "site", "issues", "live.json")
STATE = os.path.join(ROOT, "state")
KEEP_CAT = {"research_report", "explainer"}
KEEP_REL = {"rebuttal", "replication", "extension", "discussion"}
SECOND = ["deepseek/deepseek-v4-flash-0731", "nvidia/nemotron-3-ultra-550b-a55b:free"]
FEED_EVERY_DAYS = 7
DRY = "--dry-run" in sys.argv
NO_FEEDS = "--no-feeds" in sys.argv


def jload(p, default):
    try:
        return json.load(open(p, encoding="utf-8"))
    except FileNotFoundError:
        return default


def jsave(p, obj, indent=1):
    tmp = p + ".tmp"
    json.dump(obj, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=indent)
    os.replace(tmp, p)


def day(ts):
    return time.strftime("%Y-%m-%d", time.gmtime(ts))


def clean_title(t):
    import re
    t = re.sub(r"\s+", " ", t or "").strip()
    parts = re.split(r"\s+(\||—|–|·|\\|::|-)\s+", t)
    if len(parts) >= 3 and len(parts[-1].strip()) <= 34 and len("".join(parts[:-2]).strip()) >= 8:
        t = "".join(parts[:-2]).strip()
    return t[:180]


def recompute(D):
    """Counts that build.py checks against the reports (same rules as site/merge_backfill.py)."""
    sys.path.insert(0, os.path.join(ROOT, "site"))
    import build as B
    d, St = B.derived(D["reports"]), D["stats"]
    St["research_all"], St["explainer_all"] = d["research_all"], d["explainer_all"]
    St["topic_matrix"], St["topic_totals"] = d["topic_matrix"], d["topic_totals"]
    for s in ("hn", "fork", "rs"):
        p = d["per"].get(s, {"research": 0, "explainer": 0, "sites": set(), "on_hn": 0})
        St["sources"][s]["research"], St["sources"][s]["explainer"] = p["research"], p["explainer"]
    St["sources"]["fork"]["re_sites"] = len(d["per"].get("fork", {}).get("sites", ()))
    St["sources"]["rs"]["re_on_hn"] = d["per"].get("rs", {}).get("on_hn", 0)
    St["research"], St["explainer"] = St["sources"]["hn"]["research"], St["sources"]["hn"]["explainer"]
    St["chains"], St["responses"] = len(D["chains"]), sum(len(c["responses"]) for c in D["chains"])
    St["generated"] = day(time.time())
    St["window"] = [St["window"][0], St["generated"]]


def to_item(x):
    """Common shape for the LLM: HN pages and supply-side posts."""
    if "objectID" in x:
        return {"id": x["objectID"], "hn_title": x.get("hn_title", ""), "page_title": (x.get("f") or {}).get("title", ""), "domain": dom(x["url"]),
                "url": x["url"], "site": x.get("og_site", ""), "author": x.get("page_author", ""), "description": x.get("description", ""),
                "excerpt": x.get("excerpt", ""), "no_text": not x.get("excerpt")}
    return {"id": x["id"], "hn_title": "", "page_title": x["title"], "domain": x["domain"], "url": x["url"], "site": x.get("site", ""),
            "author": x.get("author", ""), "description": x.get("description", ""), "excerpt": x.get("excerpt", ""), "no_text": not x.get("excerpt")}


def to_report(x, lab, story):
    f = x.get("f") or {}
    links = " ".join(l[0] if isinstance(l, list) else l for l in x.get("links", []))
    rec = {"id": x.get("objectID") or x["id"], "url": x["url"], "domain": dom(x["url"]),
           "title": clean_title(f.get("title") or x.get("title") or x.get("hn_title")) or x.get("hn_title", ""),
           "category": lab["category"], "topic": lab["topic"], "confidence": lab["confidence"], "political": lab.get("political", "none"),
           "interactive": bool(f.get("canvas") or f.get("inter_libs") or f.get("local_scripts", 0) >= 3),
           "repo": f.get("repos", 0) > 0 or "github.com/" in links, "arxiv": f.get("arxiv", 0) > 0 or "arxiv.org/" in links,
           "hf": bool(f.get("hf")), "github_io": dom(x["url"]).endswith(".github.io"), "cit": bool(f.get("cit_title")),
           "hn_id": story["objectID"] if story else None, "points": (story or {}).get("points") or 0, "comments": (story or {}).get("num_comments") or 0,
           "date": day(story["created_at_i"]) if story else x.get("date"), "submissions": 1 if story else 0,
           "source": "hn" if story else x["source"], "summary_zh": lab["summary_zh"], "summary_en": lab["summary_en"],
           "linkers": [], "origin": "daily", "added": day(time.time()), "model": lab.get("model")}
    if x.get("doi"):
        rec["doi"] = x["doi"]
    if x.get("source") == "rs":
        rec["blog"] = x.get("site")
    return rec


def main():
    import llm
    with Run("daily") as run:
        state = jload(os.path.join(STATE, "state.json"), {"hn_since": int(time.time()) - 86400, "rs_since": day(time.time() - 86400),
                                                           "feeds_since": day(time.time() - 7 * 86400), "feed_cache": {}, "runs": 0})
        seen = set(jload(os.path.join(STATE, "seen.json"), []))
        queue = jload(os.path.join(STATE, "queue.json"), [])
        D = jload(LIVE, None)
        if D is None:
            raise SystemExit("site/issues/live.json is missing; run site/init_live.py once")
        blocked = optout.blocker()
        run.count("optout_removed", optout.purge(D, blocked))
        queue = [q for q in queue if not blocked(q["url"])]
        listed = {norm(r["url"]): r for r in D["reports"]}
        now = int(time.time())

        # 1. HN: 14 days of stories; refresh points of listed posts, pick new candidates
        stories, nreq = S.hn_stories(now - 14 * 86400, now)
        run.count("hn_requests", nreq)
        run.count("hn_stories_14d", len(stories))
        run.check("HN returned stories", len(stories) > 1000, f"{len(stories)} stories in 14 days")
        by_hn = {s["objectID"]: s for s in stories}
        for r in D["reports"]:
            s = by_hn.get(str(r.get("hn_id") or ""))
            if s:
                r["points"], r["comments"] = max(r.get("points") or 0, s.get("points") or 0), max(r.get("comments") or 0, s.get("num_comments") or 0)
        for c in D["chains"]:
            for x in [c["original"]] + c["responses"]:
                s = by_hn.get(str(x.get("hn_id") or ""))
                if s:
                    x["points"] = max(x.get("points") or 0, s.get("points") or 0)
        fresh = [s for s in stories if s["created_at_i"] > state["hn_since"] - 3600 and S.eligible(s) and not blocked(s["url"])]
        best = {}
        for s in fresh:                                   # one submission per URL: the one with the most points
            k = norm(s["url"])
            if k and k not in seen and k not in listed and (k not in best or (s["points"] or 0) > (best[k]["points"] or 0)):
                best[k] = s
        run.count("hn_new_stories", len(fresh))
        run.count("hn_new_urls_to_fetch", len(best))

        # 2. pages, with link contexts for links to listed posts
        wanted = frozenset(listed)
        pages = S.pages(list(best.values()), wanted) if best else []
        errs = sum(1 for p in pages if p.get("error"))
        run.count("pages_fetched", len(pages))
        run.count("pages_failed", errs)
        run.check("page error rate <= 40%", not pages or errs <= 0.4 * len(pages), f"{errs} of {len(pages)} failed")
        cands = []
        for p in pages:
            if p.get("f") and rule_class(dom(p.get("final") or p["url"]), p["f"]) in ("self_report", "weak"):
                cands.append(p)
        run.count("hn_candidates", len(cands))

        # 3. supply side
        rs = [x for x in S.rogue_posts(state["rs_since"]) if norm(x["url"]) not in seen and norm(x["url"]) not in listed and not blocked(x["url"])]
        run.count("rs_new_posts", len(rs))
        feeds_new, feed_status = [], {}
        if not NO_FEEDS and day(now - FEED_EVERY_DAYS * 86400) >= state.get("feeds_last", "1970-01-01"):
            feeds = [f for f in jload(os.path.join(ROOT, "sources", "feeds.json"), {"feeds": []})["feeds"] if not blocked(f["feed"]) and not blocked(f["site"])]
            titles = set(jload(os.path.join(ROOT, "sources", "template_titles.json"), {"titles": []})["titles"])
            feeds_new, feed_status, cache = S.feed_posts(feeds, state["feeds_since"], state.get("feed_cache", {}), titles)
            feeds_new = [x for x in feeds_new if norm(x["url"]) not in seen and norm(x["url"]) not in listed and not blocked(x["url"])]
            ok = feed_status.get("ok", 0) + feed_status.get("not_modified", 0)
            run.check("most homepage feeds answered", ok >= 0.6 * max(1, len(feeds)), json.dumps(feed_status))
            if not DRY:
                state["feed_cache"], state["feeds_last"], state["feeds_since"] = cache, day(now), day(now - 86400)
        run.count("feed_new_posts", len(feeds_new))

        # 4. LLM labels (plus a second opinion on borderline research/explainer labels)
        raw = {p["objectID"]: p for p in cands}
        raw.update({x["id"]: x for x in rs + feeds_new})
        for q in queue:                                   # yesterday's leftovers (HN pages keep their objectID)
            raw.setdefault(q.get("objectID") or q["id"], q)
        items = [to_item(x) for x in raw.values()]
        run.count("llm_items", len(items))
        if DRY:
            run.event("daily", "info", "dry run: stopping before LLM calls", items=len(items))
            return
        labels, left = llm.Client(run).label(items) if items else ({}, [])
        # every research/explainer label needs a second model to agree (Qwen is lenient and often says "high")
        border = [it for it in items if it["id"] in labels and labels[it["id"]]["category"] in KEEP_CAT]
        if border:
            second, _ = llm.Client(run, chain=SECOND).label(border)
            for it in border:
                s2 = second.get(it["id"])
                if s2 and s2["category"] not in KEEP_CAT:
                    run.count("second_opinion_rejected")
                    labels[it["id"]]["category"] = s2["category"]
                    labels[it["id"]]["second_opinion"] = s2["category"]
                elif s2:
                    run.count("second_opinion_agreed")
        added = 0
        for iid, lab in labels.items():
            x = raw[iid]
            seen.add(norm(x["url"]))
            if lab["category"] in KEEP_CAT:
                rec = to_report(x, lab, by_hn.get(iid) if "objectID" in x else None)
                D["reports"].append(rec)
                listed[norm(rec["url"])] = rec
                added += 1
                run.count("added_" + rec["source"])
        run.count("posts_added", added)

        # 5. links to listed posts: linking sites for every link, LLM-judged responses for the rest
        todo_pairs = []
        for p in pages:
            src_dom = dom(p.get("final") or p["url"])
            for k, anchor in p.get("links", []):
                t = listed.get(k)
                if not t or reg(src_dom) == reg(t["domain"]):
                    continue
                if reg(src_dom) not in t.setdefault("linkers", []):
                    t["linkers"].append(reg(src_dom))
                    run.count("new_linking_sites")
                todo_pairs.append({"pair_id": f"{p['objectID']}->{t['id']}", "source": p, "target": t, "source_title": (p.get("f") or {}).get("title") or p.get("hn_title", ""),
                                   "source_domain": src_dom, "source_date": day(p.get("created_at_i") or now), "source_site": p.get("og_site", ""),
                                   "source_excerpt": p.get("excerpt", ""), "anchor": anchor, "link_context": (p.get("ctx") or {}).get(k, ""),
                                   "target_title": t["title"], "target_domain": t["domain"], "target_date": t.get("date", "")})
        for x in rs + feeds_new:                          # supply-side posts: count them as linking sites too
            for l in x.get("links", []):
                t = listed.get(norm(l))
                if t and reg(x["domain"]) != reg(t["domain"]) and reg(x["domain"]) not in t.setdefault("linkers", []):
                    t["linkers"].append(reg(x["domain"]))
                    run.count("new_linking_sites")
        run.count("pairs_to_judge", len(todo_pairs))
        if todo_pairs:
            rel, _ = llm.Client(run, task="pairs").label(todo_pairs)
            chains = {norm(c["original"]["url"]): c for c in D["chains"]}
            for q in todo_pairs:
                j = rel.get(q["pair_id"])
                if not j or j["relation"] not in KEEP_REL or j["confidence"] == "low":
                    continue
                t, p = q["target"], q["source"]
                k = norm(t["url"])
                c = chains.get(k)
                if c is None:
                    c = chains[k] = {"original": {kk: v for kk, v in t.items() if kk not in ("linkers", "origin", "added", "model")}, "responses": []}
                    D["chains"].append(c)
                if any(norm(x["url"]) == norm(p["url"]) for x in c["responses"]):
                    continue
                t_ts = (by_hn.get(str(t.get("hn_id") or "")) or {}).get("created_at_i")
                c["responses"].append({"id": p["objectID"], "title": clean_title(q["source_title"]) or p.get("hn_title", ""), "url": p.get("final") or p["url"],
                                       "domain": q["source_domain"], "date": q["source_date"], "days_after": round(max(0, (p["created_at_i"] - t_ts) / 86400), 1) if t_ts else 0,
                                       "points": p.get("points") or 0, "hn_id": p["objectID"], "relation": j["relation"], "confidence": j["confidence"],
                                       "source": "hn", "summary_zh": j["summary_zh"], "summary_en": j["summary_en"], "origin": "daily"})
                run.count("responses_added")

        # 6. save
        recompute(D)
        state["hn_since"] = max([s["created_at_i"] for s in fresh] + [state["hn_since"]])
        state["rs_since"] = day(now - 86400)
        state["runs"] = state.get("runs", 0) + 1
        state["last_run"] = day(now)                     # backup runs in the workflow skip when this is today
        jsave(LIVE, D)
        jsave(os.path.join(STATE, "state.json"), state)
        jsave(os.path.join(STATE, "seen.json"), sorted(seen), indent=0)
        jsave(os.path.join(STATE, "queue.json"), [raw[it["id"]] for it in left])
        run.check("no item left unlabelled", not left, f"{len(left)} wait in state/queue.json")
        run.check("daily cost <= $0.50", run.cost <= 0.5, f"${run.cost:.4f}")


if __name__ == "__main__":
    main()
