# -*- coding: utf-8 -*-
"""價格快取（parquet，進 repo）。雲端每日增量更新的基礎。

兩種填法：
- sync_bulk()：用 FinMind by-date bulk（單日全市場，Backer）逐交易日補齊快取。
  冷啟動 ~540 交易日、暖快取每日 1 天。Stage 1 分析全程讀本地快取、0 API。
- get_price()：單檔讀取（offline=True 純讀快取，不打 API）。
"""
from __future__ import annotations

import os

import pandas as pd

import config as C
from src.finmind_client import FinMindClient

PRICE_DIR = os.path.join("data", "prices")
SYNC_MARKER = os.path.join(PRICE_DIR, "_synced_through.txt")


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


def get_price(client: FinMindClient, stock_id: str, start: str, end: str,
              offline: bool = False) -> pd.DataFrame:
    """單檔日線（升序）。

    offline=True：純讀本地快取（sync_bulk 後 Stage 1 用，0 API）；無快取回空表。
    offline=False：快取覆蓋到 end 就用，否則抓 [start,end] 後存檔（單檔 fallback）。
    """
    os.makedirs(PRICE_DIR, exist_ok=True)
    p = _path(stock_id)
    end_ts = pd.Timestamp(end)
    if os.path.exists(p):
        df = pd.read_parquet(p)
        if not df.empty and (offline or df["date"].max() >= end_ts):
            return df[df["date"] <= end_ts].reset_index(drop=True)
    if offline:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume", "turnover"])
    df = client.price(stock_id, start, end)
    if not df.empty:
        df.to_parquet(p, index=False)
    return df
