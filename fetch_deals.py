"""Yahoo!ショッピング 商品検索API(v3)からセール品を集めて deals.json に書き出す。

環境変数:
  YAHOO_APPID   … Yahoo!デベロッパーネットワークの Client ID（必須）
  VC_AFFILIATE_ID … バリューコマースのアフィリエイトID（任意。入れると商品リンクが報酬対象になる）
  MOCK_DIR      … テスト用。指定するとAPIを呼ばずにこのフォルダの <ジャンル>.json を読む
"""
import hashlib
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
                       r"氷結|ほろよい|-196|ストロングゼロ|檸檬堂|こだわり酒場|ワイン|梅酒|日本酒|焼酎|リキュール")
DRINK_EXCLUDE = re.compile(r"粉末|スティック|ティーバッグ|ドリップ|インスタント|豆|ゼリー|サプリ|プロテイン|空き|空容器|ボトルのみ|ラベル用")

MIN_OFF = 15          # これ未満の値引き率は載せない（%）
# キャンペーン（デイリーボーナス等）の今日の最大付与率は「お得な買い時」シートから読む
KAIDOKI_SHEET = "1AdcrGOlsK1kkmGUhZemJIdOn1YarvPD0fuUT_uiqb4E"
CAMP_MIN_ORDER = 3000  # デイリーボーナスの条件（1回の注文3,000円以上）
CAMP_MIN_OFF = 25      # キャンペーン込みで、この%以上安くなるなら載せる
CAMP_RATE = None       # 今日の最大付与率（main で読む）
CAMP_DATE = None
POINT_MIN = 10        # 値引きがなくても、ストアポイントがこの%以上なら載せる（ポイント高還元）
POINT_QUERIES = ["ポイント10倍", "ポイント20倍", "ポイント5倍", "COSORI", "Ninja"]  # 高還元の商品・指名メーカーを探す言葉
# 値引き中の商品も、指名したメーカーは個別に探す（ジャンルの人気順だと漏れるため）
MAKER_QUERIES = ["COSORI", "Ninja"]
# 「載せない商品」シート。ここに書いた言葉かURLを含む商品は、取得の段階で外す（空いた枠に別の商品が入る）
NG_SHEET = "18xEzO00ztRWrRg3T1Ihz1kWFBpqCqN8_QmciVmYBU8I"
NG_WORDS = []
# 管理ページ（admin.html）のボタンで記録されるシート（Googleフォームの回答）
ADMIN_SHEET = "1GT8YOJjHL17nyQX4PCqwZUjj-GFEihHCLBGTif4--Qs"
HIDE = set()          # 「隠す」を押した商品のid
ALLOW = {}            # 「載せる」を押した商品のid → {"genre","jan","name"}
CANDIDATES = []       # 値段は合格だが載っていない商品（管理ページの候補）
CHECK = []            # 他店より少し高い商品。クーポンがあれば安くなるかも（毎晩ログインして確認する）


def add_check(d: dict, target: int, memo: str = "") -> None:
    if not d.get("page") or any(x["page"] == d["page"] for x in CHECK):
        return
    CHECK.append({k: d.get(k) for k in ("genre", "name", "image", "url", "page", "now", "real",
                                         "free_real", "postage", "ship", "shop")}
                 | {"id": deal_id(d.get("page"), None, None, d["name"]), "target": target, "memo": memo})
# BTOパソコン：管理ページで自分で選ぶ方式。ここで探した物は値段に関係なく候補に出し、
# 「載せる」を押した物だけ、安いときにサイトに出す
# 種類 → (お店, 探す言葉)。管理ページの候補に「BTOパソコン」「PCパーツ」として出る
# お店はYahoo!ショッピングにあるBTOメーカー・PC専門店の公式店（2026/11 に確認）
BTO_STORES = {"pc-koubou": "パソコン工房", "mousecomputer": "マウスコンピューター", "dospara-y": "ドスパラ",
              "tsukumo-y": "ツクモ", "buxton-web": "Mission", "pcmax": "PC-MAX", "arkonline-store": "アーク"}
PARTS_STORES = ["tsukumo-y", "arkonline-store", "pc-koubou", "dospara-y"]
PICK_QUERIES = {
    "BTOパソコン": [(st, q) for st in BTO_STORES for q in ("デスクトップ", "ゲーミングPC", "ノートパソコン")],
    "PCパーツ": [(st, q) for st in PARTS_STORES for q in ("グラフィックボード", "SSD", "メモリ", "CPU", "電源ユニット")],
}
PICK_PER_STORE = {"BTOパソコン": 4, "PCパーツ": 4}  # お店×探す言葉ごとの上限
BTO_GENRE_ID = "2502"  # パソコン
BTO_EXCLUDE = re.compile(r"中古|整備済|再生品|リファービッシュ|訳あり|アウトレット|ジャンク|レンタル")
BTO_MAX = 90  # 種類ごとの候補の上限
BTO_MODE = False
# 人気上位チェック：価格比較サイトの人気ランキング上位の商品と、調べた時点の他店最安（非公開シート）。
# ポイント込みの実質価格（送料がかかるなら送料込み）がそれより安いときだけ載せる。他店の数字はサイトに出さない
RANK_SHEET = "19mhEJe7FnnRvOw-BDtFSS_kS9MlqSrVmIkXdsYVXHIE"
RANK_MAX = 80
RANK_MARGIN = 0.03     # 人気上位・おすすめは他店最安よりこの割合以上安い時だけ載せる
RANK_MIN_YEN = 300     # かつ、この金額以上安い時だけ
RANK_NG = re.compile(r"中古|訳あり|アウトレット|展示|開封|ジャンク|互換|交換用|替え|ケース|カバー|フィルム|フィルター|ホルダー|スタンド|保護|パーツ|部品|レンタル|整備済|右耳|左耳|片耳|片側")
MAX_OFF = 80          # これを超える値引き率は「通常価格」が怪しいので載せない（%）
EXCLUDE_SELLERS = {"zozo"}  # ファッション系のお店
# 載せないメーカー×商品の組み合わせ（メーカー表示名, 商品名に含まれる言葉の正規表現）
EXCLUDE_MAKER_ITEMS = [
    ("TP-Link", r"掃除機|ロボット|クリーナー"),  # TP-Link（Tapo）のロボット掃除機はいらない
]
# 柄物・ファッション系のマスクを外すための言葉（普通のマスクは残す）
MASK_FASHION = re.compile(r"柄|レース|花|水着|カラー|おしゃれ|オシャレ|刺繍|チェック|ドット|デザイン|ファッション|かわいい|可愛い|韓国|血色|くすみ|ラメ")
MIN_REVIEWS = 3       # レビューがほぼ無い商品は「通常価格」が当てにならないので除外
PER_GENRE = 30        # 1ジャンルあたり載せる最大件数
PER_MAKER = 5         # 同じジャンルに同じメーカーを載せる最大件数（Ankerだらけにしない）
FETCH = 100           # APIから1回に取る件数（上限100）
PER_MAKER_DRINK = 4   # 飲み物はメーカーが限られるので少し多め
PAGES = 3             # 1ジャンルあたり何ページ分取るか（有名メーカーで絞ると減るので多めに）


def call_api(genre_ids: str, start: int = 1, query: str = "", discounted: bool = True, jan: str = "", seller: str = "") -> dict:
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
    if jan:
        params["jan_code"] = jan
    if seller:
        params["seller_id"] = seller
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


NEAR = []  # 値段だけが理由で外れた商品（どのくらい惜しいかをログで見る用）


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
    if re.search(r"水|ウォーター", s) and not re.search(r"炭酸|スパークリング|ソーダ|茶|フレーバー|味", s):
        return (45 if ml < 1000 else 0.05 * ml), "水"   # 500mlで45円、2Lで100円
    if re.search(r"炭酸水|タンサン|強炭酸|ソーダ", s) and ml < 1000:
        return 55, "炭酸水"
    if ml <= 300:
        return 60, "飲み物"
    if ml < 1000:
        return max(65, 0.10 * ml), "飲み物"      # 500〜650mlで65円
    return 0.065 * ml, "飲み物"                  # 2Lで130円


def to_drink(hit: dict, now_ts: int):
    pl = hit.get("priceLabel") or {}
    price = pl.get("discountedPrice") or hit.get("price")
    if not price:
        return None
    name = html.unescape(hit.get("name", "")).strip()
    if DRINK_EXCLUDE.search(name) or is_ng(name, hit.get("url")):
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
        NEAR.append((round(per / limit, 2), round(per, 1), kind, name[:45]))
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
        "free_shipping": True, "ship": 2, "jan": hit.get("janCode") or None,
    }


def read_campaign_rate():
    """「お得な買い時」シートから、日本時間の今日の最大付与率(%)を読む。無ければ None。"""
    import csv, io
    url = f"https://docs.google.com/spreadsheets/d/{KAIDOKI_SHEET}/gviz/tq?tqx=out:csv"
    req = urllib.request.Request(url, headers={"User-Agent": "yasune-cho/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        rows = list(csv.reader(io.StringIO(r.read().decode("utf-8"))))
    today = datetime.now(timezone(timedelta(hours=9))).date()
    for row in rows[1:]:
        n = [int(x) for x in re.findall(r"\d+", row[0] if row else "")]
        if len(n) < 3:
            continue
        y, m, d = (n[0], n[1], n[2]) if n[0] > 999 else (n[2], n[0], n[1])
        try:
            if datetime(y, m, d).date() != today:
                continue
        except ValueError:
            continue
        v = re.sub(r"[^\d.]", "", row[1] if len(row) > 1 else "")
        return (float(v) if v else None), today.isoformat()
    return None, today.isoformat()


def _nz(s):
    return re.sub(r"\s", "", unicodedata.normalize("NFKC", s or "").lower())


def load_ng():
    """「載せない商品」シートの言葉を読む。"""
    import csv, io
    url = f"https://docs.google.com/spreadsheets/d/{NG_SHEET}/gviz/tq?tqx=out:csv"
    req = urllib.request.Request(url, headers={"User-Agent": "yasune-cho/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        rows = list(csv.reader(io.StringIO(r.read().decode("utf-8"))))
    return [w for w in (_nz(row[0]) for row in rows[1:] if row) if w]


def deal_id(page, jan, url, name):
    key = page or jan or url or name
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:10]


def load_rank():
    """人気上位チェックのシートを読む。[{genre, query, model, target, memo}]"""
    import csv, io
    url = f"https://docs.google.com/spreadsheets/d/{RANK_SHEET}/gviz/tq?tqx=out:csv"
    req = urllib.request.Request(url, headers={"User-Agent": "yasune-cho/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        rows = list(csv.reader(io.StringIO(r.read().decode("utf-8"))))
    out = []
    for row in rows[1:]:
        row = (row + [""] * 6)[:6]
        genre, query, model, target, _, memo = [c.strip() for c in row]
        digits = re.sub(r"\D", "", target)
        if query and digits:
            out.append({"genre": genre or "家電", "query": query, "model": model or query,
                        "target": int(digits), "memo": memo})
    return out[:RANK_MAX]


def _key(s):
    return re.sub(r"[\s\-‐_/]", "", unicodedata.normalize("NFKC", s or "").upper())


def _rank_ok(d):
    """人気上位の商品が、送料込みでも他店最安より安いか。"""
    t = d.get("_rank_target")
    if not t:
        return True
    base = d.get("free_real") or d["real"]  # 無料でそろえられる条件（支払い方法・LINE連携・エントリー）込み
    if d.get("ship") == 2:
        total = base
    elif d.get("postage") is None:
        if d.get("ship") != 3:
            return False
        total = base  # 条件付き送料無料は本体だけで判定
    else:
        total = base + d["postage"]
    # 1円安いだけでは載せない：他店最安より3%以上、かつ300円以上安い時だけ
    return total <= t * (1 - RANK_MARGIN) and t - total >= RANK_MIN_YEN


def load_admin():
    """管理シートを読む。同じ商品は最後に押したボタンが有効。"""
    import csv, io
    url = f"https://docs.google.com/spreadsheets/d/{ADMIN_SHEET}/gviz/tq?tqx=out:csv"
    req = urllib.request.Request(url, headers={"User-Agent": "yasune-cho/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        rows = list(csv.reader(io.StringIO(r.read().decode("utf-8"))))
    last = {}
    for row in rows[1:]:
        if len(row) < 3 or not re.fullmatch(r"[0-9a-f]{10}", row[2].strip()):
            continue
        if row[1].strip().startswith("AI"):  # ローカルAIの下見（おすすめ・見送り）は人の操作ではないので使わない
            continue
        last[row[2].strip()] = (row[1].strip(), row[3] if len(row) > 3 else "")
    hide, allow = set(), {}
    for i, (op, info) in last.items():
        if op == "隠す":
            hide.add(i)
        elif op == "載せる":
            g, jan, name = (info.split("|", 2) + ["", "", ""])[:3]
            allow[i] = {"genre": g, "jan": jan, "name": name}
    return hide, allow


def is_ng(name, url):
    n = _nz(name)
    return any(w in n or w in (url or "") for w in NG_WORDS)


from collections import Counter
REJ = Counter()       # 外れた理由の集計（ログ用）
UNKNOWN = Counter()   # 値段の条件は満たしたが、知らないメーカーで外れたブランド名（ログ用）


def rej(reason):
    REJ[reason] += 1
    return None


def to_deal(hit: dict, genre: str, now_ts: int):
    pl = hit.get("priceLabel") or {}
    was = pl.get("defaultPrice")
    sale = pl.get("discountedPrice") or hit.get("price")
    if not sale:
        return rej("価格なし")
    if not was or was <= sale:
        was = None
    off = round((1 - sale / was) * 100) if was else None
    if off is not None and off > MAX_OFF:  # 「通常価格」が怪しい
        return rej("通常価格が怪しい(80%超)")
    pt0 = hit.get("point") or {}
    pt_rate = round((pt0.get("lyLimitedBonusAmount") or 0) / sale * 100, 1)
    # キャンペーン込み（今日の最大付与率・3,000円以上の注文のとき）
    camp = {}
    if False and CAMP_RATE and sale >= CAMP_MIN_ORDER:  # 付与率×値段は上限を無視するので使わない（商品ページの内訳を使う）
        cpts = max(pt0.get("lyLimitedBonusAmount") or 0, int(sale * CAMP_RATE / 100))
        creal = sale - cpts
        camp = {"camp_rate": CAMP_RATE, "camp_points": cpts, "camp_real": creal,
                "camp_off": round((1 - creal / (was or sale)) * 100), "camp_date": CAMP_DATE}
    # 値引きが十分か、ポイントが高いか、キャンペーン込みで十分安ければ載せる
    cheap = (off or 0) >= MIN_OFF or pt_rate >= POINT_MIN or camp.get("camp_off", 0) >= CAMP_MIN_OFF
    _nm = html.unescape(hit.get("name", "")).strip()
    _reg = deal_id(page_url(hit), hit.get("janCode") or None, hit.get("url"), _nm) in ALLOW
    if not cheap and not BTO_MODE and not _reg:
        return rej("安くない")
    seller = hit.get("seller") or {}
    if seller.get("sellerId") in EXCLUDE_SELLERS:
        return rej("除外ストア")
    name = html.unescape(hit.get("name", "")).strip()  # 「&amp;」などを普通の文字に戻す
    did = deal_id(page_url(hit), hit.get("janCode") or None, hit.get("url"), name)
    if did in HIDE:
        return rej("管理で非表示")
    allowed = did in ALLOW
    if is_ng(name, hit.get("url")) and not allowed:
        return rej("載せない商品")
    if "マスク" in name and MASK_FASHION.search(name):
        return rej("柄物マスク")
    end = pl.get("periodEnd")
    if end and end < now_ts:  # すでに終わったセール
        return rej("セール終了")
    review = hit.get("review") or {}
    if (review.get("count") or 0) < MIN_REVIEWS and not BTO_MODE:
        return rej("レビュー少")
    brands = [(hit.get("brand") or {}).get("name") or ""] + [b.get("name") or "" for b in hit.get("parentBrands") or []]
    maker = find_maker([b for b in brands if b], name)
    unknown = False
    if not maker:  # 知らないメーカーは載せない（管理ページで「載せる」を押した物は載せる）
        guess = (brands[0] or (name.split()[0] if name.split() else ""))[:20]
        UNKNOWN[guess] += 1
        maker = guess or "その他"
        unknown = not allowed
        if unknown:
            REJ["知らないメーカー(候補へ)"] += 1
    if any(maker == m and re.search(rx, name) for m, rx in EXCLUDE_MAKER_ITEMS):
        return rej("除外メーカー品")
    img = (hit.get("exImage") or {}).get("url") or (hit.get("image") or {}).get("medium")
    pt = hit.get("point") or {}
    points = pt.get("lyLimitedBonusAmount") or 0             # 誰でももらえるストアポイント
    premium = pt.get("lyLimitedPremiumBonusAmount") or 0     # LYPプレミアム会員のポイント
    real = sale - points                                     # ポイントを引いた実質価格
    extra = {}
    if genre == "食品":
        ml, count, unit = parse_pack(name)
        # 食品ジャンルに混ざる飲み物のケースも、1本あたりが安いときだけ載せる
        if ml and ml >= 150 and count and count >= 6 and unit in ("本", "缶"):
            if (real / count) > drink_limit(name, ml)[0]:
                return rej("飲み物が割高")
        if count and 2 <= count <= 200 and unit:
            extra = {"unit_price": round(real / count, 1), "unit": unit, "count": count}
    return {**extra, **camp,
        "id": did,
        "_unknown": unknown,
        "_cheap": cheap,
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
        "real_off": round((1 - real / (was or sale)) * 100),
        "pt_rate": pt_rate,
        "end": end,
        "shop": seller.get("name"),
        "maker": maker,
        "rating": review.get("rate"),
        "reviews": review.get("count"),
        "free_shipping": (hit.get("shipping") or {}).get("code") == 2,
        "ship": (hit.get("shipping") or {}).get("code"),  # 1=送料別 2=送料無料 3=条件付き送料無料
        "page": page_url(hit),
        "jan": hit.get("janCode") or None,
    }


def page_url(hit: dict):
    """商品ページのURL（アフィリエイトの飛び先ではなく、ストアの元ページ）。"""
    code = hit.get("code") or ""
    sid = (hit.get("seller") or {}).get("sellerId")
    if sid and "_" in code:
        return f"https://store.shopping.yahoo.co.jp/{sid}/{code.split('_', 1)[1]}.html"
    u = hit.get("url") or ""
    return u if u.startswith("https://store.shopping.yahoo.co.jp/") else None


def fetch_postage(url: str):
    """商品ページから送料と「◯円以上で送料無料」の条件を読む。読めなければ None。
    （検索APIには送料の金額や無料の条件が入っていないため）"""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (yasune-cho/1.0)"})
    with urllib.request.urlopen(req, timeout=30) as r:
        h = r.read().decode("utf-8", "replace")
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', h, re.S)
    if not m:
        return None
    pp = json.loads(m.group(1)).get("props", {}).get("pageProps", {})
    pg = pp.get("postage") or {}
    label = (pp.get("item") or {}).get("deliveryFeeLabelType")
    return {
        "fee": pg.get("fee"),
        "pref": pg.get("prefecture"),
        "cond": (pg.get("conditionFreeText") or "").strip(),
        "free": bool(pg.get("isPostageFree")),
        "label": label,
    }


SPECIAL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "special.json")

# 目玉商品：Yahoo!ショッピングのトップから行けるセール会場に並んでいる商品を集め、管理ページの候補に出す。
# 本人が「載せる」を押した物だけ、値段がその時より上がらず在庫があるあいだサイトに出す
FEATURE_TOP = "https://shopping.yahoo.co.jp/"
FEATURE_EXTRA = ["https://shopping.yahoo.co.jp/promotion/campaign/blackfriday/sale/"]
FEATURE_MAX = 100     # 1回に詳しく調べる上限（お店のページを1件ずつ見るため）
FEATURE_MIN_OFF = 15  # これ未満の値引きは候補にしない（%）
FEAT_OK_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "featured_ok.json")
GENRE_BY_ID = {"2505": "家電", "2508": "日用品", "2498": "食品", "2502": "ガジェット", "2504": "ガジェット",
               "2499": "飲み物"}


def _html(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (yasune-cho/1.0)"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def load_featured() -> list:
    """トップとセール会場から目玉商品を集める。[{code, page, name, now, was, off, image}]"""
    pages = list(FEATURE_EXTRA)
    try:
        top = _html(FEATURE_TOP)
        pages += re.findall(r"https://shopping\.yahoo\.co\.jp/promotion/campaign/[a-z0-9_\-]+/sale/", top)
    except Exception as e:
        print(f"[目玉] トップを読めず: {e}", file=sys.stderr)
    items = {}
    for url in list(dict.fromkeys(pages))[:4]:
        time.sleep(1.5)
        try:
            h = _html(url)
        except Exception as e:
            print(f"[目玉] 読めず {url}: {e}", file=sys.stderr)
            continue
        h = h.replace('\\"', '"').replace("\\/", "/").replace("\\n", "\n")
        for blk in re.findall(r'<li class="elItem">(.*?)</li>', h, re.S):
            m = re.search(r'href="https://store\.shopping\.yahoo\.co\.jp/([a-z0-9\-]+)/([A-Za-z0-9\-_]+)\.html', blk)
            nm = re.search(r'class="elItemName">([^<]+)<', blk)
            pr = re.search(r'class="elPrice">([\d,]+)', blk)
            if not (m and nm and pr):
                continue
            code = f"{m.group(1)}/{m.group(2)}"
            was = re.search(r'class="elOriginalPrice">([\d,]+)', blk)
            img = re.search(r'class="elImage" src="([^"]+)"', blk)
            now = int(pr.group(1).replace(",", ""))
            was = int(was.group(1).replace(",", "")) if was else None
            items.setdefault(code, {"code": code, "page": f"https://store.shopping.yahoo.co.jp/{code}.html",
                                    "name": html.unescape(nm.group(1)).strip(), "now": now, "was": was,
                                    "off": round((1 - now / was) * 100) if was and was > now else None,
                                    "image": img.group(1) if img else None})
    print(f"[目玉] セール会場 {len(pages)}か所から {len(items)}件")
    return list(items.values())


def featured_candidates(shown: set) -> None:
    """目玉商品のうち、値引きが大きい物を詳しく調べて管理ページの候補に入れる。"""
    aff = os.environ.get("VC_AFFILIATE_ID") or ""
    items = [x for x in load_featured() if (x["off"] or 0) >= FEATURE_MIN_OFF]
    items.sort(key=lambda x: x["off"] or 0, reverse=True)
    n = 0
    for x in items[:FEATURE_MAX]:
        did = deal_id(x["page"], None, None, x["name"])
        if did in shown or did in HIDE or did in ALLOW or "マスク" in x["name"] and MASK_FASHION.search(x["name"]):
            continue
        d = {"id": did, "genre": "目玉", "name": x["name"], "image": x["image"], "was": x["was"], "now": x["now"],
             "off": x["off"], "points": 0, "real": x["now"], "real_off": x["off"] or 0, "pt_rate": 0,
             "maker": find_maker([], x["name"]) or "", "shop": x["code"].split("/")[0], "jan": x["code"],
             "page": x["page"], "url": aff + urllib.parse.quote(x["page"], safe="") if aff else x["page"],
             "reviews": None, "_unknown": False, "_why": "目玉商品"}
        parts = point_parts(x["page"])
        if x["page"] in SOLD_OUT:
            continue
        if parts:
            free = sum(p["p"] for p in parts if "LYP" not in p["t"])
            d["real"] = x["now"] - free
            d["real_off"] = round((1 - d["real"] / (x["was"] or x["now"])) * 100)
        CANDIDATES.append(d)
        n += 1
    print(f"[目玉] 候補に {n}件")


def _page_props(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (yasune-cho/1.0)"})
    with urllib.request.urlopen(req, timeout=30) as r:
        h = r.read().decode("utf-8", "replace")
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', h, re.S)
    return json.loads(m.group(1)).get("props", {}).get("pageProps", {}) if m else {}


def load_special(now_ts: int) -> list:
    """special.json に書いた商品（リュウジおすすめ・爆買いタコおすすめなど）を読む。
    other（他店最安）があれば送料込みで比べて安い時だけ、無ければ「他で売っていない」扱いで載せる。
    クーポンは自動で取れないので、special.json に書いた内容で計算する。"""
    global BTO_MODE
    try:
        with open(SPECIAL_PATH, encoding="utf-8") as f:
            conf = json.load(f)
    except FileNotFoundError:
        return []
    # 毎晩の確認（docs/review.json）で、管理ページの「載せる」を押した物
    try:
        with open(os.path.join(os.path.dirname(SPECIAL_PATH), "docs", "review.json"), encoding="utf-8") as f:
            rv = json.load(f).get("items", [])
    except Exception:
        rv = []
    ok = []
    for x in rv:
        if x.get("id") in ALLOW and x.get("id") not in HIDE and x.get("coupon_yen") and x.get("page"):
            code = x["page"].split("store.shopping.yahoo.co.jp/", 1)[-1].removesuffix(".html")
            ok.append({"code": code, "other": x.get("target"), "genre": x.get("genre"),
                       "coupon": {"yen": x["coupon_yen"], "min": x.get("coupon_min", 0),
                                  "end": x.get("coupon_end") or "2000-01-01T00:00:00+09:00",
                                  "text": x.get("coupon_text") or "クーポン"}})
    if ok:
        conf = {"groups": list(conf.get("groups", [])) + [{"label": "クーポンで安い", "genre": "", "items": ok}]}
    try:
        with open(FEAT_OK_PATH, encoding="utf-8") as f:
            feat_ok = json.load(f)
    except Exception:
        feat_ok = {}
    feat = []
    for i, a in ALLOW.items():
        if a.get("genre") == "目玉" and "/" in (a.get("jan") or "") and i not in HIDE:
            feat.append({"code": a["jan"], "feat": i})
    if feat:
        conf = {"groups": list(conf.get("groups", [])) + [{"label": "目玉商品", "genre": "", "items": feat}]}
    out = []
    for g in conf.get("groups", []):
        for it in g.get("items", []):
            if isinstance(it, str):
                it = {"code": it}
            cp = it.get("coupon") or g.get("coupon") or {}
            cp_ok = bool(cp) and datetime.fromisoformat(cp["end"]).timestamp() > now_ts
            if cp and not cp_ok and not it.get("other"):
                continue  # クーポンが終わった：他店と比べていない特集は、安い理由が無くなったので外す
            seller, srid = it["code"].split("/", 1)
            page = f"https://store.shopping.yahoo.co.jp/{seller}/{srid}.html"
            time.sleep(1.5)
            try:
                pp = _page_props(page)
            except Exception as e:
                print(f"[特集] 取得失敗 {page}: {e}", file=sys.stderr)
                continue
            item = pp.get("item") or {}
            name = html.unescape(item.get("name") or "").strip()
            price = item.get("applicablePrice")
            if it.get("feat") and price:
                first = feat_ok.setdefault(it["feat"], price)   # 「載せる」を押したあと最初に見た値段
                if price > first:
                    print(f"[目玉] 値段が上がったので出さない: {name[:30]} {price} > {first}")
                    continue
            if not (it.get("genre") or g.get("genre")):
                it["genre"] = next((v for k, v in GENRE_BY_ID.items() if k in [str(x) for x in (item.get("genreCategoryIds") or [])]), "その他")
            if not name or not price or (item.get("stock") or {}).get("isSoldOut") or (item.get("stock") or {}).get("isAvailable") is False:
                continue
            d = None
            for q in (name[:60], " ".join(name.split()[:3])):
                try:
                    hits = call_api("", 1, q, discounted=False, seller=seller).get("hits", [])
                except Exception:
                    hits = []
                h = next((x for x in hits if x.get("code") == f"{seller}_{srid}"), None)
                if h:
                    BTO_MODE = True
                    d = to_deal(h, it.get("genre") or g.get("genre") or "食品", now_ts)
                    BTO_MODE = False
                    break
            if not d:
                aff = os.environ.get("VC_AFFILIATE_ID") or ""
                d = {"genre": it.get("genre") or g.get("genre") or "食品", "store": "yahoo", "name": name,
                     "url": aff + urllib.parse.quote(page, safe="") if aff else page,
                     "image": f"https://item-shopping.c.yimg.jp/i/j/{seller}_{srid}",
                     "now": price, "points": 0, "premium_points": 0, "real": price,
                     "shop": item.get("shopName") or (pp.get("seller") or {}).get("name"),
                     "maker": "", "page": page, "jan": item.get("janCode") or None}
            for k in ("_unknown", "_cheap", "was", "off", "real_off", "camp_rate", "camp_points",
                      "camp_real", "camp_off", "camp_date"):
                d.pop(k, None)
            d["now"] = price
            pg = pp.get("postage") or {}
            if pg.get("isPostageFree"):
                d["ship"], d["free_shipping"] = 2, True
            else:
                d["ship"] = 3 if (pg.get("conditionFreeText") or "").strip() else 1
                d["free_shipping"] = False
                if isinstance(pg.get("fee"), int):
                    d["postage"] = pg["fee"]
                if (pg.get("conditionFreeText") or "").strip():
                    d["free_cond"] = pg["conditionFreeText"].strip()
            if cp_ok and price >= cp.get("min", 0):
                disc = int(price * cp["pct"] / 100) if cp.get("pct") else cp.get("yen", 0)
                if cp.get("max"):
                    disc = min(disc, cp["max"])
                rate = (d.get("points") or 0) / price
                d["coupon"] = cp["text"]
                d["coupon_url"] = cp.get("url")
                d["coupon_price"] = price - disc
                d["real"] = price - disc - int((price - disc) * rate)
            other = it.get("other")
            if other:
                set_free_real(d)
            total = (d.get("free_real") or d["real"]) + (d.get("postage") or 0 if d.get("ship") != 2 else 0)
            if other:
                if not (total <= other * (1 - RANK_MARGIN) and other - total >= RANK_MIN_YEN):
                    print(f"[特集] 他店より安くないので載せない: {name[:30]} {total} / {other}")
                    continue
                d["other"], d["save"] = other, other - total
                d["save_pct"] = round((other - total) * 100 / other)
            elif it.get("feat"):
                if item.get("originalPrice") and item["originalPrice"] > price:
                    d["was"] = item["originalPrice"]
                    d["off"] = round((1 - price / item["originalPrice"]) * 100)
            else:
                d["only"] = True  # 他の通販サイトでは売っていない
            d["badge"] = g.get("label")
            if g.get("label") == "爆買いタコおすすめ":
                d["pick"] = True
            if it.get("comment"):
                d["comment"] = it["comment"]
            d["id"] = deal_id(page, d.get("jan"), d.get("url"), name)
            out.append(d)
    if feat:
        with open(FEAT_OK_PATH, "w", encoding="utf-8") as f:
            json.dump(feat_ok, f, ensure_ascii=False, indent=1)
    print(f"[特集] {len(out)}件")
    return out


_PT_CACHE = {}
SOLD_OUT = set()   # 商品ページで「在庫なし」だった物


def point_parts(page: str) -> list:
    """商品ページ（ログインなし）に出るポイントの内訳。同じページは1回だけ読む。"""
    if page in _PT_CACHE:
        return _PT_CACHE[page]
    time.sleep(1.2)
    parts = []
    try:
        pp = _page_props(page)
        if ((pp.get("item") or {}).get("stock") or {}).get("isAvailable") is False:
            SOLD_OUT.add(page)
        pt = pp.get("point") or {}
    except Exception as e:
        print(f"[ポイント] 取得失敗 {page}: {e}", file=sys.stderr)
        pt = {}
    for key, cond in (("currentCampaignList", False), ("conditionalCampaignList", True)):
        for c in pt.get(key) or []:
            for x in c.get("partsCampaignList") or []:
                if x.get("ratio") or x.get("point"):
                    parts.append({"t": x.get("title") or "", "r": x.get("ratio") or 0,
                                  "p": x.get("point") or 0, "c": cond})
    _PT_CACHE[page] = parts
    return parts


def set_free_real(d: dict) -> None:
    """LYPプレミアム（有料）以外の、無料でそろえられる条件を全部そろえた実質価格。"""
    if not d.get("page") or not d.get("now"):
        return
    parts = point_parts(d["page"])
    if not parts:
        return
    base = d.get("coupon_price") or d["now"]
    free = sum(x["p"] for x in parts if "LYP" not in x["t"])
    d["free_real"] = base - int(free * base / d["now"])


def add_points(deals: list) -> None:
    """商品ページ（ログインなしで見える内容）から、ポイントの内訳を読む。
    「毎日5%」（支払い方法・LINE連携）や「ボーナスストアPlus」「LYPプレミアム」など、
    条件をそろえると付くポイントも入る。クーポンはログインしないと見えないので入らない。"""
    n = 0
    for d in deals[:90]:
        if not d.get("page"):
            continue
        parts = point_parts(d["page"])
        if not parts:
            continue
        n += 1
        tot = sum(x["p"] for x in parts)
        base = d.get("coupon_price") or d["now"]
        d["pt_parts"] = parts
        d["pt_max"] = int(tot * base / d["now"]) if d.get("now") else tot
        d["pt_max_rate"] = round(sum(x["r"] for x in parts), 1)
        d["max_real"] = base - d["pt_max"]
        free = sum(x["p"] for x in parts if "LYP" not in x["t"])   # 有料のLYPプレミアム以外の条件をそろえた分
        d["free_real"] = base - int(free * base / d["now"]) if d.get("now") else d.get("real")
    print(f"[ポイント] {n}件の内訳を商品ページで確認")


def add_postage(deals: list) -> None:
    """送料無料でない商品だけ、商品ページを見て送料と無料の条件を足す。"""
    n = 0
    for d in deals:
        if d.get("ship") == 2 or not d.get("page"):
            continue
        time.sleep(1.5)
        try:
            pg = fetch_postage(d["page"])
        except Exception as e:
            print(f"[送料] 取得失敗 {d['page']}: {e}", file=sys.stderr)
            continue
        if not pg:
            continue
        n += 1
        if pg["free"]:
            d["ship"] = 2
            continue
        if pg["label"] == "ConditionalFree" or pg["cond"]:
            d["ship"] = 3
        if isinstance(pg["fee"], int):
            d["postage"] = pg["fee"]
            d["postage_pref"] = pg["pref"]
        if pg["cond"]:
            d["free_cond"] = pg["cond"]
    print(f"[送料] {n}件の送料を商品ページで確認")


HIST_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "price_history.json")
try:
    with open(HIST_PATH, encoding="utf-8") as _f:
        PRICE_HIST = json.load(_f)
except Exception:
    PRICE_HIST = {}
USUAL_DAYS = 30      # 「いつもの値段」を見る期間（日）
USUAL_MIN_DAYS = 3   # 記録がこの日数分たまるまでは判定しない
USUAL_RATE = 0.90    # いつもの値段（期間内の真ん中の値段）より10%以上安い時だけ載せる


def record_price(d: dict) -> None:
    """登録商品の実質価格を1日1件（その日の最安）記録する。"""
    if not d.get("id") or not d.get("real"):
        return
    day = datetime.now(JST).strftime("%Y-%m-%d")
    h = PRICE_HIST.setdefault(d["id"], {})
    h[day] = min(h.get(day, d["real"]), d["real"])
    cut = (datetime.now(JST) - timedelta(days=USUAL_DAYS)).strftime("%Y-%m-%d")
    for k in [k for k in h if k < cut]:
        del h[k]


def usual_cheap(d: dict) -> bool:
    """登録商品が、いつもの値段（過去30日の真ん中の値段）より10%以上安いか。"""
    record_price(d)
    h = PRICE_HIST.get(d.get("id"), {})
    today = datetime.now(JST).strftime("%Y-%m-%d")
    past = sorted(v for k, v in h.items() if k != today)
    if len(past) < USUAL_MIN_DAYS:
        return False
    usual = past[len(past) // 2]
    if d["real"] <= usual * USUAL_RATE:
        d["usual"] = usual
        d["usual_save"] = usual - d["real"]
        d["badge"] = d.get("badge") or "いつもより安い"
        return True
    return False


SEEN_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "seen.json")


def mark_new(deals: list) -> None:
    """いつから載っているかを付ける（新着順に並べるため）。載らなくなった物は記録から消す。"""
    try:
        with open(SEEN_PATH, encoding="utf-8") as f:
            seen = json.load(f)
    except Exception:
        seen = {}
    now = datetime.now(JST).isoformat(timespec="minutes")
    out = {}
    for d in deals:
        out[d["id"]] = seen.get(d["id"], now)
        d["since"] = out[d["id"]]
    with open(SEEN_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)


def main(out_path: str) -> None:
    global CAMP_RATE, CAMP_DATE, NG_WORDS, HIDE, ALLOW, BTO_MODE
    now_ts = int(time.time())
    if not os.environ.get("MOCK_DIR"):
        try:
            NG_WORDS = load_ng()
            print(f"[載せない商品] {len(NG_WORDS)}語: {' '.join(NG_WORDS)}")
        except Exception as e:
            print(f"[載せない商品] シートを読めず: {e}", file=sys.stderr)
        try:
            HIDE, ALLOW = load_admin()
            print(f"[管理] 隠す {len(HIDE)}件 / 載せる {len(ALLOW)}件")
        except Exception as e:
            print(f"[管理] シートを読めず: {e}", file=sys.stderr)
        try:
            CAMP_RATE, CAMP_DATE = read_campaign_rate()
        except Exception as e:
            print(f"[キャンペーン] シートを読めず: {e}", file=sys.stderr)
    print(f"[キャンペーン] 今日の最大付与率: {CAMP_RATE}%")
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
                # 値引きなしでもポイント高還元の商品（「ポイント10倍」など）
                for q in POINT_QUERIES:
                    time.sleep(1.5)
                    hits += call_api(ids, 1, q, discounted=False).get("hits", [])
                # 指名したメーカー（COSORI・Ninja）は値引き中の商品も個別に探す
                if genre == "家電":
                    for q in MAKER_QUERIES:
                        time.sleep(1.5)
                        hits += call_api(ids, 1, q).get("hits", [])
                # 管理ページで「載せる」を押した商品は、JAN（なければ商品名）で個別に探す
                for a in ALLOW.values():
                    if (a["genre"] or "家電") != genre:
                        continue
                    time.sleep(1.5)
                    if a["jan"]:
                        hits += call_api("", 1, jan=a["jan"], discounted=False).get("hits", [])[:10]
                    elif a["name"]:
                        hits += call_api("", 1, a["name"][:60], discounted=False).get("hits", [])[:10]
        except Exception as e:  # 途中で失敗しても、取れた分は使う
            print(f"[{genre}] 取得失敗: {e}", file=sys.stderr)
        REJ.clear()
        UNKNOWN.clear()
        cands = [d for d in (to_deal(h, genre, now_ts) for h in hits) if d]
        CANDIDATES.extend(d for d in cands if d["_unknown"])
        cands = [d for d in cands if not d["_unknown"]]
        dup = cap = 0
        cands.sort(key=lambda d: max(d["real_off"], d.get("camp_off") or 0), reverse=True)  # お得な順に見て、上から採用する
        cands.sort(key=lambda d: d["id"] not in ALLOW)  # 「載せる」を押した物を先に
        picked, per_maker = [], {}
        for d in cands:
            keys = {d["name"]} | ({d["jan"]} if d["jan"] else set())  # 同じ商品の重複を除く
            if keys & seen:
                dup += 1
                continue
            if per_maker.get(d["maker"], 0) >= PER_MAKER and d["id"] not in ALLOW:
                cap += 1
                CANDIDATES.append(d)
                continue
            per_maker[d["maker"]] = per_maker.get(d["maker"], 0) + 1
            seen |= keys
            picked.append(d)
        CANDIDATES.extend(picked[PER_GENRE:])
        deals += picked[:PER_GENRE]
        print(f"[{genre}] {len(hits)}件取得 → {len(picked[:PER_GENRE])}件掲載")
        print(f"   外れた理由: {dict(REJ.most_common())} / 重複 {dup} / 同じメーカーの上限 {cap}")
        print(f"   値段は合格だが知らないメーカー（上位）: {UNKNOWN.most_common(25)}")

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
        print("  惜しかったもの（上限の何倍か / 1本あたり）:")
        for r in sorted(set(NEAR))[:40]:
            print(f"   x{r[0]} {r[1]}円 ({r[2]}) {r[3]}")

    # 人気上位チェック（他店最安よりポイント込みで安いときだけ載せる）
    if not os.environ.get("MOCK_DIR"):
        try:
            ranks = load_rank()
        except Exception as e:
            ranks = []
            print(f"[人気上位] シートを読めず: {e}", file=sys.stderr)
        BTO_MODE = True
        added = 0
        for rk in ranks:
            time.sleep(1.5)
            try:
                hits = call_api("", 1, rk["query"], discounted=False).get("hits", [])[:30]
            except Exception as e:
                print(f"[人気上位 {rk['query']}] 取得失敗: {e}", file=sys.stderr)
                continue
            # 型番の欄はスペース区切りで「全部含む」。先頭が - の語は「含まない」
            words = []
            for w in rk["model"].split():
                # 「Liberty 5」のように英字の後ろの短い数字はくっつけて1語にする（5だけで当たらないように）
                if words and re.fullmatch(r"\d{1,2}", w) and re.search(r"[A-Za-z]$", words[-1]) \
                        and not words[-1].startswith("-"):
                    words[-1] += w
                else:
                    words.append(w)
            need = [_key(w) for w in words if not w.startswith("-")]
            avoid = [_key(w[1:]) for w in words if w.startswith("-") and len(w) > 1]
            pick = rk["memo"].startswith("おすすめ")
            best = None
            for h in hits:
                nm = html.unescape(h.get("name", ""))
                k = _key(nm)
                if any(w not in k for w in need) or any(w in k for w in avoid) or RANK_NG.search(nm) \
                        or (h.get("price") or 0) < rk["target"] * 0.4:
                    continue
                d = to_deal(h, rk["genre"], now_ts)
                if d and (best is None or d["real"] < best["real"]):
                    best = d
            if not best:
                continue
            if best["real"] >= rk["target"]:
                if best["real"] < rk["target"] * 1.4:
                    add_check(best, rk["target"], rk["memo"])
                continue
            flag = "pick" if pick else "rank"
            same = [x for x in deals if x["id"] == best["id"]]
            if same:
                same[0][flag], same[0]["_rank_target"] = True, rk["target"]
                continue
            keys = {best["name"]} | ({best["jan"]} if best["jan"] else set())
            if keys & seen:
                continue
            seen |= keys
            best[flag], best["_rank_target"], best["_rank_only"] = True, rk["target"], True
            deals.append(best)
            added += 1
        BTO_MODE = False
        print(f"[人気上位] {len(ranks)}商品を確認 → 他店より安くて新しく載せた {added}件")

    # BTOパソコン・PCパーツ（候補に出す。「載せる」を押した物は安いときだけ掲載）
    for kind, queries in ({} if os.environ.get("MOCK_DIR") else PICK_QUERIES).items():
        hits = []
        for st, q in queries:
            time.sleep(1.5)
            try:
                hits += [{**h, "_q": q} for h in call_api("", 1, q, discounted=False, seller=st).get("hits", [])[:40]]
            except Exception as e:
                print(f"[{kind} {st} {q}] 取得失敗: {e}", file=sys.stderr)
        BTO_MODE = True
        REJ.clear()
        bto, added, per_store = [], 0, Counter()
        for h in hits:
            if BTO_EXCLUDE.search(html.unescape(h.get("name", ""))):
                continue
            d = to_deal(h, "ガジェット", now_ts)
            if not d or any(d["id"] == x["id"] for x in bto):
                continue
            keys = {d["name"]} | ({d["jan"]} if d["jan"] else set())
            if keys & seen:
                continue
            if d["id"] in ALLOW and d["_cheap"]:
                seen |= keys
                deals.append(d)
                added += 1
            else:
                st = ((h.get("seller") or {}).get("sellerId"), h.get("_q"))
                if per_store[st] >= PICK_PER_STORE[kind]:
                    continue
                per_store[st] += 1
                d["_why"] = kind
                bto.append(d)
        BTO_MODE = False
        CANDIDATES[:0] = bto[:BTO_MAX]
        print(f"[{kind}] {len(hits)}件取得 → 候補 {len(bto[:BTO_MAX])}件 / 載せる指定で掲載 {added}件  外れた理由: {dict(REJ)}")

    if not os.environ.get("MOCK_DIR"):
        add_postage(deals)
    # 送料を足すと他店最安より高くなる人気上位の商品：人気上位だけで載った物は外し、元から載っていた物は印だけ外す
    keep = []
    for d in deals:
        # 送料が商品の値段以上かかる物（数百円の冷凍食品など）は載せない
        if d.get("ship") != 2 and isinstance(d.get("postage"), int) and d["postage"] >= d["now"]:
            continue
        # 他店の最安値と比べて安いと確かめられた物だけ載せる（お店が付けた値引き率は使わない）
        t = d.get("_rank_target")
        if not t:
            if d.get("id") in ALLOW and usual_cheap(d):
                keep.append(d)
            continue
        set_free_real(d)
        if not _rank_ok(d):
            add_check(d, t)
            continue
        d.pop("_rank_target", None)
        total = (d.get("free_real") or d["real"]) + (d["postage"] if d.get("ship") != 2 and isinstance(d.get("postage"), int) else 0)
        d["other"] = t                  # 他店最安（送料込みで比べる前の値段）
        d["save"] = t - total           # 送料込みで何円安いか
        d["save_pct"] = round((t - total) * 100 / t)
        d.pop("was", None)
        d.pop("off", None)
        d.pop("real_off", None)
        keep.append(d)
    deals = keep
    try:
        sp = load_special(now_ts) if not os.environ.get("MOCK_DIR") else []
    except Exception as e:
        print(f"[特集] 読めず: {e}", file=sys.stderr)
        sp = []
    have = {d.get("page") for d in sp}
    deals = sp + [d for d in deals if d.get("page") not in have]  # 特集を先頭に。同じ商品は特集の方を使う

    # 詳細ページ（item.html?id=...）用の番号。同じ商品なら更新しても同じ番号になる
    for d in deals:
        d.setdefault("id", deal_id(d.get("page"), d.get("jan"), d.get("url"), d["name"]))
        d.pop("_unknown", None)
        d.pop("_cheap", None)
        d.pop("_rank_only", None)
    deals = [d for d in deals if d["id"] not in HIDE]
    if not os.environ.get("MOCK_DIR"):
        add_points(deals)
        if SOLD_OUT:
            print(f"[在庫なし] {len(SOLD_OUT)}件を外す: " + " ".join(SOLD_OUT))
        deals = [d for d in deals if d.get("page") not in SOLD_OUT]
    mark_new(deals)
    with open(HIST_PATH, "w", encoding="utf-8") as f:
        json.dump(PRICE_HIST, f, ensure_ascii=False)

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

    # 管理ページ用の候補（値段は合格だが、知らないメーカー・上限超えで載っていない物）
    shown = {d["id"] for d in deals}
    if not os.environ.get("MOCK_DIR"):
        try:
            featured_candidates(shown)
        except Exception as e:
            print(f"[目玉] 失敗: {e}", file=sys.stderr)
    seen_c, cand_out = set(), []
    CANDIDATES.sort(key=lambda d: max(d["real_off"], d.get("camp_off") or 0), reverse=True)
    # 並び順：BTOパソコン → 有名メーカーで件数の上限 → 知らないメーカー
    CANDIDATES.sort(key=lambda d: 0 if d.get("_why") else (2 if d["_unknown"] else 1))
    for d in CANDIDATES:
        if d["id"] in shown or d["id"] in seen_c:
            continue
        seen_c.add(d["id"])
        cand_out.append({k: d.get(k) for k in ("id", "genre", "name", "image", "was", "now", "off", "points",
                                                "real", "real_off", "pt_rate", "maker", "shop", "jan", "page", "url", "reviews")}
                        | {"why": d.get("_why") or ("知らないメーカー" if d["_unknown"] else "件数の上限")})
    with open(os.path.join(os.path.dirname(out_path) or ".", "check.json"), "w", encoding="utf-8") as f:
        json.dump({"updated": out["updated"], "items": CHECK[:60]}, f, ensure_ascii=False, indent=1)
    print(f"[クーポン確認用] {len(CHECK)}件を check.json に書き出し")
    cand_path = os.path.join(os.path.dirname(out_path) or ".", "candidates.json")
    with open(cand_path, "w", encoding="utf-8") as f:
        json.dump({"updated": out["updated"], "items": cand_out[:280]}, f, ensure_ascii=False, indent=1)
    print(f"管理ページの候補 {len(cand_out[:280])} 件を {cand_path} に書き出しました")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "docs/deals.json")
