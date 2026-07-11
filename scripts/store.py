"""持久化文章庫：JSONL 是唯一原始資料，索引可由 JSONL 重建。

文章記錄寫入 data/articles/YYYY-MM-DD.jsonl；既有 JSONL 不會被改寫。每篇
文章以正規化後的 HTTP(S) 連結做全庫去重。data/articles/index.json 只是加速
查詢的衍生索引，遺失、損毀或引用不存在檔案時會從所有 JSONL 記錄重建。
"""

import json
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit, urlunsplit

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
ARTICLES_DIR = DATA_DIR / "articles"
INDEX_SCHEMA_VERSION = 1
INDEX_PATH = ARTICLES_DIR / "index.json"

CORE_FIELDS = (
    "link",
    "title",
    "source",
    "source_name",
    "published",
    "description",
)


def normalize_link(url: str) -> Optional[str]:
    """回傳保守正規化後的 HTTP(S) URL；無效連結回傳 None。"""
    try:
        parts = urlsplit((url or "").strip())
        port = parts.port
    except (TypeError, ValueError):
        return None
    scheme = parts.scheme.lower()
    if scheme not in {"http", "https"} or not parts.hostname:
        return None
    netloc = parts.hostname.lower()
    if port:
        netloc = f"{netloc}:{port}"
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((scheme, netloc, path, parts.query, ""))


def _archive_path_is_valid(filename: object) -> bool:
    if not isinstance(filename, str) or Path(filename).name != filename:
        return False
    return (ARTICLES_DIR / filename).is_file()


def rebuild_index() -> dict[str, str]:
    """從所有 JSONL 檔重建連結索引；壞掉的歷史資料列會被略過。"""
    links = {}
    for path in sorted(ARTICLES_DIR.glob("*.jsonl")):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            link = normalize_link(record.get("link") if isinstance(record, dict) else None)
            if link:
                links.setdefault(link, path.name)
    return links


def _load_index() -> Optional[dict[str, str]]:
    try:
        data = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    links = data.get("links") if isinstance(data, dict) else None
    if data.get("schema_version") != INDEX_SCHEMA_VERSION or not isinstance(links, dict):
        return None
    if not all(
        isinstance(link, str)
        and normalize_link(link) == link
        and _archive_path_is_valid(filename)
        for link, filename in links.items()
    ):
        return None
    return links


def _write_index(links: dict[str, str]) -> None:
    payload = {"schema_version": INDEX_SCHEMA_VERSION, "links": links}
    temp_path = INDEX_PATH.with_name(f".{INDEX_PATH.name}.tmp")
    temp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True),
        encoding="utf-8",
    )
    temp_path.replace(INDEX_PATH)


def _record_from_article(article: dict, now) -> dict:
    metadata = dict(article.get("metadata") or {})
    metadata.setdefault("schema_version", INDEX_SCHEMA_VERSION)
    metadata["first_seen"] = now.isoformat()
    record = {key: article[key] for key in CORE_FIELDS if article.get(key) is not None}
    record["metadata"] = metadata
    return record


def append_new(articles: list, now) -> int:
    """只附加尚未出現在完整文章庫的新文章，回傳新增篇數。"""
    ARTICLES_DIR.mkdir(parents=True, exist_ok=True)
    links = _load_index()
    rebuilt = links is None
    if rebuilt:
        links = rebuild_index()

    filename = f"{now.strftime('%Y-%m-%d')}.jsonl"
    records = []
    for article in articles:
        link = normalize_link(article.get("link"))
        if not link or link in links:
            continue
        links[link] = filename
        records.append(_record_from_article(article, now))

    if records:
        path = ARTICLES_DIR / filename
        with path.open("a", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    if records or rebuilt:
        _write_index(links)
    return len(records)
