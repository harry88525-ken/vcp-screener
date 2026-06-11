# -*- coding: utf-8 -*-
"""
A-1 市場紅綠燈 — 覆巢之下無完卵，先看大盤再選股
=============================================
v1 用可穩定計算的訊號：① 指數趨勢（vs 20/60/120MA + 季線上彎）③ 市場廣度（站上200MA家數%）
⑤ 領導股守50MA%。② Follow-through、④ 派發日（需指數量能）標記「v1 待補」。
合成 → 🟢/🟡/🔴 → 倉位係數。一票否決：廣度 <40% → 紅。
"""
from __future__ import annotations

import numpy as np

import config as C
from src import indicators


def evaluate(index_df, breadth_pct: float, leaders_hold_50ma: float | None = None) -> dict:
    close = index_df["close"]
    ma20 = indicators.sma(close, 20).iloc[-1]
    ma60 = indicators.sma(close, 60).iloc[-1]
    ma120 = indicators.sma(close, 120).iloc[-1]
    ma60_series = indicators.sma(close, 60)
    c = float(close.iloc[-1])
    ma60_rising = len(ma60_series) > 22 and np.isfinite(ma60_series.iloc[-22]) and ma60 > ma60_series.iloc[-22]

    # ① 指數趨勢
    if np.isfinite(ma60) and c < ma60:
        trend_signal = 0                                   # 跌破季線 = 紅
    elif np.isfinite(ma20) and np.isfinite(ma120) and c > ma20 and c > ma60 and c > ma120 and ma60_rising:
        trend_signal = 2                                   # 站上全部 + 季線上彎 = 綠
    else:
        trend_signal = 1

    # ③ 市場廣度
    breadth_signal = 2 if breadth_pct > 0.60 else (1 if breadth_pct >= 0.40 else 0)

    signals = {"trend": trend_signal, "breadth": breadth_signal}
    # ⑤ 領導股守 50MA（可選）
    if leaders_hold_50ma is not None:
        signals["leaders"] = 2 if leaders_hold_50ma > 0.70 else (0 if leaders_hold_50ma < 0.50 else 1)

    score = sum(signals.values()) / (len(signals) * 2)     # 0..1
    veto = breadth_pct < C.BREADTH_VETO                    # 一票否決
    if veto or score < 0.40:
        light = "red"
    elif score >= 0.75:
        light = "green"
    else:
        light = "yellow"

    return {
        "light": light,
        "score": round(score, 3),
        "position_factor": C.POSITION_FACTOR[light],
        "index_close": round(c, 2),
        "breadth_pct": round(breadth_pct, 3),
        "trend_signal": trend_signal,
        "signals": signals,
        "veto_breadth": bool(veto),
        "todo": ["follow_through(②)", "distribution_days(④需指數量能)"],
    }
