# -*- coding: utf-8 -*-
"""
Fugle 富果行情資料層（每日價格迴圈的優先來源）
=============================================
只負責一件事：historical/candles 日線 → 與 finmind_client.price() 完全同構的
DataFrame（date/open/high/low/close/volume/turnover，升序）。呼叫端（cache.get_price）
拿到空表或例外就退回 FinMind，故本模組所有錯誤都「快速失敗」，不做長時間等待。

實測（2026-08-12 PoC）：
- 欄位語意與 FinMind TaiwanStockPrice 逐位一致（volume=股、turnover=成交金額、原始未還原價）
- 基本（免費）層 rate limit 60 req/min → FUGLE_SLEEP_SEC 節流
- 單請求跨度必須 < 1 年（400: Date range must be less than one year）→ 自動拆段
- 快照 /snapshot/* 為開發者方案（NT$1499/月）以上專屬，本模組不用

保命線：連續硬失敗（斷網/金鑰失效）達 FUGLE_MAX_FAIL_STREAK 次即整跑停用，
之後所有呼叫立即拋 FugleError（零成本），由 cache 層整體退回 FinMind 舊路徑。
"""
from __future__ import annotations

import datetime as dt
import os
import time

import pandas as pd
import requests

import config as C

_COLS = ["date", "open", "high", "low", "close", "volume", "turnover"]


class FugleError(RuntimeError):
    pass


class FugleClient:
    """無 FUGLE_KEY 時 enabled=False，candles() 直接拋 FugleError（呼叫端退 FinMind）。"""

    def __init__(self, key: str | None = None, sleep: float | None = None):
        self.key = (key or os.environ.get("FUGLE_KEY", "")).strip()
        self.sleep = float(os.environ.get("FUGLE_SLEEP_SEC",
                                          sleep if sleep is not None else C.FUGLE_SLEEP_SEC))
        self.enabled = bool(self.key)
        self.fail_streak = 0          # 連續「硬失敗」（網路/授權），404 無資料不算
        self.s = requests.Session()
        if self.key:
            self.s.headers.update({"X-API-KEY": self.key})

    # ── 內部 ──
    def _hard_fail(self, msg: str) -> FugleError:
        self.fail_streak += 1
        if self.fail_streak >= C.FUGLE_MAX_FAIL_STREAK:
            self.enabled = False      # 保命線：整跑停用，之後零成本快速拋錯
        return FugleError(msg)

    def _get_chunk(self, stock_id: str, start: str, end: str) -> list[dict]:
        """單段（跨度 <1 年）。404=無此檔 → 回空 list；硬錯誤 → 拋 FugleError。"""
        url = f"{C.FUGLE_BASE_URL}/historical/candles/{stock_id}"
        params = {"from": start, "to": end,
                  "fields": "open,high,low,close,volume,turnover"}
        attempt = 0
        while True:
            try:
                r = self.s.get(url, params=params, timeout=C.FUGLE_TIMEOUT_SEC)
            except requests.RequestException as e:
                attempt += 1
                if attempt >= C.FUGLE_MAX_RETRY:
                    raise self._hard_fail(f"Fugle candles {stock_id} 連線失敗：{e}")
                time.sleep(1.0 * attempt)
                continue

            if r.status_code == 200:
                self.fail_streak = 0
                time.sleep(self.sleep)
                j = r.json()
                return j.get("data") or []
            if r.status_code == 404:            # 該檔 Fugle 沒有（下市/特殊股）→ 交給 FinMind
                self.fail_streak = 0
                time.sleep(self.sleep)
                return []
            if r.status_code == 429:            # 限流：等一下重試，不算硬失敗
                attempt += 1
                if attempt >= C.FUGLE_MAX_RETRY:
                    raise self._hard_fail(f"Fugle candles {stock_id} 限流重試耗盡")
                wait = float(r.headers.get("Retry-After") or 5)
                time.sleep(min(wait, 30.0))
                continue
            # 401/403（金鑰失效/方案不含）或其他 → 硬失敗
            raise self._hard_fail(
                f"Fugle candles {stock_id} http={r.status_code} body={r.text[:120]!r}")

    # ── 對外 ──
    def candles(self, stock_id: str, start: str, end: str) -> pd.DataFrame:
        """日線（升序）。欄位與 finmind_client.price() 相同。

        跨度 > FUGLE_SPAN_DAYS 自動拆段（Fugle 限制單請求 <1 年）。
        無資料回空表；硬失敗拋 FugleError（含停用後的快速拋錯）。
        """
        if not self.enabled:
            raise FugleError("Fugle 已停用（無金鑰或連續失敗達上限）")
        s = dt.date.fromisoformat(start)
        e = dt.date.fromisoformat(end)
        if s > e:
            return pd.DataFrame(columns=_COLS)

        rows: list[dict] = []
        cur = s
        while cur <= e:
            chunk_end = min(cur + dt.timedelta(days=C.FUGLE_SPAN_DAYS - 1), e)
            rows.extend(self._get_chunk(stock_id, cur.isoformat(), chunk_end.isoformat()))
            cur = chunk_end + dt.timedelta(days=1)
        if not rows:
            return pd.DataFrame(columns=_COLS)

        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["date"])
        for col in ("open", "high", "low", "close", "volume", "turnover"):
            df[col] = pd.to_numeric(df.get(col), errors="coerce")
        df = (df.drop_duplicates("date", keep="last")
                .sort_values("date").reset_index(drop=True))
        return df[_COLS]
