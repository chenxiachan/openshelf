# Shared helpers for the GitPost crawlers (stdlib only).
# Network rule (user's machine): <= 6 concurrent, <= 90 requests/min, resumable.
# Live control: data/ctl/rpm holds a number (requests/min); data/ctl/PAUSE pauses all requests while it exists.
import gzip, html, json, os, re, ssl, threading, time, urllib.request
from urllib.parse import urlparse, urljoin

CTX = ssl.create_default_context(cafile="/etc/ssl/cert.pem") if os.path.exists("/etc/ssl/cert.pem") else ssl.create_default_context()
UA = "Mozilla/5.0 (compatible; openshelf-crawler/0.3; +https://chenxiachan.github.io/openshelf/about/)"
HTML_HEADERS = {"User-Agent": UA, "Accept": "text/html,*/*;q=0.5"}

EXCLUDE = set("""youtube.com youtu.be twitter.com x.com reddit.com old.reddit.com en.wikipedia.org github.com gist.github.com
arxiv.org theguardian.com nytimes.com reuters.com wsj.com bbc.com bbc.co.uk bloomberg.com ft.com theverge.com techcrunch.com
tomshardware.com wired.com theatlantic.com arstechnica.com theregister.com phoronix.com cnbc.com cnn.com economist.com apnews.com
washingtonpost.com npr.org nbcnews.com cbsnews.com axios.com businessinsider.com forbes.com theinformation.com politico.com
news.ycombinator.com bsky.app mastodon.social linkedin.com facebook.com instagram.com tiktok.com vimeo.com twitch.tv
apps.apple.com play.google.com amazon.com 404media.co engadget.com zdnet.com venturebeat.com fortune.com time.com
newyorker.com vox.com slate.com latimes.com theconversation.com independent.co.uk telegraph.co.uk dailymail.co.uk""".split())


def dom(u):
    try:
        return urlparse(u).netloc.lower().split("@")[-1].split(":")[0].removeprefix("www.")
    except Exception:
        return ""


def norm(u):
    """host+path key used to match links to candidate pages (same as the probe)."""
    try:
        p = urlparse(u.strip())
        p.port
    except Exception:
        return None
    if p.scheme not in ("http", "https"):
        return None
    host = p.netloc.lower().split("@")[-1].split(":")[0].removeprefix("www.")
    if not host:
        return None
    path = re.sub(r'/(index\.html?)?$', '', p.path) or ""
    m = re.match(r'^/posts/([A-Za-z0-9]+)(/.*)?$', path)
    if host in ("lesswrong.com", "alignmentforum.org", "greaterwrong.com") and m:
        path = "/posts/" + m.group(1)
    return host + path


def strip(h):
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', h or ''))).strip()


def meta(t, name):
    m = re.search(rf'<meta\s+[^>]*(?:name|property)\s*=\s*["\']{name}["\'][^>]*content\s*=\s*["\']([^"\']*)', t, re.I) or \
        re.search(rf'<meta\s+[^>]*content\s*=\s*["\']([^"\']*)["\'][^>]*(?:name|property)\s*=\s*["\']{name}["\']', t, re.I)
    return html.unescape(m.group(1)).strip()[:400] if m else ""


def main_text(t):
    t2 = re.sub(r'<(script|style|nav|header|footer|noscript|svg)\b.*?</\1>', ' ', t, flags=re.S | re.I)
    for tag in ("article", "main"):
        m = re.search(rf'<{tag}\b[^>]*>(.*?)</{tag}>', t2, re.S | re.I)
        if m and len(strip(m.group(1)).split()) > 80:
            return strip(m.group(1))
    m = re.search(r'<body\b[^>]*>(.*)', t2, re.S | re.I)
    return strip(m.group(1) if m else t2)


INTER = re.compile(r'(webgl|three(\.module)?(\.min)?\.js|/three@|d3(\.v\d)?(\.min)?\.js|/d3@|plotly|observablehq|tfjs|tensorflow\.js|onnxruntime|pyodide|regl|swissgl|vega(-lite|-embed)?|bokeh|p5(\.min)?\.js|pixi|twgl|getContext\(["\']webgl)', re.I)


def feat(text):
    """Rule features, identical to probe_hn_2026-11-06/scripts/features.py."""
    f = {}
    m = re.search(r'<title[^>]*>(.*?)</title>', text, re.S | re.I)
    f["title"] = html.unescape(m.group(1).strip())[:200] if m else ""
    metas = {}
    for mm in re.finditer(r'<meta\s+[^>]*>', text, re.I):
        n = re.search(r'(?:name|property)\s*=\s*["\']([^"\']+)', mm.group(0), re.I)
        if n:
            metas[n.group(1).lower()] = metas.get(n.group(1).lower(), 0) + 1
    f["cit_title"] = "citation_title" in metas
    f["cit_authors"] = metas.get("citation_author", 0)
    f["cit_pdf"] = "citation_pdf_url" in metas
    f["cit_venue"] = any(k in metas for k in ("citation_journal_title", "citation_conference_title", "citation_publisher", "dc.publisher", "prism.publicationname"))
    f["cit_doi"] = "citation_doi" in metas
    f["arxiv"] = len(set(re.findall(r'arxiv\.org/(?:abs|pdf|html)/(\d{4}\.\d{4,5})', text)))
    f["doi"] = len(set(re.findall(r'doi\.org/(10\.[^"\'\s<>]+)', text)))
    f["openreview"] = "openreview.net/forum" in text
    f["bibtex"] = bool(re.search(r'@(article|inproceedings|misc|techreport|online|unpublished)\s*\{', text, re.I))
    f["distill"] = bool(re.search(r'distill\.pub/template|<d-article|<d-front-matter|<d-byline|<d-title', text, re.I))
    f["projpage"] = bool(re.search(r'class=["\'][^"\']*publication-title|nerfies', text, re.I))
    f["alfolio"] = "al-folio" in text.lower()
    repos = set(r.lower() for r in re.findall(r'github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)', text))
    repos = {r.removesuffix(".git") for r in repos if r.split("/")[0] not in ("sponsors", "features", "topics", "about", "login", "nerfies", "alshedivat", "academicpages", "eliahuhorwitz")}
    f["repos"] = len(repos)
    f["hf"] = bool(re.search(r'huggingface\.co/(spaces|datasets|[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+)', text))
    f["colab"] = "colab.research.google.com" in text
    f["canvas"] = len(re.findall(r'<canvas', text, re.I))
    f["inter_libs"] = sorted(set(m.group(1).lower().split('.')[0].split('@')[0].strip('/') for m in INTER.finditer(text)))[:8]
    srcs = re.findall(r'<script[^>]+src=["\']([^"\']+)', text, re.I)
    f["local_scripts"] = sum(1 for x in srcs if not re.match(r'(https?:)?//', x) and not re.search(r'(jquery|bootstrap|gtag|analytics|mathjax|katex|_next/|_astro/|webpack|chunk|runtime|polyfill|vendor|fontawesome|bulma)', x, re.I))
    body = re.sub(r'<script.*?</script>|<style.*?</style>', ' ', text, flags=re.S | re.I)
    f["words"] = len(re.sub(r'<[^>]+>', ' ', body).split())
    return f


A_RE = re.compile(r'<a\s[^>]*?href\s*=\s*["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.S | re.I)


def out_links(t, final, cap=300):
    """Outbound links to other hosts: [[norm_url, anchor], ...]. Internal links are dropped."""
    own = dom(final)
    seen, out = set(), []
    for m in A_RE.finditer(t):
        try:
            n = norm(urljoin(final, html.unescape(m.group(1))))
        except Exception:
            continue
        if not n or n in seen or n.split("/")[0] == own:
            continue
        seen.add(n)
        out.append([n, strip(m.group(2))[:80]])
        if len(out) >= cap:
            break
    return out


class Throttle:
    """Global request pacing shared by all threads in this process."""

    def __init__(self, ctl_dir, default_rpm=90.0):
        self.ctl, self.rpm, self.lock, self.next, self.checked = ctl_dir, default_rpm, threading.Lock(), 0.0, 0.0
        self.default = default_rpm

    def _refresh(self):
        now = time.monotonic()
        if now - self.checked < 20:
            return
        self.checked = now
        try:
            v = float(open(os.path.join(self.ctl, "rpm")).read().strip())
            self.rpm = max(5.0, min(v, 90.0))  # never above 90 without asking the user
        except Exception:
            self.rpm = self.default

    def wait(self):
        while os.path.exists(os.path.join(self.ctl, "PAUSE")):
            time.sleep(10)
        with self.lock:
            self._refresh()
            now = time.monotonic()
            wait = self.next - now
            self.next = max(now, self.next) + 60.0 / self.rpm
        if wait > 0:
            time.sleep(wait)


def get_json(url, T, headers=None, timeout=30, tries=4):
    h = {"User-Agent": UA}
    h.update(headers or {})
    for attempt in range(tries):
        T.wait()
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=timeout, context=CTX) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (404, 410, 451):
                return {"__http": e.code}
            print("retry", attempt, "HTTP", e.code, url[:90], flush=True)
        except Exception as e:
            print("retry", attempt, type(e).__name__, url[:90], flush=True)
        time.sleep(5 + 10 * attempt)
    return None


class ShardWriter:
    """Append records to plain jsonl shards; finished shards are gzipped. Crash-safe and resumable."""

    def __init__(self, d, prefix, size=2000):
        self.d, self.prefix, self.size, self.f, self.n = d, prefix, size, None, 0
        os.makedirs(d, exist_ok=True)
        for p in sorted(os.listdir(d)):  # leftovers from a crash: drop a truncated last line, gzip
            if p.startswith(prefix) and p.endswith(".jsonl"):
                self._seal(os.path.join(d, p))

    def _seal(self, path):
        good = []
        for l in open(path, encoding="utf-8"):
            try:
                json.loads(l)
                good.append(l if l.endswith("\n") else l + "\n")
            except Exception:
                pass
        with gzip.open(path + ".gz", "wt", encoding="utf-8") as g:
            g.writelines(good)
        os.remove(path)

    def _next_path(self):
        i = 0
        while any(os.path.exists(os.path.join(self.d, f"{self.prefix}-{i:04d}{ext}")) for ext in (".jsonl", ".jsonl.gz")):
            i += 1
        return os.path.join(self.d, f"{self.prefix}-{i:04d}.jsonl")

    def write(self, rec):
        if self.f is None:
            self.path = self._next_path()
            self.f, self.n = open(self.path, "w", encoding="utf-8"), 0
        self.f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self.n += 1
        if self.n % 50 == 0:
            self.f.flush()
        if self.n >= self.size:
            self.close()

    def close(self):
        if self.f:
            self.f.close()
            self._seal(self.path)
            self.f = None


def read_shards(d, prefix):
    """Yield records from all shards (gz and plain). Later records for the same key win when callers build dicts."""
    if not os.path.isdir(d):
        return
    for p in sorted(os.listdir(d)):
        if not p.startswith(prefix):
            continue
        path = os.path.join(d, p)
        opener = gzip.open if p.endswith(".gz") else open
        try:
            with opener(path, "rt", encoding="utf-8") as f:
                for l in f:
                    try:
                        yield json.loads(l)
                    except Exception:
                        pass
        except EOFError:
            pass


def tagtext(block, name):
    m = re.search(rf'<{name}\b[^>]*>(.*?)</{name}>', block, re.S | re.I)
    if not m:
        return ""
    v = m.group(1)
    c = re.match(r'\s*<!\[CDATA\[(.*?)\]\]>\s*$', v, re.S)
    return c.group(1) if c else html.unescape(v) if name in ("content", "summary", "description") else v


def parse_feed(xml, base):
    out = []
    for b in re.findall(r'<entry\b.*?</entry>', xml, re.S | re.I) or re.findall(r'<item\b.*?</item>', xml, re.S | re.I):
        m = re.search(r'<link\b[^>]*rel=["\']alternate["\'][^>]*href=["\']([^"\']+)', b) or re.search(r'<link\b[^>]*href=["\']([^"\']+)', b)
        link = m.group(1) if m else strip(tagtext(b, "link"))
        content = tagtext(b, "content") or tagtext(b, "content:encoded") or tagtext(b, "description")
        out.append({"title": strip(tagtext(b, "title"))[:220], "url": urljoin(base, html.unescape(link)) if link else "",
                    "published": strip(tagtext(b, "published") or tagtext(b, "pubDate") or tagtext(b, "updated"))[:40],
                    "summary": strip(tagtext(b, "summary") or "")[:500], "excerpt": " ".join(strip(content).split()[:300]),
                    "links": sorted(set(re.findall(r'href=["\'](https?://[^"\']+)', html.unescape(content))))[:100]})
    return out
