import json, re, time
from pathlib import Path
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup

BASE = "https://www.batistaplay.net/"
OUT = Path(".")
M3U = OUT / "batistaplay.m3u"
REPORT = OUT / "descobertos.json"
LOG = OUT / "atualizacao.log"
TIMEOUT = 15
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; BatistaPlayM3U/1.0)"}

session = requests.Session()
session.headers.update(HEADERS)

def clean(s):
    return re.sub(r"\s+", " ", (s or "")).strip()

def get(url):
    r = session.get(url, timeout=TIMEOUT, allow_redirects=True)
    r.raise_for_status()
    return r

def same_site(url):
    try:
        return urlparse(url).netloc.endswith("batistaplay.net")
    except Exception:
        return False

def discover_pages():
    pages = {BASE}
    try:
        html = get(BASE).text
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a", href=True):
            u = urljoin(BASE, a["href"])
            if same_site(u):
                pages.add(u.split("#")[0])
        # common paths, useful when navigation is JS-driven
        for p in ["tv", "canais", "canais-de-tv", "ao-vivo", "live"]:
            pages.add(urljoin(BASE, p))
    except Exception:
        pass
    return sorted(pages)

def extract(page_url):
    html = get(page_url).text
    soup = BeautifulSoup(html, "html.parser")
    found = []

    # Capture HLS/DASH URLs from HTML, attributes and scripts.
    candidates = set()
    for tag in soup.find_all(True):
        for value in tag.attrs.values():
            vals = value if isinstance(value, list) else [value]
            for v in vals:
                if isinstance(v, str):
                    candidates.update(re.findall(r'https?://[^\'"\s<>]+(?:m3u8|mpd)(?:\?[^\'"\s<>]*)?', v, re.I))
    candidates.update(re.findall(r'https?://[^\'"\s<>]+(?:m3u8|mpd)(?:\?[^\'"\s<>]*)?', html, re.I))

    for stream in candidates:
        stream = stream.replace("\\/", "/").rstrip("),;")
        # Try to infer the closest visible channel name.
        name = ""
        for tag in soup.find_all(["a","article","div","li","h1","h2","h3","h4","button"]):
            txt = clean(tag.get_text(" ", strip=True))
            if txt and len(txt) <= 120:
                if stream.split("?")[0] in str(tag):
                    name = txt
                    break
        if not name:
            name = clean(soup.title.get_text() if soup.title else "") or "Canal"
        category = "Geral"
        parent = soup.find(string=re.compile(r"categoria|category|gênero|genero", re.I))
        if parent:
            category = clean(str(parent.parent.get_text(" ", strip=True)))[:80] or category
        found.append({"name": name, "category": category, "url": stream, "source": page_url})
    return found

def probe(url):
    try:
        r = session.get(url, timeout=8, stream=True, allow_redirects=True)
        ok = r.status_code == 200 and (
            "mpegurl" in r.headers.get("content-type","").lower()
            or "video" in r.headers.get("content-type","").lower()
            or url.lower().split("?")[0].endswith((".m3u8",".mpd"))
        )
        r.close()
        return ok
    except Exception:
        return False

def main():
    pages = discover_pages()
    all_items = []
    errors = []
    for page in pages:
        try:
            all_items.extend(extract(page))
        except Exception as e:
            errors.append({"url": page, "erro": str(e)})

    unique = {}
    for x in all_items:
        key = x["url"]
        unique[key] = x

    active = []
    for x in unique.values():
        if probe(x["url"]):
            active.append(x)

    # Deduplicate by stream and sort by category/name.
    active.sort(key=lambda x: (x["category"].casefold(), x["name"].casefold(), x["url"]))
    lines = ["#EXTM3U"]
    for x in active:
        name = clean(x["name"]) or "Canal"
        cat = clean(x["category"]) or "Geral"
        lines.append(f'#EXTINF:-1 tvg-name="{name}" tvg-country="BR" tvg-language="Portuguese" group-title="{cat}",{name}')
        lines.append(x["url"])
    M3U.write_text("\n".join(lines) + "\n", encoding="utf-8")

    report = {
        "site": BASE,
        "paginas_analisadas": len(pages),
        "streams_descobertos": len(unique),
        "streams_ativos": len(active),
        "canais": active,
        "erros": errors,
        "atualizado_em": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    LOG.write_text(
        f"Batista Play: páginas={len(pages)} descobertos={len(unique)} ativos={len(active)} erros={len(errors)}\n",
        encoding="utf-8"
    )

if __name__ == "__main__":
    main()
