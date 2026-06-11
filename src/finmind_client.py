# -*- coding: utf-8 -*-
"""
FinMind 資料層
=============
封裝 FinMind v4 API。對外回傳「乾淨、欄位正規化、按日期升序」的 DataFrame。
免費版策略：逐檔抓（整市場 by-date 與還原股價是付費限定）。
抽象化：未來升 Sponsor 只需改 fetch 內部，呼叫端不動。

欄位正規化（TaiwanStockPrice）：max→high, min→low, Trading_Volume→volume,
Trading_money→turnover(成交金額,台幣)。
"""
from __future__ import annotations

import os
import time

import pandas as pd
import requests

import config as C


class FinMindError(RuntimeError):
    pass


class FinMindClient:
    def __init__(self, token: str | None = None, sleep: float = C.FINMIND_SLEEP_SEC):
        self.token = (token or os.environ.get("FINMIND_TOKEN", "")).strip()
        if not self.token:
            raise FinMindError("缺 FINMIND_TOKEN（環境變數或 .env）")
        self.sleep = sleep
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {self.token}"})

    # ── 底層 ──
    def _get(self, dataset: str, **params) -> list[dict]:
        params["dataset"] = dataset
        last_err = None
        for attempt in range(C.FINMIND_MAX_RETRY):
            try:
                r = self.s.get(C.FINMIND_BASE_URL, params=params, timeout=60)
            except requests.RequestException as e:
                last_err = e
                time.sleep(1.5 * (attempt + 1))
                continue
            if r.status_code == 402:  # 額度上限
                raise FinMindError(f"FinMind 額度上限(402)：{dataset} {params}")
            try:
                j = r.json()
            except ValueError:
                last_err = FinMindError(f"非 JSON 回應 http={r.status_code}")
                time.sleep(1.0)
                continue
            if r.status_code == 200 and j.get("status") == 200:
                time.sleep(self.sleep)
                return j.get("data") or []
            # 400 等級錯誤（如付費限定）直接拋，不重試
            raise FinMindError(f"{dataset} http={r.status_code} status={j.get('status')} msg={j.get('msg')!r}")
        raise FinMindError(f"{dataset} 重試耗盡：{last_err}")

    # ── 資料集 ──
    def universe(self) -> pd.DataFrame:
        """全台股清單。欄位 stock_id, stock_name, industry_category, type。"""
        rows = self._get("TaiwanStockInfo")
        df = pd.DataFrame(rows)
        # 同一檔可能多列（歷史 industry 變更）→ 取最後一筆
        df = df.sort_values("date").drop_duplicates("stock_id", keep="last")
        return df[["stock_id", "stock_name", "industry_category", "type"]].reset_index(drop=True)

    def price(self, stock_id: str, start: str, end: str) -> pd.DataFrame:
        """單檔日線。欄位 date/open/high/low/close/volume/turnover（升序）。"""
        rows = self._get("TaiwanStockPrice", data_id=stock_id, start_date=start, end_date=end)
        if not rows:
            return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume", "turnover"])
        df = pd.DataFrame(rows).rename(columns={
            "max": "high", "min": "low",
            "Trading_Volume": "volume", "Trading_money": "turnover",
        })
        df["date"] = pd.to_datetime(df["date"])
        for col in ("open", "high", "low", "close", "volume", "turnover"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.sort_values("date").reset_index(drop=True)
        return df[["date", "open", "high", "low", "close", "volume", "turnover"]]

    def institutional(self, stock_id: str, start: str, end: str) -> pd.DataFrame:
        """三大法人買賣超（長格式 → 寬格式：每日各法人淨買超 + 合計）。"""
        rows = self._get("TaiwanStockInstitutionalInvestorsBuySell",
                         data_id=stock_id, start_date=start, end_date=end)
        if not rows:
            return pd.DataFrame(columns=["date", "net_total"])
        df = pd.DataFrame(rows)
        df["net"] = pd.to_numeric(df["buy"], errors="coerce") - pd.to_numeric(df["sell"], errors="coerce")
        wide = df.pivot_table(index="date", columns="name", values="net", aggfunc="sum").reset_index()
        wide["date"] = pd.to_datetime(wide["date"])
        wide["net_total"] = wide.drop(columns=["date"]).sum(axis=1)
        return wide.sort_values("date").reset_index(drop=True)

    def month_revenue(self, stock_id: str, start: str, end: str) -> pd.DataFrame:
        rows = self._get("TaiwanStockMonthRevenue", data_id=stock_id, start_date=start, end_date=end)
        df = pd.DataFrame(rows)
        if not df.empty:
            df["date"] = pd.to_datetime(df["date"])
            df["revenue"] = pd.to_numeric(df["revenue"], errors="coerce")
        return df

    def financials(self, stock_id: str, start: str, end: str) -> pd.DataFrame:
        """綜合損益表等（長格式 type/value）。原樣回傳，由 fundamentals 模組解析。"""
        rows = self._get("TaiwanStockFinancialStatements", data_id=stock_id, start_date=start, end_date=end)
        df = pd.DataFrame(rows)
        if not df.empty:
            df["date"] = pd.to_datetime(df["date"])
            df["value"] = pd.to_numeric(df["value"], errors="coerce")
        return df

    def index_price(self, index_id: str, start: str, end: str) -> pd.DataFrame:
        """加權報酬指數（RS 分母 / A-1 大盤）。欄位 date/close。"""
        rows = self._get("TaiwanStockTotalReturnIndex", data_id=index_id, start_date=start, end_date=end)
        df = pd.DataFrame(rows)
        if df.empty:
            return pd.DataFrame(columns=["date", "close"])
        df = df.rename(columns={"price": "close"})
        df["date"] = pd.to_datetime(df["date"])
        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        return df.sort_values("date").reset_index(drop=True)[["date", "close"]]
