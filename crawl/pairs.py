# Find response candidates: backfilled HN pages whose outbound links point at a listed post or a new candidate.
# Output: data/llm/pairs_found.jsonl (every match) and data/llm/pairs_todo.jsonl (matches the probe never judged, minus same-organisation links).
# Usage: python3 crawl/pairs.py
import collections, glob, gzip, json, os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from common import norm, dom  # noqa: E402

ROOT = os.path.join(HERE, "..")
PROBE = os.path.join(ROOT, "..", "probe_hn_2026-11-06")
LLM = os.path.join(ROOT, "data", "llm")


def reg(host):
    """Registrable part of a host, so blog.x.com and x.com count as the same organisation (github.io kept per user)."""
    if host.endswith(".github.io"):
        return host
    parts = host.split(".")
    return ".".join(parts[-3:] if len(parts) > 2 and parts[-2] in ("co", "ac", "com", "org") else parts[-2:])


def day(ts):
    return time.strftime("%Y-%m-%d", time.gmtime(ts or 0))


def pages():
    for p in sorted(glob.glob(os.path.join(ROOT, "data", "hn", "pages", "pages-*.jsonl*"))):
        op = gzip.open if p.endswith(".gz") else open
        with op(p, "rt", encoding="utf-8") as fh:
            for line in fh:
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue


def main():
    issue = json.load(open(os.path.join(ROOT, "site", "issues", "issue-0.json")))
    targets = {}
    for r in issue["reports"]:
        targets[norm(r["url"])] = {"tid": r.get("hn_id") or r["id"], "url": r["url"], "title": r["title"], "domain": r["domain"], "date": r["date"], "kind": "listed"}
    for p in sorted(glob.glob(os.path.join(LLM, "new_items_*.jsonl"))):
        for line in open(p):
            it = json.loads(line)
            k = norm(it["url"])
            if k and k not in targets:
                targets[k] = {"tid": it["id"], "url": it["url"], "title": it["page_title"] or it["hn_title"], "domain": it["domain"], "date": None, "kind": "new"}
    rows = {}
    for r in pages():
        rows[r["objectID"]] = r
    for t in targets.values():
        if t["date"] is None and t["tid"] in rows:
            t["date"] = day(rows[t["tid"]].get("created_at_i"))
    judged = set()
    for line in open(os.path.join(PROBE, "llm", "pairs_all.jsonl")):
        judged.add(json.loads(line)["pair_id"])

    found, stats = [], collections.Counter()
    per_source = collections.Counter()
    for sid, r in rows.items():
        if not r.get("links"):
            continue
        stats["pages_with_links"] += 1
        sdate, sdom = day(r.get("created_at_i")), dom(r.get("final") or r["url"])
        for n, anchor in r["links"]:
            t = targets.get(n)
            if not t or t["tid"] == sid or norm(r["url"]) == n:
                continue
            if t["date"] and sdate < t["date"]:
                stats["skip_source_older"] += 1
                continue
            pid = f"{sid}->{t['tid']}"
            p = {"pair_id": pid, "source_id": sid, "source_url": r.get("final") or r["url"], "source_title": r.get("f", {}).get("title") or r.get("hn_title", ""),
                 "source_hn_title": r.get("hn_title", ""), "source_domain": sdom, "source_date": sdate, "source_points": r.get("points") or 0,
                 "source_site": r.get("og_site", ""), "source_author": r.get("page_author", ""), "source_excerpt": r.get("excerpt", ""), "anchor": anchor,
                 "target_id": t["tid"], "target_url": t["url"], "target_title": t["title"], "target_domain": t["domain"], "target_date": t["date"], "target_kind": t["kind"]}
            if reg(sdom) == reg(t["domain"]):
                p["auto"] = "same_source"
            elif pid in judged:
                p["auto"] = "judged_in_probe"
            found.append(p)
            per_source[sid] += 1
    for p in found:
        p["n_targets_linked_by_source"] = per_source[p["source_id"]]
    todo = [p for p in found if "auto" not in p]
    stats.update({"pairs_found": len(found), "same_source": sum(p.get("auto") == "same_source" for p in found),
                  "judged_in_probe": sum(p.get("auto") == "judged_in_probe" for p in found), "todo": len(todo),
                  "todo_to_listed": sum(p["target_kind"] == "listed" for p in todo), "todo_to_new": sum(p["target_kind"] == "new" for p in todo),
                  "todo_sources": len({p["source_id"] for p in todo}), "todo_from_roundups(>=5 targets)": sum(p["n_targets_linked_by_source"] >= 5 for p in todo),
                  "targets": len(targets)})
    with open(os.path.join(LLM, "pairs_found.jsonl"), "w", encoding="utf-8") as fh:
        for p in found:
            fh.write(json.dumps(p, ensure_ascii=False) + "\n")
    with open(os.path.join(LLM, "pairs_todo.jsonl"), "w", encoding="utf-8") as fh:
        for p in todo:
            fh.write(json.dumps(p, ensure_ascii=False) + "\n")
    print(json.dumps(dict(stats), indent=1))
    top = collections.Counter(p["target_title"][:60] for p in todo).most_common(12)
    print("most linked targets (todo):", top)


if __name__ == "__main__":
    main()
