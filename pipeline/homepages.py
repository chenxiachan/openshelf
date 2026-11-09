# Academic homepages built from the al-folio and academicpages templates, found through their GitHub forks.
# A homepage only gets a new post when its repository is pushed. So once a day the forks of both templates are listed
# (about 230 GitHub API requests), and only the feeds of repositories pushed since they were last read are read. A site
# that posts twice a year is read twice a year, the day after each post, and a new homepage joins on its first push.
#   state/forks.json               {repo: [template, pushed_at, pushed_at when its feed was last read]}, one line each
#   sources/feeds.json             homepages whose feed has a real post (not a template sample), plus sites added by hand
#   sources/template_titles.json   sample posts shipped with the templates; a title seen on 5 or more sites joins it
# Feeds whose repository is not among the forks (added by hand, or renamed) are read once a week.
# At most PER_RUN feeds are read per run; the rest wait for the next run (the first scan reads every homepage once).
# A read gives at most PER_SITE posts, newest first: from the last WINDOW_DAYS on the first read, later from a week before
# the previous read (posts are often dated before they are pushed). Posts already labelled are dropped by the daily run,
# so a site that posts every day cannot flood a run and an old post does not keep coming back.
import calendar, concurrent.futures as cf, json, os, time

import sources as S
from common import dom, get_json

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
FEEDS = os.path.join(ROOT, "sources", "feeds.json")
TITLES = os.path.join(ROOT, "sources", "template_titles.json")
FORKS = os.path.join(ROOT, "state", "forks.json")
TEMPLATES = (("alshedivat/al-folio", "alfolio"), ("academicpages/academicpages.github.io", "academicpages"))
PER_RUN, WINDOW_DAYS, WEEKLY_DAYS, SAMPLE_SITES, PER_SITE = 2500, 30, 7, 5, 3


def day(ts):
    return time.strftime("%Y-%m-%d", time.gmtime(ts))


def iso_ts(s):
    return calendar.timegm(time.strptime(s[:19], "%Y-%m-%dT%H:%M:%S"))


def load(p, default):
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default


def base_url(repo):
    owner, name = repo.lower().split("/", 1)
    return f"https://{owner}.github.io/" if name == f"{owner}.github.io" else f"https://{owner}.github.io/{name}/"


def list_forks(token):
    """Forks that serve a GitHub Pages site and were changed after forking: {repo: (template, pushed_at)}.
    Raises when a page cannot be read, so a partial list is never taken for the whole."""
    h = {"Accept": "application/vnd.github+json", "Authorization": "Bearer " + token, "X-GitHub-Api-Version": "2022-11-28"}
    out = {}
    for src, tag in TEMPLATES:
        page = 1
        while True:
            d = get_json(f"https://api.github.com/repos/{src}/forks?per_page=100&sort=newest&page={page}", S.T, h)
            if not isinstance(d, list):
                raise RuntimeError(f"{src} forks page {page}: {d!r:.120}")
            for x in d:
                if x.get("archived") or x.get("disabled") or not x.get("has_pages") or not x.get("pushed_at"):
                    continue
                if iso_ts(x["pushed_at"]) - iso_ts(x["created_at"]) < 86400:      # never changed after forking
                    continue
                out[x["full_name"]] = (tag, x["pushed_at"])
            if len(d) < 100:
                break
            page += 1
    return out


def is_sample(title, titles):
    return (title or "").strip().lower() in titles


def collect(run, state, now, blocked, token, dry=False):
    """New homepage posts from the last WINDOW_DAYS. Returns (posts, status of known feeds, save); call save() to keep
    the updated fork list, feed list and template titles (the daily run does so after labelling)."""
    feeds_doc = load(FEEDS, {"feeds": []})
    feeds = feeds_doc["feeds"]
    titles_doc = load(TITLES, {"titles": []})
    titles = set(titles_doc["titles"])
    forks = {r: list(v) for r, v in load(FORKS, {}).items()}
    today = day(now)

    # 1. the fork list, once a day
    if token and state.get("forks_scan") != today:
        try:
            listed = list_forks(token)
            forks = {r: [t, p, (forks.get(r) or ["", "", ""])[2]] for r, (t, p) in listed.items()}
            state["forks_scan"] = today
        except Exception as e:
            run.event("homepages", "warn", f"could not list the template forks: {type(e).__name__}: {e}")
        run.check("template forks listed", state.get("forks_scan") == today, "GitHub API; the known forks are still read when they are due")
    run.count("forks_known", len(forks))

    # 2. what to read: forks pushed since their last read (newest push first), and once a week the feeds without a fork
    by_repo = {f["repo"]: f for f in feeds if f.get("repo")}
    due = sorted((r for r, v in forks.items() if v[1] != v[2] and not blocked(base_url(r))
                  and not (r in by_repo and blocked(by_repo[r]["feed"]))), key=lambda r: forks[r][1], reverse=True)
    take = due[:PER_RUN]
    run.count("forks_due", len(due))
    run.count("forks_left_for_next_run", len(due) - len(take))
    weekly = []
    if day(now - WEEKLY_DAYS * 86400) >= state.get("feeds_last", "1970-01-01"):
        weekly = [f for f in feeds if f.get("repo") not in forks and not blocked(f["feed"]) and not blocked(f["site"])]
        if not dry:
            state["feeds_last"] = today
    targets = [dict(by_repo[r], known=True) if r in by_repo else
               {"feed": base_url(r) + "feed.xml", "site": dom(base_url(r)), "template": forks[r][0], "repo": r, "known": False} for r in take]
    targets += [dict(f, known=True, weekly=True) for f in weekly]
    for f in targets:                                         # the push at which this feed was last read, "" for never
        f["prev"] = forks[f["repo"]][2] if f.get("repo") in forks and not f.get("weekly") else ""
    cache = state.get("feed_cache", {})
    with cf.ThreadPoolExecutor(S.WORKERS) as ex:
        res = list(ex.map(lambda f: S.feed(f, cache if f.get("weekly") else {}), targets))
    run.count("feeds_read", len(targets))

    # 3. sample posts: a title on SAMPLE_SITES or more sites came with the template
    sites_by_title = {}
    for f, (_, entries, _, _) in zip(targets, res):
        for e in entries:
            t = (e.get("title") or "").strip().lower()
            if t:
                sites_by_title.setdefault(t, set()).add(f["site"])
    learned = sorted(t for t, s in sites_by_title.items() if len(s) >= SAMPLE_SITES and t not in titles)
    titles.update(learned)
    run.count("template_titles_learned", len(learned))

    # 4. posts, new homepages, and the read marks
    window, posts, status, new_sites = day(now - WINDOW_DAYS * 86400), [], {}, 0
    for f, (st, entries, c, final) in zip(targets, res):
        if f["known"]:
            status[st] = status.get(st, 0) + 1
        if f.get("weekly") and st == "ok":
            cache[f["feed"]] = {k: v for k, v in c.items() if v}
        if f.get("repo") in forks and not f.get("weekly"):
            forks[f["repo"]][2] = forks[f["repo"]][1]           # read at this push, even if the feed failed: wait for the next push
        real = [e for e in entries if e.get("url") and not is_sample(e.get("title"), titles)]
        site = f["site"]
        if not f["known"] and st == "ok" and real:
            site = dom(final or f["feed"])
            entry = {"feed": final or f["feed"], "site": site, "template": f["template"], "repo": f["repo"]}
            feeds.append(entry)
            by_repo[f["repo"]] = entry
            new_sites += 1
        since = max(window, day(iso_ts(f["prev"]) - 7 * 86400)) if f["prev"] else window
        fresh = sorted(((S.feed_date(e.get("published")), e) for e in real), key=lambda x: x[0], reverse=True)
        fresh = [(d, e) for d, e in fresh if d and d >= since and not blocked(e["url"])]
        if not f.get("weekly"):                               # feeds added by hand are read in full once a week
            fresh = fresh[:PER_SITE]
        for d, e in fresh:
            posts.append({"id": "fork:" + e["url"], "source": "fork", "url": e["url"], "domain": dom(e["url"]), "title": e.get("title", ""),
                          "date": d, "site": site, "author": "", "description": e.get("summary", ""), "excerpt": e.get("excerpt", ""),
                          "links": e.get("links", [])})
    run.count("homepages_added", new_sites)
    known = sum(status.values())
    ok = status.get("ok", 0) + status.get("not_modified", 0)
    run.check("most known homepage feeds answered", known < 10 or ok >= 0.6 * known, json.dumps(status))

    def save():
        feeds_doc["feeds"] = sorted(feeds, key=lambda f: f["site"])
        with open(FEEDS, "w", encoding="utf-8") as fh:
            json.dump(feeds_doc, fh, ensure_ascii=False, indent=1)
            fh.write("\n")
        if learned:
            titles_doc["titles"] = sorted(titles)
            with open(TITLES, "w", encoding="utf-8") as fh:
                json.dump(titles_doc, fh, ensure_ascii=False, indent=1)
                fh.write("\n")
        with open(FORKS, "w", encoding="utf-8") as fh:         # one repository per line keeps the daily diff small
            fh.write("{\n" + ",\n".join(f"{json.dumps(r)}: {json.dumps(v)}" for r, v in sorted(forks.items())) + "\n}\n")
        weekly_feeds = {f["feed"] for f in feeds if f.get("repo") not in forks}
        state["feed_cache"] = {k: v for k, v in cache.items() if k in weekly_feeds}
    return posts, status, save
