# -*- coding: utf-8 -*-
"""
VCP 引擎單元測試 — 只驗「確定性數學邏輯」。
zigzag 端到端行為的校準（段數、k 值）留給真實資料（廣達）回歸，不在此硬驗。
"""
import numpy as np
import pandas as pd

import config as C
from src import vcp


# ── ATR ──
def test_wilder_atr_length_and_positive():
    high = np.array([10, 11, 12, 11, 13, 12, 14], dtype=float)
    low = np.array([9, 10, 11, 10, 11, 11, 12], dtype=float)
    close = np.array([9.5, 10.5, 11.5, 10.5, 12, 11.5, 13], dtype=float)
    atr = vcp.wilder_atr(high, low, close, period=3)
    assert len(atr) == len(high)
    assert np.all(atr > 0)


# ── 收縮序列抽取 / 配對 ──
def _base_pivots():
    # 高→低交替；深度 12% / 6% / 3%，基底長度 40 交易日
    return [
        {"idx": 100, "price": 200.0, "kind": "H"},
        {"idx": 108, "price": 176.0, "kind": "L"},   # 12% of 200
        {"idx": 116, "price": 197.0, "kind": "H"},
        {"idx": 124, "price": 185.18, "kind": "L"},  # 6% of 197
        {"idx": 132, "price": 193.0, "kind": "H"},
        {"idx": 140, "price": 187.21, "kind": "L"},  # 3% of 193
    ]


def test_extract_contractions_pairs_and_depths():
    contractions = vcp.extract_contractions(_base_pivots(), lookback_start_idx=0)
    depths = [round(c["depth"], 3) for c in contractions]
    assert depths == [0.12, 0.06, 0.03]
    assert contractions[0]["high"] == 200.0 and contractions[-1]["low"] == 187.21


def test_extract_starts_at_highest_high():
    # 前面塞一個較低的高點，基底應從最高高(200)起算
    pivots = [{"idx": 90, "price": 150.0, "kind": "H"},
              {"idx": 95, "price": 140.0, "kind": "L"}] + _base_pivots()
    contractions = vcp.extract_contractions(pivots, lookback_start_idx=0)
    assert contractions[0]["high"] == 200.0
    assert len(contractions) == 3


# ── 收縮序列驗證 ──
def test_validate_pass_clean_vcp():
    contractions = vcp.extract_contractions(_base_pivots(), lookback_start_idx=0)
    v = vcp.validate_contractions(contractions)
    assert v["valid"] is True, v["reasons"]
    assert v["n"] == 3
    assert v["duration"] == 40


def test_validate_fail_not_decreasing():
    contractions = [
        {"high_idx": 0, "high": 100.0, "low_idx": 8, "low": 95.0, "depth": 0.05},
        {"high_idx": 16, "high": 100.0, "low_idx": 24, "low": 91.0, "depth": 0.09},
    ]
    v = vcp.validate_contractions(contractions)
    assert v["valid"] is False
    assert any("未收窄" in r for r in v["reasons"])


def test_validate_fail_last_over_first_ratio():
    # 兩段 10%→6%，末段/首段 = 0.6 ≥ 0.35 → 應被擋
    contractions = [
        {"high_idx": 0, "high": 100.0, "low_idx": 8, "low": 90.0, "depth": 0.10},
        {"high_idx": 16, "high": 100.0, "low_idx": 24, "low": 94.0, "depth": 0.06},
    ]
    v = vcp.validate_contractions(contractions)
    assert v["valid"] is False
    assert any("末段/首段" in r for r in v["reasons"])


def test_validate_fail_too_deep_base():
    contractions = [
        {"high_idx": 0, "high": 100.0, "low_idx": 8, "low": 60.0, "depth": 0.40},
        {"high_idx": 16, "high": 90.0, "low_idx": 24, "low": 84.6, "depth": 0.06},
        {"high_idx": 32, "high": 88.0, "low_idx": 40, "low": 85.4, "depth": 0.03},
    ]
    v = vcp.validate_contractions(contractions)
    assert v["valid"] is False
    assert any("基底深度" in r for r in v["reasons"])


# ── 樞紐 + 量縮 ──
def _make_df(n=80, base=190.0):
    """造一段資料：前段平緩、末 10 日緊縮(約 2%)、量遞減。"""
    rng = np.random.default_rng(0)
    highs, lows, closes, vols = [], [], [], []
    for i in range(n):
        if i < n - C.PIVOT_WINDOW:
            mid = base + np.sin(i / 5) * 6     # 前段較寬幅震盪
            v = 5000 - i * 10                   # 量遞減
        else:
            mid = base + (rng.random() - 0.5) * 3  # 末段 ~±1.5（窄）
            v = 2500 - (i - (n - C.PIVOT_WINDOW)) * 50
        highs.append(mid + 1.5)
        lows.append(mid - 1.5)
        closes.append(mid)
        vols.append(max(v, 500))
    return pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=n, freq="B"),
        "open": closes, "high": highs, "low": lows, "close": closes, "volume": vols,
    })


def test_analyze_pivot_tight_and_volume_contraction():
    df = _make_df()
    piv = vcp.analyze_pivot(df)
    assert piv["tight"] is True
    assert piv["width"] < C.PIVOT_WIDTH_MAX
    assert piv["vol_contraction"] is True   # 末段量 < 均量 且 斜率<0


# ── 突破 ──
def test_detect_breakout_states():
    df = _make_df()
    piv = vcp.analyze_pivot(df)
    ph, av = piv["pivot_high"], piv["avg_vol"]
    # 待突破：收在樞紐高下方
    df1 = df.copy()
    df1.loc[df1.index[-1], "close"] = ph - 2
    assert vcp.detect_breakout(df1, ph, av)["status"] == "待突破"
    # 突破中：收破樞紐高 + 爆量
    df2 = df.copy()
    df2.loc[df2.index[-1], "close"] = ph + 0.5
    df2.loc[df2.index[-1], "volume"] = av * 2
    assert vcp.detect_breakout(df2, ph, av)["status"] == "突破中"
    # 已延伸：收 >5% 樞紐高上方
    df3 = df.copy()
    df3.loc[df3.index[-1], "close"] = ph * 1.08
    df3.loc[df3.index[-1], "volume"] = av * 2
    assert vcp.detect_breakout(df3, ph, av)["status"] == "已延伸"


# ── 評分 ──
def test_score_vcp_grade_A():
    g = vcp.score_vcp(seq_valid=True, n=3, vol_contraction=True,
                      rs_line_new_high=True, pivot_width=0.05, high_at_start=True)
    assert g == "A"


def test_score_vcp_grade_C():
    g = vcp.score_vcp(seq_valid=False, n=2, vol_contraction=False,
                      rs_line_new_high=False, pivot_width=0.09, high_at_start=False)
    assert g == "C"


# ── 風控 ──
def test_risk_metrics_uses_tighter_stop():
    rk = vcp.risk_metrics(pivot_high=193.0, pivot_low=185.0, last_contraction_low=187.0)
    assert rk["entry"] == 193.0
    assert rk["stop"] == 187.0          # 取較高(較緊)的停損
    assert abs(rk["risk_pct"] - (193 - 187) / 193) < 1e-9


# ── 頂層 smoke（不硬驗 zigzag 結果，只確保跑通且結構正確）──
def test_analyze_smoke_returns_result():
    df = _make_df(n=120)
    res = vcp.analyze(df, rs_line_new_high=False, dist_52w_high=-0.05)
    assert isinstance(res, vcp.VCPResult)
    d = res.to_dict()
    for key in ("is_vcp", "grade", "contractions", "pivot_high", "stop", "risk_pct", "breakout_status"):
        assert key in d


def test_analyze_near_high_gate_blocks_far_from_high():
    df = _make_df(n=120)
    res = vcp.analyze(df, dist_52w_high=-0.40)   # 距高 40% > 25% 門檻
    assert res.is_vcp is False
    assert any("距52週高" in r for r in res.reasons)


def test_analyze_tightness_gate_passes_without_clean_sequence():
    """緊度派核心：近高 + 樞紐緊 → is_vcp True，即使沒有乾淨收斂序列。"""
    df = _make_df(n=120)              # 末 10 日緊（~2%）
    res = vcp.analyze(df, rs_line_new_high=False, dist_52w_high=-0.05)
    assert res.is_vcp is True
    assert res.pivot_width < C.PIVOT_WIDTH_MAX


def test_analyze_loose_pivot_gate_blocks():
    """樞紐鬆（末 10 日寬幅震盪）→ is_vcp False。"""
    rng = np.random.default_rng(1)
    n = 120
    mid = 190 + np.concatenate([np.zeros(n - C.PIVOT_WINDOW),
                                (rng.random(C.PIVOT_WINDOW) - 0.5) * 60])  # 末段 ±30 寬
    df = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=n, freq="B"),
        "open": mid, "high": mid + 2, "low": mid - 2, "close": mid,
        "volume": np.linspace(5000, 3000, n),
    })
    res = vcp.analyze(df, dist_52w_high=-0.05)
    assert res.is_vcp is False
    assert any("樞紐寬" in r for r in res.reasons)
