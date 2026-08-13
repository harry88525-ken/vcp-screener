# -*- coding: utf-8 -*-
"""價格快取（parquet，進 repo）。雲端每日增量更新的基礎。

兩種填法：
- sync_bulk()：用 FinMind by-date bulk（單日全市場，Backer）逐交易日補齊快取。
  冷啟動 ~540 交易日、暖快取每日 1 天。Stage 1 分析全程讀本地快取、0 API。
- get_price()：單檔讀取（offline=True 純讀快取，不打 API）。
"""
from __future__ import annotations

import os
import time

import pandas as pd

import config as C
from src.finmind_client import FinMindClient
from src.fugle_client import FugleClient, FugleError

PRICE_DIR = os.path.join("data", "prices")
SYNC_MARKER = os.path.join(PRICE_DIR, "_synced_through.txt")
FUND_DIR = os.path.join("data", "fundamentals")
CHAIN_PATH = os.path.join("data", "industry_chain.parquet")


def _path(stock_id: str) -> str:
    return os.path.join(PRICE_DIR, f"{stock_id}.parquet")


def read_marker() -> str | None:
    """已同步到哪個交易日（ISO 字串）。冷啟動回 None。"""
    if os.path.exists(SYNC_MARKER):
        v = open(SYNC_MARKER, encoding="utf-8").read().strip()
        return v or None
    return None


def _merge_to_disk(frame: pd.DataFrame, universe_ids: set[str]) -> None:
    """把累積的多日全市場資料，按 stock_id 併進各自的 parquet（去重、升序）。"""
    if frame.empty:
        return
    sub = frame[frame["stock_id"].isin(universe_ids)]
    for sid, g in sub.groupby("stock_id"):
        p = _path(sid)
        g = g.drop(columns=["stock_id"]).sort_values("date")
        if os.path.exists(p):
            old = pd.read_parquet(p)
            g = pd.concat([old, g], ignore_index=True)
        g = g.drop_duplicates("date", keep="last").sort_values("date").reset_index(drop=True)
        g.to_parquet(p, index=False)


def sync_bulk(client: FinMindClient, trading_days: list[str], universe_ids,
              commit_cb=None, chunk: int = None) -> int:
    """以 by-date bulk 把全市場價格快取補齊到最新交易日。

    trading_days：升序 ISO 交易日（取自 index_df，只打真正開市日）。
    每 chunk 天 flush 到磁碟 + 更新 marker + commit_cb（增量 commit 防逾時蒸發）。
    回傳實際抓取的天數。
    """
    os.makedirs(PRICE_DIR, exist_ok=True)
    chunk = chunk or C.BACKFILL_CHUNK_DAYS
    uni = set(universe_ids)
    marker = read_marker()
    todo = [d for d in trading_days if marker is None or d > marker]
    if not todo:
        return 0

    buf: list[pd.DataFrame] = []
    fetched = 0

    def flush(upto: str):
        if buf:
            _merge_to_disk(pd.concat(buf, ignore_index=True), uni)
            buf.clear()
        with open(SYNC_MARKER, "w", encoding="utf-8") as f:
            f.write(upto)
        if commit_cb:
            commit_cb(upto)

    for i, d in enumerate(todo):
        df = client.price_by_date(d)
        if not df.empty:
            buf.append(df)
        fetched += 1
        if (i + 1) % chunk == 0:
            flush(d)
    flush(todo[-1])
    return fetched


def get_fundamental(fetch, name: str, stock_id: str, start: str, end: str,
                    stale_days: int = None) -> pd.DataFrame:
    """季/月財報長期快取（財報/資產負債/月營收）。

    這些是「重請求」（抓 3 年）且季月才更新——快取後 enrich 大幅省呼叫，
    也避開了害 enrich 卡死的長連線。快取新鮮（檔齡 < stale_days）就直接用，否則重抓。
    fetch：client 的方法（financials/balance_sheet/month_revenue）。
    """
    stale_days = stale_days if stale_days is not None else C.FUNDAMENTAL_CACHE_DAYS
    d = os.path.join(FUND_DIR, name)
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, f"{stock_id}.parquet")
    if os.path.exists(p) and (time.time() - os.path.getmtime(p)) / 86400 < stale_days:
        return pd.read_parquet(p)
    df = fetch(stock_id, start, end)
    if not df.empty:
        df.to_parquet(p, index=False)
    return df


def get_industry_chain(client: FinMindClient, stale_days: int = None) -> pd.DataFrame:
    """產業鏈分類長期快取（分類極少變、抓一次存著）。回傳 stock_id/sub_industry。"""
    stale_days = stale_days if stale_days is not None else C.GROUP_CHAIN_CACHE_DAYS
    if os.path.exists(CHAIN_PATH) and (time.time() - os.path.getmtime(CHAIN_PATH)) / 86400 < stale_days:
        return pd.read_parquet(CHAIN_PATH)
    try:
        df = client.industry_chain()
    except Exception:
        # 免費版抓不到（Backer 資料集）→ 退用過期舊快取（產業鏈分類極少變，舊的堪用）；沒有才回空表
        if os.path.exists(CHAIN_PATH):
            return pd.read_parquet(CHAIN_PATH)
        return pd.DataFrame(columns=["stock_id", "sub_industry"])
    if not df.empty:
        os.makedirs(os.path.dirname(CHAIN_PATH), exist_ok=True)
        df.to_parquet(CHAIN_PATH, index=False)
    return df


_FUGLE: FugleClient | None = None
_FUGLE_WARNED = False


def _fugle() -> FugleClient:
    """模組級單例：整跑共用同一個 FugleClient（fail-streak 保命線才會跨檔累積）。"""
    global _FUGLE
    if _FUGLE is None:
        _FUGLE = FugleClient()
    return _FUGLE


def get_price(client: FinMindClient, stock_id: str, start: str, end: str,
              offline: bool = False) -> pd.DataFrame:
    """單檔日線（升序）。

    offline=True：純讀本地快取（sync_bulk 後 Stage 1 用，0 API）；無快取回空表。
    offline=False：快取覆蓋到 end 就用；否則先走 Fugle candles「增量」補缺的日期
    （只抓快取最後一天之後，暖快取每檔 1 request，60/min ≈ 全市場 30 分）；
    Fugle 無此檔（404）/金鑰失效/斷網 → 整體退回 FinMind 舊路徑（抓 [start,end] 全段）。
    """
    os.makedirs(PRICE_DIR, exist_ok=True)
    p = _path(stock_id)
    end_ts = pd.Timestamp(end)
    cached = None
    if os.path.exists(p):
        cached = pd.read_parquet(p)
        if not cached.empty and (offline or cached["date"].max() >= end_ts):
            return cached[cached["date"] <= end_ts].reset_index(drop=True)
    if offline:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume", "turnover"])

    # ── 優先：Fugle 增量（欄位語意與 FinMind 逐位一致，實測 2026-08-12 PoC）──
    try:
        if cached is not None and not cached.empty:
            fetch_start = (cached["date"].max() + pd.Timedelta(days=1)).date().isoformat()
        else:
            fetch_start = start
        got = _fugle().candles(stock_id, fetch_start, end)
        if not got.empty:
            df = pd.concat([cached, got], ignore_index=True) if cached is not None else got
            df = df.drop_duplicates("date", keep="last").sort_values("date").reset_index(drop=True)
            if df["date"].max() >= end_ts:      # 補到位才算成功，否則交給 FinMind
                df.to_parquet(p, index=False)
                return df[df["date"] <= end_ts].reset_index(drop=True)
    except Exception as e:                     # 任何 Fugle 問題都不准中斷掃描 → 退 FinMind
        global _FUGLE_WARNED
        if not _FUGLE_WARNED:                  # 只提示一次，避免全市場刷 1800 行
            print(f"[fugle] 退回 FinMind：{e}")
            _FUGLE_WARNED = True

    # ── 兜底：FinMind 舊路徑（行為與改版前完全相同）──
    df = client.price(stock_id, start, end)
    if not df.empty:
        df.to_parquet(p, index=False)
    return df
