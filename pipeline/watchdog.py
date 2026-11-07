# Watchdog for the daily pipeline, GitHub only. It covers what no in-run alert can: the job did not run at all, or it
# ran but the public site did not change.
#   python3 pipeline/watchdog.py                 1. no daily run has succeeded today and none is waiting: start one (self-repair)
#                                                2. the last success is older than WATCHDOG_MAX_HOURS: alert
#                                                3. the live site's status.json is older than WATCHDOG_MAX_HOURS: alert
#                                                all fine: close open "[watchdog]" alerts
#   python3 pipeline/watchdog.py --site [--wait SECONDS]   only check that the live site was rebuilt in the last 30 minutes
#                                                (run right after a deploy; GitHub Pages can take a few minutes)
#   python3 pipeline/watchdog.py --test-alert    open a test alert issue, to confirm that the notification email arrives
# Env: GITHUB_REPOSITORY, GITHUB_TOKEN, DAILY_WORKFLOW (default daily.yml), WATCHDOG_MAX_HOURS (default 36),
# ALERT_MENTION (login to @-mention). Alerts go to ALERT_GITHUB_REPO if set, else to GITHUB_REPOSITORY.
import json, os, sys, time, urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from runlog import CTX, alert, resolve  # noqa: E402

CFG = json.load(open(os.path.join(HERE, "..", "site", "config.json"), encoding="utf-8"))
SITE = CFG.get("site_url", "").rstrip("/")


def age_hours(iso):
    return (datetime.now(timezone.utc) - datetime.fromisoformat(iso.replace("Z", "+00:00"))).total_seconds() / 3600


def site_status():
    """The live site's status.json (written by site/build_openshelf.py), or None."""
    try:
        req = urllib.request.Request(f"{SITE}/status.json?t={int(time.time())}", headers={"User-Agent": "openshelf-watchdog", "Cache-Control": "no-cache"})
        with urllib.request.urlopen(req, timeout=30, context=CTX) as r:
            return json.load(r)
    except Exception as e:
        print(f"site status: {type(e).__name__}: {e}", file=sys.stderr)
        return None


def check_site(wait):
    """Right after a deploy: the live site must show a build from the last 30 minutes."""
    t_end = time.time() + wait
    while True:
        st = site_status()
        if st and age_hours(st["built_at"]) <= 0.5:
            print(json.dumps({"site": "fresh", **st}))
            return True
        if time.time() >= t_end:
            break
        time.sleep(30)
    alert("[watchdog] the live site did not update after deploy",
          f"{SITE}/status.json still shows `{json.dumps(st)}` {wait} seconds after the deploy finished.\n\n"
          "Check the latest `pages-build-deployment` run in the Actions tab and the `gh-pages` branch.")
    return False


def api(path, data=None, method=None):
    repo, tok = os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_TOKEN"]
    req = urllib.request.Request(f"https://api.github.com/repos/{repo}/{path}", data=json.dumps(data).encode() if data is not None else None, method=method,
                                 headers={"Authorization": "Bearer " + tok, "Accept": "application/vnd.github+json", "User-Agent": "openshelf-watchdog"})
    with urllib.request.urlopen(req, timeout=30, context=CTX) as r:
        body = r.read()
        return json.loads(body) if body else {}


def main():
    repo, tok = os.environ.get("GITHUB_REPOSITORY"), os.environ.get("GITHUB_TOKEN")
    if not (repo and tok):
        sys.exit("watchdog needs GITHUB_REPOSITORY and GITHUB_TOKEN")
    os.environ.setdefault("ALERT_GITHUB_REPO", repo)
    if "--test-alert" in sys.argv:
        sent = alert("[test] alert path works", "This is a test of the Openshelf alert path. If you got an email for it, alerts reach you. "
                     "It closes itself on the next watchdog run.")
        print("test alert sent through", sent)
        return
    if "--site" in sys.argv:
        i = sys.argv.index("--wait") if "--wait" in sys.argv else -1
        sys.exit(0 if check_site(int(sys.argv[i + 1]) if i > 0 else 0) else 1)

    wf = os.environ.get("DAILY_WORKFLOW", "daily.yml")
    max_h = float(os.environ.get("WATCHDOG_MAX_HOURS", "36"))
    problems = []
    try:
        runs = api(f"actions/workflows/{wf}/runs?per_page=20").get("workflow_runs", [])
    except Exception as e:
        alert("[watchdog] cannot read workflow runs", f"GitHub API call failed: {type(e).__name__}: {e}")
        sys.exit(1)
    today = time.strftime("%Y-%m-%d", time.gmtime())
    ok = [r for r in runs if r.get("conclusion") == "success"]
    waiting = [r for r in runs if r.get("status") in ("queued", "in_progress", "waiting", "pending", "requested")]
    if not any(r["created_at"].startswith(today) for r in ok) and not waiting:
        try:
            api(f"actions/workflows/{wf}/dispatches", {"ref": os.environ.get("GITHUB_REF_NAME") or "main"}, "POST")
            print(f"no successful {wf} run today and none waiting: started one")
        except Exception as e:
            problems.append((f"[watchdog] cannot start {wf}", f"No successful run today, and starting one failed: {type(e).__name__}: {e}"))
    last = ok[0] if ok else None
    if not last:
        problems.append((f"[watchdog] {wf} has not succeeded recently", f"No successful run of `{wf}` among the last {len(runs)} runs in `{repo}`."))
    elif age_hours(last["updated_at"]) > max_h:
        h = age_hours(last["updated_at"])
        problems.append((f"[watchdog] no successful {wf} run for {int(h)} hours",
                         f"The last successful run of `{wf}` finished at {last['updated_at']} ({h:.0f} hours ago): {last.get('html_url', '')}\n\n"
                         "The watchdog has started a new run. Check whether the schedule was disabled (GitHub disables scheduled workflows "
                         "after 60 days without repository activity), whether the workflow fails before it can log, or whether a secret expired."))
    st = site_status()
    if not st:
        problems.append(("[watchdog] the live site cannot be read", f"{SITE}/status.json could not be fetched."))
    elif age_hours(st["built_at"]) > max_h:
        problems.append((f"[watchdog] the live site is {int(age_hours(st['built_at']))} hours old",
                         f"{SITE}/status.json: `{json.dumps(st)}`. The daily run may succeed without the site being deployed."))
    print(json.dumps({"workflow": wf, "last_success": last and last["updated_at"], "site": st, "problems": [p[0] for p in problems]}))
    for title, body in problems:
        alert(title, body)
    if not problems:
        resolve("[watchdog]", "Resolved: the daily run and the live site are both up to date.")
        resolve("[test]", "Closed by the watchdog.")


if __name__ == "__main__":
    main()
