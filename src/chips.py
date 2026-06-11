# -*- coding: utf-8 -*-
"""
A-7 籌碼 / 法人（CAN SLIM I）— 看大錢腳印，VCP 突破靠法人
======================================================
- 三大法人近 5/10 日淨買超（必要）
- 投信連 3 日以上買超（加分·飆股訊號）
- 突破日法人買超（確認突破真實·過濾假突破）→ 由 screener 在突破日取用 today_net
- 融資過熱（反指標·扣分）：融資使用率高 / 餘額暴增
台股每日盤後即公布三大法人，比美股 13F 季報快。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import config as C


def evaluate(inst_df: pd.DataFrame, margin_df: pd.DataFrame | None = None) -> dict:
    out = {
        "net5": None, "net10": None, "inst_net_buy": False,
        "trust_streak": 0, "trust_consecutive_buy": False,
        "today_net": None, "margin_overheat": False, "chips_ok": False,
    }
    if inst_df.empty or "net_total" not in inst_df.columns:
        return out

    nt = inst_df["net_total"].to_numpy(float)
    out["net5"] = float(nt[-5:].sum())
    out["net10"] = float(nt[-10:].sum())
    out["today_net"] = float(nt[-1])
    out["inst_net_buy"] = bool(out["net5"] > 0 and out["net10"] > 0)   # 必要

    # 投信連買（由近到遠數連續正值）
    if "Investment_Trust" in inst_df.columns:
        trust = inst_df["Investment_Trust"].fillna(0).to_numpy(float)
        streak = 0
        for v in trust[::-1]:
            if v > 0:
                streak += 1
            else:
                break
        out["trust_streak"] = int(streak)
        out["trust_consecutive_buy"] = bool(streak >= C.TRUST_CONSECUTIVE_BUY_DAYS)

    # 融資過熱（反指標）：使用率 = 今日餘額 / 限額
    if margin_df is not None and not margin_df.empty:
        m = margin_df.sort_values("date")
        try:
            bal = float(m["MarginPurchaseTodayBalance"].iloc[-1])
            lim = float(m["MarginPurchaseLimit"].iloc[-1])
            if lim > 0:
                out["margin_overheat"] = bool(bal / lim >= C.MARGIN_OVERHEAT_PCT)
        except (KeyError, ValueError, TypeError):
            pass

    # chips_ok = 法人在買（必要）且融資未過熱
    out["chips_ok"] = bool(out["inst_net_buy"] and not out["margin_overheat"])
    return out
