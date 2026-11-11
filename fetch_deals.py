"""Yahoo!ショッピング 商品検索API(v3)からセール品を集めて deals.json に書き出す。

環境変数:
  YAHOO_APPID   … Yahoo!デベロッパーネットワークの Client ID（必須）
  VC_AFFILIATE_ID … バリューコマースのアフィリエイトID（任意。入れると商品リンクが報酬対象になる）
  MOCK_DIR      … テスト用。指定するとAPIを呼ばずにこのフォルダの <ジャンル>.json を読む
"""
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta

from makers import find_maker

API = "https://shopping.yahooapis.jp/ShoppingWebService/V3/itemSearch"
JST = timezone(timedelta(hours=9))

# ジャンル名 → Yahoo!のジャンルカテゴリID（カンマ区切りでOR検索）
GENRES = {
    "家電": "2505",
    "日用品": "2508",
    "食品": "2498",
    "ガジェット": "2502,2504",  # パソコン・周辺機器 / AV機器・カメラ
}

# 食品はジャンル一覧だと産地もの（カニ・牛タン等）ばかりになるので、
# スーパーで売っているメーカー名でも探す
FOOD_QUERIES = ["サントリー", "キリン", "アサヒ", "伊藤園", "コカ・コーラ", "日清", "明治",
                "カルビー", "味の素", "UCC", "ネスカフェ", "大塚製薬"]

MIN_OFF = 15          # これ未満の値引き率は載せない（%）
MAX_OFF = 80          # これを超える値引き率は「通常価格」が怪しいので載せない（%）
EXCLUDE_SELLERS = {"zozo"}  # ファッション系のお店
# 柄物・ファッション系のマスクを外すための言葉（普通のマスクは残す）
MASK_FASHION = re.compile(r"柄|レース|花|水着|カラー|おしゃれ|オシャレ|刺繍|チェック|ドット|デザイン|ファッション|かわいい|可愛い|韓国|血色|くすみ|ラメ")
MIN_REVIEWS = 3       # レビューがほぼ無い商品は「通常価格」が当てにならないので除外
PER_GENRE = 30        # 1ジャンルあたり載せる最大件数
PER_MAKER = 3         # 同じジャンルに同じメーカーを載せる最大件数（Ankerだらけにしない）
FETCH = 100           # APIから1回に取る件数（上限100）
PAGES = 3             # 1ジャンルあたり何ページ分取るか（有名メーカーで絞ると減るので多めに）


def call_api(genre_ids: str, start: int = 1, query: str = "") -> dict:
    params = {
        "appid": os.environ["YAHOO_APPID"],
        "genre_category_id": genre_ids,
        "is_discounted": "true",
        "in_stock": "true",
        "condition": "new",
        "sort": "-review_count",
        "results": FETCH,
        "start": start,
        "image_size": 300,
    }
    if query:
        params["query"] = query
    aff = os.environ.get("VC_AFFILIATE_ID")
    if aff:
        params["affiliate_type"] = "vc"
        params["affiliate_id"] = aff
    url = API + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "yasune-cho/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def load_mock(genre: str) -> dict:
    with open(os.path.join(os.environ["MOCK_DIR"], f"{genre}.json"), encoding="utf-8") as f:
        return json.load(f)


def to_deal(hit: dict, genre: str, now_ts: int):
    pl = hit.get("priceLabel") or {}
    was = pl.get("defaultPrice")
    sale = pl.get("discountedPrice")
    if not was or not sale or sale >= was:
        return None
    off = round((1 - sale / was) * 100)
    if off < MIN_OFF or off > MAX_OFF:
        return None
    seller = hit.get("seller") or {}
    if seller.get("sellerId") in EXCLUDE_SELLERS:
        return None
    name = hit.get("name", "").strip()
    if "マスク" in name and MASK_FASHION.search(name):
        return None
    end = pl.get("periodEnd")
    if end and end < now_ts:  # すでに終わったセール
        return None
    review = hit.get("review") or {}
    if (review.get("count") or 0) < MIN_REVIEWS:
        return None
    brands = [(hit.get("brand") or {}).get("name") or ""] + [b.get("name") or "" for b in hit.get("parentBrands") or []]
    maker = find_maker([b for b in brands if b], name)
    if not maker:  # 知らないメーカーは載せない
        return None
    img = (hit.get("exImage") or {}).get("url") or (hit.get("image") or {}).get("medium")
    pt = hit.get("point") or {}
    points = pt.get("lyLimitedBonusAmount") or 0             # 誰でももらえるストアポイント
    premium = pt.get("lyLimitedPremiumBonusAmount") or 0     # LYPプレミアム会員のポイント
    real = sale - points                                     # ポイントを引いた実質価格
    return {
        "genre": genre,
        "store": "yahoo",
        "name": name,
        "url": hit.get("url"),
        "image": img,
        "was": was,
        "now": sale,
        "off": off,
        "points": points,
        "premium_points": premium,
        "real": real,
        "real_off": round((1 - real / was) * 100),
        "end": end,
        "shop": seller.get("name"),
        "maker": maker,
        "rating": review.get("rate"),
        "reviews": review.get("count"),
        "free_shipping": (hit.get("shipping") or {}).get("code") == 2,
        "jan": hit.get("janCode") or None,
    }


def main(out_path: str) -> None:
    now_ts = int(time.time())
    deals, seen = [], set()
    for i, (genre, ids) in enumerate(GENRES.items()):
        if i and not os.environ.get("MOCK_DIR"):
            time.sleep(1.5)  # 利用制限: 1秒1回まで
        hits = []
        try:
            if os.environ.get("MOCK_DIR"):
                hits = load_mock(genre).get("hits", [])
            else:
                for p in range(PAGES):
                    if p:
                        time.sleep(1.5)
                    hits += call_api(ids, 1 + p * FETCH).get("hits", [])
                if genre == "食品":
                    for q in FOOD_QUERIES:
                        time.sleep(1.5)
                        hits += call_api(ids, 1, q).get("hits", [])
        except Exception as e:  # 途中で失敗しても、取れた分は使う
            print(f"[{genre}] 取得失敗: {e}", file=sys.stderr)
        cands = [d for d in (to_deal(h, genre, now_ts) for h in hits) if d]
        cands.sort(key=lambda d: d["real_off"], reverse=True)  # お得な順に見て、上から採用する
        picked, per_maker = [], {}
        for d in cands:
            keys = {d["name"]} | ({d["jan"]} if d["jan"] else set())  # 同じ商品の重複を除く
            if keys & seen:
                continue
            if per_maker.get(d["maker"], 0) >= PER_MAKER:
                continue
            per_maker[d["maker"]] = per_maker.get(d["maker"], 0) + 1
            seen |= keys
            picked.append(d)
        deals += picked[:PER_GENRE]
        print(f"[{genre}] {len(hits)}件取得 → {len(picked[:PER_GENRE])}件掲載")

    if not deals:
        # 全部失敗したときは前回のデータを残す（空のサイトにしない）
        print("掲載できる商品が0件のため、deals.json は更新しません", file=sys.stderr)
        sys.exit(1)

    out = {
        "updated": datetime.now(JST).isoformat(timespec="minutes"),
        "deals": deals,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"合計 {len(deals)} 件を {out_path} に書き出しました")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "docs/deals.json")
