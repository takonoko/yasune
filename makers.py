"""載せてよいメーカー（ブランド）の一覧。ここに無いメーカーの商品は載せない。
英字は大文字小文字を区別しない。追加・削除はこのリストを書き換えるだけでよい。
"""
import re
import unicodedata

MAKERS = [
    # 家電
    "パナソニック", "Panasonic", "日立", "HITACHI", "東芝", "TOSHIBA", "シャープ", "SHARP",
    "三菱電機", "MITSUBISHI", "ダイキン", "DAIKIN", "ソニー", "SONY", "アイリスオーヤマ", "IRIS OHYAMA",
    "象印", "ZOJIRUSHI", "タイガー魔法瓶", "タイガー", "ティファール", "T-fal", "デロンギ", "DeLonghi", "De'Longhi",
    "バルミューダ", "BALMUDA", "ダイソン", "Dyson", "アイロボット", "iRobot", "ルンバ", "Roomba", "ブラーバ",
    "エコバックス", "ECOVACS", "ロボロック", "Roborock", "アンカー", "Anker", "eufy", "シャーク", "Shark",
    "ブラウン", "BRAUN", "フィリップス", "PHILIPS", "山善", "YAMAZEN", "ツインバード", "TWINBIRD",
    "コイズミ", "KOIZUMI", "テスコム", "TESCOM", "レコルト", "recolte", "コロナ", "CORONA", "ダイニチ", "DAINICHI",
    "富士通ゼネラル", "アラジン", "Aladdin", "クイジナート", "Cuisinart", "ヤーマン", "YA-MAN", "リファ", "ReFa",
    "SwitchBot", "スイッチボット", "TP-Link", "Tapo", "マキタ", "makita", "ドウシシャ", "DOSHISHA", "ハイセンス", "Hisense",
    "ブルーノ", "BRUNO", "ラッセルホブス", "Russell Hobbs", "ネスプレッソ", "Nespresso", "ネスカフェ",
    # ガジェット・パソコン・カメラ
    "Apple", "アップル", "エレコム", "ELECOM", "バッファロー", "BUFFALO", "サンディスク", "SanDisk",
    "キオクシア", "KIOXIA", "サムスン", "Samsung", "ロジクール", "Logicool", "Logitech", "JBL", "Bose", "ボーズ",
    "オーディオテクニカ", "audio-technica", "ゼンハイザー", "Sennheiser", "Google", "グーグル", "Belkin", "CIO",
    "UGREEN", "サンワサプライ", "SANWA SUPPLY", "キヤノン", "Canon", "エプソン", "EPSON", "ブラザー", "brother",
    "ニコン", "Nikon", "富士フイルム", "FUJIFILM", "LG", "ASUS", "Lenovo", "レノボ", "Dell", "デル", "HP",
    "Acer", "エイサー", "Microsoft", "マイクロソフト", "Nintendo", "任天堂", "GoPro", "DJI", "Insta360", "Shokz",
    "Xiaomi", "シャオミ", "HUAWEI", "ファーウェイ", "OPPO", "Beats", "Jabra", "アイ・オー・データ", "I-O DATA", "IODATA",
    "トランセンド", "Transcend", "Western Digital", "Crucial", "クルーシャル", "ナカバヤシ", "Victor", "ビクター",
    "JVC", "Kenwood", "ケンウッド", "Pioneer", "パイオニア", "オリンパス", "OM SYSTEM", "Garmin", "ガーミン",
    "カシオ", "CASIO", "セイコー", "SEIKO", "シチズン", "CITIZEN", "Fitbit", "PlayStation", "ソフトバンク",
    # 日用品
    "花王", "Kao", "P&G", "ライオン", "LION", "ユニ・チャーム", "ユニチャーム", "unicharm", "エリエール", "大王製紙",
    "ネピア", "nepia", "クリネックス", "スコッティ", "日本製紙クレシア", "小林製薬", "アース製薬", "エステー", "白元",
    "資生堂", "サンスター", "ジョンソン", "無印良品", "ニトリ", "山崎実業", "サラヤ", "シャボン玉石けん", "ミヨシ",
    "牛乳石鹸", "ユニリーバ", "ダヴ", "LUX", "アリエール", "ボールド", "ファブリーズ", "レノア",
    "さらさ", "アタック", "ビオレ", "キュキュット", "ハミング", "ナノックス", "NANOX", "クリアクリーン",
    "超快適", "快適ガード", "パンテーン", "パンパース", "ムーニー", "メリーズ", "グーン", "ジレット", "Schick",
    "貝印", "サランラップ", "旭化成", "クレラップ", "クレハ", "フマキラー", "KINCHO", "金鳥",
    "ウタマロ", "マジックリン", "バスマジックリン", "クイックルワイパー", "スコッチ・ブライト", "3M",
    # 食品
    "サントリー", "キリン", "アサヒ", "伊藤園", "コカ・コーラ", "コカコーラ", "日清", "明治", "森永", "江崎グリコ", "グリコ",
    "カルビー", "ロッテ", "味の素", "キッコーマン", "ミツカン", "ハウス食品", "エスビー", "S&B", "マルコメ",
    "東洋水産", "マルちゃん", "サッポロ", "永谷園", "ネスレ", "UCC", "AGF", "キーコーヒー", "ポッカ", "大塚食品",
    "大塚製薬", "ヤマサ", "はごろも", "いなば", "日本ハム", "伊藤ハム", "丸美屋", "カゴメ", "雪印", "不二家",
    "ブルボン", "亀田製菓", "湖池屋", "ニチレイ", "マルハニチロ", "ニッスイ", "日本水産", "ヤマキ", "にんべん",
    "エバラ", "ブルドック", "オタフク", "キユーピー", "キューピー", "Mizkan", "ポカリスエット", "アクエリアス",
    "い・ろ・は・す", "いろはす", "クリスタルガイザー", "エビアン", "evian", "ボルヴィック", "サンペレグリノ",
    "ウィルキンソン", "カップヌードル", "チキンラーメン", "サッポロ一番", "ペヤング", "まるか食品", "シマダヤ",
    "ドール", "Dole", "デルモンテ", "ケロッグ", "Kellogg", "日清シスコ", "ザバス", "SAVAS",
]


def _norm(s: str) -> str:
    return unicodedata.normalize("NFKC", s or "").lower()


_ascii = [m for m in MAKERS if re.fullmatch(r"[\x20-\x7e]+", m)]
_jp = [m for m in MAKERS if m not in _ascii]
# 英字は単語の途中で当たらないようにする（例: "HP" が "SHP" に当たらない）
_ASCII_RE = re.compile(r"(?<![a-z0-9])(" + "|".join(re.escape(_norm(m)) for m in _ascii) + r")(?![a-z0-9])")
_JP_RE = re.compile("|".join(re.escape(_norm(m)) for m in _jp))
# 「Apple Watch 対応」「iPhone用」のような互換品は、メーカー名が入っていても対象外
_COMPAT = re.compile(r"(対応|用|互換|適用|専用|交換|兼用|for)")


def find_maker(brand_names, name: str):
    """ブランド欄 → 商品名の順にメーカーを探す。見つからなければ None。"""
    for b in brand_names:
        nb = _norm(b)
        m = _ASCII_RE.search(nb) or _JP_RE.search(nb)
        if m:
            return m.group(0)
    nn = _norm(name)
    for rx in (_ASCII_RE, _JP_RE):
        for m in rx.finditer(nn):
            after = nn[m.end():m.end() + 15]
            if _COMPAT.search(after):
                continue
            return m.group(0)
    return None
