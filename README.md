# VCP 選股大腦 · L1 精準選股

台股 VCP（Volatility Contraction Pattern）自動選股系統。每日於 GitHub Actions 雲端跑（0 AI token），
輸出 `leaders.json` + 網頁報告到 GitHub Pages，打開就看今天的選股結果。

> 設計與規格鎖定在 Notion「VCP Trading Strategy」。本 repo 是 **L1（運算層）** 的實作。
> L2 族群熱點 / L3 基本面深挖透過乾淨 JSON 合約往上接，不需重做 L1。

## 架構

```
FinMind API ──> data/ 快取(parquet, 進 repo) ──> 運算 A-1~A-9 ──> docs/leaders.json ──> docs/index.html(Pages)
   (date-keyed 整市場批次抓，免費版額度也扛得住)        (純確定性計算·0 token)
```

| 層 | 模組 | A 表 |
|----|------|------|
| 大盤 | `market_light.py` | A-1 市場紅綠燈 → 倉位係數 |
| 趨勢 | `trend_template.py` | A-2 Minervini 8 條 |
| 強度 | `rs.py` | A-3 RS Rating（全台股真百分位）+ RS 線 |
| **形態** | **`vcp.py`** | **A-4 收縮序列 + 樞紐 + 突破 + 評分 A/B/C** ✅ 已實作+測試 |
| 量能 | `indicators.py` | A-5 流動性≥5,000萬 + 量縮/爆量 |
| 基本面 | `fundamentals.py` | A-6 營收季度化 / EPS / 毛利 / ROE |
| 籌碼 | `chips.py` | A-7 三大法人 / 投信連買 / 融資反指標 |
| 族群 | `groups.py` | A-8 族群強度排名 / 一致性（= L2 雛形）|
| 風控 | `risk.py` | A-9 催化劑 / 停損 / 風報比 / 倉位 |

所有門檻參數集中在 `config.py`（唯一真相來源）。

## 輸出三張清單
- **LEADERS** — 全條件通過，主要選股目標
- **READY** — RS≥80 + 趨勢對齊 + 近高，觀察名單
- **BREAKOUT** — 當日突破樞紐且量增

## 本地開發
```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
.venv/Scripts/python -m pytest tests/ -v      # 引擎單元測試
# FinMind token 放環境變數，不進 repo：
export FINMIND_TOKEN=xxxx
.venv/Scripts/python -m src.screener           # 跑一次選股（待建）
```

## 部署
- GitHub Actions 每日台股收盤後（07:00 UTC / 15:00 台北）自動跑
- FinMind token 存在 Actions Secret `FINMIND_TOKEN`（加密，永不進程式碼）
- 輸出 commit 回 repo，GitHub Pages 服務 `docs/`

## 狀態
- [x] 環境 / 骨架 / `config.py` 全參數鎖定
- [x] **A-4 VCP 引擎 + 14 項單元測試（綠）**
- [ ] FinMind 資料層（date-keyed 批次 + parquet 快取）
- [ ] 廣達 as-of 2026-03-20 真實回歸（POC 錨點）
- [ ] A-1/A-2/A-3/A-5/A-6/A-7/A-8/A-9 模組
- [ ] 報告頁 + Actions + 上 Pages
