"""「いつもチェック」の商品を Yahoo!ショッピングで調べて docs/watch.json に書き出す。
商品リストは Google スプレッドシート（リンクを知っている人が閲覧可）から読む。
決めた値段以下になったら、または過去最安を更新したら alerts.txt に書き出す（GitHub が通知に使う）。

環境変数: YAHOO_APPID（必須）, WATCH_SHEET_ID（必須）, VC_AFFILIATE_ID（任意）
"""
import csv
import html
import io
import json
import os
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta

API = "https://shopping.yahooapis.jp/ShoppingWebService/V3/itemSearch"
JST = timezone(timedelta(hours=9))
OUT = "docs/watch.json"
HISTORY = "docs/watch_history.json"
ALERTS = "alerts.txt"


def norm(s):
    return unicodedata.normalize("NFKC", s or "").lower()


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "yasune-cho/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8")


def read_sheet(sheet_id):
    url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?tqx=out:csv"
    rows = list(csv.reader(io.StringIO(get(url))))
    items = []
    for r in rows[1:]:
        r = (r + [""] * 5)[:5]
        name, jan, target, show, memo = [c.strip() for c in r]
        if not name and not jan:
            continue
        digits = "".join(ch for ch in norm(target) if ch.isdigit())
        items.append({
            "name": name, "jan": jan,
            "target": int(digits) if digits else None,
            "show": show not in ("×", "x", "X", "✕", "いいえ", "no"),
        })
    return items


def search(item):
    params = {
        "appid": os.environ["YAHOO_APPID"],
        "in_stock": "true",
        "condition": "new",
        "sort": "-score",
        "results": 30,
        "image_size": 300,
    }
    if item["jan"]:
        params["jan_code"] = item["jan"]
    else:
        params["query"] = item["name"]
    aff = os.environ.get("VC_AFFILIATE_ID")
    if aff:
        params["affiliate_type"] = "vc"
        params["affiliate_id"] = aff
    data = json.loads(get(API + "?" + urllib.parse.urlencode(params)))
    words = [w for w in norm(item["name"]).split() if w]
    best = None
    for h in data.get("hits", []):
        name = html.unescape(h.get("name", ""))
        # JANで探したときは全部対象。名前で探したときは、言葉が全部入っている商品だけ
        flat = norm(name).replace(" ", "")  # 「サントリー天然水」と「サントリー 天然水」を同じに扱う
        if not item["jan"] and not all(w in flat for w in words):
            continue
        price = h.get("price")
        if not price:
            continue
        if best is None or price < best["price"]:
            pt = (h.get("point") or {}).get("lyLimitedBonusAmount") or 0
            best = {
                "title": name.strip(), "price": price, "points": pt,
                "url": h.get("url"),
                "image": (h.get("exImage") or {}).get("url") or (h.get("image") or {}).get("medium"),
                "shop": (h.get("seller") or {}).get("name"),
            }
    return best


def main():
    sheet = os.environ["WATCH_SHEET_ID"]
    try:
        history = json.load(open(HISTORY, encoding="utf-8"))
    except Exception:
        history = {}
    items = read_sheet(sheet)
    out, alerts = [], []
    for i, it in enumerate(items):
        if i:
            time.sleep(1.5)  # 利用制限: 1秒1回まで
        key = it["jan"] or norm(it["name"])
        try:
            best = search(it)
        except Exception as e:
            print(f"[{it['name']}] 取得失敗: {e}", file=sys.stderr)
            best = None
        h = history.get(key, {"min": None, "alerted": None})
        row = {"name": it["name"], "show": it["show"], "target": it["target"], "found": bool(best)}
        if best:
            price = best["price"]
            new_min = h["min"] is None or price < h["min"]
            hit_target = it["target"] is not None and price <= it["target"]
            hit_min = it["target"] is None and new_min and h["min"] is not None
            # 同じ値段で何度も通知しないよう、前回通知した値段より下がったときだけ知らせる
            if (hit_target or hit_min) and (h["alerted"] is None or price < h["alerted"]):
                why = f"目標 {it['target']:,}円 以下" if hit_target else f"過去最安を更新（前回の最安 {h['min']:,}円）"
                alerts.append(f"- **{it['name']}**：{price:,}円（{why}）\n  {best['shop'] or ''} {best['url']}")
                h["alerted"] = price
            if new_min:
                h["min"] = price
            row.update(best)
            row["min"] = h["min"]
        history[key] = h
        out.append(row)
        print(f"[{it['name']}] {'見つからず' if not best else str(best['price']) + '円'}")

    json.dump({"updated": datetime.now(JST).isoformat(timespec="minutes"), "items": out},
              open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump(history, open(HISTORY, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    if alerts:
        open(ALERTS, "w", encoding="utf-8").write(
            "ヤスネ帳の「いつもチェック」で値下がりがありました。\n\n" + "\n".join(alerts) + "\n")
    print(f"{len(out)}件を確認、通知 {len(alerts)}件")


if __name__ == "__main__":
    main()
