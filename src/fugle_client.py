# -*- coding: utf-8 -*-
"""
Fugle 富果行情資料層
===================
取代 FinMind「付費 by-date bulk」(FinMindClient.price_by_date)——那是 Backer 限定，
退回免費版後被鎖，正是 2026-07 日更凍結的元兇。富果免費於本專案（弟弟訂閱），
且整市場「快照」一次就把全市場拿回來，不用逐檔。

分工：富果只負責「價格層」。
  - snapshot()：整市場當日快照（TSE+OTC 各一發）→ 每日增量更新
  - candles()：單檔歷史日K → 冷啟動 / 補缺口
基本面/籌碼（法人/月營收/財報/ROE/融資）仍走 FinMind 免費版，不由富果取代。

欄位正規化：完全對齊 FinMindClient，讓 cache/下游零改動。
  date / (stock_id) / open / high / low / close / volume / turnover
單位對齊 FinMind：
  - volume = 「股」。⚠️ 快照的 tradeVolume 是「張」→ ×1000 轉股；歷史K的 volume 已是股。
  - turnover = 成交金額「台幣元」。
（實測 2026-07：富果 candles 的 close/volume/turnover 與 FinMind 逐分錢一致。）
"""
from __future__ import annotations

import os
import time

import pandas as pd
import requests

FUGLE_BASE_URL = "https://api.fugle.tw/marketdata/v1.0"
FUGLE_SLEEP_SEC = 0.10          # 一般請求間隔
FUGLE_MAX_RETRY = 4
FUGLE_TIMEOUT_SEC = 30
FUGLE_RATE_BACKOFF_SEC = 30     # 遇 429 限流每次等候秒數
FUGLE_RATE_MAX_WAITS = 40

_PRICE_COLS = ["date", "open", "high", "low", "close", "volume", "turnover"]
_BULK_COLS = ["date", "stock_id", "open", "high", "low", "close", "volume", "turnover"]


class FugleError(RuntimeError):
    pass


class FugleClient:
    def __init__(self, key: str | None = None, sleep: float | None = None):
        self.key = (key or os.environ.get("FUGLE_API_KEY", "")).strip()
        if not self.key:
            raise FugleError("缺 FUGLE_API_KEY（環境變數或 .env）")
        self.sleep = float(os.environ.get("FUGLE_SLEEP_SEC", sleep if sleep is not None else FUGLE_SLEEP_SEC))
        self.s = requests.Session()
        self.s.headers.update({"X-API-KEY": self.key})

    # ── 底層 ──
    def _get(self, path: str) -> dict:
        url = FUGLE_BASE_URL + path
        attempt = 0
        rate_waits = 0
        last_err = None
        while True:
            try:
                r = self.s.get(url, timeout=FUGLE_TIMEOUT_SEC)
            except requests.RequestException as e:
                attempt += 1
                last_err = e
                if attempt >= FUGLE_MAX_RETRY:
                    raise FugleError(f"{path} 連線重試耗盡：{last_err}")
                time.sleep(1.5 * attempt)
                continue

            if r.status_code == 429:                       # 限流 → 等候後重試
                rate_waits += 1
                if rate_waits > FUGLE_RATE_MAX_WAITS:
                    raise FugleError(f"富果限流持續未解：{path}")
                time.sleep(FUGLE_RATE_BACKOFF_SEC)
                continue

            if r.status_code == 200:
                time.sleep(self.sleep)
                try:
                    return r.json()
                except ValueError:
                    raise FugleError(f"{path} 回傳非 JSON")

            # 404（如非交易日/無資料）當空處理；其餘拋出
            if r.status_code == 404:
                return {}
            raise FugleError(f"{path} http={r.status_code} body={r.text[:160]!r}")

    # ── 資料集 ──
    def snapshot(self) -> pd.DataFrame:
        """整市場「當日」快照（TSE 上市 + OTC 上櫃 各一發）。

        欄位對齊 FinMindClient.price_by_date：
        date/stock_id/open/high/low/close/volume(股)/turnover(元)。
        盤中呼叫拿到的是即時累計；收盤後拿到的是當日收盤。
        """
        frames: list[pd.DataFrame] = []
        for market in ("TSE", "OTC"):
            j = self._get(f"/snapshot/quotes/{market}")
            rows = j.get("data") or []
            if not rows:
                continue
            date = j.get("date")
            df = pd.DataFrame(rows).rename(columns={
                "symbol": "stock_id", "openPrice": "open", "highPrice": "high",
                "lowPrice": "low", "closePrice": "close",
                "tradeVolume": "volume", "tradeValue": "turnover",
            })
            df["date"] = pd.to_datetime(date)
            for col in ("open", "high", "low", "close", "turnover"):
                df[col] = pd.to_numeric(df.get(col), errors="coerce")
            # ⚠️ 快照量是「張」→ ×1000 轉「股」對齊 FinMind
            df["volume"] = pd.to_numeric(df.get("volume"), errors="coerce") * 1000
            frames.append(df[_BULK_COLS])
        if not frames:
            return pd.DataFrame(columns=_BULK_COLS)
        out = pd.concat(frames, ignore_index=True)
        # 濾掉沒成交/無效列（停牌、無開盤）
        out = out[out["close"].notna() & (out["close"] > 0)].reset_index(drop=True)
        return out

    def candles(self, stock_id: str, start: str, end: str) -> pd.DataFrame:
        """單檔歷史日K（升序）。欄位對齊 FinMindClient.price：
        date/open/high/low/close/volume(股)/turnover(元)。"""
        path = (f"/stock/historical/candles/{stock_id}"
                f"?from={start}&to={end}&fields=open,high,low,close,volume,turnover")
        j = self._get(path)
        rows = j.get("data") or []
        if not rows:
            return pd.DataFrame(columns=_PRICE_COLS)
        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["date"])
        for col in ("open", "high", "low", "close", "volume", "turnover"):
            if col not in df.columns:
                df[col] = pd.NA
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.sort_values("date").reset_index(drop=True)
        return df[_PRICE_COLS]

    def quote(self, stock_id: str) -> dict:
        """單檔即時報價（盤中突破監控用）。原樣回傳富果 intraday/quote 結構
        （含 lastPrice / bids / asks / total.tradeVolumeAtBid|Ask 內外盤）。"""
        return self._get(f"/stock/intraday/quote/{stock_id}")
