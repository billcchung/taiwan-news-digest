# 時事懶人包

自動彙整台灣主要新聞媒體 RSS，將報導同一事件的文章聚合成「事件」，
依報導媒體數量排序，呈現多家媒體的視角，並保留每日快照作為事件追蹤紀錄。

線上網站由 GitHub Pages 提供，資料由 GitHub Actions 每 2 小時自動更新。

## 新聞來源

中央社、公視新聞、聯合新聞網、自由時報、中時新聞網、ETtoday、TVBS、
三立新聞網、東森新聞、鏡週刊、報導者、風傳媒、新頭殼、今日新聞。

來源不限 RSS：TVBS 與東森新聞沒有可用的 RSS，直接爬取其即時新聞頁；
其餘以媒體自家 RSS 為主，無法讀取時自動改用 Google News 站內搜尋 RSS
作為備援（網站頁尾會標示 `*`）。

## 架構

```
scripts/fetch_news.py    抓取 RSS／爬取即時頁 → 聚合事件 → 產出 data/events.json、status.json、archive/
scripts/summarize.py     選用：以 Claude API 為熱門事件產生中立摘要（需 ANTHROPIC_API_KEY）
index.html               首頁：熱門事件（≥2 家媒體報導）+ 即時新聞
archive.html             歷史紀錄：瀏覽每日快照
.github/workflows/update-news.yml   每 2 小時執行、提交資料、部署 Pages
```

事件聚合方式：標題去除標點與雜訊詞後取字元 bigram，
Jaccard 相似度 ≥ 0.3 即視為同一事件；事件追蹤 72 小時，
以最早一篇文章的連結雜湊作為穩定事件 ID。

## 初次設定

1. 到 repo 的 **Settings → Pages → Build and deployment**，Source 選 **GitHub Actions**。
2. （選用）到 **Settings → Secrets and variables → Actions** 新增 `ANTHROPIC_API_KEY`，
   之後每次更新會為熱門事件產生 LLM 摘要；不設定也能正常運作。
3. 到 **Actions** 分頁手動執行一次「抓取新聞並部署網站」。

## 本地測試

```bash
pip install -r scripts/requirements.txt
python scripts/fetch_news.py
python -m http.server 8000   # 開 http://localhost:8000
```

## 版權說明

本專案僅彙整各媒體公開 RSS 的標題、連結與摘要，內容版權屬原媒體所有。
