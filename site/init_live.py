# One-off: turn the pilot data into the live dataset the daily pipeline appends to.
#   site/issues/live.json   pilot.json plus, per post, "linkers": independent sites that link to it (from both link scans)
#   state/seen.json         URL keys already labelled by an LLM (kept or not), so the pipeline never pays for them twice
#   state/state.json        where each source stops: newest HN story time, Rogue Scholar and feed dates
# Needs the local crawl data (data/, ../probe_hn_2026-11-06); run once on the maintainer's machine.
import glob, json, os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
sys.path.insert(0, os.path.join(ROOT, "crawl"))
from common import norm  # noqa: E402
from pairs import reg  # noqa: E402

PROBE = os.path.join(ROOT, "..", "probe_hn_2026-11-06", "llm")


def jl(p):
    return [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]


D = json.load(open(os.path.join(HERE, "issues", "pilot.json")))
by_key = {norm(r["url"]): r for r in D["reports"]}
by_hn = {str(r.get("hn_id")): norm(r["url"]) for r in D["reports"] if r.get("hn_id")}
canon = json.load(open(os.path.join(PROBE, "canon.json")))
linkers = {}


def add(key, src, tgt):
    if key in by_key and src and reg(src) != reg(tgt):
        linkers.setdefault(key, set()).add(reg(src))


for q in jl(os.path.join(ROOT, "data", "llm", "pairs_found.jsonl")):
    add(norm(q["target_url"]), q["source_domain"], q["target_domain"])
for q in jl(os.path.join(PROBE, "pairs_all.jsonl")):
    t = canon.get(q["target_id"], q["target_id"])
    add(by_hn.get(t) or by_hn.get(q["target_id"]), q["source_domain"], q["target_domain"])
for c in D["chains"]:
    for x in c["responses"]:
        add(norm(c["original"]["url"]), x["domain"], c["original"]["domain"])
for r in D["reports"]:                    # several reports can share a URL key; give every one its linkers
    r["linkers"] = sorted(linkers.get(norm(r["url"]), ()))

seen = set(by_key)
for p in glob.glob(os.path.join(PROBE, "items_*.jsonl")) + glob.glob(os.path.join(ROOT, "data", "llm", "new_items_*.jsonl")):
    for it in jl(p):
        seen.add(norm(it["url"]))
stories = jl(os.path.join(ROOT, "data", "hn", "stories.jsonl"))
today = time.strftime("%Y-%m-%d", time.gmtime())
D["issue"] = {"number": 0, "slug": "openshelf", "window": D["stats"]["window"], "published": today}
os.makedirs(os.path.join(ROOT, "state"), exist_ok=True)
json.dump(D, open(os.path.join(HERE, "issues", "live.json"), "w"), ensure_ascii=False, indent=1)
json.dump(sorted(k for k in seen if k), open(os.path.join(ROOT, "state", "seen.json"), "w"), indent=0)
state = {"hn_since": max(s["created_at_i"] for s in stories), "rs_since": today, "feeds_since": today, "feed_cache": {}, "runs": 0}
json.dump(state, open(os.path.join(ROOT, "state", "state.json"), "w"), indent=1)
print("live.json posts", len(D["reports"]), "with linkers", sum(1 for r in D["reports"] if r["linkers"]),
      "| seen keys", len(seen), "| hn_since", time.strftime("%Y-%m-%d %H:%M", time.gmtime(state["hn_since"])))
