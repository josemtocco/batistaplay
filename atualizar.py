import asyncio, json, logging, re, unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

BASE="https://www.batistaplay.net/"
HOSTS={"batistaplay.net","www.batistaplay.net"}
STREAM_RE=re.compile(r'https?://[^\'"\s<>]+?(?:\.m3u8|\.mpd)(?:\?[^\'"\s<>]*)?',re.I)
GENERIC={"guia de programação","guia de programacao","programação","programacao","canais","home","início","inicio","assistir","play","ao vivo","live","batista play","tv","menu","pesquisar","buscar","login","entrar"}
def norm(s):
    s=unicodedata.normalize("NFKD",s or "")
    return re.sub(r"\s+"," ","".join(c for c in s if not unicodedata.combining(c))).strip().casefold()
GENERIC={norm(x) for x in GENERIC}
def clean(s): return re.sub(r"\s+"," ",(s or "")).strip(" -|•·:_")
def good(s):
    s=clean(s); n=norm(s)
    return bool(s) and n not in GENERIC and not n.startswith("guia de programacao") and 2<=len(s)<=100
def unique(xs):
    out=[]; seen=set()
    for x in xs:
        x=clean(x); k=norm(x)
        if good(x) and k not in seen: seen.add(k); out.append(x)
    return out
def slug(url):
    p=urlparse(url).path.strip("/")
    if not p:return ""
    x=re.sub(r"\.(html?|php)$","",p.split("/")[-1],flags=re.I)
    return clean(re.sub(r"[-_] +"," ",x).title()) or clean(x.title())
async def names(page):
    out=[]
    for sel in ["article",".card",".channel",".canal","[class*='channel']","[class*='canal']","[class*='card']","a","button","[role='button']"]:
        try:
            for t in await page.locator(sel).all_inner_texts():
                out += [clean(x) for x in re.split(r"[\r\n]+",t) if good(x)]
        except: pass
    return unique(out)
async def links(page):
    out=[]
    try:
        data=await page.locator("a[href]").evaluate_all("els=>els.map(a=>({href:a.href,text:(a.innerText||a.textContent||'').trim()}))")
        for x in data:
            if x["href"] and good(x["text"]): out.append((x["href"],clean(x["text"])))
    except: pass
    return out
async def heads(page):
    out=[]
    for s in ["h1","h2","h3","h4"]:
        try: out+=await page.locator(s).all_inner_texts()
        except: pass
    return unique(out)
async def streams(page):
    found=set()
    try: found.update(STREAM_RE.findall(await page.content()))
    except: pass
    for s in ["video[src]","source[src]","iframe[src]"]:
        try:
            for x in await page.locator(s).evaluate_all("els=>els.map(e=>e.src||e.getAttribute('src')||'')"):
                if re.search(r"\.(m3u8|mpd)(?:\?|$)",x,re.I): found.add(x)
        except: pass
    return found
async def active(page,url):
    try:
        r=await page.request.get(url,timeout=10000)
        if not 200<=r.status<400:return False
        ct=(r.headers.get("content-type") or "").lower()
        if any(x in ct for x in ["mpegurl","dash","video"]):return True
        try:
            b=(await r.text())[:6000].lower()
            return "#extm3u" in b or "<mpd" in b
        except:return "octet-stream" in ct
    except:return False
def choose(inherited,vis,hs,title,url):
    c=[]
    if good(inherited):c.append(inherited)
    c+=vis+hs
    if good(title):c.append(title)
    if good(slug(url)):c.append(slug(url))
    u=unique(c)
    return u[0] if u else "Canal"
async def main():
    logging.basicConfig(filename="atualizacao.log",level=logging.INFO,format="%(asctime)s UTC %(levelname)s %(message)s")
    q=[(BASE,"")]; seen=set(); rec=[]; errors=[]
    async with async_playwright() as pw:
        browser=await pw.chromium.launch(headless=True)
        ctx=await browser.new_context(ignore_https_errors=True,user_agent="Mozilla/5.0 Chrome/140 Safari/537.36")
        while q and len(seen)<80:
            url,inherited=q.pop(0)
            if url in seen or urlparse(url).netloc.lower() not in HOSTS:continue
            seen.add(url); page=await ctx.new_page(); net=set()
            async def onresp(r):
                if re.search(r"\.(m3u8|mpd)(?:\?|$)",r.url,re.I):net.add(r.url)
            page.on("response",onresp)
            try:
                await page.goto(url,wait_until="domcontentloaded",timeout=30000)
                try: await page.wait_for_load_state("networkidle",timeout=8000)
                except PlaywrightTimeoutError: pass
                await page.wait_for_timeout(2500)
                ls=await links(page); vis=await names(page); hs=await heads(page)
                for href,label in ls:
                    if urlparse(href).netloc.lower() in HOSTS and href not in seen and len(q)<80:q.append((href,label))
                st=await streams(page); await page.wait_for_timeout(1000); st|=net
                nm=choose(inherited,vis,hs,await page.title(),url)
                for s in st:rec.append({"name":nm,"category":"TV","url":s,"source":url})
            except Exception as e:errors.append(f"{url}: {e}")
            finally:await page.close()
        test=await ctx.new_page(); final=[]; used=set()
        for r in rec:
            if r["url"] in used:continue
            used.add(r["url"])
            if await active(test,r["url"]):final.append(r)
        await test.close(); await browser.close()
    by={}
    for r in final:
        if r["url"] not in by or (good(r["name"]) and len(r["name"])>len(by[r["url"]]["name"])):by[r["url"]]=r
    final=list(by.values()); final.sort(key=lambda x:norm(x["name"]))
    lines=["#EXTM3U"]
    for r in final:
        n=r["name"].replace('"',"'")
        lines += [f'#EXTINF:-1 tvg-name="{n}" tvg-country="BR" tvg-language="Portuguese" group-title="{r["category"]}",{n}',r["url"]]
    Path("batistaplay.m3u").write_text("\n".join(lines)+"\n",encoding="utf-8")
    Path("descobertos.json").write_text(json.dumps({"site":BASE,"paginas_analisadas":len(seen),"streams_descobertos":len(rec),"streams_ativos":len(final),"canais":[{"nome":r["name"],"categoria":r["category"],"url":r["url"],"origem":r["source"]} for r in final],"erros":errors,"atualizado_em":datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")},ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
if __name__=="__main__":asyncio.run(main())
