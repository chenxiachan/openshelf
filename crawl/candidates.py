# Rule filter over every backfilled HN page (same rules as the probe's analyze.py), then pick the candidates
# the probe never sent to the LLM. Output: data/llm/new_items_N.jsonl batches + data/llm/candidates_stats.json
# Usage: python3 crawl/candidates.py [n_batches]
import collections, glob, gzip, json, os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
PROBE = os.path.join(ROOT, "..", "probe_hn_2026-11-06")
OUT = os.path.join(ROOT, "data", "llm")
os.makedirs(OUT, exist_ok=True)

PUB = set("""nature.com science.org sciencedirect.com link.springer.com springer.com onlinelibrary.wiley.com pnas.org cell.com dl.acm.org
acm.org ieeexplore.ieee.org journals.plos.org plos.org frontiersin.org mdpi.com biorxiv.org medrxiv.org openreview.net papers.ssrn.com
ssrn.com researchgate.net academic.oup.com jstor.org tandfonline.com journals.sagepub.com ncbi.nlm.nih.gov pmc.ncbi.nlm.nih.gov
pubmed.ncbi.nlm.nih.gov journals.aps.org iopscience.iop.org cambridge.org elifesciences.org royalsocietypublishing.org semanticscholar.org
aclanthology.org proceedings.mlr.press proceedings.neurips.cc papers.nips.cc neurips.cc jmlr.org bmj.com thelancet.com nejm.org jamanetwork.com
osf.io zenodo.org hal.science chemrxiv.org psyarxiv.com europepmc.org annualreviews.org pubs.acs.org pubs.rsc.org degruyter.com karger.com
usenix.org aeaweb.org nber.org journals.uchicago.edu physics.aps.org advances.sciencemag.org science.sciencemag.org academic.oup.com
direct.mit.edu muse.jhu.edu""".split())


def dom(u):
    return u.split("/")[2].lower().removeprefix("www.") if "://" in u else ""


def is_pub(d):
    return d in PUB or any(d.endswith("." + p) for p in PUB)


def key(u):
    return u.split("#")[0].rstrip("/").lower().replace("://www.", "://")


def classify(d, f):
    """Same classes as the probe: formal venue, self_report (scholarly markup), weak (cites arXiv/DOI in a long text), other."""
    if is_pub(d) or (f.get("cit_venue") and not d.endswith(".github.io")):
        return "formal"
    if f.get("cit_title") or f.get("bibtex") or f.get("distill") or f.get("projpage"):
        return "self_report"
    if (f.get("arxiv", 0) >= 1 or f.get("doi", 0) >= 3) and f.get("words", 0) >= 1500:
        return "weak"
    return "other"


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
    nb = int(sys.argv[1]) if len(sys.argv) > 1 else 4
    stats = collections.Counter()
    rows = {}
    for r in pages():
        stats["records"] += 1
        rows[r["objectID"]] = r          # a later record (retry pass) replaces an earlier one
    stats["pages"] = len(rows)
    cls = {}
    for oid, r in rows.items():
        if r.get("error"):
            stats["error"] += 1
            continue
        if not r.get("f"):
            stats["non_html"] += 1
            continue
        stats["parsed"] += 1
        cls[oid] = classify(dom(r.get("final") or r["url"]), r["f"])
    stats.update({"cls_" + k: v for k, v in collections.Counter(cls.values()).items()})

    # what the probe already sent to the LLM (any outcome counts as reviewed)
    seen = set()
    for p in glob.glob(os.path.join(PROBE, "llm", "items_*.jsonl")):
        for line in open(p):
            seen.add(key(json.loads(line)["url"]))
    # group candidates by URL, keep the submission with the most points
    groups = collections.defaultdict(list)
    for oid, c in cls.items():
        if c in ("self_report", "weak"):
            groups[key(rows[oid]["url"])].append(oid)
    stats["candidates_dedup"] = len(groups)
    new = []
    for k, ids in groups.items():
        if k in seen:
            stats["candidates_already_reviewed"] += 1
            continue
        ids.sort(key=lambda i: -(rows[i].get("points") or 0))
        r = rows[ids[0]]
        new.append({"id": r["objectID"], "hn_title": r.get("hn_title", ""), "page_title": r["f"].get("title", ""), "domain": dom(r["url"]),
                    "url": r["url"], "site": r.get("og_site", ""), "author": r.get("page_author", ""), "description": r.get("description", ""),
                    "excerpt": r.get("excerpt", ""), "no_text": not r.get("excerpt"), "dup_ids": ids[1:], "cls": cls[r["objectID"]],
                    "points": r.get("points") or 0, "date": time.strftime("%Y-%m-%d", time.gmtime(r.get("created_at_i") or 0))})
    new.sort(key=lambda it: it["id"])
    stats["candidates_new"] = len(new)
    stats["new_self_report"] = sum(it["cls"] == "self_report" for it in new)
    stats["new_weak"] = sum(it["cls"] == "weak" for it in new)
    stats["new_points_ge3"] = sum(it["points"] >= 3 for it in new)
    size = -(-len(new) // nb) if new else 0
    for i in range(nb):
        with open(os.path.join(OUT, f"new_items_{i + 1}.jsonl"), "w", encoding="utf-8") as fh:
            for it in new[i * size:(i + 1) * size]:
                fh.write(json.dumps({k: v for k, v in it.items() if k not in ("cls", "points", "date")}, ensure_ascii=False) + "\n")
    json.dump({"stats": dict(stats), "new": [{k: it[k] for k in ("id", "url", "cls", "points", "date")} for it in new]},
              open(os.path.join(OUT, "candidates_stats.json"), "w"), indent=1)
    print(json.dumps(dict(stats), indent=1))
    print("batches of", size, "->", OUT)


if __name__ == "__main__":
    main()
