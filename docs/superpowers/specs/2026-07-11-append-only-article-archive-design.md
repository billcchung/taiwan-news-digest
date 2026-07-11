# Append-Only Article Archive Design

## Goal

Keep each discovered article exactly once across the full archive, while preserving article provenance and author metadata.

## Scope

`data/events.json` remains a rolling 48-hour presentation dataset and may be rewritten. The durable article archive remains `data/articles/YYYY-MM-DD.jsonl`; article records are never modified or removed by the fetch pipeline.

## Storage model

Each newly discovered article is appended as one JSON object per line to the file for the current Taipei date. The article link, after deterministic normalization, is its unique identity across every archive file.

`data/articles/index.json` is a derived, replaceable lookup index. It maps each normalized article link to the archive file containing its record. It is not the source of truth. If it is missing, malformed, has an unsupported schema version, or points to a missing archive file, the pipeline rebuilds it by scanning all JSONL archive files before storing new articles.

The index is rewritten atomically after new records are appended. A failed index write must not corrupt an existing index. On the next execution, a missing or invalid index is rebuilt from JSONL files.

## Record schema

Existing top-level article fields remain: `link`, `title`, `source`, `source_name`, `published`, and `description`.

New records include a `metadata` object:

```json
{
  "schema_version": 1,
  "first_seen": "2026-07-11T14:30:00+08:00",
  "author": {"name": "王小明", "source": "rss"},
  "category": "politics",
  "acquisition": "rss",
  "feed_url": "https://example.com/feed.xml"
}
```

`author` is always present. `name` is `null` and `source` is `unknown` when no reliable author is available. Otherwise, `source` is `rss` for an RSS author field or `description` for a recognised byline pattern. `category` and `feed_url` are omitted when unavailable. `acquisition` is exactly `rss`, `scraper`, or `gnews`.

The fetch pipeline creates this metadata before persistence. The event dataset retains `author` as a flat field because the current frontend reads `article.author`; that runtime field is separate from the durable archive schema.

## Data flow

1. Fetch each source and annotate each article with author provenance, category, acquisition method, and feed URL.
2. Load the full-archive index; rebuild it from JSONL files when it is unusable.
3. Normalize each incoming link and append only records absent from the index.
4. Atomically update the index only after successful archive append.
5. Continue producing the rolling event and archive-view JSON files as today.

## Link normalization

Uniqueness uses a conservative canonical form: strip URL fragments, lowercase the scheme and host, remove a trailing slash from non-root paths, and preserve query strings. Query strings remain because they can identify distinct articles or editions.

## Error handling

Malformed JSONL rows are ignored while rebuilding the index so one historical bad row does not stop collection. A record without a valid HTTP(S) link is skipped. If writing the derived index fails after appending records, the run reports the failure and exits unsuccessfully; a later run rebuilds the index and will not duplicate the appended records.

## Tests

Add focused tests for: archive-wide duplicate rejection after more than three days; duplicate links within one run; normalization of fragments and trailing slashes; index rebuild when missing or invalid; author provenance from RSS and description extraction; metadata for RSS, scraper, and Google News acquisition. Keep the existing offline pipeline test passing.
