#!/usr/bin/env python3
"""離線測試：以本地模擬 RSS 內容跑完整 pipeline，不連外網。

用法：python scripts/test_local.py
"""

import sys
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fetch_news  # noqa: E402

TAIPEI = timezone(timedelta(hours=8))
NOW = datetime.now(TAIPEI)


def rss(items, gnews=False):
    parts = ['<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>']
    for i, (title, mins_ago) in enumerate(items):
        pub = format_datetime(NOW - timedelta(minutes=mins_ago))
        if gnews:
            title = f"{title} - 某媒體"
        parts.append(
            f"<item><title><![CDATA[{title}]]></title>"
            f"<link>https://example.com/{abs(hash(title)) % 10**10}/{i}</link>"
            f"<pubDate>{pub}</pubDate>"
            f"<description><![CDATA[{title}的內文摘要。]]></description></item>"
        )
    parts.append("</channel></rss>")
    return "".join(parts).encode("utf-8")


# 三個跨媒體事件 + 各家獨家，驗證聚合與排序
FIXTURES = {
    "cna": rss([
        ("立委林宜瑾詐領助理費1412萬 一審判刑7年", 50),
        ("立院三讀虛擬資產法 明定穩定幣發行依據", 120),
        ("藍版無人機條例6年2400億 單一採購逾1億須向立院報告", 90),
    ]),
    "pts": rss([
        ("林宜瑾涉詐助理費一審重判7年 民進黨：尊重司法", 45),
        ("中聯油脂沙拉油檢出致癌物超標 泰山福壽急回收", 200),
    ]),
    "udn": rss([
        ("詐領助理費1412萬 綠委林宜瑾一審判7年褫奪公權4年", 40),
        ("無人機條例藍白versions出爐 政院憂排擠社福預算", 85),
    ], gnews=True),
    "ltn": rss([
        ("快訊／林宜瑾詐助理費案一審判7年 全案可上訴", 42),
        ("千噸問題油流入市面 毒物專家：苯駢芘為一級致癌物", 190),
    ]),
    "chinatimes": rss([
        ("沙拉油苯駢芘超標4倍 中聯油脂千噸毒油流入市面", 195),
        ("獨家／某科技廠驚傳裁員", 30),
    ]),
    "ettoday": rss([
        ("快訊／綠委涉詐1412萬助理費遭重判七年 民進黨：尊重司法", 48),
        ("沒發票也能退！油品原料出包 泰山祭最高規格補償", 185),
    ]),
    "tvbs": rss([
        ("無人機條例國民黨版出爐 6年編2400億公務預算", 88),
    ], gnews=True),
    "setn": rss([
        ("三立獨家：夜市美食排行榜", 20),
    ], gnews=True),
}


class FakeResponse:
    def __init__(self, content):
        self.content = content

    def raise_for_status(self):
        pass


def fake_get(url, **kwargs):
    for src in fetch_news.SOURCES:
        urls = list(src["feeds"]) + [src.get("fallback", "")]
        if url in urls:
            # 模擬部分媒體自家 RSS 掛掉（udn/tvbs/setn 本來就走 fallback；
            # chinatimes 模擬主 feed 失敗、走 fallback）
            if src["id"] == "chinatimes" and url != src["fallback"]:
                raise ConnectionError("simulated primary feed failure")
            return FakeResponse(FIXTURES[src["id"]])
    raise ValueError(f"unexpected url {url}")


def main():
    fetch_news.requests.get = fake_get
    rc = fetch_news.main()
    assert rc == 0, "fetch_news.main() 應回傳 0"

    import json
    data = json.loads(
        (fetch_news.DATA_DIR / "events.json").read_text(encoding="utf-8")
    )
    events = data["events"]
    hot = [e for e in events if e["outlet_count"] >= 2]
    print("\n=== 驗證結果 ===")
    for ev in events:
        print(f"  [{ev['outlet_count']}家/{ev['article_count']}篇] {ev['title']}")

    # 林宜瑾案應聚合 5 家媒體
    lin = [e for e in hot if "林宜瑾" in e["title"] or "助理費" in e["title"]]
    assert lin and lin[0]["outlet_count"] >= 4, "林宜瑾案應聚合至少4家媒體"
    # 毒油案應聚合 >= 3 家
    oil = [e for e in hot if "油" in e["title"] or "苯駢芘" in e["title"]]
    assert oil and oil[0]["outlet_count"] >= 3, "毒油案應聚合至少3家媒體"
    # 無人機條例應聚合 >= 2 家
    drone = [e for e in hot if "無人機" in e["title"]]
    assert drone and drone[0]["outlet_count"] >= 2, "無人機條例應聚合至少2家"
    # 排序：第一名媒體數最多
    assert events[0]["outlet_count"] == max(e["outlet_count"] for e in events)
    # 不同事件不應被誤併：夜市獨家應獨立
    assert any(
        e["outlet_count"] == 1 and "夜市" in e["title"] for e in events
    ), "獨家新聞應保持獨立事件"
    # status.json：chinatimes 應標示 via_fallback
    st = json.loads(
        (fetch_news.DATA_DIR / "status.json").read_text(encoding="utf-8")
    )
    ct = next(s for s in st["sources"] if s["id"] == "chinatimes")
    assert ct["ok"] and ct["via_fallback"], "chinatimes 應成功且走備援"
    # archive 檔案存在
    day = NOW.strftime("%Y-%m-%d")
    assert (fetch_news.ARCHIVE_DIR / f"{day}.json").exists()
    assert (fetch_news.ARCHIVE_DIR / "index.json").exists()

    print("\n全部斷言通過 ✓")


if __name__ == "__main__":
    main()
