#!/usr/bin/env python3
"""時事懶人包 — 新聞抓取與事件聚合腳本。

從台灣主要新聞媒體 RSS 抓取新聞，將報導同一事件的文章聚合成「事件」，
依報導媒體數排序，輸出 data/events.json 供靜態網站使用。
每日快照存入 data/archive/ 作為事件追蹤紀錄。
"""

import hashlib
import html
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import feedparser
import requests

TAIPEI = timezone(timedelta(hours=8))
NOW = datetime.now(TAIPEI)
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
ARCHIVE_DIR = DATA_DIR / "archive"

# 文章保留 48 小時；事件（含歷史文章）追蹤 72 小時
ARTICLE_WINDOW_H = 48
EVENT_WINDOW_H = 72
# 聚合門檻：兩篇標題（去雜訊後）共享 bigram 數 >= 4 且 Jaccard >= 0.12，
# 或 Jaccard >= 0.30。共享的多位數數字（如 1412萬 的 1412）算 2 個 bigram。
MIN_SHARED = 4
MIN_JACCARD_WITH_SHARED = 0.12
HIGH_JACCARD = 0.30
MAX_EVENTS = 60
MAX_LATEST = 40

USER_AGENT = (
    "Mozilla/5.0 (compatible; TaiwanNewsDigest/1.0; "
    "+https://github.com/)"
)


def gnews(site: str) -> str:
    return (
        "https://news.google.com/rss/search?"
        f"q=site:{site}%20when:2d&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
    )


# 每個來源可有多個 feed；全部失敗時改用 Google News 站內搜尋 RSS
SOURCES = [
    {
        "id": "cna",
        "name": "中央社",
        "feeds": [
            "https://feeds.feedburner.com/rsscna/politics",
            "https://feeds.feedburner.com/rsscna/intworld",
        ],
        "fallback": gnews("cna.com.tw"),
    },
    {
        "id": "pts",
        "name": "公視新聞",
        "feeds": ["https://news.pts.org.tw/xml/newsfeed.xml"],
        "fallback": gnews("news.pts.org.tw"),
    },
    {
        "id": "udn",
        "name": "聯合新聞網",
        "feeds": [],
        "fallback": gnews("udn.com"),
    },
    {
        "id": "ltn",
        "name": "自由時報",
        "feeds": ["https://news.ltn.com.tw/rss/all.xml"],
        "fallback": gnews("ltn.com.tw"),
    },
    {
        "id": "chinatimes",
        "name": "中時新聞網",
        "feeds": [],
        "fallback": gnews("chinatimes.com"),
    },
    {
        "id": "ettoday",
        "name": "ETtoday",
        "feeds": ["https://feeds.feedburner.com/ettoday/realtime"],
        "fallback": gnews("ettoday.net"),
    },
    {
        "id": "tvbs",
        "name": "TVBS",
        "feeds": [],
        "fallback": gnews("news.tvbs.com.tw"),
    },
    {
        "id": "setn",
        "name": "三立新聞網",
        "feeds": [],
        "fallback": gnews("setn.com"),
    },
]

TAG_RE = re.compile(r"<[^>]+>")
# 標題斷詞用：移除標點、空白、常見雜訊詞
PUNCT_RE = re.compile(r"[\s\W_]+", re.UNICODE)
NOISE_WORDS = [
    "快訊", "獨家", "影音", "影", "圖", "直播", "更新", "懶人包",
    "詳全文", "專題", "焦點", "即時",
]


def clean_text(s: str) -> str:
    s = html.unescape(s or "")
    s = TAG_RE.sub("", s)
    return s.strip()


def clean_title(title: str, from_gnews: bool) -> str:
    t = clean_text(title)
    if from_gnews:
        # Google News 標題尾端會附 " - 媒體名"
        t = re.sub(r"\s*[-|–]\s*[^-|–]{1,20}$", "", t)
    t = re.sub(r"^(快訊|獨家|影音?|圖多?|直播)[／/｜|:：]\s*", "", t)
    return t.strip()


# 過於泛用、單獨不足以判定同一事件的 bigram
STOP_BIGRAMS = {
    "台灣", "新聞", "報導", "今天", "今日", "民眾", "網友", "媒體",
    "政府", "表示", "指出", "回應", "最新", "曝光",
}


def title_key_chars(title: str) -> str:
    t = title
    for w in NOISE_WORDS:
        t = t.replace(w, "")
    return PUNCT_RE.sub("", t)


def profile(title: str) -> tuple:
    """回傳 (bigram set, 多位數數字 set)。"""
    chars = title_key_chars(title)
    if len(chars) < 2:
        grams = {chars} if chars else set()
    else:
        grams = {chars[i : i + 2] for i in range(len(chars) - 1)}
    nums = set(re.findall(r"\d{2,}", chars))
    return grams, nums


def similar(pa: tuple, pb: tuple) -> tuple:
    """回傳 (是否同一事件, 強度分數)。"""
    (ga, na), (gb, nb) = pa, pb
    if not ga or not gb:
        return False, 0.0
    shared = (ga & gb) - STOP_BIGRAMS
    strength = len(shared) + 2 * len(na & nb)
    jac = len(ga & gb) / len(ga | gb)
    ok = jac >= HIGH_JACCARD or (
        strength >= MIN_SHARED and jac >= MIN_JACCARD_WITH_SHARED
    )
    return ok, strength + jac


def parse_time(entry) -> datetime:
    for key in ("published", "updated"):
        val = entry.get(key)
        if not val:
            continue
        try:
            dt = parsedate_to_datetime(val)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=TAIPEI)
            return dt.astimezone(TAIPEI)
        except (TypeError, ValueError):
            pass
    for key in ("published_parsed", "updated_parsed"):
        val = entry.get(key)
        if val:
            try:
                dt = datetime(*val[:6], tzinfo=timezone.utc)
                return dt.astimezone(TAIPEI)
            except (TypeError, ValueError):
                pass
    return NOW


def fetch_feed(url: str):
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    resp.raise_for_status()
    parsed = feedparser.parse(resp.content)
    return parsed.entries or []


def fetch_source(src: dict):
    """回傳 (articles, status)"""
    articles, errors = [], []
    seen_links = set()
    got_primary = False

    def add_entries(entries, from_gnews: bool):
        for e in entries:
            link = (e.get("link") or "").strip()
            title = clean_title(e.get("title") or "", from_gnews)
            if not link or not title or len(title) < 4:
                continue
            if link in seen_links:
                continue
            seen_links.add(link)
            pub = parse_time(e)
            if NOW - pub > timedelta(hours=ARTICLE_WINDOW_H):
                continue
            if pub > NOW + timedelta(hours=1):
                pub = NOW
            desc = clean_text(e.get("summary") or e.get("description") or "")
            articles.append(
                {
                    "title": title,
                    "link": link,
                    "source": src["id"],
                    "source_name": src["name"],
                    "published": pub.isoformat(),
                    "description": desc[:200],
                }
            )

    for url in src["feeds"]:
        try:
            entries = fetch_feed(url)
            if entries:
                got_primary = True
                add_entries(entries, from_gnews=False)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{url}: {exc}")

    if not got_primary and src.get("fallback"):
        try:
            entries = fetch_feed(src["fallback"])
            add_entries(entries, from_gnews=True)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"fallback: {exc}")

    status = {
        "id": src["id"],
        "name": src["name"],
        "ok": len(articles) > 0,
        "count": len(articles),
        "via_fallback": not got_primary and len(articles) > 0,
        "errors": errors[:3],
    }
    return articles, status


def load_previous_articles() -> list:
    """從上次的 events.json 取回仍在追蹤期內的文章，維持事件連續性。"""
    path = DATA_DIR / "events.json"
    if not path.exists():
        return []
    try:
        prev = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    cutoff = NOW - timedelta(hours=EVENT_WINDOW_H)
    out = []
    for ev in prev.get("events", []):
        for art in ev.get("articles", []):
            try:
                pub = datetime.fromisoformat(art["published"])
            except (KeyError, ValueError):
                continue
            if pub >= cutoff:
                out.append(art)
    return out


def cluster(articles: list) -> list:
    """貪婪聚合：與叢集內任一篇文章相似即併入（取最強配對的叢集）。"""
    clusters = []  # each: {"profiles": [...], "articles": [...]}
    for art in sorted(articles, key=lambda a: a["published"]):
        p = profile(art["title"])
        best, best_score = None, 0.0
        for c in clusters:
            for member in c["profiles"]:
                ok, score = similar(p, member)
                if ok and score > best_score:
                    best, best_score = c, score
        if best is not None:
            best["articles"].append(art)
            best["profiles"].append(p)
        else:
            clusters.append({"profiles": [p], "articles": [art]})
    return clusters


def build_events(clusters: list, old_summaries: dict) -> list:
    events = []
    for c in clusters:
        arts = sorted(c["articles"], key=lambda a: a["published"])
        outlets = sorted({a["source"] for a in arts})
        eid = hashlib.sha1(arts[0]["link"].encode("utf-8")).hexdigest()[:12]
        # 代表標題：報導數最多媒體中最早的一篇（偏中性來源優先）
        pref = ["cna", "pts"]
        rep = next((a for p in pref for a in arts if a["source"] == p), arts[0])
        ev = {
            "id": eid,
            "title": rep["title"],
            "outlet_count": len(outlets),
            "outlets": outlets,
            "article_count": len(arts),
            "first_seen": arts[0]["published"],
            "last_updated": arts[-1]["published"],
            "articles": list(reversed(arts)),  # 新的在前
        }
        if eid in old_summaries:
            ev["summary"] = old_summaries[eid].get("summary")
        events.append(ev)
    events.sort(key=lambda e: (e["outlet_count"], e["last_updated"]), reverse=True)
    return events[:MAX_EVENTS]


def load_summaries() -> dict:
    path = DATA_DIR / "summaries.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8"
    )


def update_archive(events: list) -> None:
    """每日快照：同一天多次執行會覆寫當日檔案（保留當日最終狀態）。"""
    day = NOW.strftime("%Y-%m-%d")
    snapshot = {
        "date": day,
        "generated_at": NOW.isoformat(),
        "events": [
            {k: v for k, v in ev.items() if k != "articles"}
            | {"articles": ev["articles"][:10]}
            for ev in events
            if ev["outlet_count"] >= 2
        ],
    }
    write_json(ARCHIVE_DIR / f"{day}.json", snapshot)
    index_path = ARCHIVE_DIR / "index.json"
    dates = set()
    if index_path.exists():
        try:
            dates = set(json.loads(index_path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            pass
    dates.add(day)
    write_json(index_path, sorted(dates, reverse=True))


def main() -> int:
    all_articles, statuses = [], []
    for src in SOURCES:
        arts, status = fetch_source(src)
        print(
            f"[{status['name']}] {'OK' if status['ok'] else 'FAIL'} "
            f"{status['count']} 篇"
            + ("（經 Google News）" if status["via_fallback"] else ""),
            flush=True,
        )
        for err in status["errors"]:
            print(f"  ! {err}", file=sys.stderr)
        all_articles.extend(arts)
        statuses.append(status)

    # 併入上次仍在追蹤期的文章（去重）
    prev = load_previous_articles()
    seen = {a["link"] for a in all_articles}
    all_articles.extend(a for a in prev if a["link"] not in seen)

    if not all_articles:
        print("所有來源都抓取失敗，保留舊資料不更新。", file=sys.stderr)
        return 1

    clusters = cluster(all_articles)
    events = build_events(clusters, load_summaries())

    latest = sorted(
        all_articles, key=lambda a: a["published"], reverse=True
    )[:MAX_LATEST]

    write_json(
        DATA_DIR / "events.json",
        {
            "generated_at": NOW.isoformat(),
            "article_count": len(all_articles),
            "events": events,
            "latest": latest,
        },
    )
    write_json(
        DATA_DIR / "status.json",
        {"generated_at": NOW.isoformat(), "sources": statuses},
    )
    update_archive(events)

    hot = sum(1 for e in events if e["outlet_count"] >= 2)
    print(f"完成：{len(all_articles)} 篇文章 → {len(events)} 個事件（{hot} 個熱門）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
