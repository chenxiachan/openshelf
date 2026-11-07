# Robust LLM labelling with a fallback chain. Chosen by the 2026-11-07 evaluation (eval/compare.py, 144 shared items):
#   1. qwen/qwen3.7-flash          agreement with manual labels 81% / 88% (same as Sonnet), about $0.11 a month
#   2. deepseek/deepseek-v4-flash   78% / 85%, fastest, most specific summaries
#   3. nvidia/nemotron-3-ultra:free 81% / 85%, free but slow and sometimes incomplete
# Every batch is validated (ids, enum values, both summaries). Items a model leaves out or gets wrong are retried in
# smaller batches on the same model, then on the next one. Items that fail on every model are returned as "unlabelled"
# and carried over to the next run instead of blocking the pipeline. Every call is logged with model, tokens and cost.
# Key: env OPENROUTER_API_KEY (CI secret), else the macOS Keychain item (account "openshelf", service "openrouter-api-key").
import json, os, re, subprocess, time, urllib.error, urllib.request

from runlog import CTX, ROOT

CHAIN = [m.strip() for m in os.environ.get("LLM_CHAIN", "qwen/qwen3.7-flash,deepseek/deepseek-v4-flash-0731,nvidia/nemotron-3-ultra-550b-a55b:free").split(",") if m.strip()]
PRICE = {"qwen/qwen3.7-flash": (0.030, 0.13), "deepseek/deepseek-v4-flash-0731": (0.043, 0.30), "nvidia/nemotron-3-ultra-550b-a55b:free": (0.0, 0.0)}  # USD per 1M tokens, 2026-11-06
CATS = {"research_report", "explainer", "release", "commentary", "other"}
TOPICS = {"ai-ml", "interpretability-alignment", "security-privacy", "math-theory", "systems-hardware", "robotics-graphics", "bio-health",
          "physics-earth", "econ-social", "engineering-env", "humanities", "scholarly-communication", "other"}
POLITICAL = {"strong", "some", "none"}
CONF = {"high", "medium", "low"}
FIELDS = ("id", "hn_title", "page_title", "domain", "url", "site", "author", "description", "excerpt", "no_text")
RELATIONS = {"rebuttal", "replication", "extension", "discussion", "coverage", "mention", "same_source", "unclear"}
PAIR_FIELDS = ("pair_id", "source_title", "source_domain", "source_date", "source_site", "source_excerpt", "anchor", "link_context",
               "target_title", "target_domain", "target_date")


class LLMError(Exception):
    pass


def api_key():
    k = os.environ.get("OPENROUTER_API_KEY")
    if not k and os.path.exists("/usr/bin/security"):
        k = subprocess.run(["security", "find-generic-password", "-a", "openshelf", "-s", "openrouter-api-key", "-w"], capture_output=True, text=True).stdout.strip()
    if not k:
        raise LLMError("no OpenRouter key: set OPENROUTER_API_KEY")
    return k


def balance():
    """Money left in USD as (account credits, this key's monthly limit); None where unknown or unlimited. Never raises."""
    out = []
    for path, left in (("credits", lambda d: d["total_credits"] - d["total_usage"]), ("key", lambda d: d.get("limit_remaining"))):
        try:
            req = urllib.request.Request("https://openrouter.ai/api/v1/" + path, headers={"Authorization": "Bearer " + api_key(), "User-Agent": "openshelf-pipeline"})
            with urllib.request.urlopen(req, timeout=20, context=CTX) as r:
                out.append(left(json.load(r)["data"]))
        except Exception:
            out.append(None)
    return tuple(out)


def instructions(task="classify"):
    t = open(os.path.join(ROOT, "prompts", f"{TASKS[task]['prompt']}.md"), encoding="utf-8").read().split("---", 1)[1].strip()
    if task == "classify":
        t += ('\n\nReturn only a JSON object of the form {"items": [ ... ]} with one object per input item, in input order, '
              "each with the fields id, category, topic, political, summary_zh, summary_en, confidence. No other text.")
    return t


def valid(o):
    return (isinstance(o, dict) and o.get("category") in CATS and o.get("topic") in TOPICS and o.get("political") in POLITICAL
            and o.get("confidence") in CONF and isinstance(o.get("summary_zh"), str) and o["summary_zh"].strip()
            and isinstance(o.get("summary_en"), str) and o["summary_en"].strip())


def valid_pair(o):
    return (isinstance(o, dict) and o.get("relation") in RELATIONS and o.get("confidence") in CONF
            and isinstance(o.get("summary_zh"), str) and o["summary_zh"].strip() and isinstance(o.get("summary_en"), str) and o["summary_en"].strip())


TASKS = {"classify": {"prompt": "classify", "id": "id", "fields": FIELDS, "valid": valid},
         "pairs": {"prompt": "pairs", "id": "pair_id", "fields": PAIR_FIELDS, "valid": valid_pair}}


def parse(text):
    text = re.sub(r"^```(?:json)?|```$", "", (text or "").strip(), flags=re.M).strip()
    for cand in (text, (re.search(r"\{.*\}", text, re.S) or [None])[0]):
        if not cand:
            continue
        try:
            v = json.loads(cand)
            return v.get("items", []) if isinstance(v, dict) else v if isinstance(v, list) else []
        except Exception:
            continue
    return []


class Client:
    def __init__(self, run, chain=None, max_tokens=16000, timeout=300, task="classify"):
        self.run, self.chain, self.max_tokens, self.timeout = run, chain or CHAIN, max_tokens, timeout
        self.task = TASKS[task]
        self.task_name = task
        self.key = api_key()
        self.no_json_mode = set()
        self.dead = set()        # models that failed hard in this run (auth, removed, quota): skip them for the rest of the run
        self.tripped = set()     # circuit breaker: 3 failed calls in a row, skip for the rest of the run
        self.fails = {}
        self.sys = instructions(task)
        self.force_fail = set(filter(None, os.environ.get("LLM_FORCE_FAIL", "").split(",")))   # test hook: pretend these models are down

    def _call(self, model, items):
        if model in self.force_fail:
            raise LLMError("forced failure (LLM_FORCE_FAIL)")
        payload = [{f: it.get(f, "") for f in self.task["fields"]} for it in items]
        body = {"model": model, "temperature": 0, "max_tokens": self.max_tokens, "usage": {"include": True},
                "messages": [{"role": "system", "content": self.sys}, {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]}
        if model not in self.no_json_mode:
            body["response_format"] = {"type": "json_object"}
        req = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions", data=json.dumps(body).encode(),
                                     headers={"Authorization": "Bearer " + self.key, "Content-Type": "application/json", "X-Title": "openshelf-pipeline"})
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=CTX) as r:
                j = json.load(r)
        except urllib.error.HTTPError as e:
            msg = e.read().decode("utf-8", "ignore")[:300]
            if e.code == 400 and "response_format" in msg and model not in self.no_json_mode:
                self.no_json_mode.add(model)
                return self._call(model, items)
            if e.code in (401, 402, 403, 404):
                self.dead.add(model)
            raise LLMError(f"http {e.code}: {msg}")
        if "choices" not in j:
            raise LLMError("no choices: " + str(j.get("error", j))[:200])
        u = j.get("usage") or {}
        pin, pout = PRICE.get(model, (0.0, 0.0))
        cost = u.get("cost") if isinstance(u.get("cost"), (int, float)) else ((u.get("prompt_tokens") or 0) * pin + (u.get("completion_tokens") or 0) * pout) / 1e6
        self.run.add_cost(cost)
        self.run.count("llm_requests")
        self.run.count(f"llm_requests:{model}")
        out = parse(j["choices"][0]["message"].get("content"))
        self.run.event("llm", "info", "call", task=self.task_name, model=model, n_in=len(items), n_out=len(out), seconds=round(time.time() - t0, 1),
                       prompt_tokens=u.get("prompt_tokens"), completion_tokens=u.get("completion_tokens"), cost_usd=round(cost, 6),
                       finish=j["choices"][0].get("finish_reason"))
        return out

    def _try(self, model, items, retries=2):
        """Label items with one model; returns {id: label} for the ones that came back valid."""
        good, key = {}, self.task["id"]
        ids = {str(it[key]) for it in items}
        for attempt in range(retries + 1):
            todo = [it for it in items if str(it[key]) not in good]
            if not todo or model in self.dead or model in self.tripped:
                break
            try:
                res = self._call(model, todo)
                self.fails[model] = 0
                for o in res:
                    if isinstance(o, dict) and str(o.get(key)) in ids and self.task["valid"](o):
                        o[key] = str(o[key])
                        o["model"] = model
                        good[o[key]] = o
            except LLMError as e:
                self.run.event("llm", "warn", "call failed", model=model, attempt=attempt + 1, error=str(e)[:300])
                self.fails[model] = self.fails.get(model, 0) + 1
                if self.fails[model] >= 3 and model not in self.tripped:
                    self.tripped.add(model)
                    self.run.event("llm", "warn", "circuit breaker: model skipped for the rest of this run", model=model)
                if model in self.dead or model in self.tripped:
                    break
                time.sleep(min(30, 5 * (attempt + 1)))
                continue
            missing = len(ids) - len(good)
            if missing and attempt < retries:
                self.run.event("llm", "info", "incomplete batch, retrying the rest", model=model, missing=missing)
        return good

    def label(self, items, batch=10):
        """Label every item, walking down the chain. Returns (labels, unlabelled_items)."""
        labels, key = {}, self.task["id"]
        for i in range(0, len(items), batch):
            chunk = items[i:i + batch]
            for depth, model in enumerate(self.chain):
                todo = [it for it in chunk if str(it[key]) not in labels]
                if not todo:
                    break
                if model in self.dead or model in self.tripped:
                    continue
                got = self._try(model, todo)
                labels.update(got)
                if depth > 0 and got:
                    self.run.count("llm_items_by_fallback", len(got))
                    self.run.event("llm", "warn", "used fallback model", model=model, items=len(got))
            time.sleep(1.0)
        left = [it for it in items if str(it[key]) not in labels]
        self.run.count("llm_items_labelled", len(labels))
        if left:
            self.run.count("llm_items_unlabelled", len(left))
            self.run.event("llm", "warn", "items left unlabelled after the whole chain; carried over to the next run", n=len(left))
        for m in self.dead:
            self.run.event("llm", "error", "model unusable in this run (auth, quota or removed)", model=m)
        return labels, left
