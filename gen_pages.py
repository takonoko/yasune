"""検索に載るように、商品ごとの静的ページ（item-<id>.html）と sitemap.xml を作る。
fetch_deals.py のあと（GitHub Actions）に動かす。

- item.html をひな形にして、タイトル・説明・商品データ（JSON-LD）・本文の下書きを入れる
  （見た目はこれまでどおり item.html のスクリプトが作る）
- 掲載が終わった商品のページは14日残してから消す（検索から来た人に「終了」と出す）
- index.html の一覧にも、スクリプトなしで読めるリンクを入れる（検索ロボット用）
"""
import html
import json
import os
import re
from datetime import datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))
BASE = "https://takonoko.github.io/yasune/"
DOCS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs")
KEEP_DAYS = 14
FIXED = [("", "hourly", "1.0"), ("muji.html", "daily", "0.7"), ("about.html", "monthly", "0.4"),
         ("privacy.html", "yearly", "0.2")]


def esc(s):
    return html.escape(str(s or ""), quote=True)


def short_name(name):
    n = re.sub(r"【[^】]*】|［[^］]*］|\[[^\]]*\]|＼[^／]*／|★|☆|◆|■|送料無料|ポイント\d+倍|クーポン(対象|利用で)?[^ ]*", " ", name or "")
    n = re.sub(r"\s+", " ", n).strip(" |｜/・")
    return n[:48] or (name or "")[:48]


def best_real(d):
    vals = [v for v in (d.get("real"), d.get("free_real"), d.get("max_real")) if isinstance(v, (int, float)) and v > 0]
    return int(min(vals)) if vals else d.get("now")


def page_for(tpl, d, updated):
    pid = d["id"]
    name = short_name(d["name"])
    real = best_real(d)
    now = d.get("now") or 0
    when = updated[:16].replace("T", " ")
    title = f"{name}｜実質{real:,}円（ポイント・クーポン込み）- ヤスネ帳"
    bits = [f"セール価格{now:,}円"]
    if d.get("coupon_price"):
        bits.append(f"クーポンで{d['coupon_price']:,}円")
    bits.append(f"ポイント還元込みの実質{real:,}円")
    desc = f"{name}。" + "、".join(bits) + f"。{d.get('shop') or 'Yahoo!ショッピング'}。{when}時点の価格です。"
    url = f"{BASE}item-{pid}.html"
    ld = {"@context": "https://schema.org", "@type": "Product", "name": d["name"][:150],
          "image": [d["image"]] if d.get("image") else None,
          "brand": {"@type": "Brand", "name": d["maker"]} if d.get("maker") else None,
          "gtin13": d["jan"] if re.fullmatch(r"\d{13}", str(d.get("jan") or "")) else None,
          "offers": {"@type": "Offer", "price": now, "priceCurrency": "JPY", "url": d.get("page") or url,
                     "availability": "https://schema.org/InStock",
                     "seller": {"@type": "Organization", "name": d.get("shop") or "Yahoo!ショッピング"}}}
    if d.get("rating") and d.get("reviews"):
        ld["aggregateRating"] = {"@type": "AggregateRating", "ratingValue": d["rating"], "reviewCount": d["reviews"]}
    ld = {k: v for k, v in ld.items() if v is not None}
    crumbs = {"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [
        {"@type": "ListItem", "position": 1, "name": "ヤスネ帳", "item": BASE},
        {"@type": "ListItem", "position": 2, "name": name, "item": url}]}
    head = (f'<title>{esc(title)}</title>\n<meta name="description" content="{esc(desc)}">\n'
            f'<link rel="canonical" href="{url}">\n'
            f'<meta property="og:type" content="product">\n<meta property="og:site_name" content="ヤスネ帳">\n'
            f'<meta property="og:title" content="{esc(title)}">\n<meta property="og:description" content="{esc(desc)}">\n'
            f'<meta property="og:url" content="{url}">\n'
            + (f'<meta property="og:image" content="{esc(d["image"])}">\n' if d.get("image") else "")
            + '<meta name="twitter:card" content="summary">\n'
            f'<script type="application/ld+json">{json.dumps(ld, ensure_ascii=False)}</script>\n'
            f'<script type="application/ld+json">{json.dumps(crumbs, ensure_ascii=False)}</script>')
    body = (f'<main id="main"><h1>{esc(d["name"])}</h1>'
            f'<p>セール価格 {now:,}円 ／ ポイント・クーポン込みの実質 <b>{real:,}円</b>（{esc(when)}時点）</p>'
            + (f'<p>{esc(d["coupon"])}</p>' if d.get("coupon") else "")
            + (f'<p>{esc(d["comment"])}</p>' if d.get("comment") else "")
            + f'<p>販売：{esc(d.get("shop") or "")}　<a href="{esc(d.get("url"))}" rel="noopener sponsored">お店で見る</a></p></main>')
    s = tpl
    s = re.sub(r"<title>.*?</title>\s*<meta name=\"description\"[^>]*>", lambda m: head, s, count=1, flags=re.S)
    s = s.replace('<main id="main"><p class="empty">読み込み中…</p></main>', body, 1)
    s = s.replace('const id=new URLSearchParams(location.search).get("id");', f'const id={json.dumps(pid)};', 1)
    return s


def ended_page(tpl, info):
    pid = info["id"]
    s = tpl
    head = (f'<title>{esc(info["name"])}（掲載終了）- ヤスネ帳</title>\n'
            f'<meta name="description" content="この商品のセール掲載は終わりました。ヤスネ帳ではYahoo!ショッピングのセール品をポイント込みの実質価格でまとめています。">\n'
            f'<meta name="robots" content="noindex">\n<link rel="canonical" href="{BASE}item-{pid}.html">')
    s = re.sub(r"<title>.*?</title>\s*<meta name=\"description\"[^>]*>", lambda m: head, s, count=1, flags=re.S)
    s = s.replace('<main id="main"><p class="empty">読み込み中…</p></main>',
                  f'<main id="main"><p class="empty">「{esc(info["name"])}」の掲載は終わりました（最後に確認：{esc(info["last"][:10])}）。<br><a href="./">いま安い商品の一覧へ</a></p></main>', 1)
    s = s.replace('const id=new URLSearchParams(location.search).get("id");', f'const id={json.dumps(pid)};', 1)
    return s


def static_list(deals):
    cards = []
    for d in deals[:120]:
        cards.append(f'<a class="card" href="item-{d["id"]}.html"><span>{esc(short_name(d["name"]))}</span> '
                     f'<b>実質{best_real(d):,}円</b></a>')
    return "<!--S-->" + "".join(cards) + "<!--/S-->"


def main():
    with open(os.path.join(DOCS, "deals.json"), encoding="utf-8") as f:
        dj = json.load(f)
    deals = [d for d in dj.get("deals", []) if d.get("id")]
    updated = dj.get("updated") or datetime.now(JST).isoformat(timespec="minutes")
    with open(os.path.join(DOCS, "item.html"), encoding="utf-8") as f:
        tpl = f.read()
    idx_path = os.path.join(DOCS, "pages.json")
    try:
        with open(idx_path, encoding="utf-8") as f:
            idx = json.load(f)
    except Exception:
        idx = {}
    now = datetime.now(JST)
    live = set()
    for d in deals:
        with open(os.path.join(DOCS, f"item-{d['id']}.html"), "w", encoding="utf-8") as f:
            f.write(page_for(tpl, d, updated))
        live.add(d["id"])
        idx[d["id"]] = {"id": d["id"], "name": short_name(d["name"]), "last": now.isoformat(timespec="minutes")}
    for pid in list(idx):
        if pid in live:
            continue
        info = idx[pid]
        path = os.path.join(DOCS, f"item-{pid}.html")
        if now - datetime.fromisoformat(info["last"]) > timedelta(days=KEEP_DAYS):
            if os.path.exists(path):
                os.remove(path)
            del idx[pid]
            continue
        with open(path, "w", encoding="utf-8") as f:
            f.write(ended_page(tpl, info))
    with open(idx_path, "w", encoding="utf-8") as f:
        json.dump(idx, f, ensure_ascii=False)
    day = now.strftime("%Y-%m-%d")
    urls = [f"  <url><loc>{BASE}{p}</loc><lastmod>{day}</lastmod><changefreq>{c}</changefreq><priority>{pr}</priority></url>"
            for p, c, pr in FIXED]
    urls += [f"  <url><loc>{BASE}item-{pid}.html</loc><lastmod>{day}</lastmod><changefreq>daily</changefreq><priority>0.6</priority></url>"
             for pid in sorted(live)]
    with open(os.path.join(DOCS, "sitemap.xml"), "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
                + "\n".join(urls) + "\n</urlset>\n")
    ip = os.path.join(DOCS, "index.html")
    with open(ip, encoding="utf-8") as f:
        s = f.read()
    lst = static_list(deals)
    if "<!--S-->" in s:
        s = re.sub(r"<!--S-->.*?<!--/S-->", lambda m: lst, s, count=1, flags=re.S)
    else:
        s = s.replace('<section class="grid" id="grid"></section>', f'<section class="grid" id="grid">{lst}</section>', 1)
    s = s.replace("item.html?id=${encodeURIComponent(d.id)}", "item-${encodeURIComponent(d.id)}.html")
    with open(ip, "w", encoding="utf-8") as f:
        f.write(s)
    print(f"[ページ] 商品ページ {len(live)}件・終了ページ {len(idx) - len(live)}件・サイトマップ {len(urls)}件")


if __name__ == "__main__":
    main()
