# -*- coding: utf-8 -*-
"""A-2 趨勢模板 / A-3 RS / A-5 流動性 單元測試。"""
import numpy as np
import pandas as pd

import config as C
from src import indicators, trend_template, rs


def _series_df(start, end, n=260, turnover=1e8):
    close = np.linspace(start, end, n)
    return pd.DataFrame({
        "date": pd.date_range("2025-01-01", periods=n, freq="B"),
        "open": close, "high": close + 0.5, "low": close - 0.5, "close": close,
        "volume": np.full(n, 1_000_000.0), "turnover": np.full(n, float(turnover)),
    })


# ── A-5 流動性 / 指標 ──
def test_trend_metrics_uptrend_alignment_and_liquidity():
    m = indicators.trend_metrics(_series_df(100, 200))
    assert m["ma50"] > m["ma150"] > m["ma200"]          # 均線多頭排列
    assert m["ma200"] > m["ma200_prev"]                 # 200MA 上升
    assert -0.05 < m["dist_52w_high"] <= 0.0            # 貼近 52 週高
    assert m["dist_52w_low"] >= C.DIST_52W_LOW_MIN
    assert m["liquid"] is True


def test_liquidity_below_threshold():
    m = indicators.trend_metrics(_series_df(100, 200, turnover=1e7))   # 1,000 萬 < 5,000 萬
    assert m["liquid"] is False


# ── A-2 趨勢模板 ──
def test_trend_template_uptrend_full_pass():
    df = _series_df(100, 200)
    r = trend_template.evaluate(df, rs_rating=85)
    assert r["price_template_ok"] is True
    assert r["all_ok"] is True
    assert r["passed"] == 8


def test_trend_template_rs_gate_for_8th():
    df = _series_df(100, 200)
    r = trend_template.evaluate(df, rs_rating=70)     # RS 70 < 80
    assert r["price_template_ok"] is True
    assert r["all_ok"] is False
    assert r["conditions"]["8_rs_rating_80"] is False


def test_trend_template_downtrend_fails():
    df = _series_df(200, 100)                          # 下降趨勢
    r = trend_template.evaluate(df, rs_rating=90)
    assert r["price_template_ok"] is False
    assert r["conditions"]["2_150_above_200"] is False


# ── A-3 RS ──
def test_raw_rs_outperform_positive():
    stock = _series_df(100, 220)     # +120%
    index = _series_df(100, 120)     # +20%
    assert rs.raw_rs(stock, index) > 0


def test_raw_rs_underperform_negative():
    stock = _series_df(100, 110)     # +10%
    index = _series_df(100, 150)     # +50%
    assert rs.raw_rs(stock, index) < 0


def test_rs_line_rising():
    stock = _series_df(100, 220)
    index = _series_df(100, 120)
    r = rs.rs_line_metrics(stock, index)
    assert r["rs_line_rising"] is True


def test_percentile_rating_ranks_1_to_99():
    raw = pd.Series([-0.5, -0.1, 0.0, 0.2, 0.9])
    rating = rs.percentile_rating(raw)
    assert rating.min() >= 1 and rating.max() <= 99
    assert rating.iloc[-1] > rating.iloc[0]           # 最強者評分最高
