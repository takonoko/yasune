"""Yahoo!ショッピング 商品検索API(v3)からセール品を集めて deals.json に書き出す。

環境変数:
  YAHOO_APPID   … Yahoo!デベロッパーネットワークの Client ID（必須）
  VC_AFFILIATE_ID … バリューコマースのアフィリエイトID（任意。入れると商品リンクが報酬対象になる）
  MOCK_DIR      … テスト用。指定するとAPIを呼ばずにこのフォルダの <ジャンル>.json を読む
"""
import html
import json
import os
import re
import sys
import time
import urllib.parse
import unicodedata
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
# スーパーで売っているメーカー名・まとめ買い商品でも探す（参考サイトで多かったもの）
FOOD_QUERIES = ["日清 12個", "カップヌードル 12個", "どん兵衛 12個", "サッポロ一番", "明星", "明治", "森永", "カルビー",
                "井村屋", "なとり", "ロッテ", "ハウス食品 カレー", "ヤマモリ", "永谷園", "マ・マー",
                "味の素", "パックご飯 24個", "キッコーマン", "UCC ドリップ", "ネスカフェ"]

# 飲み物はケース買い（×24本など）を探して「1本あたりの値段」で判断する。
# 定価からの値引きではなく、1本あたりが十分安いときだけ載せる
DRINK_GENRE = "飲み物"
DRINK_QUERIES = ["綾鷹 24本", "伊藤園 お茶 24本", "十六茶 24本", "麦茶 24本", "生茶 24本", "伊右衛門 24本",
                 "ウィルキンソン 24本", "炭酸水 24本", "コカ・コーラ 24本", "三ツ矢サイダー 24本", "カルピス 24本",
                 "天然水 2L 9本", "天然水 500ml 24本", "午後の紅茶 24本", "缶コーヒー 30本", "ボトルコーヒー 24本",
                 "カゴメ 野菜ジュース 30本", "アクエリアス 24本",
                 "スーパードライ 24缶", "一番搾り 24缶", "黒ラベル 24缶", "プレミアムモルツ 24缶", "サントリー生ビール 24缶",
                 "金麦 24缶", "本麒麟 24缶", "氷結 24缶", "ほろよい 24缶", "ノンアルコール 24缶"]
NONALC = re.compile(r"ノンアル|アルコールフリー|0\.00%|オールフリー|グリーンズフリー|カラダfree|ゼロイチ|アルコール0")
BEER = re.compile(r"ビール|スーパードライ|一番搾り|黒ラベル|プレミアムモルツ|プレモル|ヱビス|エビス|ラガー|晴れ風")
ALC_OTHER = re.compile(r"チューハイ|酎ハイ|サワー|ハイボール|金麦|本麒麟|のどごし|クリアアサヒ|麦とホップ|淡麗|発泡酒|新ジャンル|"
                       r"氷結|ほろよい|-196|ストロング|檸檬堂|こだわり酒場|ワイン|梅酒|日本酒|焼酎|リキュール")
DRINK_EXCLUDE = re.compile(r"粉末|スティック|ティーバッグ|ドリップ|インスタント|豆|ゼリー|サプリ|プロテイン|空き|空容器|ボトルのみ|ラベル用")

MIN_OFF = 15          # これ未満の値引き率は載せない（%）
MAX_OFF = 80          # これを超える値引き率は「通常価格」が怪しいので載せない（%）
EXCLUDE_SELLERS = {"zozo"}  # ファッション系のお店
# 柄物・ファッション系のマスクを外すための言葉（普通のマスクは残す）
MASK_FASHION = re.compile(r"柄|レース|花|水着|カラー|おしゃれ|オシャレ|刺繍|チェック|ドット|デザイン|ファッション|かわいい|可愛い|韓国|血色|くすみ|ラメ")
MIN_REVIEWS = 3       # レビューがほぼ無い商品は「通常価格」が当てにならないので除外
PER_GENRE = 30        # 1ジャンルあたり載せる最大件数
PER_MAKER = 3         # 同じジャンルに同じメーカーを載せる最大件数（Ankerだらけにしない）
FETCH = 100           # APIから1回に取る件数（上限100）
PER_MAKER_DRINK = 4   # 飲み物はメーカーが限られるので少し多め
PAGES = 3             # 1ジャンルあたり何ページ分取るか（有名メーカーで絞ると減るので多めに）


def call_api(genre_ids: str, start: int = 1, query: str = "", discounted: bool = True) -> dict:
    params = {
        "appid": os.environ["YAHOO_APPID"],
        "is_discounted": "true",
        "in_stock": "true",
        "condition": "new",
        "sort": "-review_count",
        "results": FETCH,
        "start": start,
        "image_size": 300,
    }
    if genre_ids:
        params["genre_category_id"] = genre_ids
    if query:
        params["query"] = query
    if not discounted:
        del params["is_discounted"]
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


def _n(s):
    return unicodedata.normalize("NFKC", s or "").lower()


def parse_pack(name: str):
    """商品名から「1本の容量(ml)」と「本数」を読む。読めなければ (None, None, None)。"""
    s = _n(name).replace("×", "x").replace("*", "x")
    ml = None
    m = re.search(r"(\d+(?:\.\d+)?)\s*(ml|l|リットル|g)(?![a-wyz])", s)
    if m:
        v = float(m.group(1))
        ml = v * 1000 if m.group(2) in ("l", "リットル") else v  # 缶コーヒーの「185g」はほぼ185ml
    count, unit = None, None
    for rx in (r"(\d+)\s*(本|缶|個|袋|食|パック)(?:入り?|パック)?\s*x\s*(\d+)\s*(?:ケース|箱|セット|個|パック)",
               r"x\s*(\d+)\s*(本|缶|個|袋|食|パック)",
               r"(\d+)\s*(本|缶|個|袋|食)\s*(?:入り?|セット|まとめ|\)|】|\]|\s|$)"):
        m = re.search(rx, s)
        if m:
            g = m.groups()
            if len(g) == 3 and g[2] and g[2].isdigit():
                count, unit = int(g[0]) * int(g[2]), g[1]
            elif g[0].isdigit():
                count, unit = int(g[0]), g[1]
            else:
                count, unit = int(g[1]) if g[1].isdigit() else None, g[1]
            break
    if unit == "パック":
        unit = "個"
    return ml, count, unit


def drink_limit(name: str, ml: float):
    """1本あたりの「これ以下なら安い」の上限（円）と種類を返す。"""
    s = _n(name)
    if NONALC.search(s) and (BEER.search(s) or "テイスト" in s or "フリー" in s):
        return 0.25 * ml, "ノンアル"            # 350ml缶で約88円
    if not NONALC.search(s):
        if BEER.search(s) and not ALC_OTHER.search(s):
            return 0.47 * ml, "ビール"          # 350ml缶で約165円
        if ALC_OTHER.search(s) or BEER.search(s):
            return 0.29 * ml, "お酒"            # 350ml缶で約100円
    if ml <= 300:
        return 60, "飲み物"
    if ml < 1000:
        return max(65, 0.10 * ml), "飲み物"      # 500〜650mlで65円
    return 0.08 * ml, "飲み物"                   # 2Lで160円


def to_drink(hit: dict, now_ts: int):
    pl = hit.get("priceLabel") or {}
    price = pl.get("discountedPrice") or hit.get("price")
    if not price:
        return None
    name = html.unescape(hit.get("name", "")).strip()
    if DRINK_EXCLUDE.search(name):
        return None
    ml, count, unit = parse_pack(name)
    if not ml or not count or count < 6 or count > 120 or unit not in ("本", "缶", "個"):
        return None
    if ml < 100 or ml > 2500:
        return None
    if (hit.get("shipping") or {}).get("code") != 2:  # 送料別だと1本あたりが当てにならない
        return None
    seller = hit.get("seller") or {}
    if seller.get("sellerId") in EXCLUDE_SELLERS:
        return None
    review = hit.get("review") or {}
    if (review.get("count") or 0) < MIN_REVIEWS:
        return None
    maker = find_maker([], name)
    if not maker:
        return None
    end = pl.get("periodEnd")
    if end and end < now_ts:
        end = None
    pt = hit.get("point") or {}
    points = pt.get("lyLimitedBonusAmount") or 0
    real = price - points
    per = real / count
    limit, kind = drink_limit(name, ml)
    if per > limit:
        return None
    was = pl.get("defaultPrice")
    off = round((1 - price / was) * 100) if was and was > price else None
    img = (hit.get("exImage") or {}).get("url") or (hit.get("image") or {}).get("medium")
    return {
        "genre": DRINK_GENRE, "store": "yahoo", "name": name, "url": hit.get("url"), "image": img,
        "was": was if off else None, "now": price, "off": off,
        "points": points, "premium_points": pt.get("lyLimitedPremiumBonusAmount") or 0,
        "real": real, "real_off": round((1 - real / was) * 100) if off else None,
        # 「安い」の上限より何%安いか。並べ替えに使う
        "value": round((1 - per / (limit / 0.8)) * 100),
        "unit_price": round(per, 1), "unit": "本" if unit != "缶" else "缶", "count": count, "kind": kind,
        "end": end if off else None, "shop": seller.get("name"), "maker": maker,
        "rating": review.get("rate"), "reviews": review.get("count"),
        "free_shipping": True, "jan": hit.get("janCode") or None,
    }


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
    name = html.unescape(hit.get("name", "")).strip()  # 「&amp;」などを普通の文字に戻す
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
    extra = {}
    if genre == "食品":
        _, count, unit = parse_pack(name)
        if count and 2 <= count <= 200 and unit:
            extra = {"unit_price": round(real / count, 1), "unit": unit, "count": count}
    return {**extra,
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

    # 飲み物（ケース買い・1本あたりで判断）
    if not os.environ.get("MOCK_DIR"):
        hits = []
        for q in DRINK_QUERIES:
            time.sleep(1.5)
            try:
                hits += call_api("", 1, q, discounted=False).get("hits", [])
            except Exception as e:
                print(f"[飲み物 {q}] 取得失敗: {e}", file=sys.stderr)
        cands = [d for d in (to_drink(h, now_ts) for h in hits) if d]
        cands.sort(key=lambda d: d["value"], reverse=True)
        picked, per_maker = [], {}
        for d in cands:
            keys = {d["name"]} | ({d["jan"]} if d["jan"] else set())
            if keys & seen or per_maker.get(d["maker"], 0) >= PER_MAKER_DRINK:
                continue
            per_maker[d["maker"]] = per_maker.get(d["maker"], 0) + 1
            seen |= keys
            picked.append(d)
        deals += picked[:PER_GENRE]
        print(f"[飲み物] {len(hits)}件取得 → 条件に合う {len(cands)}件 → {len(picked[:PER_GENRE])}件掲載")
        for d in picked[:PER_GENRE]:
            print(f"   {d['unit_price']}円/{d['unit']} ({d['kind']}) {d['name'][:50]}")

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
