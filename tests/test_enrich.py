# -*- coding: utf-8 -*-
"""A-6 基本面 / A-7 籌碼 / A-8 族群 / A-1 紅綠燈 單元測試。"""
import numpy as np
import pandas as pd

from src import chips, fundamentals, groups, market_light


# ── A-7 籌碼 ──
def test_chips_net_buy_and_trust_streak():
    df = pd.DataFrame({
        "date": pd.date_range("2026-03-01", periods=12, freq="B"),
        "Investment_Trust": [1, 1, -1, 2, 3, 1, 2, 1, 3, 2, 4, 5],   # 末 5 連正
        "net_total": [10, -5, 3, 8, 9, 4, 7, 6, 5, 4, 9, 8],
    })
    r = chips.evaluate(df)
    assert r["inst_net_buy"] is True
    assert r["trust_streak"] >= 3 and r["trust_consecutive_buy"] is True
    assert r["chips_ok"] is True


def test_chips_margin_overheat_flag():
    inst = pd.DataFrame({"date": pd.date_range("2026-03-01", periods=10, freq="B"),
                         "net_total": [1] * 10, "Investment_Trust": [1] * 10})
    margin = pd.DataFrame({"date": pd.date_range("2026-03-01", periods=3, freq="B"),
                           "MarginPurchaseTodayBalance": [95, 96, 98],
                           "MarginPurchaseLimit": [100, 100, 100]})
    r = chips.evaluate(inst, margin)
    assert r["margin_overheat"] is True
    assert r["chips_ok"] is False        # 法人買但融資過熱 → 不 ok


# ── A-6 基本面 ──
def _fin_long(qs, eps, rev, gp, oi, ni):
    rows = []
    for i, q in enumerate(qs):
        rows += [{"date": q, "type": "EPS", "value": eps[i]},
                 {"date": q, "type": "Revenue", "value": rev[i]},
                 {"date": q, "type": "GrossProfit", "value": gp[i]},
                 {"date": q, "type": "OperatingIncome", "value": oi[i]},
                 {"date": q, "type": "IncomeAfterTaxes", "value": ni[i]}]
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    df["value"] = df["value"].astype(float)
    return df


def test_fundamentals_full_pass():
    qs = ["2024-03-31", "2024-06-30", "2024-09-30", "2024-12-31",
          "2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31"]
    fin = _fin_long(qs, eps=[1, 1, 1, 1, 1.3, 1.3, 1.4, 1.5], rev=[100] * 8,
                    gp=[20, 20, 20, 20, 21, 21, 22, 23], oi=[10] * 8, ni=[15] * 8)
    bal = pd.DataFrame({"date": pd.to_datetime(["2025-12-31"]), "type": ["Equity"], "value": [300.0]})
    rev_rows = [{"date": f"{2024 + k // 12}-{k % 12 + 1:02d}-10",
                 "revenue": 100 + k, "revenue_year": 2024 + k // 12, "revenue_month": k % 12 + 1}
                for k in range(18)]
    r = fundamentals.evaluate(fin, bal, pd.DataFrame(rev_rows))
    assert r["eps_yoy_ok"] is True            # 1.5/1.0 = +50%
    assert r["roe_ok"] is True                # TTM 60 / 300 = 20%
    assert r["margin_trend_ok"] is True       # 毛利率上升
    assert r["rev_yoy_ok"] is True
    assert r["fundamental_ok"] is True


# ── A-8 族群 ──
def _grow(ind, rsv, close, ma200, vcp):
    return {"industry": ind, "rs_rating": rsv,
            "trend_metrics": {"close": close, "ma200": ma200}, "vcp": {"is_vcp": vcp}}


def test_groups_ranking_consistency_top():
    rows = [_grow("半導體", 95, 100, 90, True), _grow("半導體", 92, 100, 90, True),
            _grow("半導體", 90, 100, 90, True), _grow("航運", 40, 100, 90, False),
            _grow("食品", 30, 100, 110, False)]
    groups.annotate(rows)
    semis = [r for r in rows if r["industry"] == "半導體"]
    assert all(r["group_rank"] == 1 for r in semis)   # 最強族群
    assert all(r["group_top"] for r in semis)
    assert semis[0]["group_real"] is True             # ≥3 檔 VCP


# ── A-1 市場紅綠燈 ──
def test_market_light_green_and_red_veto():
    n = 160
    df = pd.DataFrame({"date": pd.date_range("2025-06-01", periods=n, freq="B"),
                       "close": np.linspace(100, 150, n)})
    g = market_light.evaluate(df, breadth_pct=0.80)
    assert g["light"] == "green" and g["position_factor"] == 1.0
    r = market_light.evaluate(df, breadth_pct=0.30)
    assert r["light"] == "red" and r["veto_breadth"] is True
