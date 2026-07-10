"""數字宣稱一致性檢查。

從同一事件各媒體的標題與摘要抽取具型別的數字（死傷人數、地震規模、金額等），
比對媒體之間是否一致：多家一致可視為已交叉印證，數字不同則標示歧異
（可能是報導時間差，也可能是錯誤），供讀者查證。

只做確定性的正規表達式比對，不用模型，避免不可驗證的推論。
保守規則：同一媒體自己就給出多個不同數字時（如持續更新的傷亡數），
該媒體不參與這個類別的比對，避免把時間演進誤判成媒體間歧異。
"""

import re

# (類別, [regex, ...])：每個 regex 的第一個捕捉群組是數字
CLAIM_PATTERNS = [
    (
        "死亡人數",
        [
            r"(\d+)\s*(?:人|名|員)?(?:死亡|身亡|罹難|喪生|喪命|不治|亡)",
            r"(\d+)死",
        ],
    ),
    (
        "受傷人數",
        [
            r"(\d+)\s*(?:人|名)?(?:受傷|輕重傷|輕傷|重傷)",
            r"(\d+)傷",
        ],
    ),
    ("地震規模", [r"規模\s*(\d+(?:\.\d+)?)"]),
    ("最大震度", [r"震度\s*(\d+)\s*級"]),
    ("風力級數", [r"(\d+)\s*級(?:以上)?(?:強?陣風|強風|陣風)"]),
]

# 金額另外處理：需要把 兆/億/萬 正規化成同一單位才能比較。
# 排除常見量詞，避免「50萬人」「3億劑」被當成金額。
MONEY_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(兆|億|萬)"
    r"(?!人|戶|名|次|劑|噸|株|隻|輛|棟|例|張|份|件|杯|顆|支|台|坪|歲)"
    r"\s*(?:餘)?(元|美元)?"
)
MONEY_UNIT = {"兆": 1e12, "億": 1e8, "萬": 1e4}


def extract_claims(text: str) -> dict:
    """回傳 {類別: {(正規化值, 顯示字串), ...}}"""
    found = {}
    for ctype, patterns in CLAIM_PATTERNS:
        for pat in patterns:
            for m in re.finditer(pat, text):
                num = m.group(1)
                found.setdefault(ctype, set()).add((float(num), num))
    for m in MONEY_RE.finditer(text):
        num, unit, currency = m.groups()
        norm = float(num) * MONEY_UNIT[unit]
        disp = f"{num}{unit}{currency or '元'}"
        ctype = "金額（美元）" if currency == "美元" else "金額"
        found.setdefault(ctype, set()).add((norm, disp))
    return found


def analyze(articles: list) -> list:
    """比對一個事件內各媒體的數字宣稱。

    回傳 [{type, agree, outlet_count, values: [{value, outlets}]}]，
    只包含至少兩家媒體都有提到的類別。
    """
    per_outlet = {}  # 類別 -> 媒體名 -> {(norm, disp), ...}
    for art in articles:
        text = f"{art.get('title', '')} {art.get('description', '')}"
        for ctype, values in extract_claims(text).items():
            per_outlet.setdefault(ctype, {}).setdefault(
                art["source_name"], set()
            ).update(values)

    claims = []
    for ctype, outlets in per_outlet.items():
        # 一家媒體只在自己數字一致時參與比對
        clean = {o: next(iter(vs)) for o, vs in outlets.items() if len(vs) == 1}
        if len(clean) < 2:
            continue
        groups = {}  # (norm, disp) -> [媒體名]
        for outlet, val in clean.items():
            groups.setdefault(val, []).append(outlet)
        values = [
            {"value": disp, "outlets": sorted(names)}
            for (_, disp), names in sorted(
                groups.items(), key=lambda kv: -len(kv[1])
            )
        ]
        claims.append(
            {
                "type": ctype,
                "agree": len(groups) == 1,
                "outlet_count": len(clean),
                "values": values,
            }
        )
    claims.sort(key=lambda c: (c["agree"], -c["outlet_count"]))
    return claims
