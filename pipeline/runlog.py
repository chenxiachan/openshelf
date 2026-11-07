# Run log, health checks and alerts for the daily pipeline.
#
# Every run writes one JSON line per event to data/logs/runs/<run_id>.jsonl and appends a one-line summary to
# data/logs/runs.jsonl. At the end of a run, failed health checks or errors raise an alert through every channel
# that is configured (all optional, all by environment variable):
#   ALERT_GITHUB_REPO + GITHUB_TOKEN   open (or comment on) an issue labelled "alert" in that repo; GitHub emails you
#   NTFY_TOPIC                         push notification to your phone through ntfy.sh (free app, topic name is the secret)
#   HEARTBEAT_URL                      a dead-man switch such as healthchecks.io: pinged on success, "<url>/fail" on failure,
#                                      so you also hear about it when the job never ran at all
# Without any channel the alert is printed to stderr and written to data/logs/ALERT.txt.
# ALERT_MENTION (a GitHub login) is @-mentioned in every alert, so the email arrives even without watching the repo.
# A run that ends "ok" closes the open alert issues of the same pipeline ("[name] ..."), so open alerts are current problems.
# Run(name, history=path) also appends the summary to a file that is kept in git (state/runs.jsonl for the daily run).
import json, os, socket, ssl, sys, time, traceback, urllib.request

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
LOGS = os.path.join(ROOT, "data", "logs")
CTX = ssl.create_default_context(cafile="/etc/ssl/cert.pem") if os.path.exists("/etc/ssl/cert.pem") else ssl.create_default_context()


def _post(url, data, headers, timeout=20):
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
        return r.status, r.read()


class Run:
    """One pipeline run. Use as a context manager so a crash still produces a summary and an alert."""

    def __init__(self, name, history=None):
        self.name, self.history = name, history
        self.id = time.strftime("%Y%m%d-%H%M%S", time.gmtime()) + "-" + name
        self.t0 = time.time()
        self.counts, self.checks, self.errors, self.warnings = {}, [], [], []
        self.cost = 0.0
        os.makedirs(os.path.join(LOGS, "runs"), exist_ok=True)
        self.path = os.path.join(LOGS, "runs", self.id + ".jsonl")
        self.event("run", "info", "start", host=socket.gethostname(), ci=bool(os.environ.get("GITHUB_ACTIONS")))

    # ---- recording ----
    def event(self, stage, level, msg, **fields):
        rec = {"t": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "run": self.id, "stage": stage, "level": level, "msg": msg, **fields}
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        if level in ("warn", "error") or os.environ.get("PIPELINE_VERBOSE"):
            print(f"[{rec['t']}] {level.upper():5} {stage}: {msg} {json.dumps(fields, ensure_ascii=False) if fields else ''}", file=sys.stderr, flush=True)
        if level == "error":
            self.errors.append(f"{stage}: {msg}")
        elif level == "warn":
            self.warnings.append(f"{stage}: {msg}")

    def count(self, key, n=1):
        self.counts[key] = self.counts.get(key, 0) + n

    def add_cost(self, usd):
        self.cost += usd or 0.0

    def check(self, name, ok, detail=""):
        """A health check; a failed check makes the run raise an alert even if nothing crashed."""
        self.checks.append({"name": name, "ok": bool(ok), "detail": detail})
        self.event("check", "info" if ok else "warn", name, ok=bool(ok), detail=detail)

    # ---- finishing ----
    def __enter__(self):
        return self

    def __exit__(self, et, ev, tb):
        if et is not None:
            self.event("run", "error", f"crashed: {et.__name__}: {ev}", traceback="".join(traceback.format_exception(et, ev, tb))[-4000:])
        self.finish()
        return False

    def finish(self):
        failed = [c for c in self.checks if not c["ok"]]
        status = "error" if self.errors else ("degraded" if failed else "ok")
        summary = {"run": self.id, "name": self.name, "status": status, "seconds": round(time.time() - self.t0, 1),
                   "cost_usd": round(self.cost, 5), "counts": self.counts, "failed_checks": failed, "errors": self.errors[:20],
                   "warnings": len(self.warnings)}
        with open(os.path.join(LOGS, "runs.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(summary, ensure_ascii=False) + "\n")
        if self.history:
            short = {k: summary[k] for k in ("run", "status", "seconds", "cost_usd", "counts")}
            short["failed_checks"] = [c["name"] for c in failed]
            with open(self.history, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(short, ensure_ascii=False) + "\n")
        self.event("run", "info", "finish", status=status)
        if status != "ok":
            lines = [f"Run `{self.id}` finished with status **{status}**.", ""]
            lines += [f"- error: {e}" for e in self.errors[:10]]
            lines += [f"- failed check: {c['name']} ({c['detail']})" for c in failed]
            lines += ["", f"counts: `{json.dumps(self.counts)}`", f"cost: ${self.cost:.4f}", f"full log: `data/logs/runs/{self.id}.jsonl`"]
            alert(f"[{self.name}] {status}: " + (self.errors[0] if self.errors else failed[0]["name"])[:90], "\n".join(lines))
        else:
            resolve(f"[{self.name}]", f"Resolved: run `{self.id}` finished ok.")
        heartbeat(ok=(status != "error"))
        print(json.dumps(summary, ensure_ascii=False), flush=True)
        return summary


def _gh():
    repo, tok = os.environ.get("ALERT_GITHUB_REPO"), os.environ.get("GITHUB_TOKEN")
    h = {"Authorization": "Bearer " + (tok or ""), "Accept": "application/vnd.github+json", "Content-Type": "application/json", "User-Agent": "openshelf-pipeline"}
    return (repo if repo and tok else None), h


def resolve(prefix, note):
    """Close the open alert issues whose title starts with prefix. Never raises. Returns the closed issue numbers."""
    repo, h = _gh()
    if not repo:
        return []
    closed = []
    try:
        q = urllib.request.Request(f"https://api.github.com/repos/{repo}/issues?state=open&labels=alert&per_page=50", headers=h)
        with urllib.request.urlopen(q, timeout=20, context=CTX) as r:
            for i in json.load(r):
                if i.get("title", "").startswith(prefix) and "pull_request" not in i:
                    _post(f"https://api.github.com/repos/{repo}/issues/{i['number']}/comments", json.dumps({"body": note}).encode(), h)
                    req = urllib.request.Request(f"https://api.github.com/repos/{repo}/issues/{i['number']}", method="PATCH",
                                                 data=json.dumps({"state": "closed", "state_reason": "completed"}).encode(), headers=h)
                    urllib.request.urlopen(req, timeout=20, context=CTX).read()
                    closed.append(i["number"])
    except Exception as e:
        print(f"resolve: github failed: {type(e).__name__}: {e}", file=sys.stderr)
    return closed


def alert(title, body):
    """Send an alert through every configured channel; never raises, falls back to a local file."""
    sent = []
    repo, h = _gh()
    who = os.environ.get("ALERT_MENTION", "").strip().lstrip("@")
    if who:
        body += f"\n\ncc @{who}"
    if repo:
        try:
            # one open issue per problem: comment on it instead of opening duplicates
            q = urllib.request.Request(f"https://api.github.com/repos/{repo}/issues?state=open&labels=alert&per_page=50", headers=h)
            with urllib.request.urlopen(q, timeout=20, context=CTX) as r:
                same = [i for i in json.load(r) if i.get("title") == title]
            if same:
                _post(f"https://api.github.com/repos/{repo}/issues/{same[0]['number']}/comments", json.dumps({"body": body}).encode(), h)
            else:
                _post(f"https://api.github.com/repos/{repo}/issues", json.dumps({"title": title, "body": body, "labels": ["alert"]}).encode(), h)
            sent.append("github")
        except Exception as e:
            print(f"alert: github failed: {type(e).__name__}: {e}", file=sys.stderr)
    topic = os.environ.get("NTFY_TOPIC")
    if topic:
        try:
            _post("https://ntfy.sh/" + topic, body[:3500].encode(), {"Title": title[:200], "Priority": "high", "Tags": "warning"})
            sent.append("ntfy")
        except Exception as e:
            print(f"alert: ntfy failed: {type(e).__name__}: {e}", file=sys.stderr)
    if not sent:
        os.makedirs(LOGS, exist_ok=True)
        with open(os.path.join(LOGS, "ALERT.txt"), "a", encoding="utf-8") as fh:
            fh.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%SZ', time.gmtime())} {title}\n{body}\n")
        print(f"ALERT (no channel configured): {title}", file=sys.stderr)
    return sent


def heartbeat(ok=True):
    url = os.environ.get("HEARTBEAT_URL")
    if not url:
        return
    try:
        urllib.request.urlopen(url.rstrip("/") + ("" if ok else "/fail"), timeout=15, context=CTX).read()
    except Exception as e:
        print(f"heartbeat failed: {type(e).__name__}: {e}", file=sys.stderr)
