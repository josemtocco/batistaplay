import asyncio
import json
import re
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

BASE = "https://www.batistaplay.net/"
HOST = "batistaplay.net"
MAX_PAGES = 80
OUT = Path(".")
M3U = OUT / "batistaplay.m3u"
REPORT = OUT / "descobertos.json"
LOG = OUT / "atualizacao.log"

STREAM_RE = re.compile(
    r'https?://[^\'"\s<>]+?(?:\.m3u8|\.mpd)(?:\?[^\'"\s<>]*)?',
    re.I,
)
STREAM_HINT_RE = re.compile(
    r'(?:src|file|url|stream|source|playlist|hls|dash)\s*["\':=]+\s*["\']([^"\']+)["\']',
    re.I,
)

def clean(s):
    return re.sub(r"\s+", " ", s or "").strip()

def same_site(url):
    try:
        host = urlparse(url).netloc.lower()
        return host == HOST or host.endswith("." + HOST)
    except Exception:
        return False

def normalize_url(url, base=BASE):
    if not url:
        return ""
    url = url.replace("\\/", "/").replace("\\u0026", "&").strip().strip("'\"")
    return urljoin(base, url)

def channel_name(page_title, link_text, headings):
    for value in (link_text, *headings, page_title):
        value = clean(value)
        if value and len(value) <= 120:
            bad = {"home", "início", "inicio", "assistir", "play", "ao vivo", "batista play"}
            if value.casefold() not in bad:
                return value
    return "Canal"

def category_from_context(text):
    text = clean(text)
    # Prefer recognizable category headings.
    for pat in [
        r"(?:categoria|category)\s*[:\-]\s*([^|]{2,80})",
        r"(?:gênero|genero)\s*[:\-]\s*([^|]{2,80})",
    ]:
        m = re.search(pat, text, re.I)
        if m:
            return clean(m.group(1))
    return "Geral"

async def extract_page(page, url, records, discovered_pages):
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(2500)
    except Exception as e:
        return {"url": url, "erro": str(e)}

    title = await page.title()
    body = await page.locator("body").inner_text(timeout=10000)
    headings = await page.locator("h1,h2,h3,h4").all_inner_texts()
    page_text = body[:100000]

    # Every internal link becomes a possible channel/category page.
    links = await page.locator("a[href]").evaluate_all(
        """els => els.map(a => ({
            href: a.href,
            text: (a.innerText || a.textContent || '').trim()
        }))"""
    )
    for item in links:
        u = normalize_url(item.get("href",""), url)
        if same_site(u):
            discovered_pages.add(u.split("#")[0])

    # Inspect HTML, attributes and inline scripts.
    html = await page.content()
    blobs = [html, page_text]
    for script in await page.locator("script").all_inner_texts():
        blobs.append(script)

    candidates = set()
    for blob in blobs:
        for match in STREAM_RE.findall(blob):
            candidates.add(normalize_url(match, url))
        for match in STREAM_HINT_RE.findall(blob):
            if "m3u8" in match.lower() or "mpd" in match.lower():
                candidates.add(normalize_url(match, url))

    # Capture iframe/player URLs. A channel may expose a player page instead
    # of exposing the media URL in the parent HTML.
    frames = await page.locator("iframe[src],video[src],source[src]").evaluate_all(
        """els => els.map(e => e.src).filter(Boolean)"""
    )
    for frame_url in frames:
        frame_url = normalize_url(frame_url, url)
        if frame_url:
            if re.search(r"\.(m3u8|mpd)(?:\?|$)", frame_url, re.I):
                candidates.add(frame_url)
            else:
                discovered_pages.add(frame_url)

    # Clicking channel/player cards can trigger a dynamically generated stream.
    buttons = await page.locator("a,button,[role=button]").evaluate_all(
        """els => els.map((e,i)=>({
            i,
            text:(e.innerText||e.textContent||'').trim(),
            href:e.href||'',
            onclick:e.getAttribute('onclick')||''
        })).filter(x=>x.text||x.href||x.onclick).slice(0,150)"""
    )

    for item in buttons:
        href = normalize_url(item.get("href",""), url)
        text = clean(item.get("text",""))
        onclick = item.get("onclick","")
        combined = f"{href} {onclick}"
        for match in STREAM_RE.findall(combined):
            candidates.add(normalize_url(match, url))
        if href and same_site(href):
            discovered_pages.add(href.split("#")[0])

    # Build records from direct discoveries.
    for stream in candidates:
        if stream.lower().startswith(("http://","https://")):
            records.append({
                "name": channel_name(title, "", headings),
                "category": category_from_context(" ".join(headings) + " " + page_text[:4000]),
                "url": stream,
                "source": url,
            })

    # Listen to network requests while clicking likely player/channel elements.
    async def on_response(response):
        u = response.url
        if re.search(r"\.(m3u8|mpd)(?:\?|$)", u, re.I):
            records.append({
                "name": channel_name(title, "", headings),
                "category": category_from_context(" ".join(headings) + " " + page_text[:4000]),
                "url": u,
                "source": url,
            })

    page.on("response", on_response)
    try:
        locator = page.locator("a,button,[role=button]")
        count = min(await locator.count(), 60)
        for i in range(count):
            try:
                el = locator.nth(i)
                txt = clean(await el.inner_text(timeout=1000))
                href = await el.get_attribute("href")
                # Focus on elements that look like a channel/player.
                if not (txt or href):
                    continue
                if href and (href.startswith("#") or href.startswith("javascript:")):
                    pass
                elif href and same_site(normalize_url(href, url)):
                    continue
                elif not re.search(r"tv|canal|play|assist|ao vivo|live|stream", f"{txt} {href or ''}", re.I):
                    continue
                await el.click(timeout=1800)
                await page.wait_for_timeout(1200)
            except Exception:
                continue
    finally:
        try:
            page.remove_listener("response", on_response)
        except Exception:
            pass

    return {"url": url, "streams": len(candidates), "links": len(links)}

async def probe(page, item):
    url = item["url"]
    try:
        response = await page.request.get(url, timeout=10000)
        if response.status < 200 or response.status >= 400:
            return False
        ctype = (response.headers.get("content-type") or "").lower()
        text = ""
        try:
            text = (await response.text())[:5000]
        except Exception:
            pass
        if "m3u8" in url.lower() or "#extm3u" in text.lower() or "mpegurl" in ctype:
            return True
        if ".mpd" in url.lower() or "dash+xml" in ctype or "<mpd" in text.lower():
            return True
        return "video/" in ctype
    except Exception:
        return False

async def main():
    started = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
    records = []
    pages = {BASE}
    errors = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140 Safari/537.36",
            ignore_https_errors=True,
            viewport={"width": 1440, "height": 900},
        )
        page = await context.new_page()

        # Crawl in rounds so channel pages discovered from the home page are visited.
        visited = set()
        for _ in range(4):
            queue = [u for u in list(pages) if u not in visited and same_site(u)]
            if not queue:
                break
            for url in queue[:MAX_PAGES - len(visited)]:
                if len(visited) >= MAX_PAGES:
                    break
                visited.add(url)
                result = await extract_page(page, url, records, pages)
                if result and result.get("erro"):
                    errors.append(result)

        # Deduplicate streams before testing.
        unique = {}
        for item in records:
            u = item["url"].split("#")[0]
            unique[u] = item

        active = []
        for item in unique.values():
            if await probe(page, item):
                active.append(item)

        await browser.close()

    # Deduplicate and sort for SS IPTV.
    final = {}
    for item in active:
        final[item["url"]] = item
    active = sorted(final.values(), key=lambda x: (
        clean(x["category"]).casefold(),
        clean(x["name"]).casefold(),
        x["url"],
    ))

    lines = ["#EXTM3U"]
    for item in active:
        name = clean(item["name"]) or "Canal"
        category = clean(item["category"]) or "Geral"
        name = name.replace('"', "'")
        category = category.replace('"', "'")
        lines.append(
            f'#EXTINF:-1 tvg-name="{name}" tvg-country="BR" '
            f'tvg-language="Portuguese" group-title="{category}",{name}'
        )
        lines.append(item["url"])
    M3U.write_text("\n".join(lines) + "\n", encoding="utf-8")

    report = {
        "site": BASE,
        "paginas_analisadas": len(visited),
        "paginas_descobertas": len(pages),
        "streams_descobertos": len(unique),
        "streams_ativos": len(active),
        "canais": active,
        "erros": errors,
        "atualizado_em": started,
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    LOG.write_text(
        f"{started}\nPáginas analisadas: {len(visited)}\n"
        f"Páginas descobertas: {len(pages)}\n"
        f"Streams descobertos: {len(unique)}\n"
        f"Streams ativos: {len(active)}\n"
        f"Erros: {len(errors)}\n",
        encoding="utf-8",
    )

if __name__ == "__main__":
    asyncio.run(main())
