# Opt-outs: websites and single posts whose authors asked to be left out (site/optout.json).
# An opted-out site is not fetched, labelled or listed, its entries are deleted from site/issues/live.json, and it is
# dropped from other posts' linking sites. Unlike editorial exclusions, opt-outs are never named on the site.
# Usage: python3 pipeline/optout.py      purge live.json now (every daily run does the same before it collects)
import json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
FILE = os.path.join(ROOT, "site", "optout.json")
LIVE = os.path.join(ROOT, "site", "issues", "live.json")
sys.path.insert(0, os.path.join(ROOT, "crawl"))
from common import dom, norm  # noqa: E402


def blocker(path=FILE):
    """A function that says whether a URL or a bare host is opted out."""
    try:
        O = json.load(open(path, encoding="utf-8"))
    except FileNotFoundError:
        O = {}
    hosts = {d.strip().lower().removeprefix("www.") for d in O.get("domains", []) if d.strip()}
    urls = {norm(u) for u in O.get("urls", []) if u.strip()} - {None}

    def blocked(s):
        s = (s or "").strip()
        if not s:
            return False
        h = dom(s) if "://" in s else s.lower().removeprefix("www.")
        return any(h == d or h.endswith("." + d) for d in hosts) or ("://" in s and norm(s) in urls)
    blocked.empty = not hosts and not urls
    return blocked


def purge(D, blocked):
    """Delete opted-out posts, responses and linking sites from a dataset in place. Returns the number of entries removed."""
    if blocked.empty:
        return 0
    n = len(D["reports"]) + sum(len(c["responses"]) for c in D["chains"])
    D["reports"] = [r for r in D["reports"] if not blocked(r["url"])]
    for r in D["reports"]:
        if r.get("linkers"):
            r["linkers"] = [l for l in r["linkers"] if not blocked(l)]
    for c in D["chains"]:
        c["responses"] = [x for x in c["responses"] if not blocked(x["url"])]
    D["chains"] = [c for c in D["chains"] if c["responses"] and not blocked(c["original"]["url"])]
    return n - len(D["reports"]) - sum(len(c["responses"]) for c in D["chains"])


def main():
    sys.path.insert(0, HERE)
    import daily
    D = json.load(open(LIVE, encoding="utf-8"))
    n = purge(D, blocker())
    if n:
        daily.recompute(D)
        daily.jsave(LIVE, D)
    print("opt-out: removed", n, "entries from site/issues/live.json")


if __name__ == "__main__":
    main()
