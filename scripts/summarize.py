#!/usr/bin/env python3
"""選用的 LLM 事件分析步驟（需要 ANTHROPIC_API_KEY）。

對報導媒體數 >= 2 的熱門事件產生「可查證」的結構化分析，寫回 events.json
的 analysis 欄位：
- consensus：至少兩家不同媒體都報導的事實，每句附報導編號
- single_source：僅單一媒體提到的說法（明確標示，不與共識混在一起）
- disputes：各媒體說法矛盾之處，並列各方講法

每一句都帶 refs（報導編號），前端渲染成可點的原文連結，讀者可逐句查證。
「至少兩家媒體」不只靠提示詞：程式會驗證每條 consensus 的 refs 是否真的
跨越兩家不同媒體，不符者自動降級為 single_source。

分析以 (事件id, 文章數) 快取於 data/summaries.json，文章沒變就不重新產生。
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
MAX_ARTICLES = 12  # 每次分析最多送幾篇
PER_OUTLET_CAP = 2  # 每家媒體最多取幾篇，維持媒體多樣性

PROMPT = """以下是台灣多家媒體對同一新聞事件的報導（標題與摘要），已編號 [1] 到 [{n}]。
請逐條比對各家內容，輸出結構化分析：

- consensus：至少兩家「不同媒體」都有報導的事實。每條是一句中立陳述，
  refs 列出支持該陳述的報導編號，且必須來自至少兩家不同媒體。
- single_source：只有一家媒體提到、但對理解事件重要的說法。
- disputes：各媒體說法互相矛盾之處（數字、時間、定性不同）。
  topic 是爭點，positions 並列各方說法與其報導編號。

規則：
- 只能根據下列文字，不得加入任何外部知識或自行推論。
- 每一條陳述都必須附 refs（整數編號陣列）。
- 使用繁體中文，語氣中立，不加形容詞渲染，不下結論。
- consensus 以 2-5 條為宜；沒有內容的欄位輸出空陣列。

{articles}"""

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "consensus": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "refs": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["text", "refs"],
                "additionalProperties": False,
            },
        },
        "single_source": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "refs": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["text", "refs"],
                "additionalProperties": False,
            },
        },
        "disputes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string"},
                    "positions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "text": {"type": "string"},
                                "refs": {
                                    "type": "array",
                                    "items": {"type": "integer"},
                                },
                            },
                            "required": ["text", "refs"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["topic", "positions"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["consensus", "single_source", "disputes"],
    "additionalProperties": False,
}


def pick_articles(event: dict) -> list:
    """挑選送進模型的文章：每家媒體最多 PER_OUTLET_CAP 篇，共 MAX_ARTICLES 篇。"""
    picked, per_outlet = [], {}
    for art in sorted(event["articles"], key=lambda a: a["published"]):
        n = per_outlet.get(art["source"], 0)
        if n >= PER_OUTLET_CAP:
            continue
        per_outlet[art["source"]] = n + 1
        picked.append(art)
        if len(picked) >= MAX_ARTICLES:
            break
    return picked


def call_model(api_key: str, articles: list) -> dict:
    lines = []
    for i, a in enumerate(articles, 1):
        lines.append(f"[{i}]【{a['source_name']}】{a['title']}")
        if a.get("description"):
            lines.append(f"    {a['description'][:150]}")
    resp = requests.post(
        API_URL,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": MODEL,
            "max_tokens": 1200,
            "output_config": {
                "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}
            },
            "messages": [
                {
                    "role": "user",
                    "content": PROMPT.format(
                        n=len(articles), articles="\n".join(lines)
                    ),
                }
            ],
        },
        timeout=90,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("stop_reason") == "refusal":
        return {}
    text = "".join(b.get("text", "") for b in data.get("content", []))
    return json.loads(text)


def validate(raw: dict, articles: list) -> dict:
    """驗證 refs 合法性，並用程式強制「共識須跨兩家媒體」的規則。"""
    n = len(articles)
    outlet_of = {i + 1: articles[i]["source"] for i in range(n)}

    def clean_refs(refs):
        seen, out = set(), []
        for r in refs or []:
            if isinstance(r, int) and 1 <= r <= n and r not in seen:
                seen.add(r)
                out.append(r)
        return out

    analysis = {"consensus": [], "single_source": [], "disputes": []}
    for item in raw.get("consensus", []):
        refs = clean_refs(item.get("refs"))
        text = (item.get("text") or "").strip()
        if not refs or not text:
            continue
        entry = {"text": text, "refs": refs}
        if len({outlet_of[r] for r in refs}) >= 2:
            analysis["consensus"].append(entry)
        else:
            analysis["single_source"].append(entry)  # 降級：實際上只有一家
    for item in raw.get("single_source", []):
        refs = clean_refs(item.get("refs"))
        text = (item.get("text") or "").strip()
        if refs and text:
            analysis["single_source"].append({"text": text, "refs": refs})
    for d in raw.get("disputes", []):
        positions = []
        for p in d.get("positions", []):
            refs = clean_refs(p.get("refs"))
            text = (p.get("text") or "").strip()
            if refs and text:
                positions.append({"text": text, "refs": refs})
        if len(positions) >= 2 and (d.get("topic") or "").strip():
            analysis["disputes"].append(
                {"topic": d["topic"].strip(), "positions": positions}
            )

    if not analysis["consensus"] and not analysis["disputes"]:
        return {}
    analysis["sources"] = {
        str(i + 1): {
            "name": articles[i]["source_name"],
            "link": articles[i]["link"],
        }
        for i in range(n)
    }
    return analysis


def analyze_event(api_key: str, event: dict):
    articles = pick_articles(event)
    if len({a["source"] for a in articles}) < 2:
        return None
    raw = call_model(api_key, articles)
    if not raw:
        return None
    return validate(raw, articles) or None


def main() -> int:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        print("未設定 ANTHROPIC_API_KEY，跳過 LLM 分析。")
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
        cached = cache.get(ev["id"], {})
        if (
            cached.get("analysis")
            and cached.get("article_count") == ev["article_count"]
        ):
            ev["analysis"] = cached["analysis"]
            continue
        if done >= MAX_SUMMARIES_PER_RUN:
            if cached.get("analysis"):
                ev["analysis"] = cached["analysis"]
            continue
        try:
            analysis = analyze_event(api_key, ev)
        except Exception as exc:  # noqa: BLE001
            print(f"分析失敗 {ev['id']}: {exc}", file=sys.stderr)
            continue
        if analysis:
            ev["analysis"] = analysis
            cache[ev["id"]] = {
                "analysis": analysis,
                "article_count": ev["article_count"],
            }
            done += 1
            print(f"已分析：{ev['title'][:30]}")

    # 清掉不在本次事件清單裡的舊快取
    ids = {ev["id"] for ev in payload.get("events", [])}
    cache = {k: v for k, v in cache.items() if k in ids}

    events_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    cache_path.write_text(
        json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"完成：本次新產生 {done} 則分析。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
