# -*- coding: utf-8 -*-
"""
A-3 相對強度
===========
RS Rating（數字）：多期相對大盤表現加權 → raw_rs，由 screener 全市場百分位排名成 1-99。
RS 線（個股÷大盤）：方向（上升=必要）、創 52 週新高領先股價（加分旗標·Minervini 頭號訊號）。
免費版用原始收盤價（還原股價付費鎖），股息偏差屬二階，先接受。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import config as C


def _align(stock_df: pd.DataFrame, index_df: pd.DataFrame) -> pd.DataFrame:
    s = stock_df[["date", "close"]].rename(columns={"close": "s"})
    i = index_df[["date", "close"]].rename(columns={"close": "i"})
    m = pd.merge(s, i, on="date", how="inner").sort_values("date").reset_index(drop=True)
    return m


def raw_rs(stock_df: pd.DataFrame, index_df: pd.DataFrame) -> float:
    """多期（3/6/9/12 月）個股報酬 − 大盤報酬，加權。NaN 表示資料不足。"""
    m = _align(stock_df, index_df)
    if len(m) < max(C.RS_WEIGHTS) + 1:
        return float("nan")
    s, i = m["s"].to_numpy(float), m["i"].to_numpy(float)
    total = 0.0
    for days, w in C.RS_WEIGHTS.items():
        if len(m) <= days:
            return float("nan")
        s_ret = s[-1] / s[-1 - days] - 1
        i_ret = i[-1] / i[-1 - days] - 1
        total += w * (s_ret - i_ret)
    return float(total)


def rs_line_metrics(stock_df: pd.DataFrame, index_df: pd.DataFrame) -> dict:
    """RS 線方向 + 是否創 52 週新高領先股價。"""
    m = _align(stock_df, index_df)
    out = {"rs_line_rising": False, "rs_line_new_high": False, "rs_line": float("nan")}
    if len(m) < C.RS_LINE_RISING_LOOKBACK + 1:
        return out
    line = (m["s"] / m["i"]).to_numpy(float)
    out["rs_line"] = float(line[-1])
    out["rs_line_rising"] = bool(line[-1] > line[-1 - C.RS_LINE_RISING_LOOKBACK])

    win = min(C.RS_LINE_NEW_HIGH_WINDOW, len(line))
    rs_at_high = line[-1] >= line[-win:].max() - 1e-12
    # 股價「未」創新高（RS 線領先價格 = Minervini 頭號訊號）
    price = m["s"].to_numpy(float)
    price_not_new_high = price[-1] < price[-win:].max() - 1e-9
    out["rs_line_new_high"] = bool(rs_at_high and price_not_new_high)
    return out


def percentile_rating(raw_values: pd.Series) -> pd.Series:
    """全市場 raw_rs → 1-99 真百分位（IBD 法）。NaN 不參與排名。"""
    pct = raw_values.rank(pct=True, na_option="keep") * 98 + 1
    return pct.round().clip(1, 99)
