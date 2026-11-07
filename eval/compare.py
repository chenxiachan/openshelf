# Fair comparison of every model in data/eval/*.jsonl on the items all of them answered, plus side-by-side summaries.
# Usage: python3 eval/compare.py [n_samples]
import glob, json, os, random, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from openrouter_eval import CATS, load  # noqa: E402

OUT = os.path.join(HERE, "..", "data", "eval")


def four(c):
    return c if c in ("research_report", "explainer", "release") else "other"


def main():
    n_samples = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    items, son = load()
    runs = {}
    for p in sorted(glob.glob(os.path.join(OUT, "*.jsonl"))):
        got = {}
        for l in open(p, encoding="utf-8"):
            d = json.loads(l)
            if d.get("category") in CATS:
                got[d["id"]] = d
        runs[os.path.basename(p)[:-6]] = got
    runs = {k: v for k, v in runs.items() if len(v) >= 100}
    common = [it for it in items if all(it["id"] in g for g in runs.values())]
    print(f"items answered by all {len(runs)} models: {len(common)} of {len(items)}\n")
    rows = [("sonnet (pilot)", {it["id"]: son[it["id"]] for it in items})] + list(runs.items())
    print(f"{'model':45} {'answered':>8} {'4-class':>8} {'research?':>9} {'=sonnet':>8} {'topic=':>7}")
    for name, got in rows:
        c = common
        ex = sum(four(got[it["id"]]["category"]) == it["_manual"] for it in c) / len(c)
        bi = sum((got[it["id"]]["category"] == "research_report") == (it["_manual"] == "research_report") for it in c) / len(c)
        se = sum(got[it["id"]]["category"] == son[it["id"]]["category"] for it in c) / len(c)
        tp = sum(got[it["id"]].get("topic") == son[it["id"]].get("topic") for it in c) / len(c)
        print(f"{name:45} {len(got):>8} {ex:>8.0%} {bi:>9.0%} {se:>8.0%} {tp:>7.0%}")
    random.seed(11)
    print("\n--- sample summaries (same items) ---")
    for it in random.sample(common, min(n_samples, len(common))):
        print(f"\n# {(it['page_title'] or it['hn_title'])[:90]}  [{it['_manual']}]")
        print(f"  sonnet zh: {son[it['id']].get('summary_zh', '')}")
        for name, got in runs.items():
            g = got[it["id"]]
            print(f"  {name[:28]:28} {g['category'][:8]:8} zh: {g.get('summary_zh', '')}")
            print(f"  {'':28} {'':8} en: {g.get('summary_en', '')}")


if __name__ == "__main__":
    main()
