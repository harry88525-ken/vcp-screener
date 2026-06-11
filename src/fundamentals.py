# -*- coding: utf-8 -*-
"""
A-6 基本面（CAN SLIM C+A）— 角色＝加分 + 倉位，非進場觸發
=========================================================
FinMind 財報為「單季」值（已驗：廣達 Q4 Revenue 638.6B ≈ 月營收×3）。
- 季 EPS YoY ≥ 20%（同季去年比）
- 毛利率 / 營益率：持平或上升（最近兩季）
- ROE ≥ 15%（TTM 稅後淨利 ÷ 最新股東權益）
- 月營收：近 3 月累計 YoY ≥ 5% + 季度化加速（本 3 月段 > 前 3 月段）
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import config as C


def _quarterly(fin_df: pd.DataFrame) -> pd.DataFrame:
    """長格式 → 寬格式（index=date 季別，columns=type）。"""
    if fin_df.empty:
        return pd.DataFrame()
    w = fin_df.pivot_table(index="date", columns="type", values="value", aggfunc="last")
    return w.sort_index()


def _yoy(series: pd.Series) -> float:
    if len(series) < 5 or pd.isna(series.iloc[-1]) or pd.isna(series.iloc[-5]) or series.iloc[-5] == 0:
        return float("nan")
    return float(series.iloc[-1] / series.iloc[-5] - 1)


def evaluate(fin_df: pd.DataFrame, bal_df: pd.DataFrame, rev_df: pd.DataFrame) -> dict:
    out = {
        "eps_yoy": None, "eps_yoy_ok": False,
        "gross_margin": None, "margin_trend_ok": False,
        "roe": None, "roe_ok": False,
        "rev_3m_yoy": None, "rev_yoy_ok": False, "rev_accelerating": False,
        "fundamental_ok": False,
    }
    q = _quarterly(fin_df)

    # 季 EPS YoY
    if "EPS" in q:
        out["eps_yoy"] = round(_yoy(q["EPS"]), 4) if np.isfinite(_yoy(q["EPS"])) else None
        out["eps_yoy_ok"] = bool(out["eps_yoy"] is not None and out["eps_yoy"] >= C.EPS_YOY_MIN)

    # 毛利率 / 營益率 持平或上升（最近兩季）
    if {"GrossProfit", "Revenue"}.issubset(q.columns) and len(q) >= 2:
        gm = (q["GrossProfit"] / q["Revenue"]).dropna()
        if len(gm) >= 2:
            out["gross_margin"] = round(float(gm.iloc[-1]), 4)
            gm_ok = gm.iloc[-1] >= gm.iloc[-2] - 0.005
            om_ok = True
            if "OperatingIncome" in q.columns:
                om = (q["OperatingIncome"] / q["Revenue"]).dropna()
                if len(om) >= 2:
                    om_ok = om.iloc[-1] >= om.iloc[-2] - 0.005
            out["margin_trend_ok"] = bool(gm_ok and om_ok)

    # ROE = TTM 稅後淨利 / 最新權益
    if not bal_df.empty and "IncomeAfterTaxes" in q.columns and len(q) >= 4:
        ttm_ni = q["IncomeAfterTaxes"].iloc[-4:].sum()
        bw = bal_df.pivot_table(index="date", columns="type", values="value", aggfunc="last").sort_index()
        eq_col = "EquityAttributableToOwnersOfParent" if "EquityAttributableToOwnersOfParent" in bw.columns else (
            "Equity" if "Equity" in bw.columns else None)
        if eq_col and len(bw) and bw[eq_col].iloc[-1] > 0:
            out["roe"] = round(float(ttm_ni / bw[eq_col].iloc[-1]), 4)
            out["roe_ok"] = bool(out["roe"] >= C.ROE_MIN)

    # 月營收：近 3 月累計 YoY + 季度化加速
    if not rev_df.empty and {"revenue", "revenue_year", "revenue_month"}.issubset(rev_df.columns):
        r = rev_df.sort_values(["revenue_year", "revenue_month"]).reset_index(drop=True)
        rv = r["revenue"].to_numpy(float)
        if len(rv) >= 15:
            cur3 = rv[-3:].sum()
            prev_year3 = rv[-15:-12].sum()
            prev3 = rv[-6:-3].sum()
            prev_year_prev3 = rv[-18:-15].sum() if len(rv) >= 18 else float("nan")
            if prev_year3 > 0:
                out["rev_3m_yoy"] = round(cur3 / prev_year3 - 1, 4)
                out["rev_yoy_ok"] = bool(out["rev_3m_yoy"] >= C.REVENUE_TTM3_YOY_MIN)
            if prev_year3 > 0 and np.isfinite(prev_year_prev3) and prev_year_prev3 > 0:
                cur_yoy = cur3 / prev_year3 - 1
                prev_yoy = prev3 / prev_year_prev3 - 1
                out["rev_accelerating"] = bool(cur_yoy > prev_yoy)

    out["fundamental_ok"] = bool(out["eps_yoy_ok"] and out["margin_trend_ok"]
                                 and out["roe_ok"] and out["rev_yoy_ok"])
    return out
