#!/usr/bin/env python3
"""Focused tests for the append-only article archive."""

import json
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
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


def main():
    tests = [
        test_archive_wide_duplicate_is_not_appended,
        test_duplicate_in_batch_is_appended_once,
        test_invalid_index_is_rebuilt_without_duplicate_records,
        test_malformed_archive_rows_are_ignored_when_rebuilding_index,
        test_new_record_adds_first_seen_metadata,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")


if __name__ == "__main__":
    main()
