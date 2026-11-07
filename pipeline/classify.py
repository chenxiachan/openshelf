# Label new candidates with the fallback chain and log the run.
# Usage: python3 pipeline/classify.py OUT.jsonl IN.jsonl [IN.jsonl ...]
#   Items still unlabelled after the whole chain are appended to data/queue/unlabelled.jsonl and picked up next time.
# Health checks (a failure raises an alert): fallback share <= 30%, no item left unlabelled, cost per run <= $0.20.
import json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from runlog import ROOT, Run  # noqa: E402
from llm import Client  # noqa: E402

QUEUE = os.path.join(ROOT, "data", "queue", "unlabelled.jsonl")
MAX_COST = float(os.environ.get("PIPELINE_MAX_COST_PER_RUN", "0.20"))


def read(paths):
    seen, out = set(), []
    for p in paths:
        if not os.path.exists(p):
            continue
        for line in open(p, encoding="utf-8"):
            if line.strip():
                it = json.loads(line)
                if str(it["id"]) not in seen:
                    seen.add(str(it["id"]))
                    out.append(it)
    return out


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: classify.py OUT.jsonl [IN.jsonl ...]   (with no input it only works through the carried-over queue)")
    out_path, inputs = sys.argv[1], sys.argv[2:]
    with Run("classify") as run:
        carried = read([QUEUE])
        items = carried + [it for it in read(inputs) if str(it["id"]) not in {str(c["id"]) for c in carried}]
        run.count("items_in", len(items))
        run.count("items_carried_over", len(carried))
        if not items:
            run.event("classify", "info", "nothing to label")
            return
        labels, left = Client(run).label(items)
        with open(out_path, "a", encoding="utf-8") as fh:
            for it in items:
                if str(it["id"]) in labels:
                    fh.write(json.dumps(labels[str(it["id"])], ensure_ascii=False) + "\n")
        os.makedirs(os.path.dirname(QUEUE), exist_ok=True)
        with open(QUEUE, "w", encoding="utf-8") as fh:          # the queue now holds only what is still unlabelled
            for it in left:
                fh.write(json.dumps(it, ensure_ascii=False) + "\n")
        fb = run.counts.get("llm_items_by_fallback", 0)
        run.check("fallback share <= 30%", fb <= 0.3 * max(1, len(labels)), f"{fb} of {len(labels)} labelled by a fallback model")
        run.check("no item left unlabelled", not left, f"{len(left)} carried over to the next run")
        run.check(f"cost per run <= ${MAX_COST:.2f}", run.cost <= MAX_COST, f"${run.cost:.4f}")


if __name__ == "__main__":
    main()
