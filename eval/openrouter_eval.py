# Compare free OpenRouter models with the pilot's labels: 170 HN candidates that have a manual title label and a Sonnet label.
# Same instructions as prompts/classify.md, 20 items per request, sequential and slow (free tier: 20 requests/min, 50/day without credits).
# Key: env OPENROUTER_API_KEY, else the macOS Keychain item (account "openshelf", service "openrouter-api-key"). Never written to disk.
# Usage: python3 eval/openrouter_eval.py MODEL [MODEL ...]
import collections, csv, glob, json, os, re, ssl, subprocess, sys, time, urllib.error, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
PROBE = os.path.join(ROOT, "..", "probe_hn_2026-11-06")
OUT = os.path.join(ROOT, "data", "eval")
os.makedirs(OUT, exist_ok=True)
CTX = ssl.create_default_context(cafile="/etc/ssl/cert.pem")
BATCH, GAP = int(os.environ.get("EVAL_BATCH", "20")), 5.0
CATS = {"research_report", "explainer", "release", "commentary", "other"}
MAN = {"R": "research_report", "E": "explainer", "M": "release", "X": "other"}


def key():
    k = os.environ.get("OPENROUTER_API_KEY")
    if not k:
        k = subprocess.run(["security", "find-generic-password", "-a", "openshelf", "-s", "openrouter-api-key", "-w"],
                           capture_output=True, text=True).stdout.strip()
    if not k:
        sys.exit("no OpenRouter key in env or Keychain")
    return k


def instructions():
    t = open(os.path.join(ROOT, "prompts", "classify.md"), encoding="utf-8").read()
    body = t.split("---", 1)[1].strip()
    return body + ('\n\nReturn only a JSON object of the form {"items": [ ... ]} with one object per input item, in input order, '
                   "each with the fields id, category, topic, political, summary_zh, summary_en, confidence. No other text.")


def load():
    man = {}
    for r in csv.DictReader(open(os.path.join(PROBE, "data", "labeled.csv"))):
        man.setdefault(r["url"].split("#")[0].rstrip("/"), r["label"])
    items, son = [], {}
    for p in sorted(glob.glob(os.path.join(PROBE, "llm", "items_*.jsonl"))):
        for l in open(p):
            d = json.loads(l)
            k = d["url"].split("#")[0].rstrip("/")
            if k in man:
                d["_manual"] = MAN[man[k]]
                items.append(d)
    for p in sorted(glob.glob(os.path.join(PROBE, "llm", "out_items_*.jsonl"))):
        for l in open(p):
            if l.strip():
                d = json.loads(l)
                son[d["id"]] = d
    return items, son


NO_JSON_MODE = set()


def call(model, msgs, k, tries=4):
    for a in range(tries):
        req_body = {"model": model, "messages": msgs, "temperature": 0, "max_tokens": int(os.environ.get("EVAL_MAX_TOKENS", "9000"))}
        if model not in NO_JSON_MODE:
            req_body["response_format"] = {"type": "json_object"}
        body = json.dumps(req_body).encode()
        req = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions", data=body,
                                     headers={"Authorization": "Bearer " + k, "Content-Type": "application/json", "X-Title": "openshelf-eval"})
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=300, context=CTX) as r:
                j = json.load(r)
            if "choices" not in j:
                raise RuntimeError(str(j.get("error", j))[:200])
            return j, time.time() - t0
        except urllib.error.HTTPError as e:
            msg = e.read().decode("utf-8", "ignore")[:300]
            print(f"   http {e.code}: {msg}", flush=True)
            if e.code == 400 and "response_format" in msg and model not in NO_JSON_MODE:
                NO_JSON_MODE.add(model)       # some providers reject JSON mode; the prompt already asks for JSON only
                continue
            if e.code in (400, 401, 402, 403, 404):
                return {"error": f"http {e.code}: {msg}"}, time.time() - t0
            time.sleep(20 * (a + 1))
        except Exception as e:
            print(f"   {type(e).__name__}: {str(e)[:200]}", flush=True)
            time.sleep(10 * (a + 1))
    return {"error": "gave up"}, 0.0


def parse(text):
    text = re.sub(r"^```(?:json)?|```$", "", (text or "").strip(), flags=re.M).strip()
    try:
        return json.loads(text).get("items", [])
    except Exception:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            try:
                return json.loads(m.group(0)).get("items", [])
            except Exception:
                return []
    return []


def run(model, items, son, k):
    slug = re.sub(r"[^a-z0-9]+", "-", model.lower()).strip("-")
    path = os.path.join(OUT, slug + ".jsonl")
    got = {}
    if os.path.exists(path):                 # resume: keep earlier answers
        for l in open(path):
            d = json.loads(l)
            got[d["id"]] = d
    sysmsg = instructions()
    todo = [it for it in items if it["id"] not in got]
    stats = collections.Counter()
    secs = []
    for i in range(0, len(todo), BATCH):
        chunk = todo[i:i + BATCH]
        payload = [{f: it.get(f, "") for f in ("id", "hn_title", "page_title", "domain", "url", "site", "author", "description", "excerpt", "no_text")} for it in chunk]
        j, dt = call(model, [{"role": "system", "content": sysmsg}, {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}], k)
        stats["requests"] += 1
        if "error" in j:
            stats["failed_requests"] += 1
            print(f"  [{model}] batch {i // BATCH + 1}: {j['error'][:120]}", flush=True)
            if j["error"].startswith("http 4"):
                break
            continue
        secs.append(dt)
        u = j.get("usage") or {}
        stats["prompt_tokens"] += u.get("prompt_tokens") or 0
        stats["completion_tokens"] += u.get("completion_tokens") or 0
        out = parse(j["choices"][0]["message"].get("content"))
        ids = {it["id"] for it in chunk}
        with open(path, "a", encoding="utf-8") as fh:
            for o in out:
                if isinstance(o, dict) and str(o.get("id")) in ids and str(o["id"]) not in got:
                    o["id"] = str(o["id"])
                    got[o["id"]] = o
                    fh.write(json.dumps(o, ensure_ascii=False) + "\n")
        print(f"  [{model}] batch {i // BATCH + 1}: {len(out)} parsed of {len(chunk)} in {dt:.0f}s", flush=True)
        time.sleep(GAP)
    return slug, got, stats, secs


def score(items, son, got):
    n = len(items)
    ok = [it for it in items if it["id"] in got and got[it["id"]].get("category") in CATS]
    def four(c): return c if c in ("research_report", "explainer", "release") else "other"
    m = {"answered": len(ok), "of": n}
    if ok:
        m["manual_exact"] = sum(four(got[it["id"]]["category"]) == it["_manual"] for it in ok) / len(ok)
        m["manual_research_binary"] = sum((got[it["id"]]["category"] == "research_report") == (it["_manual"] == "research_report") for it in ok) / len(ok)
        s = [it for it in ok if it["id"] in son]
        m["sonnet_exact"] = sum(got[it["id"]]["category"] == son[it["id"]]["category"] for it in s) / len(s)
        m["sonnet_research_binary"] = sum((got[it["id"]]["category"] == "research_report") == (son[it["id"]]["category"] == "research_report") for it in s) / len(s)
        m["sonnet_topic"] = sum(got[it["id"]].get("topic") == son[it["id"]].get("topic") for it in s) / len(s)
        m["has_both_summaries"] = sum(bool(got[it["id"]].get("summary_zh")) and bool(got[it["id"]].get("summary_en")) for it in ok) / len(ok)
    return m


def main():
    models = sys.argv[1:]
    if not models:
        sys.exit("usage: openrouter_eval.py MODEL [MODEL ...]")
    items, son = load()
    k = key()
    base = {"answered": len(items), "of": len(items)}
    base.update({"manual_exact": sum((son[it["id"]]["category"] if son[it["id"]]["category"] in ("research_report", "explainer", "release") else "other") == it["_manual"] for it in items) / len(items),
                 "manual_research_binary": sum((son[it["id"]]["category"] == "research_report") == (it["_manual"] == "research_report") for it in items) / len(items)})
    report = {"sonnet (pilot)": base}
    for model in models:
        print("==", model, flush=True)
        slug, got, stats, secs = run(model, items, son, k)
        r = score(items, son, got)
        r.update(dict(stats))
        if secs:
            r["median_seconds_per_request"] = round(sorted(secs)[len(secs) // 2], 1)
        report[model] = r
        tag = re.sub(r"[^a-z0-9]+", "-", "-".join(m.split("/")[-1] for m in models).lower())[:80]
        json.dump(report, open(os.path.join(OUT, f"report-{tag}.json"), "w"), indent=1)
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
