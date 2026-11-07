# Merge the full backfill into the pilot data: new HN candidates labelled by the LLM, new response chains,
# refreshed HN points, and method stats from the full crawl. Reads issues/issue-0.json, writes issues/pilot.json.
# Usage: python3 site/merge_backfill.py
import collections, copy, glob, gzip, json, os, re, statistics, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
sys.path.insert(0, os.path.join(ROOT, "crawl"))
from common import norm  # noqa: E402

LLM = os.path.join(ROOT, "data", "llm")
KEEP_REL = {"rebuttal", "replication", "extension", "discussion"}
SEP = re.compile(r'\s+(\||—|–|·|\\|::|-)\s+')


def day(ts):
    return time.strftime("%Y-%m-%d", time.gmtime(ts or 0))


def clean_title(t):
    """Same rule as the probe: drop a short trailing site name such as 'Title | Blog'."""
    t = re.sub(r'\s+', ' ', t or '').strip()
    parts = SEP.split(t)
    if len(parts) >= 3:
        last, head = parts[-1].strip(), "".join(parts[:-2]).strip()
        if len(last) <= 34 and len(head) >= 8:
            t = head
    return t[:180]


def pages():
    for p in sorted(glob.glob(os.path.join(ROOT, "data", "hn", "pages", "pages-*.jsonl*"))):
        op = gzip.open if p.endswith(".gz") else open
        with op(p, "rt", encoding="utf-8") as fh:
            for line in fh:
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue


def jl(path):
    return [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]


def main():
    D = copy.deepcopy(json.load(open(os.path.join(HERE, "issues", "issue-0.json"))))
    S = D["stats"]
    stories = {s["objectID"]: s for s in jl(os.path.join(ROOT, "data", "hn", "stories.jsonl"))}
    rows = {}
    for r in pages():
        rows[r["objectID"]] = r
    cst = json.load(open(os.path.join(LLM, "candidates_stats.json")))["stats"]

    # 1. refresh HN points and comments with the later snapshot (they only grow)
    refreshed = 0
    def refresh(x):
        nonlocal refreshed
        s = stories.get(str(x.get("hn_id") or ""))
        if s:
            p, c = s.get("points") or 0, s.get("num_comments") or 0
            if p > (x.get("points") or 0) or c > (x.get("comments") or 0):
                refreshed += 1
            x["points"], x["comments"] = max(p, x.get("points") or 0), max(c, x.get("comments") or 0)
    for r in D["reports"]:
        refresh(r)

    # 2. new HN candidates labelled by the LLM
    inp, lab = {}, {}
    for p in sorted(glob.glob(os.path.join(LLM, "new_items_*.jsonl"))):
        for it in jl(p):
            inp[it["id"]] = it
    for p in sorted(glob.glob(os.path.join(LLM, "out_new_items_*.jsonl"))):
        for it in jl(p):
            lab[it["id"]] = it
    assert set(inp) == set(lab), "every new candidate needs a label"
    have = {norm(r["url"]) for r in D["reports"]}
    added = collections.Counter()
    for oid, it in inp.items():
        l = lab[oid]
        added["labelled_" + l["category"]] += 1
        if l["category"] not in ("research_report", "explainer") or norm(it["url"]) in have:
            continue
        f = rows[oid].get("f") or {}
        ids = [oid] + it.get("dup_ids", [])
        st = [stories[i] for i in ids if i in stories]
        best = max(st, key=lambda s: s.get("points") or 0) if st else {"objectID": oid}
        D["reports"].append({
            "id": oid, "url": it["url"], "domain": it["domain"], "title": clean_title(it["page_title"] or it["hn_title"]) or it["hn_title"],
            "category": l["category"], "topic": l["topic"], "confidence": l["confidence"],
            "interactive": bool(f.get("canvas") or f.get("inter_libs") or f.get("local_scripts", 0) >= 3),
            "repo": f.get("repos", 0) > 0, "arxiv": f.get("arxiv", 0) > 0, "hf": bool(f.get("hf")), "github_io": it["domain"].endswith(".github.io"),
            "cit": bool(f.get("cit_title")), "hn_id": best["objectID"], "points": max((s.get("points") or 0) for s in st) if st else 0,
            "comments": sum((s.get("num_comments") or 0) for s in st), "date": day(min(s["created_at_i"] for s in st)) if st else day(rows[oid].get("created_at_i")),
            "submissions": len(st) or 1, "source": "hn", "summary_zh": l["summary_zh"], "summary_en": l["summary_en"], "origin": "backfill"})
        have.add(norm(it["url"]))
        added[l["category"]] += 1

    # 3. new response chains from the full link scan
    by_url = {norm(r["url"]): r for r in D["reports"]}
    chains = {norm(c["original"]["url"]): c for c in D["chains"]}
    in_chain = {norm(x["url"]) for c in D["chains"] for x in c["responses"]}
    pairs = {p["pair_id"]: p for p in jl(os.path.join(LLM, "pairs_todo_ctx.jsonl"))}
    judged = jl(os.path.join(LLM, "out_pairs_new.jsonl")) if os.path.exists(os.path.join(LLM, "out_pairs_new.jsonl")) else []
    ov_path = os.path.join(LLM, "pair_overrides.json")
    overrides = {(norm(o["source_url"]), norm(o["target_url"])): o["to"] for o in (json.load(open(ov_path)) if os.path.exists(ov_path) else [])}
    n_over = 0
    for j in judged:
        p = pairs[j["pair_id"]]
        k = (norm(p["source_url"]), norm(p["target_url"]))
        if k in overrides and j["relation"] != overrides[k]:
            j["relation"], j["overridden"] = overrides[k], True
            n_over += 1
    rel_count = collections.Counter(j["relation"] for j in judged)
    new_resp = 0
    for j in judged:
        p = pairs[j["pair_id"]]
        if j["relation"] not in KEEP_REL or j["confidence"] == "low":
            continue
        tgt = by_url.get(norm(p["target_url"]))
        if not tgt or norm(p["source_url"]) in in_chain:
            continue
        src_row = rows.get(p["source_id"], {})
        t_ts = (stories.get(str(tgt.get("hn_id") or "")) or {}).get("created_at_i")
        s_ts = src_row.get("created_at_i")
        days = round(((s_ts - t_ts) if (s_ts and t_ts) else (time.mktime(time.strptime(p["source_date"], "%Y-%m-%d")) - time.mktime(time.strptime(tgt["date"], "%Y-%m-%d")))) / 86400, 1)
        s_story = stories.get(p["source_id"], {})
        resp = {"id": p["source_id"], "title": clean_title(p["source_title"] or p["source_hn_title"]) or p["source_hn_title"], "url": p["source_url"],
                "domain": p["source_domain"], "date": p["source_date"], "days_after": max(days, 0), "points": s_story.get("points") or p["source_points"],
                "hn_id": p["source_id"], "relation": j["relation"], "confidence": j["confidence"], "source": "hn",
                "summary_zh": j["summary_zh"], "summary_en": j["summary_en"], "origin": "backfill"}
        k = norm(tgt["url"])
        if k not in chains:
            chains[k] = {"original": {kk: tgt[kk] for kk in tgt if kk != "origin"}, "responses": []}
            D["chains"].append(chains[k])
        chains[k]["responses"].append(resp)
        in_chain.add(norm(p["source_url"]))
        new_resp += 1
    for c in D["chains"]:
        c["responses"].sort(key=lambda x: x["days_after"])
        o = by_url.get(norm(c["original"]["url"]))
        if o:
            c["original"].update({"points": o.get("points"), "comments": o.get("comments")})
    D["chains"].sort(key=lambda c: -(c["original"].get("points") or 0))

    # 4. stats from the full crawl (derived counts are recomputed by build.py's check, so set them consistently here)
    sys.path.insert(0, HERE)
    import build as B
    d = B.derived(D["reports"])
    S["research_all"], S["explainer_all"] = d["research_all"], d["explainer_all"]
    S["topic_matrix"], S["topic_totals"] = d["topic_matrix"], d["topic_totals"]
    for s in ("hn", "fork", "rs"):
        S["sources"][s]["research"], S["sources"][s]["explainer"] = d["per"][s]["research"], d["per"][s]["explainer"]
    S["sources"]["fork"]["re_sites"] = len(d["per"]["fork"]["sites"])
    S["sources"]["rs"]["re_on_hn"] = d["per"]["rs"]["on_hn"]
    S["research"], S["explainer"] = S["sources"]["hn"]["research"], S["sources"]["hn"]["explainer"]
    S["stories"] = S["sources"]["hn"]["scope"] = len(stories)
    S["with_url"] = sum(1 for s in stories.values() if s.get("url"))
    S["first_crawl"], S["parsed"] = cst["pages"], cst["parsed"]
    S["candidates"] = S["sources"]["hn"]["pool"] = 463 + len(inp)          # probe-reviewed plus newly reviewed
    S["link_pages_scanned"], S["link_pages_total"] = cst["parsed"], cst["pages"]
    found = jl(os.path.join(LLM, "pairs_found.jsonl"))
    S["pairs_total"], S["pairs_auto_same"] = len(found), sum(p.get("auto") == "same_source" for p in found)
    S["pairs_llm"] = len(found) - S["pairs_auto_same"]
    S["chains"], S["responses"] = len(D["chains"]), sum(len(c["responses"]) for c in D["chains"])
    op = [c["original"].get("points") or 0 for c in D["chains"] if c["original"].get("hn_id")]
    rp = [x.get("points") or 0 for c in D["chains"] for x in c["responses"] if x.get("hn_id")]
    hi = sorted(({"domain": x["domain"], "points": x["points"]} for c in D["chains"] for x in c["responses"] if (x.get("points") or 0) >= 100), key=lambda h: -h["points"])
    S["median_orig_points"], S["median_resp_points"] = int(statistics.median(op)), int(statistics.median(rp))
    S["resp_hi"] = hi
    rest = [x.get("points") or 0 for c in D["chains"] for x in c["responses"] if x.get("hn_id") and (x.get("points") or 0) < 100]
    S["median_resp_rest"] = int(statistics.median(rest)) if rest else 0
    S["generated"] = time.strftime("%Y-%m-%d")
    S["backfill"] = {"pages": cst["pages"], "errors": cst["error"], "new_candidates": len(inp), "added": dict(added), "pairs_judged": len(judged),
                     "relations": dict(rel_count), "manual_overrides": n_over, "new_responses": new_resp, "points_refreshed": refreshed}
    D["issue"] = {"number": 0, "slug": "pilot", "window": S["window"], "published": S["generated"]}
    json.dump(D, open(os.path.join(HERE, "issues", "pilot.json"), "w"), ensure_ascii=False, indent=1)
    print(json.dumps(S["backfill"], ensure_ascii=False))
    print("reports", len(D["reports"]), "research", S["research_all"], "explainers", S["explainer_all"], "chains", S["chains"], "responses", S["responses"],
          "| median points orig", S["median_orig_points"], "resp", S["median_resp_points"], "| >=100:", [(h["domain"], h["points"]) for h in hi])


if __name__ == "__main__":
    main()
