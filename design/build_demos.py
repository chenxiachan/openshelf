# Data for the Openshelf design demos (design/demo-b, design/demo-c). Reuses the site's data and editorial rules.
# Usage: python3 design/build_demos.py      then open http://127.0.0.1:8766/design/demo-b/ and /design/demo-c/
import hashlib, json, os, sys
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "site"))
import build as B  # noqa: E402  (the site build; importing it only loads config and data)

D = B.apply_editorial(B.ISSUE)
en, zh = B.stream(D, "en"), B.stream(D, "zh")
items = []
for a, b in zip(en["items"], zh["items"]):
    assert a["url"] == b["url"]
    x = {k: v for k, v in a.items() if k not in ("summary", "resp")}
    x["id"] = hashlib.sha1(a["url"].encode()).hexdigest()[:7]
    x["s"] = {"en": a["summary"], "zh": b["summary"]}
    x["resp"] = []
    for ra, rb in zip(a["resp"], b["resp"]):
        r = {k: v for k, v in ra.items() if k != "summary"}
        r["id"] = hashlib.sha1(ra["url"].encode()).hexdigest()[:7]
        r["s"] = {"en": ra["summary"], "zh": rb["summary"]}
        x["resp"].append(r)
    items.append(x)

# independent sites linking to each post (responses included, same-organisation links excluded), from both link scans
sys.path.insert(0, os.path.join(HERE, "..", "crawl"))
from pairs import reg  # noqa: E402
PROBE = os.path.join(HERE, "..", "..", "probe_hn_2026-11-06", "llm")
def ukey(u): return u.split("#")[0].rstrip("/").lower().replace("://www.", "://")
by_hn = {str(r.get("hn_id")): ukey(r["url"]) for r in D["reports"] if r.get("hn_id")}
canon = json.load(open(os.path.join(PROBE, "canon.json")))
linkers = {}
def add(target_key, src_dom, tgt_dom):
    if target_key and src_dom and reg(src_dom) != reg(tgt_dom):
        linkers.setdefault(target_key, set()).add(reg(src_dom))
for l in open(os.path.join(HERE, "..", "data", "llm", "pairs_found.jsonl"), encoding="utf-8"):
    q = json.loads(l); add(ukey(q["target_url"]), q["source_domain"], q["target_domain"])
for l in open(os.path.join(PROBE, "pairs_all.jsonl"), encoding="utf-8"):
    q = json.loads(l); t = canon.get(q["target_id"], q["target_id"]); add(by_hn.get(t) or by_hn.get(q["target_id"]), q["source_domain"], q["target_domain"])
for x in items:
    for r in x["resp"]:
        add(ukey(x["url"]), r["domain"], x["domain"])
    x["links"] = len(linkers.get(ukey(x["url"]), ()))

latest = max(i["date"] for i in items)
week_start = (date.fromisoformat(latest) - timedelta(days=6)).isoformat()
first = {}
for i in items:
    first.setdefault(i["domain"], {"date": i["date"], "n": 0})
    first[i["domain"]]["date"] = min(first[i["domain"]]["date"], i["date"])
    first[i["domain"]]["n"] += 1
new_sites = sorted(({"domain": d, **v} for d, v in first.items() if v["date"] >= week_start), key=lambda v: (v["date"], v["domain"]), reverse=True)
channels = {s: sum(any(v["s"] == s for v in i["via"]) for i in items) for s in B.VIA_ORDER}

maps = {lang: {k: B.I18N[lang][k] for k in ("topic", "rel", "src")} for lang in ("en", "zh")}
out = {"items": items, "figures": en["figures"], "latest": latest, "week_start": week_start, "new_sites": new_sites,
       "channels": channels, "window": D["stats"]["window"], "maps": maps}
js = "window.SHELF = " + json.dumps(out, ensure_ascii=False, separators=(",", ":")) + ";\n"
OUT_JS = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "demo-data.js")
open(OUT_JS, "w", encoding="utf-8").write(js)
print(os.path.basename(OUT_JS), "| linked posts", sum(1 for i in items if i["links"]), "max", max(i["links"] for i in items), "|", "demo-data.js", len(items), "items,", sum(len(i["resp"]) for i in items), "responses,", len(new_sites), "sites first seen since", week_start,
      "| channels", channels, "|", round(len(js) / 1024), "KB")
