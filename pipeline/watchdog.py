# Watchdog for the daily pipeline, GitHub only: asks the Actions API when the daily workflow last succeeded and opens
# an "alert" issue (GitHub emails the maintainer) when that was more than WATCHDOG_MAX_HOURS ago, or never.
# This covers the case no in-run alert can: the job did not run at all (schedule disabled, workflow broken, account issue).
# Runs in its own scheduled workflow. Env: GITHUB_REPOSITORY, GITHUB_TOKEN, DAILY_WORKFLOW (file name, default daily.yml),
# WATCHDOG_MAX_HOURS (default 36). Alerts go to ALERT_GITHUB_REPO if set, else to GITHUB_REPOSITORY.
import json, os, sys, time, urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from runlog import CTX, alert  # noqa: E402


def main():
    repo, tok = os.environ.get("GITHUB_REPOSITORY"), os.environ.get("GITHUB_TOKEN")
    if not (repo and tok):
        sys.exit("watchdog needs GITHUB_REPOSITORY and GITHUB_TOKEN")
    wf = os.environ.get("DAILY_WORKFLOW", "daily.yml")
    max_h = float(os.environ.get("WATCHDOG_MAX_HOURS", "36"))
    os.environ.setdefault("ALERT_GITHUB_REPO", repo)
    url = f"https://api.github.com/repos/{repo}/actions/workflows/{wf}/runs?status=success&per_page=1"
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + tok, "Accept": "application/vnd.github+json", "User-Agent": "openshelf-watchdog"})
    try:
        with urllib.request.urlopen(req, timeout=30, context=CTX) as r:
            runs = json.load(r).get("workflow_runs", [])
    except Exception as e:
        alert("[watchdog] cannot read workflow runs", f"GitHub API call failed: {type(e).__name__}: {e}")
        sys.exit(1)
    if not runs:
        alert(f"[watchdog] {wf} has never succeeded", f"No successful run of `{wf}` was found in `{repo}`.")
        return
    last = datetime.fromisoformat(runs[0]["updated_at"].replace("Z", "+00:00"))
    age_h = (datetime.now(timezone.utc) - last).total_seconds() / 3600
    print(json.dumps({"workflow": wf, "last_success": runs[0]["updated_at"], "age_hours": round(age_h, 1), "max_hours": max_h}))
    if age_h > max_h:
        alert(f"[watchdog] no successful {wf} run for {int(age_h)} hours",
              f"The last successful run of `{wf}` finished at {runs[0]['updated_at']} ({age_h:.0f} hours ago): {runs[0].get('html_url', '')}\n\n"
              "Check whether the schedule was disabled (GitHub disables scheduled workflows after 60 days without repository activity), "
              "whether the workflow fails before it can log, or whether a secret expired.")


if __name__ == "__main__":
    main()
