# Production build of the Openshelf site into site/public/.
#   1. site/build.py with the Openshelf brand, shelf logo and celadon theme: About pages, feeds, data download, 404, robots, sitemap
#   2. the stream front page (site/stream.html, the design of demo C) in English at / and Chinese at /zh/
#   3. shelf-data.js (posts, responses, link counts, both summaries), analytics client, llms.txt, CNAME when a domain is set
# Votes and sign-in stay off until the Worker backend is deployed (config.json: "features").
# Usage: python3 site/build_openshelf.py
import json, os, shutil, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
OUT = os.path.join(HERE, "public")
sys.path.insert(0, HERE)
import build as B  # noqa: E402

CFG = B.CFG
SLOGAN = {"en": "The latest research from people's own websites, collected every day.",
          "zh": "研究者发在自己网站上的最新研究，每天收集。"}


def main():
    B.main(["--brand", "Openshelf", "--logo", "shelf", "--theme", "celadon"])
    subprocess.run([sys.executable, os.path.join(ROOT, "design", "build_demos.py"), os.path.join(OUT, "shelf-data.js")], check=True)
    os.makedirs(os.path.join(OUT, "static"), exist_ok=True)
    shutil.copy(os.path.join(ROOT, "design", "analytics.js"), os.path.join(OUT, "static", "analytics.js"))
    repo = CFG.get("repo_url", "").rstrip("/")
    feats = {"votes": False, "login": False, "issue_url": f"{repo}/issues/new?template=submit.yml" if repo else None}
    feats.update(CFG.get("features", {}))
    analytics = {"endpoint": CFG["analytics_endpoint"]} if CFG.get("analytics_endpoint") else {}
    tpl = open(os.path.join(HERE, "stream.html"), encoding="utf-8").read()
    for lang, sub in (("en", ""), ("zh", "zh/")):
        root = "../" if sub else ""
        vals = {"root": root, "lang": lang, "html_lang": "en" if lang == "en" else "zh-CN", "about_href": "about/", "privacy_href": "privacy/",
                "feed_href": "feed.xml", "meta_desc": B.esc(SLOGAN[lang]),
                "features": json.dumps(feats), "analytics": json.dumps(analytics),
                "maintainer_link": (f' · <a href="{B.esc(CFG["maintainer"].get("url", ""))}">{B.esc(CFG["maintainer"]["name"])}</a>'
                                    if (CFG.get("maintainer") or {}).get("name") else "")}
        page = tpl
        for k, v in vals.items():
            page = page.replace("{{" + k + "}}", v)
        assert "{{" not in page, "unfilled placeholder in stream.html"
        B.write(os.path.join(OUT, sub, "index.html"), page)
    site = CFG.get("site_url", "").rstrip("/")
    B.write(os.path.join(OUT, "llms.txt"), f"""# Openshelf

> {SLOGAN['en']} Research reports and explainers that people publish on their own websites (personal pages, lab blogs,
> company research sites), found through author feeds, archives and discussion sites, each checked by a language model,
> with the posts that answer them linked as threads. Chinese edition: {site}/zh/

## Data (free to reuse under CC BY 4.0; linked works belong to their authors)
- [All posts, responses and editorial decisions (JSON)]({site}/data/{B.ISSUE['issue']['slug']}.json)
- [Front-page data with summaries in English and Chinese (JS)]({site}/shelf-data.js)
- [Atom feed, English]({site}/feed.xml)
- [Atom feed, Chinese]({site}/zh/feed.xml)

## About
- [About and method]({site}/about/)
- [Privacy]({site}/privacy/)
- [Source code and submissions]({repo})
""")
    if CFG.get("domain"):
        B.write(os.path.join(OUT, "CNAME"), CFG["domain"].strip() + "\n")
    print("openshelf build done:", OUT, "| features", feats)


if __name__ == "__main__":
    main()
