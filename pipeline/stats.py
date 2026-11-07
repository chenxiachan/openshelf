# Site statistics from the Worker (worker/): daily views and visitors, the most opened and most voted posts, searches.
# Usage: python3 pipeline/stats.py [DAYS]     (default 7; today is partial)
# Token: env OPENSHELF_PIPELINE_TOKEN, else the macOS Keychain item (account "openshelf", service "openshelf-pipeline-token").
import json, os, re, subprocess, sys, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
sys.path.insert(0, HERE)
from runlog import CTX  # noqa: E402

API = (json.load(open(os.path.join(ROOT, "site", "config.json"), encoding="utf-8")).get("api") or "").rstrip("/")


def token():
    t = os.environ.get("OPENSHELF_PIPELINE_TOKEN")
    if not t and sys.platform == "darwin":
        t = subprocess.run(["security", "find-generic-password", "-a", "openshelf", "-s", "openshelf-pipeline-token", "-w"], capture_output=True, text=True).stdout.strip()
    return t


def get(path, auth=True):
    h = {"User-Agent": "openshelf-stats"}
    if auth:
        h["Authorization"] = "Bearer " + token()
    with urllib.request.urlopen(urllib.request.Request(API + path, headers=h), timeout=30, context=CTX) as r:
        return json.load(r)


def titles():
    """Post id -> title, from the built front-page data if it is there."""
    p = os.path.join(ROOT, "site", "public", "shelf-data.js")
    if not os.path.exists(p):
        return {}
    t = open(p, encoding="utf-8").read()
    d = json.loads(t[t.index("{"):t.rindex("}") + 1])
    return {x["id"]: x["title"] for x in d.get("items", [])}


def main():
    if not API or not token():
        sys.exit("needs api in site/config.json and the pipeline token")
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    T, opens, searches = titles(), {}, {}
    print(f"{'day':<12}{'views':>7}{'visitors':>10}{'opens':>7}")
    for i in range(days - 1, -1, -1):
        day = time.strftime("%Y-%m-%d", time.gmtime(time.time() - i * 86400))
        a = get(f"/agg?day={day}")
        o = [x for x in a["items"] if x["type"] == "open"]
        for x in o:
            opens[x["item"]] = opens.get(x["item"], 0) + x["visitors"]
        for x in a["searches"]:
            searches[x["q"]] = searches.get(x["q"], 0) + x["n"]
        print(f"{day:<12}{a['totals']['views'] or 0:>7}{a['totals']['visitors'] or 0:>10}{sum(x['n'] for x in o):>7}")
    print(f"\nmost opened posts ({days} days, visitors per day added up)")
    for k, n in sorted(opens.items(), key=lambda x: -x[1])[:15]:
        print(f"{n:>5}  {T.get(k, k)[:90]}")
    v = get("/votes", auth=False)["items"]
    if v:
        print("\nmost voted posts (all time, up - down)")
        for k, (up, down) in sorted(v.items(), key=lambda x: -(x[1][0] - x[1][1]))[:10]:
            print(f"{up - down:>5}  (+{up} -{down})  {T.get(k, k)[:80]}")
    if searches:
        print("\nsearches: " + ", ".join(f"{q} ({n})" for q, n in sorted(searches.items(), key=lambda x: -x[1])[:20]))


if __name__ == "__main__":
    main()
