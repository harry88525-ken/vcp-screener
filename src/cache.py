# -*- coding: utf-8 -*-
"""價格快取（parquet，進 repo）。雲端每日增量更新的基礎。"""
from __future__ import annotations

import os

import pandas as pd

import config as C
from src.finmind_client import FinMindClient

PRICE_DIR = os.path.join("data", "prices")


def _path(stock_id: str) -> str:
    return os.path.join(PRICE_DIR, f"{stock_id}.parquet")


def get_price(client: FinMindClient, stock_id: str, start: str, end: str) -> pd.DataFrame:
    """有快取且覆蓋到 end 就用快取；否則抓 [start,end] 後存檔（增量待 L1 全量時補）。"""
    os.makedirs(PRICE_DIR, exist_ok=True)
    p = _path(stock_id)
    end_ts = pd.Timestamp(end)
    if os.path.exists(p):
        df = pd.read_parquet(p)
        if not df.empty and df["date"].max() >= end_ts:
            return df[df["date"] <= end_ts].reset_index(drop=True)
    df = client.price(stock_id, start, end)
    if not df.empty:
        df.to_parquet(p, index=False)
    return df
