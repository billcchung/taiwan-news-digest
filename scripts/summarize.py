#!/usr/bin/env python3
"""選用的 LLM 摘要步驟（需要 ANTHROPIC_API_KEY）。

對報導媒體數 >= 2 的熱門事件產生中立摘要，寫回 events.json 的 summary 欄位。
摘要以 (事件id, 文章數) 快取於 data/summaries.json，文章沒變就不重新產生。
沒有設定 API key 時直接跳過，不影響主流程。
"""

import json
import os
import sys
from pathlib import Path

import requests

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
API_URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-haiku-4-5"
MAX_SUMMARIES_PER_RUN = 10  # 控制成本

PROMPT = """以下是台灣多家媒體對同一新聞事件的報導標題與摘要。
請用繁體中文寫一段 2-4 句的中立摘要，說明發生了什麼事。
若各媒體報導角度有明顯差異，最後用一句話點出差異。
只輸出摘要本文，不要加任何前言或標題。

{articles}"""


def summarize(api_key: str, event: dict) -> str | None:
    lines = []
    for a in event["articles"][:8]:
        lines.append(f"【{a['source_name']}】{a['title']}")
        if a.get("description"):
            lines.append(f"  {a['description'][:120]}")
    resp = requests.post(
        API_URL,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": MODEL,
            "max_tokens": 400,
            "messages": [
                {
                    "role": "user",
                    "content": PROMPT.format(articles="\n".join(lines)),
                }
            ],
        },
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    return "".join(
        b.get("text", "") for b in data.get("content", [])
    ).strip() or None


def main() -> int:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        print("未設定 ANTHROPIC_API_KEY，跳過 LLM 摘要。")
        return 0

    events_path = DATA_DIR / "events.json"
    if not events_path.exists():
        print("找不到 events.json，先執行 fetch_news.py。", file=sys.stderr)
        return 1
    payload = json.loads(events_path.read_text(encoding="utf-8"))

    cache_path = DATA_DIR / "summaries.json"
    cache = {}
    if cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass

    done = 0
    for ev in payload.get("events", []):
        if ev["outlet_count"] < 2:
            continue
        cached = cache.get(ev["id"])
        if cached and cached.get("article_count") == ev["article_count"]:
            ev["summary"] = cached["summary"]
            continue
        if done >= MAX_SUMMARIES_PER_RUN:
            if cached:
                ev["summary"] = cached["summary"]
            continue
        try:
            text = summarize(api_key, ev)
        except Exception as exc:  # noqa: BLE001
            print(f"摘要失敗 {ev['id']}: {exc}", file=sys.stderr)
            continue
        if text:
            ev["summary"] = text
            cache[ev["id"]] = {
                "summary": text,
                "article_count": ev["article_count"],
            }
            done += 1
            print(f"已摘要：{ev['title'][:30]}")

    # 清掉不在本次事件清單裡的舊快取
    ids = {ev["id"] for ev in payload.get("events", [])}
    cache = {k: v for k, v in cache.items() if k in ids}

    events_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    cache_path.write_text(
        json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"完成：本次新產生 {done} 則摘要。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
