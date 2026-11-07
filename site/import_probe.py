# One-off: turn the probe page data (data_v2.json, Chinese summaries) plus the English summaries into issues/issue-0.json.
import json, os, glob
HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "..", "probe_hn_2026-11-06", "page", "data_v2.json")
D = json.load(open(SRC))
en = {}
for p in sorted(glob.glob(os.path.join(HERE, "i18n_work", "sum_out_*.jsonl"))):
    for l in open(p):
        x = json.loads(l)
        en[x["key"]] = x["summary_en"]


def bilingual(r):
    zh = r.pop("summary", "")
    r["summary_zh"] = zh
    r["summary_en"] = en.get(r["url"], "")
    if zh and not r["summary_en"]:
        print("missing English summary:", r["url"])
    return r


D["reports"] = [bilingual(dict(r)) for r in D["reports"]]
for c in D["chains"]:
    c["original"] = bilingual(dict(c["original"]))
    c["responses"] = [bilingual(dict(r)) for r in c["responses"]]
D["issue"] = {"number": 0, "slug": "issue-0", "window": D["stats"]["window"], "published": "2026-11-06"}
os.makedirs(os.path.join(HERE, "issues"), exist_ok=True)
out = os.path.join(HERE, "issues", "issue-0.json")
json.dump(D, open(out, "w"), ensure_ascii=False, indent=1)
print("wrote", out, len(D["reports"]), "reports,", sum(len(c["responses"]) for c in D["chains"]), "responses, english summaries", len(en))
