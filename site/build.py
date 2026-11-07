# Build the static bilingual site into site/public/. Stdlib only.
# Pages per language (English at /, Chinese at /zh/): the daily stream (index.html) and About & method (about/index.html).
# Usage: python3 build.py            reads config.json, i18n.json, editorial.json, template.html, about.html and the issue file
#        python3 build.py --out DIR --brand Openshelf --logo shelf --theme celadon     (a variant build, e.g. a design demo)
import argparse, copy, html, json, os, re, shutil
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "public")
CFG = json.load(open(os.path.join(HERE, "config.json")))
I18N = json.load(open(os.path.join(HERE, "i18n.json")))
ED = json.load(open(os.path.join(HERE, "editorial.json")))
TPL_HOME = open(os.path.join(HERE, "template.html"), encoding="utf-8").read()
TPL_ABOUT = open(os.path.join(HERE, "about.html"), encoding="utf-8").read()
ISSUE = json.load(open(os.path.join(HERE, CFG["issue"])))
SITE = CFG.get("site_url", "").rstrip("/")
LANGS = {"en": "", "zh": "zh/"}          # output folder per language
PAGES = {"home": "", "about": "about/"}  # folder per page inside a language folder
VIA_ORDER = ("fork", "rs", "hn")         # author's own feed, then archives, then discussion sites
CAT = {"research_report": "research", "explainer": "explainer"}
LOGOS = {
    "branch": ('<svg viewBox="0 0 32 32" aria-hidden="true"><path d="M11 7v18M11 13c0 4 10 2 10 7" stroke="currentColor" stroke-width="2.6" fill="none" stroke-linecap="round"/>'
               '<circle cx="11" cy="8" r="3.2" fill="currentColor"/><circle cx="11" cy="24" r="3.2" fill="currentColor"/>'
               '<circle cx="21" cy="21" r="3.2" fill="none" stroke="currentColor" stroke-width="2.2"/></svg>'),
    "shelf": ('<svg viewBox="0 0 32 32" aria-hidden="true" style="color:var(--ink)"><g fill="currentColor"><rect x="6" y="9" width="4.6" height="16.5" rx="1.3"/>'
              '<rect x="12.6" y="13" width="4.6" height="12.5" rx="1.3"/></g><rect class="lean" x="20" y="9.4" width="4.6" height="16.1" rx="1.3" fill="currentColor" transform="rotate(14 20 25.5)"/>'
              '<rect x="4" y="25.6" width="24" height="2.4" rx="1.2" fill="currentColor"/></svg>'),
}
FAVICONS = {"branch": "favicon.svg", "shelf": "favicon-shelf.svg"}
BRAND, LOGO, THEME = CFG.get("brand", "GitPost"), CFG.get("logo", "branch"), CFG.get("theme", "")


def fmt(n):
    return f"{n:,}" if isinstance(n, int) else str(n)


def esc(s):
    return html.escape(s, quote=True)


# ---------------------------------------------------------------- data
def derived(reports):
    """Counts that depend on which reports are in the issue (recomputed after editorial exclusions)."""
    out = {"research_all": 0, "explainer_all": 0, "topic_matrix": {}, "topic_totals": {}, "per": {}}
    for r in reports:
        s, c = r["source"], r["category"]
        p = out["per"].setdefault(s, {"research": 0, "explainer": 0, "sites": set(), "on_hn": 0})
        if c == "research_report":
            p["research"] += 1; out["research_all"] += 1
        elif c == "explainer":
            p["explainer"] += 1; out["explainer_all"] += 1
        else:
            continue
        p["sites"].add(r["domain"])
        p["on_hn"] += bool(r.get("hn_id")) and s != "hn"
        out["topic_matrix"].setdefault(s, {})
        out["topic_matrix"][s][r["topic"]] = out["topic_matrix"][s].get(r["topic"], 0) + 1
        out["topic_totals"][s] = out["topic_totals"].get(s, 0) + 1
    return out


def check_consistency(D):
    """Before exclusions, recomputed counts must equal the stored ones, otherwise the build is wrong."""
    S, d = D["stats"], derived(D["reports"])
    assert d["research_all"] == S["research_all"] and d["explainer_all"] == S["explainer_all"], "research/explainer totals differ"
    assert d["topic_matrix"] == S["topic_matrix"] and d["topic_totals"] == S["topic_totals"], "topic matrix differs"
    for s in ("hn", "fork", "rs"):
        assert d["per"][s]["research"] == S["sources"][s]["research"] and d["per"][s]["explainer"] == S["sources"][s]["explainer"], s
    assert len(d["per"]["fork"]["sites"]) == S["sources"]["fork"]["re_sites"], "fork re_sites differs"
    assert d["per"]["rs"]["on_hn"] == S["sources"]["rs"]["re_on_hn"], "rs re_on_hn differs"
    assert all(r["category"] in CAT for r in D["reports"]), "unexpected category in reports"


def ukey(u):
    return u.split("#")[0].rstrip("/").lower().replace("://www.", "://")


def apply_editorial(D):
    """Domain exclusions plus rule-based exclusions of single URLs (for example the political-topic filter)."""
    D = copy.deepcopy(D)
    ex = {e["domain"]: e for e in ED.get("exclude", [])}
    by_url = {ukey(e["url"]): e for e in ED.get("exclude_urls", [])}
    removed, hidden = {}, {}

    def drop(r):
        if r["domain"] in ex:
            removed[r["domain"]] = removed.get(r["domain"], 0) + 1
            return True
        e = by_url.get(ukey(r["url"]))
        if e:
            hidden.setdefault(e["rule"], []).append(r["title"])
            return True
        if r.get("political") == "strong" and "political" in ED.get("exclude_rules", {}):   # labelled by the daily pipeline
            hidden.setdefault("political", []).append(r["title"])
            return True
        return False
    D["reports"] = keep = [r for r in D["reports"] if not drop(r)]
    for c in D["chains"]:
        c["responses"] = [r for r in c["responses"] if not drop(r)]
    D["chains"] = [c for c in D["chains"] if c["original"]["domain"] not in ex and ukey(c["original"]["url"]) not in by_url and c["responses"]]
    d, S = derived(keep), D["stats"]
    S["research_all"], S["explainer_all"] = d["research_all"], d["explainer_all"]
    S["topic_matrix"], S["topic_totals"] = d["topic_matrix"], d["topic_totals"]
    for s in ("hn", "fork", "rs"):
        S["sources"][s]["research"], S["sources"][s]["explainer"] = d["per"][s]["research"], d["per"][s]["explainer"]
    S["sources"]["fork"]["re_sites"] = len(d["per"]["fork"]["sites"])
    S["sources"]["rs"]["re_on_hn"] = d["per"]["rs"]["on_hn"]
    S["research"], S["explainer"] = S["sources"]["hn"]["research"], S["sources"]["hn"]["explainer"]
    S["chains"], S["responses"] = len(D["chains"]), sum(len(c["responses"]) for c in D["chains"])
    D["editorial"] = [{"domain": k, "n": removed.get(k, 0), "reason_en": v["reason_en"], "reason_zh": v["reason_zh"], "decided": v.get("decided")} for k, v in ex.items()]
    D["editorial_rules"] = [{"rule": k, "n": len(t), "titles": t, **{f: ED["exclude_rules"][k][f] for f in ("label_en", "label_zh", "desc_en", "desc_zh")}}
                            for k, t in hidden.items()]
    return D


def via(r, comments=True):
    """Every channel a post was found through, in a fixed order, so no source is the default."""
    v = {}
    if r["source"] == "fork":
        v["fork"] = {"s": "fork"}
    if r["source"] == "rs":
        v["rs"] = {"s": "rs", "doi": r.get("doi")} if r.get("doi") else {"s": "rs"}
    if r.get("hn_id"):
        h = {"s": "hn", "id": str(r["hn_id"]), "p": r.get("points") or 0}
        if comments and r.get("comments") is not None:
            h["c"] = r["comments"]
        v["hn"] = h
    return [v[s] for s in VIA_ORDER if s in v]


def stream(D, lang):
    """Front-page payload: one record per post, summaries in the page language, answers attached to the post they answer."""
    key = "summary_" + lang
    chains = {c["original"]["url"]: c for c in D["chains"]}
    in_reports = {r["url"] for r in D["reports"]}
    assert set(chains) <= in_reports, "a chain original is missing from the reports"

    def resp(x):
        return {"rel": x["relation"], "days": x["days_after"], "url": x["url"], "title": x["title"], "domain": x["domain"],
                "summary": x.get(key) or "", "via": via(x, comments=False)}

    items = []
    for r in D["reports"]:
        c = chains.get(r["url"])
        items.append({"url": r["url"], "title": r["title"], "domain": r["domain"], "date": r["date"], "cat": CAT[r["category"]],
                      "topic": r["topic"], "interactive": r["interactive"], "repo": r["repo"], "arxiv": r["arxiv"],
                      "doi": bool(r.get("doi")), "summary": r.get(key) or "", "via": via(r),
                      "resp": [resp(x) for x in sorted(c["responses"], key=lambda x: x["days_after"])] if c else []})
    figures = {"posts": len(items), "sites": len({i["domain"] for i in items}), "sources": len(VIA_ORDER), "threads": len(chains)}
    return {"items": items, "figures": figures}


# ---------------------------------------------------------------- pages
def rel(lang, page, target_lang, target_page):
    """Relative link from one generated page to another (works on any host and in a subfolder)."""
    depth = (LANGS[lang] + PAGES[page]).count("/")
    return ("../" * depth + LANGS[target_lang] + PAGES[target_page]) or "./"


def common(D, lang, page):
    L = I18N[lang]
    alt = "zh" if lang == "en" else "en"
    depth = (LANGS[lang] + PAGES[page]).count("/")
    root = "../" * depth
    S = D["stats"]
    w = S["window"]
    vals = {"hn_pool": fmt(S["sources"]["hn"]["pool"])}
    subs = {}
    for k, v in L.items():
        if isinstance(v, str):
            subs[k] = esc(re.sub(r"\{(\w+)\}", lambda m: vals.get(m.group(1), m.group(0)), v))
    home, about = rel(lang, page, lang, "home"), rel(lang, page, lang, "about")
    feed_href = root + LANGS[lang] + "feed.xml"
    data_href = root + "data/" + D["issue"]["slug"] + ".json"
    subs.update(root=root, feed_href=feed_href, data_href=data_href, brand=esc(BRAND))
    subs["theme_css"] = f'\n<link rel="stylesheet" href="{root}static/theme-{THEME}.css">' if THEME else ""
    subs["status_line"] = esc(L["status"].format(date=S.get("generated", ""), **{"from": w[0], "to": w[1]}))
    cur = lambda p: ' aria-current="page"' if p == page else ""
    view = lambda v: f' data-view="{v}"' if page == "home" else ""
    subs["topbar_html"] = (
        f'<header class="topbar"><a class="brand" href="{esc(home)}">{LOGOS[LOGO]}{esc(BRAND)}</a>'
        f'<nav class="nav" aria-label="{esc(L["nav_label"])}">'
        f'<a href="{esc(home)}"{view("latest")}>{esc(L["nav_latest"])}</a>'
        f'<a href="{esc(home)}#threads"{view("threads")}>{esc(L["nav_threads"])}</a>'
        f'<a href="{esc(about)}"{cur("about")}>{esc(L["nav_about"])}</a>'
        f'<a href="{esc(feed_href)}">{esc(L["nav_feed"])}</a></nav>'
        f'<a class="lang-switch" href="{esc(rel(lang, page, alt, page))}" hreflang="{I18N[alt]["html_lang"]}" lang="{I18N[alt]["html_lang"]}">{esc(L["alt_label"])}</a></header>')
    repo = f'<a href="{esc(CFG["repo_url"])}">GitHub</a>' if CFG.get("repo_url") else ""
    subs["footer_html"] = (
        f'<footer><span>{esc(L["foot_generated"].replace("{date}", S.get("generated", "")))}</span>'
        f'<span class="links"><a href="{esc(data_href)}">{esc(L["foot_data"])}</a><a href="{esc(feed_href)}">{esc(L["foot_feed"])}</a>{repo}</span>'
        f'<span>{esc(L["foot_license"])}</span></footer>')
    head = []
    if SITE:
        here, there = SITE + "/" + LANGS[lang] + PAGES[page], SITE + "/" + LANGS[alt] + PAGES[page]
        head += [f'<link rel="canonical" href="{esc(here)}">', f'<meta property="og:url" content="{esc(here)}">',
                 f'<link rel="alternate" hreflang="{I18N[lang]["html_lang"]}" href="{esc(here)}">',
                 f'<link rel="alternate" hreflang="{I18N[alt]["html_lang"]}" href="{esc(there)}">',
                 f'<link rel="alternate" hreflang="x-default" href="{esc(SITE + "/" + PAGES[page])}">']
    subs["head_extra"] = "\n".join(head)
    return subs


def fill(tpl, subs, data, lang):
    out = re.sub(r"\{\{(\w+)\}\}", lambda m: subs[m.group(1)], tpl)
    safe = lambda s: s.replace("</", "<\\/").replace("<!--", "<\\!--")
    i18n = json.dumps(I18N[lang], ensure_ascii=False, separators=(",", ":"))
    return out.replace("__DATA_JSON__", safe(json.dumps(data, ensure_ascii=False, separators=(",", ":")))).replace("__I18N_JSON__", safe(i18n))


def page_home(D, lang):
    return fill(TPL_HOME, common(D, lang, "home"), stream(D, lang), lang)


def page_about(D, lang):
    L, subs = I18N[lang], common(D, lang, "about")
    contact = CFG.get("contact") or (CFG.get("repo_url", "").rstrip("/") + "/issues" if CFG.get("repo_url") else "")
    if contact:
        target = contact if re.match(r"https?://|mailto:", contact) else "mailto:" + contact
        label = contact.removeprefix("mailto:")
        subs["about_contact_html"] = "<p>" + esc(L["about_contact"]).replace("{contact}", f'<a href="{esc(target)}">{esc(label)}</a>') + "</p>"
    else:
        subs["about_contact_html"] = ""
    m = CFG.get("maintainer") or {}
    if m.get("name"):
        txt = esc(L["maintainer"])
        txt = txt.replace("{name}", f'<a href="{esc(m.get("url") or m.get("github", ""))}">{esc(m["name"])}</a>')
        txt = txt.replace("{github}", f'<a href="{esc(m.get("github", ""))}">GitHub</a>' if m.get("github") else "GitHub")
        subs["maintainer_html"] = "<p>" + txt + "</p>"
    else:
        subs["maintainer_html"] = ""
    ex = [e for e in D.get("editorial", []) if e["n"]]
    if ex:
        items = "".join("<li>" + esc(L["excluded_item"].format(domain=e["domain"], n=e["n"], reason=e["reason_" + lang])) + "</li>" for e in ex)
        rules = "".join(f'<p>{esc(r["label_" + lang])}（{r["n"]}）：{esc(r["desc_" + lang])}</p><ul class="excluded">'
                        + "".join(f"<li>{esc(t)}</li>" for t in r["titles"]) + "</ul>" for r in D.get("editorial_rules", []))
        if lang == "en":
            rules = rules.replace("（", " (").replace("）：", "): ")
        subs["editorial_section_html"] = (f'<section class="s prose" id="editorial"><div class="sec-head"><span class="kicker">{esc(L["editorial_kicker"])}</span>'
                                          f'<h2>{esc(L["editorial_h2"])}</h2></div><p>{esc(L["excluded_h"])}</p><ul class="excluded">{items}</ul>{rules}</section>')
    else:
        subs["editorial_section_html"] = ""
    return fill(TPL_ABOUT, subs, D["stats"], lang)


def feed(D, lang):
    L = I18N[lang]
    base = (SITE + "/" + LANGS[lang]) if SITE else ""
    items = sorted((r for r in D["reports"] if r["category"] == "research_report"), key=lambda r: r["date"], reverse=True)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    x = lambda s: html.escape(s or "", quote=False)
    parts = ['<?xml version="1.0" encoding="utf-8"?>', f'<feed xmlns="http://www.w3.org/2005/Atom" xml:lang="{L["html_lang"]}">',
             f"<title>{x(L['feed_title'])}</title>", f"<id>{x(base or 'urn:gitpost:' + lang)}</id>", f"<updated>{now}</updated>"]
    if base:
        parts.append(f'<link href="{x(base)}"/>')
        parts.append(f'<link rel="self" href="{x(base + "feed.xml")}"/>')
    for r in items:
        parts.append("<entry>")
        parts.append(f"<title>{x(r['title'])}</title>")
        parts.append(f'<link href="{x(r["url"])}"/>')
        parts.append(f"<id>{x(r['url'])}</id>")
        parts.append(f"<updated>{r['date']}T00:00:00Z</updated>")
        parts.append(f"<author><name>{x(r['domain'])}</name></author>")
        parts.append(f"<category term=\"{x(r['topic'])}\" label=\"{x(L['topic'].get(r['topic'], r['topic']))}\"/>")
        if r.get("summary_" + lang):
            parts.append(f"<summary>{x(r['summary_' + lang])}</summary>")
        parts.append("</entry>")
    parts.append("</feed>")
    return "\n".join(parts) + "\n"


def not_found():
    e, z = I18N["en"], I18N["zh"]
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(BRAND)} · {esc(e['nf_title'])}</title><meta name="color-scheme" content="light dark"><link rel="icon" href="/favicon.svg" type="image/svg+xml">
<style>body{{margin:0;padding:48px 16px;font:16px/1.6 system-ui,"PingFang SC",sans-serif;background:#f6f6f8;color:#14161c}}
@media (prefers-color-scheme:dark){{body{{background:#0f1115;color:#e7eaf0}}}}main{{max-width:640px;margin:0 auto}}a{{color:inherit}}h1{{margin:0 0 12px}}</style></head>
<body><main><h1>{esc(e['nf_title'])}</h1><p>{esc(e['nf_body'])} <a href="/">{esc(BRAND)}</a></p>
<p lang="zh-CN">{esc(z['nf_body'])} <a href="/zh/">{esc(BRAND)}</a></p></main></body></html>
"""


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w", encoding="utf-8").write(text)


def rebrand(obj, name):
    """Swap the working name in every UI string (a variant build such as a design demo)."""
    if isinstance(obj, dict):
        return {k: rebrand(v, name) for k, v in obj.items()}
    return obj.replace("GitPost", name) if isinstance(obj, str) else obj


def main(argv=None):
    global OUT, BRAND, LOGO, THEME, I18N
    ap = argparse.ArgumentParser()
    ap.add_argument("--out"); ap.add_argument("--brand"); ap.add_argument("--logo", choices=sorted(LOGOS)); ap.add_argument("--theme")
    a = ap.parse_args(argv)
    OUT = os.path.abspath(a.out) if a.out else OUT
    BRAND, LOGO, THEME = a.brand or BRAND, a.logo or LOGO, a.theme if a.theme is not None else THEME
    if THEME:
        assert os.path.exists(os.path.join(HERE, "static", f"theme-{THEME}.css")), f"no static/theme-{THEME}.css"
    if BRAND != "GitPost":
        I18N = rebrand(I18N, BRAND)
    check_consistency(ISSUE)
    D = apply_editorial(ISSUE)
    os.makedirs(OUT, exist_ok=True)
    marker = os.path.join(OUT, ".built-by-build-py")
    if os.listdir(OUT) and not os.path.exists(marker) and OUT != os.path.join(HERE, "public"):
        raise SystemExit(f"refusing to empty {OUT}: it was not created by build.py")
    for name in os.listdir(OUT):             # empty the folder but keep it, so a running preview server keeps working
        p = os.path.join(OUT, name)
        shutil.rmtree(p) if os.path.isdir(p) and not os.path.islink(p) else os.remove(p)
    os.makedirs(os.path.join(OUT, "data"))
    for lang, sub in LANGS.items():
        write(os.path.join(OUT, sub, "index.html"), page_home(D, lang))
        write(os.path.join(OUT, sub, PAGES["about"], "index.html"), page_about(D, lang))
        write(os.path.join(OUT, sub, "feed.xml"), feed(D, lang))
    json.dump(D, open(os.path.join(OUT, "data", D["issue"]["slug"] + ".json"), "w"), ensure_ascii=False, indent=1)
    shutil.copytree(os.path.join(HERE, "static"), os.path.join(OUT, "static"), ignore=shutil.ignore_patterns("favicon*.svg"))
    shutil.copy(os.path.join(HERE, "static", FAVICONS[LOGO]), os.path.join(OUT, "favicon.svg"))
    write(os.path.join(OUT, "404.html"), not_found())
    write(os.path.join(OUT, ".nojekyll"), "")
    write(marker, "This folder is regenerated by site/build.py; anything else put here is deleted on the next build.\n")
    write(os.path.join(OUT, "robots.txt"), "User-agent: *\nAllow: /\n" + (f"Sitemap: {SITE}/sitemap.xml\n" if SITE else ""))
    if SITE:
        urls = "".join(f"<url><loc>{esc(SITE + '/' + s + p)}</loc></url>" for s in LANGS.values() for p in PAGES.values())
        write(os.path.join(OUT, "sitemap.xml"), f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>\n')
    S, st = D["stats"], stream(D, "en")
    print("built", OUT, "| brand", BRAND, "| logo", LOGO, "| theme", THEME or "default")
    print("  posts", st["figures"]["posts"], "sites", st["figures"]["sites"], "research", S["research_all"], "explainers", S["explainer_all"], "threads", st["figures"]["threads"])
    print("  per source", {s: (S["sources"][s]["research"], S["sources"][s]["explainer"]) for s in ("hn", "fork", "rs")})
    print("  excluded", [(e["domain"], e["n"]) for e in D["editorial"]], "| rules", [(r["rule"], r["n"]) for r in D.get("editorial_rules", [])])
    if not CFG.get("site_url"):
        print("  note: site_url is empty, so canonical/hreflang/sitemap are left out")
    if not (CFG.get("contact") or CFG.get("repo_url")):
        print("  note: no contact or repo_url, so the removal-request line is hidden")


if __name__ == "__main__":
    main()
