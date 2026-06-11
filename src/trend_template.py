# -*- coding: utf-8 -*-
"""
A-2 Minervini 趨勢模板（8 條，已補齊 8/8）
========================================
確認「健康 Stage 2 上升趨勢」，VCP 形態只有在這之上才有意義。
#1-#7 從價格算；#8（RS Rating≥80）需全市場百分位，由 screener 帶入 rs_rating。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import config as C
from src import indicators


def evaluate(df: pd.DataFrame, rs_rating: float | None = None) -> dict:
    m = indicators.trend_metrics(df)
    c, ma50, ma150, ma200 = m["close"], m["ma50"], m["ma150"], m["ma200"]

    conds = {
        "1_close_above_150_200": np.isfinite(ma150) and np.isfinite(ma200) and c > ma150 and c > ma200,
        "2_150_above_200": np.isfinite(ma150) and np.isfinite(ma200) and ma150 > ma200,
        "3_200_rising": np.isfinite(m["ma200_prev"]) and np.isfinite(ma200) and ma200 > m["ma200_prev"],
        "4_50_above_150_200": np.isfinite(ma50) and np.isfinite(ma150) and np.isfinite(ma200) and ma50 > ma150 > ma200,
        "5_close_above_50": np.isfinite(ma50) and c > ma50,
        "6_above_52w_low": np.isfinite(m["dist_52w_low"]) and m["dist_52w_low"] >= C.DIST_52W_LOW_MIN,
        "7_within_52w_high": np.isfinite(m["dist_52w_high"]) and m["dist_52w_high"] >= C.DIST_52W_HIGH_MIN,
    }
    conds = {k: bool(v) for k, v in conds.items()}
    price_ok = all(conds.values())                         # #1-#7（純價格）
    conds["8_rs_rating_80"] = bool(rs_rating is not None and rs_rating >= C.RS_RATING_MIN)
    passed = sum(conds.values())
    return {
        "conditions": conds,
        "passed": passed,
        "price_template_ok": price_ok,          # 7 條價格門票
        "all_ok": price_ok and conds["8_rs_rating_80"],   # 完整 8/8
        "metrics": m,
    }
