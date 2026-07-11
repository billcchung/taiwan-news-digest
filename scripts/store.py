"""持久化文章庫：data/articles/YYYY-MM-DD.jsonl（一行一篇，只追加不改寫）。

events.json 是滾動 48 小時的「檢視層」，每次執行整份重算，舊文章會掉出去；
這裡才是不會被覆蓋的完整紀錄。每次執行以文章連結為 key，比對最近幾天的
檔案，已存在的跳過，只把新文章附加到當天檔尾。

紀錄欄位：link, title, source, source_name, published, description,
author, category, via_gnews, first_seen（首次被本系統看到的時間）。
"""

import json
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
ARTICLES_DIR = DATA_DIR / "articles"
# 追蹤窗只有 48 小時，回看 3 天的檔案足以涵蓋所有可能重複的連結
LOOKBACK_DAYS = 3

STORED_FIELDS = (
    "link",
    "title",
    "source",
    "source_name",
    "published",
    "description",
    "author",
    "category",
    "via_gnews",
)


def _recent_files(now):
    from datetime import timedelta

    for offset in range(LOOKBACK_DAYS):
        day = (now - timedelta(days=offset)).strftime("%Y-%m-%d")
        yield ARTICLES_DIR / f"{day}.jsonl"


def load_known_links(now) -> set:
    known = set()
    for path in _recent_files(now):
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                known.add(json.loads(line)["link"])
            except (json.JSONDecodeError, KeyError):
                continue
    return known


def append_new(articles: list, now) -> int:
    """把不在文章庫裡的新文章附加到當天的 jsonl，回傳新增篇數。"""
    ARTICLES_DIR.mkdir(parents=True, exist_ok=True)
    known = load_known_links(now)
    records = []
    for art in articles:
        link = art.get("link")
        if not link or link in known:
            continue
        known.add(link)
        rec = {k: art[k] for k in STORED_FIELDS if art.get(k) is not None}
        rec["first_seen"] = now.isoformat()
        records.append(rec)
    if records:
        path = ARTICLES_DIR / f"{now.strftime('%Y-%m-%d')}.jsonl"
        with path.open("a", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return len(records)
