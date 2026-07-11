# Append-Only Article Archive Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve every discovered article exactly once across the full JSONL archive and attach durable author and provenance metadata.

**Architecture:** `scripts/store.py` is the only persistent-archive writer. It maintains a replaceable URL-to-file JSON index that can be rebuilt from JSONL files. `scripts/fetch_news.py` adds author and acquisition provenance before storage; its flat `author` value stays available to the rolling event dataset.

**Tech Stack:** Python 3 standard library, feedparser, requests, JSONL, standalone Python assertion scripts.

## Global Constraints

- `data/articles/YYYY-MM-DD.jsonl` records are append-only and never rewritten.
- Article identity is a conservatively normalized HTTP(S) URL; query strings remain significant.
- `data/articles/index.json` is derived state and rebuildable from JSONL.
- New durable metadata uses `metadata.schema_version` equal to `1`.
- `data/events.json` and daily event archives retain their rolling rewrite behavior.

---

### Task 1: Add archive and metadata tests

**Files:**

- Create: `scripts/test_store.py`
- Modify: `scripts/test_local.py`

**Interfaces:**

- Consumes: `store.append_new(articles: list, now: datetime) -> int`
- Produces: isolated executable tests for archive-wide identity, index recovery, and ingestion metadata.

- [ ] **Step 1: Write the failing storage tests**

```python
def test_archive_wide_duplicate_is_not_appended():
    assert store.append_new([article("https://example.com/news/42#first")], day(7)) == 1
    assert store.append_new([article("https://EXAMPLE.com/news/42/#later")], day(11)) == 0

def test_invalid_index_is_rebuilt_without_duplicate_records():
    assert store.append_new([article("https://example.com/a")], day(7)) == 1
    store.INDEX_PATH.write_text("{broken", encoding="utf-8")
    assert store.append_new([article("https://example.com/a"), article("https://example.com/b")], day(11)) == 1
```

Use `tempfile.TemporaryDirectory()` and temporarily replace `store.ARTICLES_DIR` and `store.INDEX_PATH`. Add cases for duplicate links in one batch, malformed JSONL rows, and `metadata.first_seen`.

- [ ] **Step 2: Run the storage test to verify it fails**

Run: `python scripts/test_store.py`

Expected: FAIL because global normalized URL identity and `INDEX_PATH` do not exist.

- [ ] **Step 3: Isolate the existing pipeline test**

```python
with tempfile.TemporaryDirectory() as temp_dir:
    temp_data = Path(temp_dir) / "data"
    fetch_news.DATA_DIR = temp_data
    fetch_news.ARCHIVE_DIR = temp_data / "archive"
    store.ARTICLES_DIR = temp_data / "articles"
    store.INDEX_PATH = store.ARTICLES_DIR / "index.json"
    rc = fetch_news.main()
```

Import `store` in `scripts/test_local.py` and retain all existing assertions.

- [ ] **Step 4: Run the integration test to verify it stays green**

Run: `python scripts/test_local.py`

Expected: PASS without changes under the repository `data/` directory.

- [ ] **Step 5: Commit the test scaffold**

Run: `git add scripts/test_store.py scripts/test_local.py && git commit -m "test: cover persistent article archive"`

### Task 2: Implement append-only full-archive storage

**Files:**

- Modify: `scripts/store.py`
- Test: `scripts/test_store.py`

**Interfaces:**

- Consumes: article dictionaries with core article fields and optional `metadata`.
- Produces: `normalize_link(url: str) -> str | None`, `append_new(articles: list, now: datetime) -> int`, and `data/articles/index.json` containing normalized URL keys and archive filenames.

- [ ] **Step 1: Run the focused test and verify the expected failure**

Run: `python scripts/test_store.py`

Expected: FAIL because storage scans only a three-day window.

- [ ] **Step 2: Add conservative URL normalization**

```python
def normalize_link(url: str) -> str | None:
    parts = urlsplit((url or "").strip())
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        return None
    netloc = parts.hostname.lower()
    if parts.port:
        netloc = f"{netloc}:{parts.port}"
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), netloc, path, parts.query, ""))
```

Use `urllib.parse.urlsplit` and `urlunsplit`; preserve query strings and remove fragments.

- [ ] **Step 3: Replace lookback scanning with a recoverable index**

```python
INDEX_SCHEMA_VERSION = 1
INDEX_PATH = ARTICLES_DIR / "index.json"

def rebuild_index() -> dict[str, str]:
    links = {}
    for path in sorted(ARTICLES_DIR.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                link = normalize_link(json.loads(line).get("link"))
            except json.JSONDecodeError:
                continue
            if link:
                links.setdefault(link, path.name)
    return links
```

Validate the JSON index. Rebuild it if absent, malformed, schema version is not `1`, `links` is not a dictionary, or an indexed archive file is missing. Use a temporary sibling file and `Path.replace()` for writes.

- [ ] **Step 4: Append only unseen records and apply first-seen metadata**

```python
metadata = dict(art.get("metadata") or {})
metadata.setdefault("schema_version", 1)
metadata["first_seen"] = now.isoformat()
record = {key: art[key] for key in CORE_FIELDS if art.get(key) is not None}
record["metadata"] = metadata
```

Reject invalid links. Deduplicate against the index and within the batch. Append records before atomically writing the updated index.

- [ ] **Step 5: Run the focused test to verify it passes**

Run: `python scripts/test_store.py`

Expected: PASS for duplicate rejection, index recovery, and first-seen metadata.

- [ ] **Step 6: Commit the storage implementation**

Run: `git add scripts/store.py scripts/test_store.py && git commit -m "feat: index append-only article archive"`

### Task 3: Capture author and provenance metadata at ingestion

**Files:**

- Modify: `scripts/fetch_news.py`
- Modify: `scripts/test_store.py`

**Interfaces:**

- Consumes: RSS entries, scraper entries, and Google News fallback entries.
- Produces: `article["metadata"]` with `schema_version`, `author`, optional `category` and `feed_url`, and `acquisition`; preserves `article["author"]` for the frontend.

- [ ] **Step 1: Add failing metadata tests**

```python
assert rss_article["metadata"]["author"] == {"name": "王小明", "source": "rss"}
assert byline_article["metadata"]["author"] == {"name": "陳心瑜", "source": "description"}
assert unknown_article["metadata"]["author"] == {"name": None, "source": "unknown"}
assert gnews_article["metadata"]["acquisition"] == "gnews"
```

Exercise `fetch_source` with a fake `fetch_feed`; do not make HTTP requests.

- [ ] **Step 2: Run the focused test to verify it fails**

Run: `python scripts/test_store.py`

Expected: FAIL because incoming articles have no nested metadata or author provenance.

- [ ] **Step 3: Pass acquisition and source URL into entry conversion**

```python
def add_entries(entries, acquisition: str, feed_url: str | None = None):
    metadata = {
        "schema_version": 1,
        "author": {"name": author, "source": author_source},
        "acquisition": acquisition,
    }
    if category:
        metadata["category"] = category[:30]
    if feed_url:
        metadata["feed_url"] = feed_url
    art["metadata"] = metadata
```

Call it with `rss`, `scraper`, or `gnews` and the relevant feed or scraper-page URL. Set `author_source` to `rss`, `description`, or `unknown`; continue adding the flat author field only when a name exists.

- [ ] **Step 4: Run focused tests to verify metadata extraction passes**

Run: `python scripts/test_store.py`

Expected: PASS for RSS, description-derived, unknown, and Google News metadata.

- [ ] **Step 5: Commit ingestion metadata**

Run: `git add scripts/fetch_news.py scripts/test_store.py && git commit -m "feat: record article provenance metadata"`

### Task 4: Document and verify end-to-end behavior

**Files:**

- Modify: `README.md`
- Test: `scripts/test_store.py`
- Test: `scripts/test_local.py`

**Interfaces:**

- Consumes: persistent JSONL records and their derived index.
- Produces: accurate operational documentation and passing test scripts.

- [ ] **Step 1: Update the persistence documentation**

```markdown
`data/articles/index.json` is a derived, rebuildable URL index. The JSONL files
remain the authoritative append-only records. Each new record has a
`metadata` object with schema version, first-seen timestamp, author and its
provenance, acquisition method, and available category and feed URL.
```

State that identity is global across the archive and query strings remain part of identity.

- [ ] **Step 2: Run the complete suite**

Run: `python scripts/test_store.py && python scripts/test_local.py`

Expected: both scripts exit `0`; integration testing does not modify tracked `data/` files.

- [ ] **Step 3: Inspect generated-data changes**

Run: `git status --short`

Expected: no changed files under `data/`.

- [ ] **Step 4: Commit documentation and final test changes**

Run: `git add README.md scripts/test_store.py scripts/test_local.py && git commit -m "docs: describe article archive metadata"`
