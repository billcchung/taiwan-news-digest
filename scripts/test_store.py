#!/usr/bin/env python3
"""Focused tests for the append-only article archive."""

import json
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fetch_news  # noqa: E402
import store  # noqa: E402


UTC = timezone.utc


def day(number: int) -> datetime:
    return datetime(2026, 7, number, 12, tzinfo=UTC)


def article(link: str) -> dict:
    return {
        "link": link,
        "title": "測試文章",
        "source": "test",
        "source_name": "測試來源",
        "published": "2026-07-07T10:00:00+00:00",
        "description": "測試摘要",
        "metadata": {"acquisition": "rss"},
    }


@contextmanager
def temporary_archive():
    original_dir = store.ARTICLES_DIR
    original_index = getattr(store, "INDEX_PATH", None)
    with tempfile.TemporaryDirectory() as temp_dir:
        store.ARTICLES_DIR = Path(temp_dir) / "articles"
        store.INDEX_PATH = store.ARTICLES_DIR / "index.json"
        try:
            yield store.ARTICLES_DIR
        finally:
            store.ARTICLES_DIR = original_dir
            if original_index is None:
                del store.INDEX_PATH
            else:
                store.INDEX_PATH = original_index


def records(articles_dir: Path) -> list:
    out = []
    for path in sorted(articles_dir.glob("*.jsonl")):
        out.extend(json.loads(line) for line in path.read_text(encoding="utf-8").splitlines())
    return out


def test_archive_wide_duplicate_is_not_appended():
    with temporary_archive() as articles_dir:
        assert store.append_new([article("https://example.com/news/42#first")], day(7)) == 1
        assert store.append_new([article("https://EXAMPLE.com/news/42/#later")], day(11)) == 0
        assert len(records(articles_dir)) == 1


def test_duplicate_in_batch_is_appended_once():
    with temporary_archive() as articles_dir:
        assert store.append_new([
            article("https://example.com/a"),
            article("https://example.com/a#second-copy"),
        ], day(11)) == 1
        assert len(records(articles_dir)) == 1


def test_non_string_links_are_rejected():
    with temporary_archive() as articles_dir:
        malformed = article("https://example.com/a")
        malformed["link"] = 42
        assert store.append_new([malformed], day(11)) == 0
        assert records(articles_dir) == []


def test_invalid_index_is_rebuilt_without_duplicate_records():
    with temporary_archive() as articles_dir:
        assert store.append_new([article("https://example.com/a")], day(7)) == 1
        store.INDEX_PATH.write_text("{broken", encoding="utf-8")
        assert store.append_new([
            article("https://example.com/a"),
            article("https://example.com/b"),
        ], day(11)) == 1
        assert len(records(articles_dir)) == 2
        index = json.loads(store.INDEX_PATH.read_text(encoding="utf-8"))
        assert index["schema_version"] == 1


def test_malformed_archive_rows_are_ignored_when_rebuilding_index():
    with temporary_archive() as articles_dir:
        articles_dir.mkdir(parents=True)
        (articles_dir / "2026-07-07.jsonl").write_text(
            "not json\n" + json.dumps(article("https://example.com/a")) + "\n",
            encoding="utf-8",
        )
        assert store.append_new([article("https://example.com/a")], day(11)) == 0


def test_new_record_adds_first_seen_metadata():
    with temporary_archive() as articles_dir:
        assert store.append_new([article("https://example.com/a")], day(11)) == 1
        stored = records(articles_dir)[0]
        assert stored["metadata"]["first_seen"] == day(11).isoformat()
        assert stored["metadata"]["schema_version"] == 1


def fetched_article(entry: dict, fallback: bool = False) -> dict:
    source = {
        "id": "test",
        "name": "測試來源",
        "feeds": [] if fallback else ["https://example.com/rss.xml"],
        "fallback": "https://news.google.com/rss/search?q=example" if fallback else None,
    }
    original_fetch_feed = fetch_news.fetch_feed
    fetch_news.fetch_feed = lambda url: [entry]
    try:
        articles, status = fetch_news.fetch_source(source)
    finally:
        fetch_news.fetch_feed = original_fetch_feed
    assert status["ok"] and len(articles) == 1
    return articles[0]


def feed_entry(**overrides) -> dict:
    entry = {
        "title": "測試新聞標題",
        "link": "https://example.com/news/1",
        "published": format_datetime(fetch_news.NOW),
        "description": "測試新聞摘要",
    }
    entry.update(overrides)
    return entry


def test_rss_author_metadata():
    stored = fetched_article(feed_entry(author="王小明", category="政治"))
    assert stored["metadata"]["author"] == {"name": "王小明", "source": "rss"}
    assert stored["metadata"]["category"] == "政治"
    assert stored["metadata"]["acquisition"] == "rss"
    assert stored["metadata"]["feed_url"] == "https://example.com/rss.xml"


def test_description_author_metadata():
    stored = fetched_article(feed_entry(description="〔記者陳心瑜／新北報導〕測試摘要"))
    assert stored["author"] == "陳心瑜"
    assert stored["metadata"]["author"] == {"name": "陳心瑜", "source": "description"}


def test_unknown_author_and_google_news_metadata():
    unknown = fetched_article(feed_entry())
    assert unknown["metadata"]["author"] == {"name": None, "source": "unknown"}
    fallback = fetched_article(feed_entry(), fallback=True)
    assert fallback["metadata"]["acquisition"] == "gnews"
    assert fallback["metadata"]["feed_url"].startswith("https://news.google.com/")


def test_scraper_metadata():
    source = {
        "id": "test",
        "name": "測試來源",
        "feeds": [],
        "scraper": lambda: [feed_entry()],
        "scraper_url": "https://example.com/realtime",
    }
    articles, status = fetch_news.fetch_source(source)
    assert status["ok"] and len(articles) == 1
    assert articles[0]["metadata"]["acquisition"] == "scraper"
    assert articles[0]["metadata"]["feed_url"] == "https://example.com/realtime"


def main():
    tests = [
        test_archive_wide_duplicate_is_not_appended,
        test_duplicate_in_batch_is_appended_once,
        test_non_string_links_are_rejected,
        test_invalid_index_is_rebuilt_without_duplicate_records,
        test_malformed_archive_rows_are_ignored_when_rebuilding_index,
        test_new_record_adds_first_seen_metadata,
        test_rss_author_metadata,
        test_description_author_metadata,
        test_unknown_author_and_google_news_metadata,
        test_scraper_metadata,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")


if __name__ == "__main__":
    main()
