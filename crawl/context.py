# Refetch only the source pages of unjudged response pairs and cut the text around each link, for the LLM to judge.
# Throttled with the shared Throttle (data/ctl/rpm, data/ctl/PAUSE). Usage: RPM=60 python3 crawl/context.py
import concurrent.futures as cf, html, json, os, re, sys, urllib.request
from collections import defaultdict
from urllib.parse import urljoin

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from common import A_RE, CTX, UA, Throttle, norm, strip  # noqa: E402

ROOT = os.path.join(HERE, "..")
LLM = os.path.join(ROOT, "data", "llm")
T = Throttle(os.path.join(ROOT, "data", "ctl"), default_rpm=float(os.environ.get("RPM", "60")))
MARK_A, MARK_B = "⟦LINK⟧", "⟦/LINK⟧"


def fetch(url):
    T.wait()
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=20, context=CTX) as r:
        return r.geturl(), r.read(4_000_000).decode("utf-8", "ignore")


def contexts(page, final, wanted, width=420):
    """For each wanted target key, the plain text around the first link to it, with the link marked."""
    body = re.sub(r"<(script|style|noscript)\b.*?</\1>", " ", page, flags=re.S | re.I)
    out = {}
    for m in A_RE.finditer(body):
        try:
            k = norm(urljoin(final, html.unescape(m.group(1))))
        except Exception:
            continue
        if k in wanted and k not in out:
            before = strip(body[max(0, m.start() - 6000):m.start()])[-width:]
            after = strip(body[m.end():m.end() + 6000])[:width]
            out[k] = before + " " + MARK_A + strip(m.group(2)) + MARK_B + " " + after
    return out


def main():
    todo = [json.loads(l) for l in open(os.path.join(LLM, "pairs_todo.jsonl"))]
    by_src = defaultdict(list)
    for p in todo:
        by_src[p["source_url"]].append(p)

    def work(src):
        try:
            final, page = fetch(src)
            ctx = contexts(page, final, {norm(p["target_url"]) for p in by_src[src]})
            return src, ctx, None
        except Exception as e:
            return src, {}, type(e).__name__

    got, errs = 0, {}
    with cf.ThreadPoolExecutor(4) as ex:
        for src, ctx, err in ex.map(work, list(by_src)):
            if err:
                errs[err] = errs.get(err, 0) + 1
            for p in by_src[src]:
                c = ctx.get(norm(p["target_url"]))
                p["link_context"] = c or ""
                p["context_status"] = "ok" if c else (err or "link_not_found")
                got += bool(c)
    with open(os.path.join(LLM, "pairs_todo_ctx.jsonl"), "w", encoding="utf-8") as fh:
        for p in todo:
            fh.write(json.dumps(p, ensure_ascii=False) + "\n")
    print("pairs", len(todo), "sources", len(by_src), "with context", got, "fetch errors", errs)


if __name__ == "__main__":
    main()
