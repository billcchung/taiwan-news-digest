"""事件關鍵詞（標籤）抽取。

不依賴斷詞器或模型：從事件內各篇標題找「多篇都出現的最長中文片段」當標籤。
做法：
1. 每篇標題切成純中文片段，列舉 2~6 字的子字串，統計「幾篇標題出現過」（df）。
2. 保留 df >= 40% 篇數的候選，先把片段擴張成更長且 df 相近的完整詞
   （風假 → 颱風假），再依 df*長度 貪婪挑選，挑過的詞所覆蓋的 bigram
   不再重複使用，避免同一片語的滑動視窗碎片洗版。

另提供跨事件的標籤彙整（aggregate），供前端做標籤篩選列。
"""

import re
from collections import Counter

PUNCT_SPLIT = re.compile(r"[\s\W_a-zA-Z0-9]+")

# 太泛用、不適合當標籤的詞
TAG_STOP = {
    "颱風", "新聞", "台灣", "今天", "今日", "最新", "快訊", "影音", "直播",
    "縣市", "民眾", "網友", "媒體", "宣布", "曝光", "來襲", "逼近", "全台",
    "不斷更新", "懶人包", "影響", "回應", "表示", "指出", "持續", "注意",
    "上市櫃", "一次看", "上半年", "重新",
}

MAX_TAG_LEN = 6
MAX_TAGS = 3


def _grams2(s: str) -> set:
    return {s[i : i + 2] for i in range(len(s) - 1)} if len(s) >= 2 else {s}


def extract_tags(titles: list, max_tags: int = MAX_TAGS) -> list:
    n = len(titles)
    if n < 2:
        return []
    need = max(2, -(-n * 2 // 5))  # ceil(40%)
    df = Counter()
    for title in titles:
        cands = set()
        for seg in PUNCT_SPLIT.split(title):
            length = len(seg)
            for size in range(2, min(MAX_TAG_LEN, length) + 1):
                for i in range(length - size + 1):
                    cands.add(seg[i : i + size])
        for c in cands:
            df[c] += 1
    # 數字被切掉後殘留的單位開頭片段（2000萬交保 → 萬交保）不能當標籤
    BAD_HEAD = "萬億兆元年月日時分歲人名家戶級"
    kept = {
        c: f
        for c, f in df.items()
        if f >= need and c not in TAG_STOP and c[0] not in BAD_HEAD
    }

    def expand(c):
        best = c
        for f in kept:
            if c in f and len(f) > len(best) and kept[f] >= kept[c] * 0.7:
                best = f
        return best

    order = sorted(kept, key=lambda c: (-kept[c] * len(c), -len(c)))
    tags, used = [], set()
    for c in order:
        c = expand(c)
        g = _grams2(c)
        if g & used:
            continue
        tags.append(c)
        used |= g
        if len(tags) >= max_tags:
            break
    return tags


def aggregate(events: list, top_n: int = 12, min_events: int = 2) -> list:
    """跨事件彙整標籤：互相包含的變體（巴威／巴威颱風）合併為一個。

    回傳 [{"tag": 代表字串, "count": 事件數}]，依事件數排序。
    """
    cnt = Counter(t for ev in events for t in ev.get("tags", []))
    merged = {}
    for tag in sorted(cnt, key=lambda t: (-cnt[t], -len(t))):
        rep = next((r for r in merged if tag in r or r in tag), None)
        if rep:
            merged[rep] += cnt[tag]
        else:
            merged[tag] = cnt[tag]
    out = [
        {"tag": t, "count": c}
        for t, c in sorted(merged.items(), key=lambda kv: -kv[1])
        if c >= min_events
    ]
    return out[:top_n]
