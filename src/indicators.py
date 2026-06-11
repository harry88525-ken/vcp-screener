# -*- coding: utf-8 -*-
"""共用技術指標（單檔價格 DataFrame → 最新指標值）。"""
from __future__ import annotations

import numpy as np
import pandas as pd

import config as C


def sma(series: pd.Series, n: int) -> pd.Series:
    return series.rolling(n, min_periods=n).mean()


def _safe(x) -> float:
    try:
        v = float(x)
        return v if np.isfinite(v) else float("nan")
    except (TypeError, ValueError):
        return float("nan")


def trend_metrics(df: pd.DataFrame) -> dict:
    """回傳趨勢/位置/流動性所需的最新值。資料不足的欄位為 NaN。"""
    close = df["close"]
    ma50, ma150, ma200 = sma(close, C.MA_FAST), sma(close, C.MA_MID), sma(close, C.MA_SLOW)
    n = len(df)
    win = C.TRADING_DAYS_PER_YEAR
    hi = df["high"].iloc[-win:].max()
    lo = df["low"].iloc[-win:].min()
    c = _safe(close.iloc[-1])

    ma200_prev = float("nan")
    if n >= C.MA_SLOW + C.MA200_RISING_LOOKBACK:
        ma200_prev = _safe(ma200.iloc[-1 - C.MA200_RISING_LOOKBACK])

    out = {
        "close": c,
        "ma50": _safe(ma50.iloc[-1]),
        "ma150": _safe(ma150.iloc[-1]),
        "ma200": _safe(ma200.iloc[-1]),
        "ma200_prev": ma200_prev,
        "high_52w": _safe(hi),
        "low_52w": _safe(lo),
        "avg_turnover_50": _safe(df["turnover"].iloc[-C.LIQUIDITY_WINDOW:].mean()),
    }
    out["dist_52w_high"] = (c / out["high_52w"] - 1) if out["high_52w"] > 0 else float("nan")
    out["dist_52w_low"] = (c / out["low_52w"] - 1) if out["low_52w"] > 0 else float("nan")
    out["liquid"] = bool(out["avg_turnover_50"] >= C.LIQUIDITY_MIN_TURNOVER)
    return out
